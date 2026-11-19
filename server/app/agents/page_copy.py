"""Page copy preparation, validation and bounded generation.

The renderer owns slot visibility. This module owns factual copy and never changes
the storyboard, catalog, seller comment or assembly output in place.
"""
import asyncio
from dataclasses import replace
from collections import Counter
from copy import deepcopy
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import re
import time
import unicodedata

from . import vision_llm
from . import page_copy_checks as checks
from .page_copy_body_effects import check_body_effect
from .page_copy_items import compact_section_texts
from .fit_axes import FIT_AXES, confirmed_copy_fit
from .page_template_assembler import assemble_v4, wrap_text
from .page_templates import CATALOG, text_slot_for_role, walk_layout

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "page_copy"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GROUPS = ("intro", "points", "fabric", "voice", "misc")
PROVIDERS = ("gpt", "gemini", "claude")


class CallLimitError(RuntimeError):
    """The evaluation runner's hard budget stop; never treated as provider failure."""


def _strings(values):
    return list(dict.fromkeys(v.strip() for v in values or [] if isinstance(v, str) and v.strip()))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def template_of(storyboard, analysis):
    value = ((analysis or {}).get("pageStyle") or {}).get("templateId")
    if not value:
        value = str((storyboard or [{}])[0].get("pageSectionKey") or "emo").split(".")[0]
    if value not in ("emo", "show"):
        raise ValueError(f"Unsupported copy template: {value}")
    return value


def _group(kind):
    return kind if kind in ("intro", "points", "fabric") else "voice" if kind in ("review", "faq") else "misc"


def feature_slots(points, template):
    return [{"id":f"F{i}", "key":"F", "blockId":None, "sectionKey":f"{template}.features",
             "kind":"feature", "role":"desc", "template":template, "designId":None,
             "fontRole":"body", "fontSize":32, "width":888, "maxWidth":888,
             "group":"points", "target":"feature", "point":p, "maxChars":60, "draft":False}
            for i,p in enumerate(_strings(points))]


def slots_for_page(storyboard, product, analysis, catalog=None):
    """Collect emitted copy roles, including text-only sections and part cards.

    walk_layout is used only to describe roles the renderer already emitted, never
    to decide visibility. Hug containers expose both empty width and capacity.
    """
    catalog = CATALOG if catalog is None else catalog
    # Assembly has a single catalog today; silently mixing it with another is unsafe.
    if catalog != CATALOG:
        raise ValueError("Copy discovery requires the assembly catalog")
    analysis, product = analysis or {}, product or {}
    template = template_of(storyboard, analysis)
    slots, seen = [], set()
    if storyboard:
        clean_analysis = {k:v for k,v in analysis.items() if k not in ("partDescriptionCopy","featureCopy","pageCopy")}
        blocks = assemble_v4(storyboard, [], [], product, True, analysis=clean_analysis,
                             page_style={**catalog["defaults"], **(analysis.get("pageStyle") or {}), "templateId":template})
        section_keys = list(dict.fromkeys(b["pageSectionKey"] for b in storyboard))
        first_ids = {key:next(b["id"] for b in storyboard if b["pageSectionKey"]==key) for key in section_keys}
        for block in blocks:
            kind, section_key = block.get("pageSectionKind"), block.get("pageSectionKey")
            if kind not in catalog["kinds"]: continue
            key = f"S{section_keys.index(section_key)+1}"
            nodes = list(walk_layout(catalog["designs"][block["pageDesignId"]]["layout"]))
            names = {e.get("copyRole"):e.get("text") for e in block["elements"] if e.get("copyRole")}
            for el in block["elements"]:
                role = el.get("copyRole")
                definition = text_slot_for_role(catalog["kinds"][kind],role,template_id=template,seller_comment=analysis.get("mdComment") or "") if role else None
                if not definition or definition["source"] not in ("ai","aiDraft","seller+ai"): continue
                if role=="tagline" or re.fullmatch(r"colors\.\d+\.desc|items\.\d+\.who",role): continue
                identity=f"{key}:{role}"
                if identity in seen: continue
                pattern=re.sub(r"\.\d+(?=\.|$)",".n",role)
                node=next((n for n in nodes if n.get("copyRole")==pattern and n.get("type")=="text"),{})
                is_part=kind=="points" and bool(re.fullmatch(r"items\.\d+\.desc",role))
                point=names.get(role.removesuffix(".desc")+".name","") if is_part else None
                if is_part and not point: continue
                seen.add(identity)
                width=el["w"]
                max_width=width
                # Hug list widths are capacities defined by the chosen design.
                for n in nodes:
                    if n.get("type")=="list" and n.get("hug") and pattern.startswith(str(n.get("copyRole","")).rsplit(".",1)[0]):
                        pad=n.get("padding") or 0
                        pad=pad[1] if isinstance(pad,list) else pad
                        max_width=max(width,(n.get("maxW") or n.get("w") or width)-2*pad-(node.get("prefixWidth") or 0))
                if kind=="intro" and role in ("body","title"):
                    for n in nodes:
                        if n.get("splitParagraphs")=="body" and n.get("hug"):
                            pad=n.get("padding") or 0; pad=pad[1] if isinstance(pad,list) else pad
                            max_width=max(width,(n.get("maxW") or width)-2*pad)
                draft=(kind in ("review","sns","fabric") or kind=="faq" and role.endswith(".a")
                       or role=="fit.tip" or kind=="intro" and role=="body" and not str(analysis.get("mdComment") or "").strip())
                slots.append({"id":identity,"key":key,"blockId":first_ids[section_key],"sectionKey":section_key,
                              "kind":kind,"role":role,"template":template,"designId":block["pageDesignId"],
                              "fontRole":node.get("typographyRole") or definition["style"],
                              "fontSize":el["style"]["size"],"width":width,"maxWidth":max_width,
                              "tracking":el["style"].get("tracking",0),"maxChars":definition.get("maxChars"),
                              "group":_group(kind),"target":"part" if is_part else "section","point":_part_name(point) if is_part else point,"draft":bool(draft)})
    slots.extend(feature_slots(analysis.get("sellingPoints") or analysis.get("aiSuggestedPoints") or [],template))
    return slots


_MATERIALS = {
    "면":r"(?<![가-힣a-z])(?:면(?=$|[^가-힣A-Za-z]|[은을이과의에으도]|혼방|소재)|코튼|cotton)",
    "울":r"(?<![가-힣a-z])(?:울(?=$|[^가-힣A-Za-z]|[은을이과의에도로]|혼방|소재)|모직|양모|wool)",
    "폴리에스터":r"폴리에스[터테르]+|폴리(?!우레탄)|polyester",
    "나일론":r"나일론|nylon", "스판":r"스판(?:덱스)?|폴리우레탄|spandex|elastane|polyurethane",
    "레이온":r"레이온|비스코스|rayon|viscose", "아크릴":r"아크릴|acrylic",
    "린넨":r"린넨|리넨|linen", "캐시미어":r"캐시미어|캐시미르|cashmere",
    "실크":r"실크|silk", "모달":r"모달|modal", "텐셀":r"텐셀|리오셀|tencel|lyocell",
    "알파카":r"알파카|alpaca", "앙고라":r"앙고라|angora", "가죽":r"가죽|레더|leather",
    "데님":r"데님|denim", "트위드":r"트위드|tweed", "플리스":r"플리스|후리스|fleece",
}


def _material_words(text):
    return {name for name,pattern in _MATERIALS.items() if re.search(pattern,text,re.I)}


def care_family(product, analysis, materials):
    """Mirror careFamilyFor's product-first selection, not just its fibre table."""
    name=" ".join(str(v or "") for v in (product.get("name"),analysis.get("customCategory")))
    mat=" ".join(m["name"] for m in materials);all_text=name+" "+mat
    sub=analysis.get("subCategory") or product.get("subCategory")
    for family,pattern,subcategories in [
        ("suede",r"스웨이드|스웨드|suede",()),
        ("leather",r"레더|가죽|leather|램스킨|lambskin",()),
        ("padding",r"패딩|덕\s*다운|구스\s*다운|거위털|오리털|웰론|puffer|다운\s*(점퍼|자켓|재킷|조끼|베스트|코트|파카)",("padding",)),
        ("fleece",r"플리스|후리스|fleece|보아|뽀글이|덤블(?!\s?워싱)|쉐르파|sherpa",()),
        ("windbreaker",r"윈드브레이커|바람막이|windbreaker|아노락|anorak",("windbreaker",)),
        ("tracktop",r"트랙\s?탑|트랙\s?자켓|트랙\s?재킷|track\s?(top|jacket)",()),
        ("denim",r"데님|denim|청자켓|청재킷|청바지|진\s?자켓|jean",("jeans",)),
    ]:
        if re.search(pattern,all_text,re.I) or sub in subcategories:return family
        if family=="leather" and any(re.search(r"폴리우레탄|polyurethane|^\s*pu\s*$",m["name"],re.I) and m.get("ratio",0)>=50 for m in materials):return family
    if sub in ("knit","cardigan") or re.search(r"니트|가디건|스웨터|knit|cardigan|sweater",name,re.I):return "knit"
    if (product.get("clothingType") or product.get("clothing_type"))=="outer" and re.search(r"울|wool|니트|캐시미어|앙고라|모헤어|양모",mat,re.I):return "generic"
    for family,pattern in [("cotton",r"면|코튼|cotton"),("knit",r"울|wool|니트|캐시미어|앙고라|모헤어|양모"),
                           ("acrylic","아크릴"),("denim","데님|denim"),("functional","쿨맥스|coolmax|기능성"),
                           ("poly","폴리|나일론|스판|모달|레이온")]:
        if re.search(pattern,mat,re.I):return family
    return "generic"


def _part_name(value):
    """Presentation numbering is not part of the source detail's identity."""
    return re.sub(r"^\s*\d+[.)]\s*", "", str(value or "")).strip()


def _part(facts, point):
    return next((p for p in facts.get("parts", []) if _part_name(p["name"]) == _part_name(point)), {})


def _garment_type(product, analysis):
    value = str(product.get("clothing_type") or product.get("clothingType") or analysis.get("clothingType") or "")
    if value in ("top", "bottom", "outer", "dress"):
        return value
    for kind, pattern in (("bottom", r"하의|바지|팬츠|슬랙스|스커트|치마"),
                          ("outer", r"아우터|코트|재킷|자켓|점퍼|패딩"), ("dress", r"원피스|드레스"), ("top", r"상의|셔츠|니트|블라우스|맨투맨")):
        if re.search(pattern, value or str(product.get("name") or "")):
            return kind
    category = (analysis.get("fitProfile") or {}).get("category")
    return "bottom" if category in ("pants", "skirt") else category if category in ("top", "outer", "dress") else ""


def facts_bundle(product, analysis, slots):
    product,analysis=product or {},analysis or {}
    materials=[]
    preset=str(analysis.get("materialsSource") or analysis.get("materialSource") or "").lower()=="preset"
    for item in analysis.get("materials") or product.get("materials") or []:
        item={"name":item} if isinstance(item,str) else item
        if not isinstance(item,dict) or not isinstance(item.get("name"),str): continue
        row={"name":item["name"]}
        ratio=item.get("ratio")
        if not preset and isinstance(ratio,(int,float)) and not isinstance(ratio,bool) and 0<=ratio<=100:row["ratio"]=ratio
        materials.append(row)
    dominant=max(materials,key=lambda m:m.get("ratio",-1),default={}).get("name","")
    colors=_strings([c.get("name") or c.get("label") or CATALOG["swatchLabels"].get(c.get("swatchId"),"") if isinstance(c,dict) else c for c in product.get("colors") or []])
    profile=analysis.get("fitProfile") or {}
    garment_type=_garment_type(product,analysis)
    axes={}
    axis_sources={}
    for axis,value in (profile.get("axes") or {}).items():
        entries=(FIT_AXES.get(profile.get("category"),{}).get(axis,{}) or {}).get(profile.get("gender"),[])
        match=next((e for e in entries if e["value"]==value),None)
        source=(profile.get("axisSources") or {}).get(axis)
        if garment_type=="bottom" and (axis!="length" or source not in ("seller","measure","ai")):
            continue
        if match:
            axes[axis]=match.get("label") or match.get("name") or value
            if source:axis_sources[axis]=source
    fit_value=confirmed_copy_fit(analysis)
    # Legacy analyses without a fitProfile still carry their confirmed fit.
    if "fitProfile" not in analysis: fit_value=analysis.get("fit")
    fit="" if garment_type=="bottom" else CATALOG["fitLabels"].get(fit_value,fit_value or "")
    parts=[]
    for item in (analysis.get("detailRecommendations") or {}).get("candidates") or []:
        if isinstance(item,dict) and item.get("label"):
            parts.append({"name":_part_name(item["label"]),"evidence":item.get("reason") or item.get("visualEvidence") or "", "features":item.get("featurePoints") or []})
    for s in slots:
        if s["target"]=="part" and not any(p["name"]==_part_name(s["point"]) for p in parts):parts.append({"name":_part_name(s["point"]),"evidence":""})
    highlights=_strings(analysis.get("sellingPoints") or analysis.get("aiSuggestedPoints") or [])
    name=product.get("name") or ""
    clothing=product.get("clothing_type") or product.get("clothingType") or analysis.get("clothingType") or ""
    clothing={"top":"상의","bottom":"하의","outer":"아우터","dress":"원피스"}.get(clothing,clothing)
    short=re.search(r"\(([^)]+)\)",clothing)
    garment=short[1] if short else clothing
    feel_rows=_read_json(DATA_DIR/"page_copy_fabric_feel.json")["rows"]
    evidence=" ".join([name,*highlights,*[p["name"]+" "+p["evidence"] for p in parts],str(analysis.get("fabricStructure") or "")])
    dominant_words=_material_words(dominant)
    selected=[]
    for row in feel_rows:
        matching=any(word and re.search(r"(?<![가-힣A-Za-z])"+re.escape(word)+r"(?![A-Za-z])",evidence,re.I) for word in row["lookup_words"])
        material_match=any(_material_words(w)&dominant_words for w in row["lookup_words"])
        # Material rows must match the dominant component; structure rows match evidence.
        simple_material=row["lookup_words"][0] in ("면","울","폴리에스터","나일론","레이온","아크릴","린넨","캐시미어","스판","실크","텐셀","모달")
        spandex=row["lookup_words"][0]=="스판" and "스판" in _material_words(_json(materials))
        if simple_material and material_match or not simple_material and (matching or material_match and " " not in row["lookup_words"][0]) or spandex:selected.append(row)
    category_rows=_read_json(DATA_DIR/"page_copy_category.json")["rows"]
    category_matches=[(len(w),r) for r in category_rows for w in r["lookup_words"]
                      if w.casefold() in (name+" "+garment).casefold()]
    category=max(category_matches,key=lambda pair:pair[0],default=(0,{}))[1]
    care=_read_json(DATA_DIR/"page_copy_care.json")
    family=care_family(product,analysis,materials)
    care_lines=care["families"][family]["lines"]
    facts={"name":name,"clothingType":clothing,"garment":garment,"colors":colors,"colorCount":len(colors),
           "fit":fit,"axes":axes,"axisSources":axis_sources,"garmentType":garment_type,"materials":materials,"dominantMaterial":dominant,"materialPreset":preset,
           "sellingPoints":highlights,"parts":parts,"sellerComment":checks.clean_seller_text(str(analysis.get("mdComment") or "").strip()),
           "existingPartCopy":[r for r in analysis.get("partDescriptionCopy") or [] if isinstance(r,dict)],
           "fabricFeel":{"forbidden":_strings([w for r in selected for w in r["forbidden"]])},
           "fabricStructure":_strings([str(analysis.get("fabricStructure") or ""), *[w for r in selected for w in r["lookup_words"] if re.search(r"골지|립|쭈리|스웨트|트윌|와플|메시|시스루|코듀로이|새틴|플리스|트위드|시폰|데님",w) and w in evidence]]),
           "warmthEvidence":checks.warmth_evidence(materials, evidence+" "+checks.clean_seller_text(str(analysis.get("mdComment") or ""))),
           "faqEvidence":{"hasLightColor":any(any(w in c for w in ("화이트","아이보리","크림","white","ivory")) for c in colors),
                          "sleeve":axes.get("sleeve") or analysis.get("sleeveLength") or "","fit":fit,"careFamily":family,"care":_strings(care_lines)},
           "category":{"name":category.get("name",""),"pairings":category.get("pairings",[])}}
    if garment_type=="bottom":
        cut_text=" ".join([name,facts["sellerComment"],*highlights,*[p["evidence"] for p in parts],
                           str(analysis.get("description") or ""),str(analysis.get("summary") or "")])
        facts["bottomCut"]=_strings(re.findall(r"세미\s*와이드|와이드|스트레이트|슬림|부츠컷|조거|테이퍼드|일자|배기|플레어|A라인|H라인",cut_text))
    return {k:v for k,v in facts.items() if v not in ("",[],{}) or k in ("sellerComment","parts","colors","materials")}


def plans(seed, slots, facts):
    """Assign diversity constraints, never the detail, situation or sentence ending."""
    digest=hashlib.sha256(_json(seed).encode()).hexdigest()
    rng=random.Random(digest)
    topics=["색","핏과 사이즈","두께와 계절","촉감","같이 입은 옷","디테일","입고 갈 상황"]
    rng.shuffle(topics)
    lengths=[[8,12],[16,22],[26,32]];rng.shuffle(lengths)
    faq_topics=["비침","두께와 계절","사이즈 고르는 법","세탁","늘어남"];rng.shuffle(faq_topics)
    result={"seed":digest,"slots":{}}
    counts=Counter()
    for s in slots:
        kind=s["kind"];entry={}
        if kind=="review":
            i=counts[kind];entry={"topic":topics[i%len(topics)],"length":lengths[i%3]};counts[kind]+=1
        elif kind=="faq":
            i=int(s["role"].split(".")[1]);entry={"topic":faq_topics[i%len(faq_topics)]}
        elif s["target"]=="part":
            entry={"evidenceOnly":s["template"]=="show"}
        elif s["target"]=="feature":
            entry={"differentFromPart":_part_name(s["point"])}
        if entry:result["slots"][s["id"]]=entry
    return result


def copy_schema():
    def obj(props):return {"type":"object","properties":props,"required":list(props),"additionalProperties":False}
    def arr(items):return {"type":"array","items":items}
    string={"type":"string"}
    pair=obj({"point":string,"desc":string})
    pair["properties"]["label"]=string
    return obj({"sections":arr(obj({"key":string,"texts":arr(obj({"role":string,"text":string}))})),
                "partDescriptionCopy":arr(pair),"featureCopy":arr(pair)})


def _exemplars(slots, facts, plan, stage, excluded):
    path=PROMPT_DIR/"exemplars_v2.json"
    data=_read_json(path)
    examples=data.get("exemplars",[]) if isinstance(data,dict) else data
    if not isinstance(examples,list):raise ValueError("Invalid exemplar list")
    for row in examples:
        if (not isinstance(row,dict) or set(row)!={"slot","template","garment","text","origin"} or
            row["template"] not in ("emo","show","all") or row["garment"] not in ("top","bottom","outer","dress") or
            row["origin"] not in ("shop","owner4","lead") or not isinstance(row["text"],str) or not row["text"].strip()):
            raise ValueError("Invalid exemplar row")
    # An unknown garment cannot establish the required different-garment condition.
    if not facts.get("garmentType"):return []
    rng=random.Random(_json([plan.get("seed",""),stage]));selected=[];seen=set(excluded or [])
    for key,kind,target,template in dict.fromkeys((_slot_kind(s),s["kind"],s["target"],s["template"]) for s in slots):
        candidates=[e for e in examples if e["template"] in (template,"all") and
                    e["garment"]!=facts["garmentType"] and e["slot"] in (key,kind,target) and
                    re.sub(r"\s+","",e["text"]) not in seen]
        rng.shuffle(candidates)
        count=0
        limit=2 if len(candidates) in (2,4,5) else 3
        for example in candidates:
            identity=re.sub(r"\s+","",example["text"])
            if identity in seen:continue
            selected.append(example);seen.add(identity);count+=1
            if count==limit:break
    return selected


def build_request(slots, facts, plans, provider, model, *, feature=False,
                  prompt_version="v1", mode="single", stage="single", excluded_exemplars=None):
    if provider not in PROVIDERS:raise ValueError("Unknown page copy provider")
    if prompt_version not in ("v1","v2"):raise ValueError("Invalid page copy prompt version")
    if mode != "single" or stage not in ("single", "rewrite"):raise ValueError("Only single page copy mode is supported")
    if feature:prompt_version="v1"
    operation="single"
    prefix="feature_" if feature else ""
    if prompt_version=="v2":
        rules=(PROMPT_DIR/f"{operation}_rules_v2.md").read_text(encoding="utf-8")
        # Model names are file basenames, never paths supplied by a route.
        candidate=PROMPT_DIR/f"{operation}_{model}_v2.md"
        model_template=bool(re.fullmatch(r"[A-Za-z0-9_.-]+",model)) and candidate.is_file()
        template=(candidate if model_template else PROMPT_DIR/f"{operation}_{provider}_v2.md").read_text(encoding="utf-8")
    else:
        rules=(PROMPT_DIR/f"{prefix}rules_v1.md").read_text(encoding="utf-8")
        template=(PROMPT_DIR/f"{prefix}{provider}_v1.md").read_text(encoding="utf-8")
    settings=_read_json(PROMPT_DIR/f"settings_{prompt_version}.json")
    config={**settings["providers"][provider],**settings.get("models",{}).get(model,{})}
    required={"rules","facts","plans","examples","slots"}
    optional=set()
    if prompt_version=="v2":
        required={"rules","facts","slots","exemplars"}
        optional={"examples","plans"}
    placeholders=set(re.findall(r"\{\{([^{}]+)\}\}",template))
    if not required<=placeholders or placeholders-(required|optional) or "\n---USER---\n" not in template:
        raise ValueError("Page copy prompt is missing a required placeholder or USER delimiter")
    examples=_read_json(PROMPT_DIR/f"{prefix}examples_v1.json")["examples"] if "examples" in placeholders else []
    rng=random.Random(plans.get("seed",""));selected=[];seen_examples=set()
    for kind,role,page_template in dict.fromkeys((s["kind"],re.sub(r"\.\d+", ".n",s["role"]),s["template"]) for s in slots):
        candidates=[e for e in examples if e.get("template") in (page_template,"all","*") and e.get("slot") in (kind,kind+"."+role,"part" if kind=="points" else kind)]
        for example in rng.sample(candidates,min(len(candidates),7 if kind=="points" else 4)):
            pair={k:example[k] for k in ("before","after") if k in example}
            identity=_json(pair or example)
            if identity not in seen_examples:selected.append(example);seen_examples.add(identity)
    public=[]
    for slot in slots:
        item={k:slot[k] for k in ("id","kind","role","template","target","point") if slot.get(k) is not None}
        cap,max_lines,line_cap=_limits(slot,facts)
        item.update(maxChars=cap,draft=slot.get("draft",False))
        if max_lines is not None:item["maxLines"]=max_lines
        if line_cap is not None and max_lines>1:item["maxLineChars"]=line_cap
        if prompt_version=="v2":
            item["voice"]=("yo" if slot["kind"]=="review" else "noun" if slot["role"]=="title" or slot["kind"]=="for_you" or slot["template"]=="show" and slot["target"] in ("part","feature") else "nida" if slot["template"]=="show" else "yo")
        public.append(item)
    exemplars=_exemplars(slots,facts,plans,stage,excluded_exemplars) if prompt_version=="v2" else []
    exemplar_warnings=[]
    if prompt_version=="v2":
        for key,kind,target in dict.fromkeys((_slot_kind(s),s["kind"],s["target"]) for s in slots):
            count=sum(e["slot"] in (key,kind,target) for e in exemplars)
            if count<2:exemplar_warnings.append({"reason":"insufficient_exemplars","slotKind":key,"count":count,"stage":stage})
    values={"rules":rules,"facts":_json(facts),"plans":_json(plans),"examples":_json(selected),
            "slots":_json(public),"exemplars":_json(exemplars)}
    filled=re.sub(r"\{\{(\w+)\}\}",lambda m:values[m[1]],template)
    system,prompt=filled.split("\n---USER---\n",1)
    schema=copy_schema()
    for field in ("partDescriptionCopy","featureCopy"):
        pair=schema["properties"][field]["items"]
        if feature:
            # The editor keeps its original v1 pair schema.
            pair["properties"].pop("label",None)
        elif provider=="gpt":
            # GPT strict schemas require every property; nullable represents the
            # optional public label without making it mandatory in stored results.
            pair["properties"]["label"]={"type":["string","null"]}
            pair["required"]=list(pair["properties"])
    return {"provider":provider,"model":model,"system_prompt":system,"prompt":prompt,"schema":schema,
            "settings":config,"slots":deepcopy(slots),"groups":list(dict.fromkeys(s["group"] for s in slots)),
            "stage":stage,"prompt_version":prompt_version,"mode":mode,"exemplars":exemplars,"warnings":exemplar_warnings}


_PROMOTION=r"배송|교환|반품|할인|최저가|재입고|품절|인스타|링크|(?<![a-z])dm(?![a-z])|신상|1\+1|한정|특가|세일|쿠폰|적립금|자사몰|카카오|카톡|채널|유튜브|틱톡|블로그|네이버|스토어|문의\s*주세요|보상|책임지지|@|가격|가성비"
_CLAIMS=r"따뜻|따듯|따스|시원|보풀.{0,5}(?:없|안\s*생)|줄지\s*않|수축.{0,5}없|통기|통풍|방수|방풍|발수|항균|보온|자외선|\buv\b|흡수|쾌적|피부\s*자극|구김|내구|견고|튼튼|누구나|하루\s*종일|비치지\s*않|비침\s*없|늘어짐\s*없|바람(?:을|도)?\s*막|물을?\s*막|땀(?:을|이)?\s*(?:잘|흡수|차|배출)|변형(?:이|을)?\s*(?:없|방지|않|줄)"
_CARE=r"세탁|건조기|드라이|물빨래|손빨래|손세탁|오래\s*입|오래도록|형태\s*유지|모양\s*유지|뒤틀림|틀어짐|구애\s*없이|상관없이"
_PROCESS=r"국내\s*제작|국내산|국산|자체\s*제작|수작업|핸드메이드|가공|제작\s*과정|®|™|社"
_BODY=r"골반|엉덩이|허벅지|무릎|종아리|발목|힙|허리선|배꼽|옆구리|손목|손등|팔꿈치|쇄골|정강이|발등"
_HYPE=r"완벽|놀라운|최초|유일|선사|자리매김|향연|녹여낸|담아낸|아우르|독보적|압도적|획기적|혁신적|진정한|궁극|필수템|어디에나|특별한(?!\s*날)|인생템|무조건|최고|판매량|판매\s*\d|\d+\s*위|랭킹|순위"
_OTHER=r"목걸이|귀걸이|팔찌|가방|액세서리|악세서리|청바지|슬랙스|스커트|치마|하의|상의|재킷|자켓|셔츠|티셔츠|코트"
_ENDING_YO=re.compile(r"[가-힣]요[.!]?$" )


def _limits(s,facts):
    kind,role,show=s["kind"],s["role"],s["template"]=="show"
    if s["target"]=="feature":return 60,1,60
    if s["target"]=="part":return (26,1,26) if show else (44,2,22)
    if kind=="first" and role=="label":return 28,1,28
    if role=="title":return (16 if kind=="fabric" and show else 20),1,20
    if kind=="intro" and role=="body":
        cap=min(300,max(120,len(facts.get("sellerComment",""))))
        return cap,4 if show else 3,32 if show else 70
    if kind=="fabric" and role=="body":return (56,2,28) if show else (50,3,50)
    if kind=="review":return 34,1,34
    if kind=="faq":return (20,1,20) if role.endswith(".q") else (56,2,56)
    if kind=="for_you":return 28,1,28
    if role=="summary.line":return 30,1,30
    if role.startswith("summary."):return 14,1,14
    if role=="fit.tip":return 34,1,34
    if role=="fit.body":return 80,2,80
    return s.get("maxChars") or 120,3,120


def _response_texts(response,slots,*,warnings=None):
    if not isinstance(response,dict):return {},set()
    sections_by_kind={};requested_keys=set()
    for slot in slots:
        if slot["target"]=="feature":continue
        requested_keys.add(slot["key"])
        sections_by_kind.setdefault(slot["kind"],set()).add((slot["key"],slot.get("sectionKey",slot["key"])))
    rows=[]
    for section in response.get("sections",[]) if isinstance(response.get("sections"),list) else []:
        if not isinstance(section,dict):continue
        key=section.get("key");alias=None
        if isinstance(key,str) and key not in requested_keys and not re.fullmatch(r"S\d+",key):
            candidates=sections_by_kind.get(key,set())
            if len(candidates)==1:alias=next(iter(candidates))[0]
        for t in section.get("texts",[]) if isinstance(section.get("texts"),list) else []:
            if not isinstance(t,dict):continue
            if alias is not None:
                matched=[s for s in slots if s["target"]=="section" and s["key"]==alias and s["role"]==t.get("role")]
                if not matched:continue
                if warnings is not None:
                    warnings.extend({"reason":"section_key_by_kind","slotId":s["id"],"key":alias,"receivedKey":key}
                                    for s in matched)
            rows.append(("section",alias if alias is not None else key,t.get("role"),t.get("text")))
    for field,target in (("partDescriptionCopy","part"),("featureCopy","feature")):
        for row in response.get(field,[]) if isinstance(response.get(field),list) else []:
            if isinstance(row,dict):rows.append((target,row.get("point"),"desc",row.get("desc")))
    result={};duplicate=set()
    for s in slots:
        matching=[text for target,key,role,text in rows if target==s["target"] and
                  (key==s["key"] and role==s["role"] if target=="section" else isinstance(key,str) and _part_name(key)==_part_name(s.get("point")))]
        if len(matching)>1:duplicate.add(s["id"])
        if matching:result[s["id"]]=matching[0]
    return result,duplicate


def _faq_care_supported(text, facts):
    """Allow table-supported instructions and cautionary stretch, never a guarantee.

    These are conservative linguistic checks; the selected table remains in the
    prompt for review, and every accepted answer remains a seller-checkable draft.
    """
    care=" ".join(facts.get("faqEvidence",{}).get("care",[]))
    if not care:return False
    avoid=r"피하|피해|피해요|말아|않|금지|사용하지|쓰지"
    for token in ("건조기","뜨거운 물","물세탁","물빨래","섬유유연제"):
        if token not in text:continue
        matching=[line for line in care.split(".") if token in line]
        if not matching:return False
        if any(re.search(avoid,line) for line in matching) and not re.search(avoid,text):return False
    for pattern in ("중성세제","손세탁","드라이","찬물","세탁망","단독","미지근","뒤집","눕혀|눕히|평평", "접어서|접어", "그늘", "클리너", "브러시"):
        if re.search(pattern,text) and not re.search(pattern,care):return False
    if re.search(r"세탁기|삶|표백|뜨겁게|온수|회전식",text):return False
    if re.search(r"늘어나|늘어날|늘어남|늘어지|신축|탄성|텐션",text):
        if not ("늘어나" in care and re.search(r"젖|걸",text) and re.search(r"늘어날\s*수|늘어나니|늘어나요|늘어질\s*수",text)):
            return False
    if re.search(r"세탁|건조|말려|보관|늘어",text) and re.search(r"괜찮|가능|문제\s*없|안심|변형\s*없|줄지\s*않",text):return False
    return True


def _review_topics(text,facts):
    topics=set()
    if re.search(r"컬러|색(?:이|은|을|도|감|상|깔|\s)|아이보리|블루|로즈",text) or any(c in text for c in facts.get("colors",[])):topics.add("색")
    if re.search(r"핏|품이|품은|품도|사이즈",text):topics.add("핏과 사이즈")
    if re.search(r"두께|봄|여름|가을|겨울|간절기",text):topics.add("두께와 계절")
    if re.search(r"촉감|부드[러럽]|포근|살에\s*닿",text):topics.add("촉감")
    if any(p in text for p in facts.get("category",{}).get("pairings",[])):topics.add("같이 입은 옷")
    if re.search(r"단추|카라|주름|포켓|주머니|자수|배색",text):topics.add("디테일")
    if re.search(r"출근|산책|주말|모임|여행",text):topics.add("입고 갈 상황")
    return topics


_DETAIL_CONCEPTS = (r"지퍼", r"단추|버튼", r"주머니|포켓", r"주름|핀턱|플리츠", r"밴딩|밴드|고무줄",
                    r"끈|스트링|스트랩|리본", r"카라|깃", r"소매|커프스|소맷단", r"안감", r"자수",
                    r"스티치|박음질", r"패치", r"쇠|금속|메탈", r"트임|슬릿")


def _detail_claims(text, pattern):
    claims=set()
    for match in re.finditer(pattern,text):
        after=text[match.end():match.end()+8]
        claims.add(bool(re.match(r"(?:이|가|은|는)?\s*(?:없|아닌|아니라|없이)",after)))
    return claims


def _unsupported_part_fact(text, part, *, warmth=False):
    evidence=str(part.get("evidence") or "")+" "+" ".join(part.get("features") or [])
    source=part.get("name","")+" "+evidence
    if warmth:source+=" 따뜻"
    # Recognized additions are compared to source concepts, never exact wording.
    if checks.unsupported_additions(source,text,_material_words):return True
    for pattern in _DETAIL_CONCEPTS:
        supported=_detail_claims(evidence,pattern) or _detail_claims(part.get("name",""),pattern)
        if _detail_claims(text,pattern)-supported:return True
    return False


def _checks_trade_terms(s):
    return (s["kind"]=="intro" and s["role"] in ("title","body") or
            s["target"] in ("part","feature") or s["kind"]=="review")


def _slot_kind(s):
    if s["target"] in ("part","feature"):return s["target"]+".desc"
    return s["kind"]+"."+re.sub(r"items\.\d+\.","",s["role"])


def _matches_slot_kind(slot, kinds):
    return slot["kind"] in kinds or _slot_kind(slot) in kinds


def _slot_reasons(text,s,facts,plan):
    if not isinstance(text,str) or not text.strip():return ["missing"]
    reasons=[];kind,role=s["kind"],s["role"];show=s["template"]=="show"
    md=kind=="intro" and role=="body" and bool(facts.get("sellerComment"))
    seller=facts.get("sellerComment","") if md else ""
    part_evidence=_json(_part(facts,s.get("point"))) if s["target"]=="part" else ""
    question=kind=="faq" and role.endswith(".q");answer=kind=="faq" and role.endswith(".a")
    cap,max_lines,line_cap=_limits(s,facts);lines=text.split("\n");n=len(text.replace("\n",""))
    if n>cap or len(lines)>max_lines or any(not line.strip() or len(line)>line_cap for line in lines):reasons.append("length")
    if text!=text.strip() or any(line!=line.strip() for line in lines):reasons.append("whitespace")
    emoji=[c for c in text if unicodedata.category(c) in ("So","Sk") or c in "♥♡★☆"]
    emoji_ok=md and not show and len(emoji)==1 and text.endswith(emoji[0]) and emoji[0] in seller
    if any(unicodedata.category(c).startswith("C") and c!="\n" for c in text) or re.search(r"\*\*|(?m:^\s*#)|[`<>→←⇒—–―•]",text) or emoji and not emoji_ok:
        reasons.append("plain_text")
    if kind=="first" and role=="label":
        if not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+){1,3}",text):reasons.append("english_label")
    elif not re.search(r"[가-힣]",text):reasons.append("hangul")
    measured=text.removesuffix(emoji[0]) if emoji_ok else text
    width=s.get("maxWidth",s.get("width",888));size=s.get("fontSize",32)
    wrapped=[wrap_text(line,width,size,CATALOG["textMetrics"],s.get("tracking",0)) for line in lines]
    physical_limit=(2*max_lines if kind=="intro" and role=="body" and not show else max_lines)
    # One review/feature sentence is one logical line, not a prohibition on wrapping.
    if kind=="review":physical_limit=2
    if s["target"]=="feature":physical_limit=3
    if sum(len(v) for v in wrapped)>physical_limit or kind=="intro" and role=="body" and not show and any(len(v)>2 for v in wrapped):reasons.append("wrapped_lines")
    if any(len(wrap_text(word,width,size,CATALOG["textMetrics"],s.get("tracking",0)))>1 or len(word)*size>width and re.fullmatch(r"[가-힣]+",word) for word in text.split()):reasons.append("long_word")
    # Name exemptions remove only exact factual phrases, not arbitrary occurrences of a banned syllable.
    names=[facts.get("name",""),facts.get("clothingType",""),*facts.get("sellingPoints",[]),*[p["name"] for p in facts.get("parts",[])]]
    exempt=text
    for name in sorted(filter(None,names),key=len,reverse=True):exempt=exempt.replace(name,"")
    if check_body_effect(text, facts)[0]:reasons.append("body_effect")
    for pattern,reason in [(_PROMOTION,"promotion"),(_PROCESS,"process_claim"),(_CARE,"care_claim"),(_CLAIMS,"unverified_claim"),(_BODY,"body_position"),(_HYPE,"hype")]:
        source=exempt if reason != "body_position" else text
        for match in re.finditer(pattern,source,re.I):
            word=match[0]
            if reason=="promotion" and kind=="review" and word=="가격":pass
            if reason=="care_claim" and (question or answer and _faq_care_supported(text,facts)):continue
            if reason=="unverified_claim" and question:continue
            if reason=="unverified_claim" and word in ("따뜻", "따듯", "따스") and facts.get("warmthEvidence"):continue
            if reason=="hype" and kind=="review" and word in ("인생템","무조건","최고"):continue
            if reason=="body_position" and (word in part_evidence or word in _json(facts.get("axes",{}))):continue
            if md and reason not in ("promotion","unverified_claim") and re.search(re.escape(word),seller):continue
            reasons.append(reason);break
    if re.search(r"주어요[.!]?$|되어요[.!]?$|보아요[.!]?$",measured):reasons.append("uncontracted")
    if re.search(r"되어지|보여지|보여져|여겨지",text):reasons.append("double_passive")
    materials=_material_words(seller if md else " ".join(m["name"] for m in facts.get("materials",[])))
    material_text=text
    if kind in ("review","for_you"):
        for pairing in facts.get("category",{}).get("pairings",[]):
            material_text=re.sub(re.escape(pairing)+r"(?=이랑|랑|와|과|에|위|아래|하고|\s*함께)","",material_text)
    structures={"데님","트위드","플리스"}
    if not md:materials|=structures&_material_words(_json(facts.get("fabricStructure",[])))
    if s["target"]=="part":materials|=_material_words(part_evidence)
    mentioned=_material_words(material_text)
    if mentioned-materials:reasons.append("unknown_material")
    if (role in ("title","label") or role.startswith("summary.")) and mentioned-structures:
        first=min(mentioned-structures,key=lambda name:re.search(_MATERIALS[name],material_text,re.I).start())
        if not md and first not in _material_words(facts.get("dominantMaterial","")):reasons.append("minority_material")
    if kind=="first" and facts.get("materialPreset") and _material_words(text):reasons.append("preset_material")
    numbers=set(re.findall(r"\d+(?:\.\d+)?",text))
    number_sources=seller if md else _json([facts.get("materials",[]),facts.get("colorCount"),facts.get("sellingPoints",[])])
    if part_evidence:number_sources+=" "+part_evidence
    if answer:number_sources+=" "+_json(facts.get("faqEvidence",{}).get("care",[]))
    if numbers-set(re.findall(r"\d+(?:\.\d+)?",number_sources)) and "number_added" in checks.unsupported_additions(number_sources,text,_material_words):reasons.append("unknown_number")
    if numbers and re.search(r"만족|판매|누적|후기\s*\d|조회|위|등",text):reasons.append("unknown_number")
    if not answer:
        if re.search(r"\d+(?:\.\d+)?\s*%",text):reasons.append("reread_ratio")
        if len(facts.get("colors",[]))>1 and all(c in text for c in facts["colors"]):reasons.append("reread_colors")
        name=facts.get("name","")
        if name and name in text and not (md and name==facts.get("garment")):reasons.append("reread_name")
    if checks.size_advice(text, facts.get("fit", ""), faq_answer=answer):reasons.append("size_advice")
    if re.search(r"하나도\s*안\s*춥|전혀\s*안\s*춥|추위.{0,5}없|완벽\s*보온",text):reasons.append("unverified_claim")
    if re.search(r"사계절|한여름|한겨울|간절기",text) and not question and not (md and re.search(r"사계절|한여름|한겨울|간절기",seller)):
        if not (answer and plan.get("topic")=="두께와 계절" and "간절기" in text and facts.get("faqEvidence",{}).get("sleeve")):reasons.append("season_claim")
    if role=="title" and re.search(r"[?.]$|[요다][.!]?$",text):reasons.append("title_ending")
    if s["target"] in ("part","feature"):
        if s["target"]=="part" and re.search(_OTHER,exempt):reasons.append("other_garment")
        if show and (re.search(r"[요다][.!]?$",text) or "." in text):reasons.append("voice")
        if not show and (not _ENDING_YO.search(lines[-1]) or "니다" in text):reasons.append("voice")
        if s["target"]=="feature" and len(re.findall(r"[.!?](?:\s|$)",text))>1:reasons.append("sentence_count")
        if s["target"]=="part" and show:
            part=_part(facts,s.get("point"))
            if not part.get("evidence") and not any(_part_name(s.get("point")) in h for h in facts.get("sellingPoints",[])):reasons.append("missing_part_evidence")
            elif _unsupported_part_fact(text,part,warmth=facts.get("warmthEvidence",False)):reasons.append("unsupported_part_fact")
        if plan.get("omit"):reasons.append("missing_part_evidence")
    if kind=="intro" and role=="body":
        if re.search(r"강조|해\s*주세요|해\s*주십시오|부각|보여\s*주세요",text):reasons.append("seller_instruction")
        if show:
            if not 2<=len(lines)<=4 or any(not line.rstrip(".").endswith("니다") for line in lines):reasons.append("voice")
        elif any(not _ENDING_YO.search(line) and not line.endswith("답니다.") for line in measured.split("\n")):reasons.append("voice")
        if not md and not show and len(lines)!=2:reasons.append("paragraph_count")
    if kind=="fabric" and role=="body":
        if show and (len(lines)!=2 or any(not l.rstrip(".").endswith("니다") for l in lines)):reasons.append("voice")
        if not show and (not _ENDING_YO.search(text) or "니다" in text):reasons.append("voice")
    if kind=="fabric":
        if re.search(r"복원력|친환경|보풀|따갑지|산뜻|충전재|줄지\s*않|물이?\s*빠지지\s*않",text):reasons.append("fabric_forbidden")
        compact=re.sub(r"\s+","",text)
        if any(re.sub(r"\s+","",term) in compact for term in facts.get("fabricFeel",{}).get("forbidden",[])):reasons.append("fabric_forbidden")
    if role in ("fit.body","summary.line"):
        if show and re.search(r"[요다][.!]?$",text) or not show and not _ENDING_YO.search(text):reasons.append("voice")
    if kind=="for_you" and role.startswith("items."):
        if not text.endswith("분"):reasons.append("recommendation_ending")
        colors=[c for c in facts.get("colors",[]) if c in text]
        if re.search(r"보다|대신|말고",text) and (len(colors)>=2 or colors and re.search(r"무채색|유채색|밝은\s*색|어두운\s*색",text)):reasons.append("color_comparison")
    if question:
        if not text.endswith("?"):reasons.append("question_mark")
        if re.search(r"소재|원단|컬러|색상|어떤\s*색|핏은|핏이|무슨\s*핏",text):reasons.append("reread_question")
    if answer:
        if 31<=n<=35:reasons.append("faq_length_gap")
        if show and not text.rstrip(".").endswith("니다") or not show and not _ENDING_YO.search(text):reasons.append("voice")
        if re.search(r"비침(?:이|은)?\s*(?:전혀\s*)?없|늘어나지\s*않|늘어남\s*없",text):reasons.append("unverified_claim")
        care_claim=re.search(_CARE+r"|말려|보관|젖|온수|뜨거|뜨겁|표백|삶|다림|다려|세제|세탁망|담가",text)
        if care_claim and not _faq_care_supported(text,facts):reasons.append("unsupported_care")
    if kind=="review":
        if re.search(r"[함음움][.!?]*$",text):reasons.append("review_noun_ending")
        if plan.get("length"):
            low,high=plan["length"]
            if not low-3<=n<=high+3:reasons.append("review_length")
        if len(re.findall(r"[.!?](?:\s|$)",text))>1 or "\n" in text:reasons.append("sentence_count")
        if re.search(r"\d|\b[SMLX]+\b|가격|가성비|배송|도착|세탁|빨래|건조|사진이랑|화면|실물",text):reasons.append("review_claim")
    if facts.get("garmentType")=="bottom" and re.search(r"오버\s*핏|품이?\s*(?:넉넉|여유)",text):reasons.append("bottom_top_fit")
    if _checks_trade_terms(s) or kind=="for_you":
        if checks.trade_terms(text):reasons.append("trade_term")
    if re.fullmatch(r"summary\.\d+",role) and text in [facts.get("fit"),*facts.get("colors",[]),*[m["name"] for m in facts.get("materials",[])]]:reasons.append("reread_fact")
    return list(dict.fromkeys(reasons))


def _review_length(text, plan):
    count=len(text.replace("\\n","\n").replace("\n",""))
    interval=plan.get("length")
    deviation=0 if not interval else count-interval[0] if count<interval[0] else count-interval[1] if count>interval[1] else 0
    return {"characters":count,"planned":interval,"deviation":deviation}


def _ignored_part_copies(response, slots):
    """Record wrong-location part output without letting it replace the part array."""
    if not isinstance(response,dict) or not isinstance(response.get("sections"),list):return []
    parts={(s["key"],s["role"]):s for s in slots if s["target"]=="part"}
    warnings=[]
    for section in response["sections"]:
        if not isinstance(section,dict) or not isinstance(section.get("texts"),list):continue
        for row in section["texts"]:
            if not isinstance(row,dict):continue
            key,role=section.get("key"),row.get("role")
            if not isinstance(key,str) or not isinstance(role,str):continue
            s=parts.get((key,role))
            if s:
                warnings.append({"reason":"part_in_sections_ignored","slotId":s["id"],
                                 "key":key,"role":role,"text":row.get("text")})
    return warnings


def _slot_batches(slots):
    """FAQ pairs are the smallest unit that can be validated and committed."""
    batches = []; pairs = {}
    for slot in slots:
        pair = re.fullmatch(r"items\.(\d+)\.[qa]", slot["role"]) if slot["kind"] == "faq" else None
        if pair:
            key = (slot["sectionKey"], pair[1])
            if key not in pairs:
                pairs[key] = []; batches.append(pairs[key])
            pairs[key].append(slot)
        else:batches.append([slot])
    return batches


def validate(response, slots, facts, plans, *, exemplars=None):
    warnings=_ignored_part_copies(response,slots)
    texts,duplicates=_response_texts(response,slots,warnings=warnings)
    accepted={};rejected={};rows=[];prior_topics=set();part_texts={}
    exemplar_texts=[e["text"] if isinstance(e,dict) else e for e in exemplars or []]
    # Parts are validated first so a seller feature never duplicates a rendered part.
    ordered=sorted(slots,key=lambda s:s["target"]=="feature")
    for s in ordered:
        text=texts.get(s["id"])
        if isinstance(text,str):text=text.replace("\\n","\n")
        reasons=_slot_reasons(text,s,facts,plans.get("slots",{}).get(s["id"],{}))
        if s["id"] in duplicates:reasons.append("duplicate_slot")
        if isinstance(text,str) and checks.exemplar_copy(text,exemplar_texts):reasons.append("exemplar_copy")
        if isinstance(text,str) and s["kind"]=="review":
            topics=_review_topics(text,facts)
            planned=plans.get("slots",{}).get(s["id"],{}).get("topic")
            if len(topics)==1 and topics&prior_topics:reasons.append("review_topic_duplicate")
            if planned and topics and planned not in topics:reasons.append("review_topic")
            if not reasons:prior_topics.update({planned} if planned in topics else topics if len(topics)==1 else set())
        if isinstance(text,str) and s["target"]=="feature":
            existing=[r.get("desc","") for r in facts.get("existingPartCopy",[])]
            if re.sub(r"\s+","",text).rstrip(".!?") in [re.sub(r"\s+","",v).rstrip(".!?") for v in [*part_texts.values(),*existing]]:reasons.append("part_feature_duplicate")
        if reasons:rejected[s["id"]]=list(dict.fromkeys(reasons))
        else:
            accepted[s["id"]]=text
            if s["target"]=="part":part_texts[s["point"]]=text
            warnings.extend({"slotId": s["id"], **warning} for warning in check_body_effect(text, facts)[1])
        rows.append({"slotId":s["id"],"group":s["group"],"kind":s["kind"],"role":s["role"],"target":s["target"],"text":text if isinstance(text,str) else "","passed":not reasons,"reasons":list(dict.fromkeys(reasons))})
        if s["kind"]=="review" and isinstance(text,str):
            metric=_review_length(text,plans.get("slots",{}).get(s["id"],{}))
            rows[-1]["reviewLength"]=metric
            if metric["deviation"]:
                warnings.append({"reason":"review_length_deviation","slotId":s["id"],**metric})
    reviews=[s for s in slots if s["kind"]=="review" and s["id"] in accepted]
    if len(reviews)>=3:
        lengths=[len(accepted[s["id"]].replace("\n","")) for s in reviews]
        if max(lengths)-min(lengths)<8:
            s=max(reviews,key=lambda row:int(row["role"].split(".")[1]))
            accepted.pop(s["id"])
            rejected[s["id"]]=["review_length_spread"]
            next(r for r in rows if r["slotId"]==s["id"]).update(passed=False,reasons=["review_length_spread"])
    def reject(identity,reason):
        accepted.pop(identity,None)
        rejected.setdefault(identity,[])
        if reason not in rejected[identity]:rejected[identity].append(reason)
        next(r for r in rows if r["slotId"]==identity).update(passed=False,reasons=rejected[identity])

    for pair in _slot_batches(slots):
        if pair[0]["kind"] != "faq" or not re.fullmatch(r"items\.\d+\.[qa]", pair[0]["role"]):continue
        fields = {s["role"].rsplit(".", 1)[1] for s in pair if s["id"] in accepted}
        if fields != {"q", "a"}:
            for s in pair:reject(s["id"], "faq_pair_incomplete")

    prior_faq=[];prior_recommendations=[]
    for slot in slots:
        identity=slot["id"]
        if identity not in accepted:continue
        text=accepted[identity]
        if slot["kind"]=="fabric" and slot["role"]=="body":
            title=next((accepted.get(s["id"],"") for s in slots if s["sectionKey"]==slot["sectionKey"] and s["role"]=="title"),"")
            if title and checks.fabric_restatement(title,text):reject(identity,"fabric_title_repeated")
        if slot["kind"]=="faq":
            item=slot["role"].rsplit(".",1)[0]
            pair=[s for s in slots if s["sectionKey"]==slot["sectionKey"] and s["role"].rsplit(".",1)[0]==item]
            if not any(s["id"]==identity for s in pair):continue
            key=(slot["sectionKey"],item)
            if any(k==key for k,_,_ in prior_faq):continue
            question=next((accepted.get(s["id"],"") for s in pair if s["role"].endswith(".q")),"")
            answer=next((accepted.get(s["id"],"") for s in pair if s["role"].endswith(".a")),"")
            if any(k[0]==key[0] and checks.faq_overlap(q,a,question,answer) for k,q,a in prior_faq):
                for s in pair:
                    if s["id"] in accepted:reject(s["id"],"faq_overlap")
            else:prior_faq.append((key,question,answer))
        if slot["kind"]=="for_you":
            prior=[(key,t) for key,t,section in prior_recommendations if section==slot["sectionKey"]]
            if checks.recommendation_overlap([*[t for _,t in prior],text]):reject(identity,"recommendation_duplicate")
            else:prior_recommendations.append((identity,text,slot["sectionKey"]))
        if slot["target"]=="feature":
            part_text=next((part_texts[name] for name in part_texts if _part_name(name)==_part_name(slot["point"])),"")
            if part_text and checks.related_detail_overlap(part_text,text):reject(identity,"part_feature_duplicate")
    warnings.extend(checks.page_repetitions(slots,accepted,facts))
    all_text=" ".join(accepted.values())
    for word in ("단정","깔끔","또렷","윤곽","인상","실루엣","정돈","자연스","차분","산뜻","보여요","수 있어요","연출","완성"):
        if all_text.count(word)>1:warnings.append({"reason":"repeated_style","word":word,"count":all_text.count(word)})
    return {"accepted":accepted,"rejected":rejected,"rows":rows,"warnings":warnings}


def _order(value, fallback=("gemini","gpt")):
    names=[s.strip().lower() for s in value.split(",")] if isinstance(value,str) else list(value or [])
    if not names or any(n not in PROVIDERS for n in names) or len(names)!=len(set(names)):
        log.warning("Invalid page-copy provider order; using fallback")
        return list(fallback)
    return names


def _routing(settings, overrides, group):
    overrides=overrides or {}
    common=_order(getattr(settings,"model_order_page_copy","gemini,gpt"))
    group_order=getattr(settings,f"model_order_page_copy_{group}","")
    order=_order(group_order,common) if group_order else common
    route=(overrides.get("groups") or {}).get(group,{})
    if overrides.get("order") is not None:order=_order(overrides["order"],common)
    if route.get("order") is not None:order=_order(route["order"],order)
    models={"gpt":getattr(settings,"model_text_gpt_page_copy","") or settings.model_text,
            "gemini":getattr(settings,"model_routing_text_gemini_page_copy","") or settings.model_text_gemini,
            "claude":getattr(settings,"model_text_claude_page_copy","")}
    models.update(overrides.get("models") or {});models.update(route.get("models") or {})
    configs={p:{**(overrides.get("settings") or {}).get(p,{}),**(route.get("settings") or {}).get(p,{})} for p in PROVIDERS}
    return {"order":order,"models":models,"settings":configs}


def _usage(provider, raw):
    if not isinstance(raw,dict):return {}
    if "input_tokens" in raw and "output_tokens" in raw:
        if provider=="claude":
            cached=raw.get("cache_read_input_tokens",0)
            return {"input_tokens":raw["input_tokens"]+cached+raw.get("cache_creation_input_tokens",0),
                    "output_tokens":raw["output_tokens"],"cached_input_tokens":cached,
                    **({"reasoning_tokens":raw["thinking_tokens"]} if "thinking_tokens" in raw else {})}
        return dict(raw)
    if provider=="gemini":
        if "promptTokenCount" not in raw or "candidatesTokenCount" not in raw:return {}
        return {"input_tokens":raw["promptTokenCount"],"output_tokens":raw["candidatesTokenCount"],
                "reasoning_tokens":raw.get("thoughtsTokenCount",0),"cached_input_tokens":raw.get("cachedContentTokenCount",0),"output_includes_reasoning":False}
    if "prompt_tokens" not in raw or "completion_tokens" not in raw:return {}
    return {"input_tokens":raw["prompt_tokens"],"output_tokens":raw["completion_tokens"],
            "reasoning_tokens":(raw.get("completion_tokens_details") or {}).get("reasoning_tokens",0),
            "cached_input_tokens":(raw.get("prompt_tokens_details") or {}).get("cached_tokens",0),"output_includes_reasoning":True}


async def _call(settings, request, timeout, metadata):
    p=request["provider"];cfg=request["settings"]
    if p=="claude":
        raw=await vision_llm._call_claude(settings,request["model"],request["prompt"],[],request["schema"],timeout,
            effort=cfg.get("effort"),thinking=cfg.get("thinking"),metadata=metadata,
            system_prompt=request["system_prompt"],max_tokens=cfg["max_output_tokens"])
    else:
        # One logged call must mean one HTTP attempt, including in budgeted evals.
        # Account failover remains unchanged for every existing vision caller.
        if p=="gpt":
            from ..openai_key_fallback import _select_key
            selected=_select_key(settings)
            if selected:settings=replace(settings,openai_api_key=selected[0],openai_api_key_fallback="")
        raw,_=await vision_llm.analyze_with_fallback(settings,request["prompt"],[],request["schema"],
            order=[p],models={p:request["model"]},reasoning_effort=cfg.get("reasoning_effort"),
            thinking_level=cfg.get("thinking_level"),thinking_budget=cfg.get("thinking_budget"),
            thinking_config=cfg.get("thinking_config"),
            timeout_seconds=timeout,system_prompt=request["system_prompt"],require_complete_envelope=True,
            max_output_tokens=cfg["max_output_tokens"],metadata=metadata)
    return {"response":raw,"usage":_usage(p,metadata.get("usage"))}


def _has_key(settings, provider):
    if provider=="claude":return bool((getattr(settings,"anthropic_api_key","") or "").strip())
    if provider=="gpt":return vision_llm.has_openai_api_key(settings)
    return bool((settings.gemini_api_key or "").strip())


def _response_from_accepted(slots,accepted):
    sections={};parts={};features={}
    for s in slots:
        if s["id"] not in accepted:continue
        text=accepted[s["id"]]
        if s["target"]=="section":sections.setdefault(s["key"],[]).append({"role":s["role"],"text":text})
        elif s["target"]=="part":parts[s["point"]]=text
        else:features[s["point"]]=text
    return {"sections":[{"key":k,"texts":v} for k,v in sections.items()],"partDescriptionCopy":[{"point":p,"desc":v} for p,v in parts.items()],"featureCopy":[{"point":p,"desc":v} for p,v in features.items()]}


def _page_routes(settings, overrides=None, *, feature=False):
    """Normalize routes without collapsing multiple models from one provider."""
    overrides = overrides or {}
    if overrides.get("groups"):
        raise ValueError("Page copy uses one route for the whole page; groups are unsupported")
    configs = overrides.get("settings") or {}
    raw = overrides.get("routes")
    if raw is None and (feature or "order" in overrides or "models" in overrides):
        route = _routing(settings, overrides, "points")
        return [{"provider": p, "model": route["models"].get(p), "settings": route["settings"][p]}
                for p in route["order"] if route["models"].get(p)]
    if raw is None:
        raw = getattr(settings, "page_copy_routes", "gemini:gemini-4-argon,gemini:gemini-4-hydrogen")
    if isinstance(raw, str):
        pairs = [part.strip().split(":", 1) for part in raw.split(",")]
        if any(len(pair) != 2 for pair in pairs):raise ValueError("Invalid page copy routes")
        raw = [{"provider": p.strip().lower(), "model": m.strip()} for p, m in pairs]
    if not isinstance(raw, list) or not raw:raise ValueError("Invalid page copy routes")
    result = []
    for row in raw:
        if (not isinstance(row, dict) or row.get("provider") not in PROVIDERS or
                not isinstance(row.get("model"), str) or not row["model"].strip() or
                any(c.isspace() for c in row["model"])):
            raise ValueError("Invalid page copy route")
        result.append({"provider": row["provider"], "model": row["model"],
                       "settings": {**configs.get(row["provider"], {}), **row.get("settings", {})}})
    return result


def has_provider_key(settings, overrides=None):
    """Read configured primary/fallback key presence without making any API call."""
    return any(_has_key(settings, row["provider"]) for row in _page_routes(settings, overrides)[:2])


def _record_call_usage(record):
    """Use the existing usage ledger; text models retain unknown USD pricing."""
    from .. import image_usage

    normalized = record.get("usage") or {}
    usage = {"normalized": normalized,
             "page_copy": {"provider": record["provider"], "status": record["status"], "rewrite": record["rewrite"]}}
    if isinstance(normalized, dict):
        usage.update(promptTokenCount=normalized.get("input_tokens"),
                     candidatesTokenCount=normalized.get("output_tokens"))
        # OpenAI/Claude output totals already include their reasoning tokens.
        includes = normalized.get("output_includes_reasoning", record["provider"] != "gemini")
        if not includes:usage["thoughtsTokenCount"] = normalized.get("reasoning_tokens")
    with image_usage.job_scope(stage="page_copy_rewrite" if record["rewrite"] else "page_copy"):
        image_usage.record(model=record["model"], image_size="text", usage=usage,
                           latency_ms=round(record["seconds"] * 1000), has_image=False)
    log.info("page_copy_call %s", _json({k: record[k] for k in ("provider", "model", "usage", "seconds", "rewrite", "status")}))


def _valid_response(raw):
    if not isinstance(raw, dict):return False
    if raw and not set(raw) & {"sections", "partDescriptionCopy", "featureCopy"}:return False
    for name in ("sections", "partDescriptionCopy", "featureCopy"):
        if name not in raw:continue  # Missing optional slots can be intentionally empty.
        if not isinstance(raw[name], list):return False
        for row in raw[name]:
            if not isinstance(row, dict):return False
            if name == "sections":
                if not isinstance(row.get("key"), str) or not isinstance(row.get("texts"), list):return False
                if any(not isinstance(item, dict) or not isinstance(item.get("role"), str) or
                       not isinstance(item.get("text"), str) for item in row["texts"]):return False
            elif (not isinstance(row.get("point"), str) or
                  not isinstance(row.get("desc"), str)):return False
    return True


def _labels(response, slots):
    labels = {}
    for name, target in (("partDescriptionCopy", "part"), ("featureCopy", "feature")):
        for row in response.get(name, []):
            label = row.get("label")
            if (not isinstance(label, str) or not label.strip() or len(re.sub(r"\s+", "", label)) > 8 or
                    checks.trade_terms(label) or re.search(_HYPE, label) or
                    re.search(r"[\n\r<>`]|[\x00-\x1f]", label)):
                continue
            for slot in slots:
                if slot["target"] == target and _part_name(slot.get("point")) == _part_name(row.get("point")):
                    labels[slot["id"]] = label.strip()
    return labels


def md_fact_fallback(facts, template):
    points = facts.get("sellingPoints") or [p.get("name") for p in facts.get("parts", [])]
    point = next((p.strip() for p in points if isinstance(p, str) and p.strip()), "")
    garment = str(facts.get("garment") or facts.get("clothingType") or "").strip()
    if not point or not garment:return ""
    final = ord(point[-1]) - ord("가")
    particle = "이" if 0 <= final < 11172 and final % 28 else "가"
    ending = "입니다." if template == "show" else "예요."
    return f"{point}{particle} 포인트인 {garment}{ending}"


def copy_results_from_result(result, storyboard=None):
    """Assembly payload; resolve legacy S keys by the same first-block rule."""
    first_ids = {}
    for block in storyboard or []:
        first_ids.setdefault(block.get("pageSectionKey"), block.get("id"))
    aliases = {f"S{i+1}": block_id for i, block_id in enumerate(first_ids.values())}
    output = []
    for row in (result or {}).get("copyResults", []):
        if not isinstance(row, dict):continue
        block_id = row.get("blockId") or aliases.get(row.get("key"))
        if not block_id:continue
        texts = [{"role": item["role"], "text": item["text"]} for item in row.get("texts", [])
                 if isinstance(item, dict) and isinstance(item.get("role"), str) and isinstance(item.get("text"), str)]
        output.append({"blockId": block_id, "texts": texts})
    return output


async def generate(settings, storyboard, product, analysis, *, seed, timeout_seconds=None, overrides=None):
    slots = slots_for_page(storyboard, product, analysis, CATALOG)
    return await generate_slots(settings, slots, product, analysis, seed=seed,
                                timeout_seconds=timeout_seconds, overrides=overrides)


async def generate_slots(settings, slots, product, analysis, *, seed, timeout_seconds=None, overrides=None, feature=False):
    """One page request, at most one route fallback and one successful-route rewrite."""
    overrides = overrides or {}
    if overrides.get("mode", "single") != "single" or any(k in overrides for k in ("polish", "capture_draft", "share_draft")):
        raise ValueError("Only single page copy mode is supported")
    slots = deepcopy(slots)
    facts = facts_bundle(product, analysis, slots)
    original = str((analysis or {}).get("mdComment") or "").strip()
    cleaned = facts.get("sellerComment", "")
    for slot in slots:
        if slot["kind"] == "intro" and slot["role"] == "body":slot["draft"] = not bool(cleaned)
    plan = plans(seed, slots, facts)
    version = "v1" if feature else overrides.get("prompt_version", getattr(settings, "page_copy_prompt_version", "v2"))
    if version not in ("v1", "v2"):raise ValueError("Invalid page copy prompt version")
    duration = (settings.analysis_timeout_seconds if feature else getattr(settings, "page_copy_timeout_seconds", 90)) if timeout_seconds is None else timeout_seconds
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:raise ValueError("Invalid page-copy timeout")
    deadline = time.monotonic() + duration
    journal = {"calls": [], "slots": [], "warnings": [], "plans": plan, "mode": "single", "prompt_version": version}
    accepted = {}; owners = {}; latest = {}; received = {}; labels = {}; used_exemplars = set()
    caller = overrides.get("caller"); dry = bool(overrides.get("dry_run"))
    config = _read_json(PROMPT_DIR/f"settings_{version}.json")
    rewrite_kinds = overrides.get("rewrite_slots", config.get("rewrite_slots", ["intro.body", "review", "faq", "for_you"]))
    required_kinds = overrides.get("required_slots", config.get("required_slots", []))
    for key, value in (("rewrite_slots", rewrite_kinds), ("required_slots", required_kinds)):
        if not isinstance(value, list) or any(not isinstance(k, str) or not k.strip() for k in value):
            raise ValueError(f"{key} must be a list of nonempty slot kinds")
    routes = _page_routes(settings, overrides, feature=feature)
    if not feature:routes = routes[:2]
    usable = []
    for route in routes:
        if not dry and not caller and not _has_key(settings, route["provider"]):
            journal["warnings"].append({"provider": route["provider"], "reason": "missing_key", "stage": "single"})
            log.warning("Page copy missing provider key: %s", route["provider"])
        else:usable.append(route)
    # Page calls have one fallback. The existing editor provider chain is kept.
    successful_route = None

    async def run_call(route, wanted, stage, remaining_attempts=1):
        provider, model = route["provider"], route["model"]
        rewrite = stage == "rewrite"
        slot_plan = {**plan, "slots": {s["id"]: plan["slots"][s["id"]] for s in wanted if s["id"] in plan["slots"]}}
        request = build_request(wanted, facts, slot_plan, provider, model, feature=feature,
            prompt_version=version, stage=stage, excluded_exemplars=used_exemplars)
        journal["warnings"].extend(request["warnings"])
        used_exemplars.update(re.sub(r"\s+", "", e["text"]) for e in request["exemplars"])
        request["settings"].update(route["settings"])
        allowed = {"max_output_tokens", "reasoning_effort"} if provider == "gpt" else {"max_output_tokens", "thinking_level", "thinking_budget", "thinking_config"} if provider == "gemini" else {"max_output_tokens", "effort", "thinking"}
        if set(request["settings"]) - allowed:raise ValueError("Unsupported page-copy model settings")
        cap = request["settings"].get("max_output_tokens")
        if type(cap) is not int or cap <= 0:raise ValueError("Page-copy output cap must be a positive integer")
        request["rewrite"] = rewrite
        if rewrite:
            context = {"rejections": {s["id"]: {"text": latest[s["id"]].get("text", ""), "reasons": latest[s["id"]]["reasons"]} for s in wanted}}
            request["prompt"] += "\n\nCORRECTION CONTEXT (data):\n" + _json(context)
        record = {"groups": request["groups"], "provider": provider, "model": model, "request": request,
                  "response": None, "validation": {}, "usage": {}, "seconds": 0, "stage": stage, "rewrite": rewrite}
        if dry:
            record["status"] = "dry_run"; journal["calls"].append(record)
            if overrides.get("after_call"):overrides["after_call"](record)
            return True
        # Preserve some total budget for a model fallback after a hung first route.
        remaining = (deadline - time.monotonic()) / remaining_attempts
        if remaining <= 0:
            journal["warnings"].append({"reason": "deadline", "stage": stage})
            log.warning("Page copy deadline exhausted before %s", stage)
            return False
        if overrides.get("before_call"):overrides["before_call"](request)
        started = time.monotonic(); metadata = {}
        try:
            envelope = await asyncio.wait_for(caller(request, remaining) if caller else _call(settings, request, remaining, metadata), remaining)
            if not isinstance(envelope, dict) or not _valid_response(envelope.get("response")):
                raise ValueError("Invalid provider response shape")
            raw = envelope["response"]; record["response"] = raw; record["usage"] = envelope.get("usage") or {}
            candidate_texts, _duplicates = _response_texts(raw, wanted)
            own = validate(raw, wanted, facts, slot_plan, exemplars=request["exemplars"])
            own_rows = {row["slotId"]: row for row in own["rows"]}
            batch_warnings = list(own["warnings"]); call_rows = []
            candidate_labels = _labels(raw, wanted)
            for batch in _slot_batches(wanted):
                identities = {slot["id"] for slot in batch}
                proposed = {**accepted, **{identity: own["accepted"].get(identity, "") for identity in identities}}
                protected = [prior for prior in slots if prior["id"] in accepted and prior["id"] not in identities]
                check_slots = protected + batch
                full = validate(_response_from_accepted(check_slots, proposed), check_slots, facts, plan)
                protected_reasons = [reason for prior in protected for reason in full["rejected"].get(prior["id"], [])]
                batch_warnings.extend(full["warnings"])
                for slot in batch:
                    identity = slot["id"]; text = own["accepted"].get(identity, "")
                    reasons = list(own["rejected"].get(identity) or full["rejected"].get(identity) or [])
                    if "missing" in reasons and _matches_slot_kind(slot, required_kinds):
                        reasons = ["missing_required" if reason == "missing" else reason for reason in reasons]
                    reasons.extend(protected_reasons)
                    candidate = candidate_texts.get(identity, "")
                    row = {"slotId": identity, "group": slot["group"], "kind": slot["kind"], "role": slot["role"], "target": slot["target"],
                           "text": text or (candidate if isinstance(candidate, str) else ""), "passed": not reasons,
                           "reasons": list(dict.fromkeys(reasons)), "rewrite": rewrite, "stage": stage, "provider": provider, "model": model}
                    if "reviewLength" in own_rows[identity]:row["reviewLength"] = own_rows[identity]["reviewLength"]
                    call_rows.append(row); latest[identity] = row
                    if row["text"].strip():received[identity] = deepcopy(row)
                    if not reasons:
                        accepted[identity] = text; owners[identity] = row
                        if identity in candidate_labels:labels[identity] = candidate_labels[identity]
                    else:accepted.pop(identity, None)
            batch_warnings = list({_json(w): w for w in batch_warnings}.values())
            record["validation"] = {"rows": call_rows, "warnings": batch_warnings}; record["status"] = "ok"
            journal["warnings"].extend(batch_warnings)
        except CallLimitError:raise
        except asyncio.CancelledError:
            record.update(status="cancelled", seconds=time.monotonic() - started,
                          usage=_usage(provider, metadata.get("usage")))
            journal["calls"].append(record)
            _record_call_usage(record)
            if overrides.get("after_call"):overrides["after_call"](record)
            raise
        except Exception as exc:
            record.update(status="error", error=type(exc).__name__, usage=_usage(provider, metadata.get("usage")))
            log.warning("Page copy provider failure provider=%s model=%s retry=%s error=%s", provider, model, rewrite, type(exc).__name__)
            for slot in wanted:
                required_missing = rewrite and "missing_required" in latest.get(slot["id"], {}).get("reasons", [])
                latest[slot["id"]] = {"slotId": slot["id"], "group": slot["group"], "kind": slot["kind"], "role": slot["role"],
                    "target": slot["target"], "text": "", "passed": False, "reasons": ["provider_error", *(["missing_required"] if required_missing else [])],
                    "rewrite": rewrite, "stage": stage, "provider": provider, "model": model}
        record["seconds"] = time.monotonic() - started; journal["calls"].append(record)
        _record_call_usage(record)
        if overrides.get("after_call"):overrides["after_call"](record)
        return record["status"] == "ok"

    phase = "single"
    try:
        if slots:
            for index, route in enumerate(usable):
                if await run_call(route, slots, "single", len(usable)-index):
                    successful_route = route; break
        if successful_route and not dry:
            phase = "rewrite"
            failed = [s for s in slots if s["id"] not in accepted and latest.get(s["id"], {}).get("reasons") and
                      ("missing_required" in latest[s["id"]]["reasons"] or
                       latest[s["id"]].get("text", "").strip() and _matches_slot_kind(s, rewrite_kinds))]
            failed_ids = {s["id"] for s in failed}
            # A rejected partner is never rewritten against stale accepted copy.
            for batch in _slot_batches(slots):
                if any(s["id"] in failed_ids for s in batch):
                    failed_ids.update(s["id"] for s in batch)
            failed = [s for s in slots if s["id"] in failed_ids]
            if failed:await run_call(successful_route, failed, "rewrite")
    except CallLimitError as error:
        journal["stopped"] = {"phase": phase, "reason": "call_limit"}
        error.partial_result = _finish_result(slots, accepted, owners, latest, received, journal, original, cleaned, dry, facts, required_kinds, labels, successful_route)
        raise
    return _finish_result(slots, accepted, owners, latest, received, journal, original, cleaned, dry, facts, required_kinds, labels, successful_route)


def _finish_result(slots, accepted, owners, latest, received, journal, original, cleaned, dry, facts, required_kinds=(), labels=None, route=None):
    """Materialize valid slots and MD fallback; rejected text remains audit-only."""
    copies = {}; part_results = {}; feature_results = {}; labels = labels or {}
    section_kinds = {}
    for slot in slots:
        if slot["target"] != "feature" and not dry:
            copies.setdefault(slot["blockId"], [])
            section_kinds[slot["blockId"]] = slot["kind"]
    for slot in slots:
        identity = slot["id"]; text = accepted.get(identity, ""); fallback = False; fact_fallback = False
        required_empty = (_matches_slot_kind(slot, required_kinds) and not text and "missing_required" in latest.get(identity, {}).get("reasons", []))
        if not dry and not text and slot["kind"] == "intro" and slot["role"] == "body":
            if cleaned and not required_empty:
                if check_body_effect(cleaned, facts)[0]:
                    journal["warnings"].append({"reason": "body_effect_seller_fallback_rejected", "slotId": identity})
                else:text = cleaned; fallback = True
            elif not cleaned:
                text = md_fact_fallback(facts, slot["template"])
                if check_body_effect(text, facts)[0]:text = ""
                fact_fallback = bool(text)
        if text and (fallback or fact_fallback):
            journal["warnings"].extend({"slotId": identity, **warning} for warning in check_body_effect(text, facts)[1])
        row = deepcopy(owners.get(identity) or (latest if required_empty else received).get(identity) or latest.get(identity) or
            {"slotId": identity, "group": slot["group"], "kind": slot["kind"], "role": slot["role"], "target": slot["target"], "passed": False, "reasons": ["not_called"]})
        source = (("seller_original" if cleaned == original else "seller_cleaned") if fallback else "fact_fallback" if fact_fallback else "ai")
        row.update(text=text or ("" if required_empty else row.get("text", "")), source=source, draft=slot.get("draft", False))
        row.setdefault("rewrite", False); row.setdefault("provider", None); row.setdefault("model", None)
        journal["slots"].append(row)
        if not text:continue
        if slot["target"] == "section":
            copies.setdefault(slot["blockId"], []).append({"role": slot["role"], "text": text, "draft": False if fallback else bool(fact_fallback or slot.get("draft", False))})
        else:
            point = _part_name(slot["point"]) if slot["target"] == "part" else slot["point"]
            item = {"point": point, "desc": text}
            if identity in labels:item["label"] = labels[identity]
            (part_results if slot["target"] == "part" else feature_results)[point] = item
    journal["warnings"] = list({_json(w): w for w in [*journal["warnings"], *checks.page_repetitions(slots, accepted, facts)]}.values())
    return {"copyResults": [{"blockId": k, "texts": compact_section_texts(v, kind=section_kinds.get(k))} for k, v in copies.items()],
            "partDescriptionCopy": list(part_results.values()), "featureCopy": list(feature_results.values()),
            "promptVersion": journal["prompt_version"], "model": (route or {}).get("model"), "log": journal}
