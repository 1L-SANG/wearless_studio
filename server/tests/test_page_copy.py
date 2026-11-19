"""Page copy contracts. All completions are local fakes."""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from app.agents.page_templates import CATALOG, get_template
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


def pc():
    return importlib.import_module("app.agents.page_copy")


def board(template="emo"):
    out = []
    for section in get_template(template)["sections"]:
        if section["presence"] != "default":
            continue
        key = f"{template}.{section['instanceKey']}"
        base = dict(taxonomyVersion=4, pageSectionKey=key, pageSectionKind=section["kind"],
                    pageDesignId=section["designId"], pageSectionUsage=section.get("usage"), sectionId=key)
        if section["kind"] in ("intro", "points", "review", "faq", "for_you"):
            out.append(dict(base, id=key+".text", source="text"))
        for index, slot in enumerate(section.get("photoSlots", [])):
            recipe = slot.get("recipe") or {}
            out.append(dict(base, id=key+f".photo{index}", source="ai", slotKey=section["instanceKey"]+"."+slot["slotKey"],
                            contentRole=slot.get("contentRole"), colorId="ivory", **{k:recipe[k] for k in ("cutType","shot","direction") if k in recipe},
                            **({"detailTargetId":"collar"} if section["kind"] == "points" else {})))
    return out


PRODUCT = {"name":"오픈 카라 골지 니트", "clothingType":"top", "colors":[{"id":"ivory","name":"아이보리","isBase":True},{"id":"blue","name":"블루"}]}
ANALYSIS = {"fit":"regular", "fitProfile":{"category":"top","gender":"women","axes":{"fit":"semi_over","sleeve":"short"}},
            "materials":[{"name":"면","ratio":60},{"name":"폴리에스터","ratio":40}],
            "sellingPoints":["전면 브랜드 로고", "어깨 셔링 퍼프 소매"],
            "detailRecommendations":{"candidates":[{"id":"collar","label":"오픈 카라","reason":"단추 없는 카라 마감"}]}}


def slot(kind="points", role="items.0.desc", template="emo", target="part", **kw):
    return dict(id="S1:"+role, key="S1", blockId="block", sectionKey=template+"."+kind,
                kind=kind, role=role, template=template, designId="test", fontRole="body", fontSize=32,
                width=888, group="points", target=target, point="오픈 카라", **kw)


def response_for(slots, texts):
    sections, parts, features = {}, [], []
    for s,text in zip(slots,texts):
        if s["target"] == "section":
            sections.setdefault(s["key"],[]).append({"role":s["role"],"text":text})
        else:
            (parts if s["target"] == "part" else features).append({"point":s["point"],"desc":text})
    return {"sections":[{"key":k,"texts":v} for k,v in sections.items()],"partDescriptionCopy":parts,"featureCopy":features}


def test_module_contract_exists():
    assert importlib.util.find_spec("app.agents.page_copy") is not None


def test_template_priority_and_legacy_page_style_absence():
    assert pc().template_of(board("show"),{}) == "show"
    assert pc().template_of(board(),{"pageStyle":{"templateId":"show"}}) == "show"
    assert pc().template_of([], {}) == "emo"
    with pytest.raises(ValueError): pc().template_of([],{"pageStyle":{"templateId":"bogus"}})


@pytest.mark.parametrize("template", ["emo","show"])
def test_slots_use_rendered_design_and_group_names(template):
    slots = pc().slots_for_page(board(template), PRODUCT, ANALYSIS, CATALOG)
    assert {s["group"] for s in slots} <= {"intro","points","fabric","voice","misc"}
    assert any(s["kind"] == "intro" and s["role"] == "body" for s in slots)
    assert any(s["target"] == "part" for s in slots)
    assert len([s for s in slots if s["target"] == "feature"]) == 2
    assert all(s["width"] > 0 and s["fontRole"] and s["template"] == template for s in slots)
    assert not any(s["role"].endswith("who") or s["role"].endswith(".name") for s in slots)
    assert any(s["kind"] == "review" for s in slots) == (template == "emo")
    assert len({s["id"] for s in slots}) == len(slots)


def test_changed_design_and_added_sections_follow_renderer():
    story = board("show")
    for b in story:
        if b["pageSectionKind"] == "intro": b["pageDesignId"]="T01-D07"
    # The first design links its own intro: explicitly changing the cover breaks that link.
    for b in story:
        if b["pageSectionKind"] == "first": b["pageDesignId"]="P01-D09"
    extra=[dict(b, pageSectionKey="show.review", sectionId="show.review",id="extra"+b["id"]) for b in board() if b["pageSectionKind"]=="review"]
    slots=pc().slots_for_page(story+extra,PRODUCT,ANALYSIS,CATALOG)
    assert any(s["kind"]=="intro" and s["role"]=="title" for s in slots)
    assert len([s for s in slots if s["kind"]=="review"])==3
    assert all(s["template"]=="show" for s in slots)


def test_facts_and_plans_are_grounded_and_seeded():
    slots=pc().slots_for_page(board(),PRODUCT,ANALYSIS,CATALOG)
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,slots)
    assert facts["colorCount"]==2 and facts["dominantMaterial"]=="면"
    assert facts["fit"]=="세미 오버핏" and "allowed" not in facts["fabricFeel"]
    assert facts["faqEvidence"]["hasLightColor"] is True and facts["faqEvidence"]["care"]
    assert facts["category"]["pairings"]
    a=pc().plans(["project","job"],slots,facts)
    assert a==pc().plans(["project","job"],slots,facts)
    assert a!=pc().plans(["project","job2"],slots,facts)
    reviews=[a["slots"][s["id"]] for s in slots if s["kind"]=="review"]
    assert len({r["topic"] for r in reviews})==3
    assert sorted(tuple(r["length"]) for r in reviews)==[(8,12),(16,22),(26,32)]
    assert all(set(r)=={"topic","length"} for r in reviews)


@pytest.mark.parametrize("s,good,bad,reason", [
    (slot(),"깃을 눕혀 펼친 채로 입어요","목걸이를 함께 하기 좋아요","other_garment"),
    (slot(),"소매를 접어서 입어요","핏을 **완성**해요","plain_text"),
    (slot(template="show"),"단추 없이 트인 카라 마감","깃을 눕혀 입어요","voice"),
    (slot("fabric","title",target="section"),"부드러운 골지","방수되는 원단","unverified_claim"),
    (slot("fabric","body",target="section"),"살에 닿는 느낌이 부드러워요","세탁해도 변형이 없어요","care_claim"),
    (slot("faq","items.0.q",target="section"),"비침은 없나요?","어떤 소재인가요?","reread_question"),
    (slot("faq","items.0.a",target="section"),"밝은 색은 살짝 비칠 수 있어요","정사이즈로 골라 주세요","size_advice"),
    (slot("for_you","items.0",target="section"),"셔츠 대신 니트를 찾던 분","블루보다 아이보리를 좋아하는 분","color_comparison"),
    (slot("first","label",target="section"),"Open Collar Knit","Open Collar 니트","english_label"),
    (slot("intro","title",target="section"),"카라가 포인트인 니트","오픈 카라 골지 니트","reread_name"),
    (slot(target="feature"),"어깨에 잡힌 주름이 소매로 이어져요","다리가 길어 보여요","body_effect"),
])
def test_slot_validation_keeps_good_and_explains_rejection(s,good,bad,reason):
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s]); plan=pc().plans(1,[s],facts)
    assert not pc()._slot_reasons(good, s, facts, plan.get("slots", {}).get(s["id"], {}))
    result=pc().validate(response_for([s],[bad]),[s],facts,plan)
    assert reason in result["rejected"][s["id"]]


@pytest.mark.parametrize("text,reason",[("면 60%로 만들었어요","reread_ratio"),("캐시미어가 섞였어요","unknown_material"),("단추가 99개예요","unknown_number"),("허벅지를 덮어줘요","body_position"),("할인 중인 상품이에요","promotion"),("제작 과정이 특별해요","process_claim"),("옷이 보여져요","double_passive")])
def test_common_bans(text,reason):
    s=slot(); facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    assert reason in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"][s["id"]]


def test_md_comment_only_accepts_seller_numbers_and_materials():
    s=slot("intro","body",target="section")
    facts=pc().facts_bundle(PRODUCT,{**ANALYSIS,"mdComment":"부드러운 면 니트. 카라 부분 강조해 주세요"},[s])
    for text,reason in [("폴리에스터를 더했어요","unknown_material"),("카라를 강조해 주세요","seller_instruction"),("면 60%를 섞었어요","unknown_number")]:
        assert reason in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"][s["id"]]
    assert s["id"] in pc().validate(response_for([s],["부드러운 면 니트예요"]),[s],facts,{"slots":{}})["accepted"]


def test_rewrite_only_failed_slots_once_and_keep_other_slots(monkeypatch):
    slots=[slot("intro","title",target="section"),slot()]
    monkeypatch.setattr(pc(),"slots_for_page",lambda *args:slots)
    requests=[]
    async def fake(request,timeout):
        requests.append(request)
        return {"response":response_for(request["slots"],["카라가 포인트인 니트" if s["kind"]=="intro" else "최고예요" for s in request["slots"]]),"usage":{"input_tokens":10,"output_tokens":20}}
    result=asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"caller":fake,"rewrite_slots":["part.desc"]}))
    assert len(requests)==2 and len(requests[1]["slots"])==1
    assert result["copyResults"][0]["texts"][0]["text"]=="카라가 포인트인 니트"
    assert not result["partDescriptionCopy"]
    assert result["log"]["calls"][1]["rewrite"] is True


def test_md_failure_returns_original_without_rewrite(monkeypatch):
    s=slot("intro","body",target="section"); monkeypatch.setattr(pc(),"slots_for_page",lambda *args:[s])
    async def fake(request,timeout):return {"response":{},"usage":{}}
    result=asyncio.run(pc().generate(make_settings(),[],PRODUCT,{**ANALYSIS,"mdComment":"카라 포인트"},seed=1,timeout_seconds=3,overrides={"caller":fake}))
    assert result["copyResults"][0]["texts"]==[{"role":"body","text":"카라 포인트","draft":False}]
    assert len(result["log"]["calls"])==1


@pytest.mark.parametrize("provider",["gpt","gemini","claude"])
def test_provider_request_has_shared_data_and_no_internal_block_ids(provider):
    s=slot("intro","title",target="section"); facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    request=pc().build_request([s],facts,pc().plans(1,[s],facts),provider,"test-model")
    assert all(k in request for k in ("system_prompt","prompt","schema","settings"))
    assert "blockId" not in request["prompt"] and "test-model" not in request["prompt"]
    assert "세미 오버핏" in request["prompt"]


def test_missing_prompt_or_placeholder_raises(tmp_path,monkeypatch):
    monkeypatch.setattr(pc(),"PROMPT_DIR",tmp_path)
    with pytest.raises(FileNotFoundError): pc().build_request([],{}, {},"gpt","x")


def test_all_groups_use_one_model_request(monkeypatch):
    slots=[slot("intro","title",target="section"),dict(slot("fabric","title",target="section"),id="S2:title",key="S2",group="fabric")]
    slots[0]["group"]="intro"
    monkeypatch.setattr(pc(),"slots_for_page",lambda *args:slots)
    settings=make_settings(); seen=[]
    async def fake(req,timeout):
        seen.append(req)
        return {"response":response_for(req["slots"],["카라 포인트" for s in req["slots"]]),"usage":{}}
    result=asyncio.run(pc().generate(settings,[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=2,overrides={"caller":fake,"routes":"gemini:test-argon,gemini:test-hydrogen"}))
    assert {(r["provider"],r["model"]) for r in seen}=={("gemini","test-argon")}
    assert len(result["log"]["calls"])==1
    assert len(seen[0]["slots"])==2


def test_transport_metadata_and_default_model_are_opt_in(monkeypatch):
    from app.agents import vision_llm as v
    seen=[]
    async def fake(settings,model,prompt,images,schema,timeout,**kwargs):
        seen.append((model,kwargs.copy()))
        if "metadata" in kwargs:kwargs["metadata"]["usage"]={"promptTokenCount":11,"candidatesTokenCount":7,"thoughtsTokenCount":3}
        return {"sections":[]}
    monkeypatch.setitem(v._PROVIDERS,"gemini",(fake,lambda s:s.model_text_gemini,lambda s:True))
    settings=make_settings();metadata={}
    asyncio.run(v.analyze_with_fallback(settings,"x",[],{},order=["gemini"],metadata=metadata,max_output_tokens=123))
    assert seen[0][1]["max_output_tokens"]==123 and metadata["usage"]["promptTokenCount"]==11
    asyncio.run(v.complete_json(settings,"x",{}))
    assert seen[1][0]==seen[0][0] and "metadata" not in seen[1][1] and "max_output_tokens" not in seen[1][1]


def test_claude_body_legacy_bytes_and_opt_in_thinking():
    from app.agents.vision_llm import _claude_body
    expected={"model":"claude-test","max_tokens":32000,"thinking":{"type":"adaptive"},"output_config":{"effort":"low","format":{"type":"json_schema","schema":{"type":"object","properties":{},"additionalProperties":False,"required":[]}}},"messages":[{"role":"user","content":[{"type":"text","text":"hello"}]}]}
    body=_claude_body("claude-test","hello",[],{"type":"object","properties":{}},"low",None,32000)
    assert json.dumps(body,ensure_ascii=False).encode()==json.dumps(expected,ensure_ascii=False).encode()
    custom=_claude_body("claude-test","hello",[],{"type":"object","properties":{}},"low",None,32000,thinking={"type":"adaptive","display":"omitted"})
    assert custom["thinking"]["display"]=="omitted"


def test_call_limit_propagates_and_dry_run_never_calls(monkeypatch):
    s=slot("intro","title",target="section");monkeypatch.setattr(pc(),"slots_for_page",lambda *args:[s])
    def stop(req):raise pc().CallLimitError("budget")
    with pytest.raises(pc().CallLimitError):
        asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"before_call":stop,"caller":lambda *args:None}))
    result=asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"dry_run":True,"caller":lambda *args:pytest.fail("sent")}))
    assert result["log"]["calls"][0]["status"]=="dry_run"


@pytest.mark.parametrize("text",["뜨거운 물로 세탁해도 괜찮아요","건조기에 넣어서 말려 주세요","한겨울에도 입기 좋은 두께예요"])
def test_faq_cannot_invent_care_or_stretch_claims(text):
    s=slot("faq","items.0.a",target="section")
    facts=pc().facts_bundle({"name":"니트"},{"materials":[{"name":"울","ratio":100}]},[s])
    assert s["id"] in pc().validate(response_for([s],[text]),[s],facts,{"slots":{s["id"]:{"topic":"세탁"}}})["rejected"]


@pytest.mark.parametrize("text",["중성세제로 손세탁해 주세요","젖은 채 걸면 늘어날 수 있어요","눕혀서 말려 주세요"])
def test_faq_accepts_care_table_paraphrases(text):
    s=slot("faq","items.0.a",target="section")
    facts=pc().facts_bundle({"name":"니트"},{"materials":[{"name":"울","ratio":100}]},[s])
    assert not pc()._slot_reasons(text, s, facts, {"topic": "세탁"})


def test_fabric_lookup_uses_word_boundaries_and_all_fibre_aliases():
    s=slot("fabric","body",target="section")
    facts=pc().facts_bundle({"name":"슬립 원피스"},{"materials":[{"name":"면","ratio":100}]},[s])
    assert "골지" not in facts.get("fabricStructure",[])
    assert s["id"] in pc().validate(response_for([s],["손에 닿는 느낌이 쫀쫀해요"]),[s],facts,{"slots":{}})["accepted"]
    facts=pc().facts_bundle({"name":"블라우스"},{"materials":[{"name":"텐셀","ratio":100}]},[s])
    assert facts["materials"][0]["name"]=="텐셀"
    assert s["id"] in pc().validate(response_for([s],["살에 닿는 느낌이 부드러워요"]),[s],facts,{"slots":{}})["accepted"]


def test_fabric_performance_is_rejected_but_unlisted_feel_is_allowed():
    s=slot("fabric","body",target="section")
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    for text in ["복원력이 뛰어난 원단이에요","보풀이 전혀 없어요"]:
        assert s["id"] in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"]
    for text in ["은은한 광택이 감돌아요","찰랑이며 떨어져요"]:
        assert s["id"] in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["accepted"]


def test_review_actual_topics_cannot_repeat():
    slots=[dict(slot("review",f"items.{i}.text",target="section"),id=f"S1:items.{i}.text") for i in range(3)]
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,slots)
    text=["아이보리 진짜 예뻐요","블루 색이 마음에 쏙 드네요","로즈 색이 정말 예쁜 옷"]
    result=pc().validate(response_for(slots,text),slots,facts,{"slots":{}})
    assert slots[0]["id"] in result["accepted"]
    assert "review_topic_duplicate" in result["rejected"][slots[1]["id"]]


def test_physical_wrap_and_show_line_boundaries():
    s=slot("fabric","body",template="show",target="section")
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    good="살에 닿는 느낌이 부드럽습니다.\n골지를 따라 늘어나는 짜임입니다."
    assert s["id"] in pc().validate(response_for([s],[good]),[s],facts,{"slots":{}})["accepted"]
    narrow={**s,"width":160,"maxWidth":160}
    assert s["id"] in pc().validate(response_for([s],[good]),[narrow],facts,{"slots":{}})["rejected"]


@pytest.mark.parametrize("size,accepted",[(30,True),(31,False),(32,False),(35,False),(36,True),(56,True),(57,False)])
def test_faq_character_gap_boundary(size,accepted):
    s=slot("faq","items.0.a",target="section")
    # Large synthetic box isolates the character contract from layout limits.
    s.update(width=5000,maxWidth=5000)
    text=("가 "*30)[:size-2]+"예요"
    reasons = pc()._slot_reasons(text, s, pc().facts_bundle({}, {}, [s]), {})
    assert (not reasons) is accepted


def test_missing_placeholder_fails_loudly(tmp_path,monkeypatch):
    from page_copy_test_support import write_page_copy_prompts
    write_page_copy_prompts(tmp_path/"prompts")
    path=tmp_path/"prompts"/"gpt_v1.md"
    path.write_text(path.read_text().replace("{{facts}}","facts"))
    monkeypatch.setattr(pc(),"PROMPT_DIR",path.parent)
    with pytest.raises(ValueError,match="placeholder"):pc().build_request([],{}, {},"gpt","x")


@pytest.mark.parametrize("word",["가볍습니다","차갑습니다","얇습니다","얇지만"])
def test_korean_feel_inflections_are_allowed_in_other_slots(word):
    s=slot();facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    text=word+" 눈에 들어와요"
    assert "feel_not_allowed" not in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"].get(s["id"],[])


def test_square_shape_is_not_rustling_feel():
    s=slot();facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    text="주머니가 사각 모양이에요"
    assert s["id"] in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["accepted"]


@pytest.mark.parametrize("name,category",[("티셔츠","티셔츠"),("sweatshirt","맨투맨·후드")])
def test_category_uses_specific_name_first(name,category):
    assert pc().facts_bundle({"name":name},{},[])["category"]["name"]==category


def test_correction_never_invalidates_previously_accepted_review(monkeypatch):
    slots=[dict(slot("review",f"items.{i}.text",target="section"),id=f"S1:items.{i}.text",group="voice") for i in range(3)]
    monkeypatch.setattr(pc(),"slots_for_page",lambda *args:slots)
    plan={"seed":"fixed","slots":{slots[0]["id"]:{"topic":"색","ending":"noun","length":[8,12]},slots[1]["id"]:{"topic":"핏과 사이즈","ending":"yo","length":[16,22]},slots[2]["id"]:{"topic":"같이 입은 옷","ending":"nida","length":[26,32]}}}
    monkeypatch.setattr(pc(),"plans",lambda *args:plan)
    calls=[]
    async def fake(request,timeout):
        calls.append(request)
        return {"response":response_for(request["slots"],["여유로운 핏이 취향"] if request["rewrite"] else ["**금지**","이런 여유 있는 핏이 참 마음에 들어요","데님과 함께 입었는데 오늘도 이 조합을 골랐습니다"]),"usage":{}}
    result=asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"caller":fake}))
    texts=result["copyResults"][0]["texts"]
    assert [t["text"] for t in texts] == ["이런 여유 있는 핏이 참 마음에 들어요", "데님과 함께 입었는데 오늘도 이 조합을 골랐습니다"]
    assert [t["role"] for t in texts] == ["items.0.text", "items.1.text"]
    assert not next(r for r in result["log"]["slots"] if r["slotId"] == "S1:items.0.text")["passed"]
    assert len(texts)==2 and len(calls)==2


@pytest.mark.parametrize("text",["면을 섞어 부드러워요","울도 섞여 있어요"])
def test_material_names_in_facts_survive_korean_word_boundaries(text):
    s=slot("fabric","body",target="section")
    facts=pc().facts_bundle({}, {"materials":[{"name":"면","ratio":80},{"name":"울","ratio":20}]}, [s])
    assert "unknown_material" not in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"].get(s["id"],[])


def test_label_must_lead_with_dominant_not_minor_material():
    s=slot("first","label",target="section")
    facts=pc().facts_bundle({}, {"materials":[{"name":"폴리에스터","ratio":80},{"name":"레이온","ratio":20}]},[s])
    assert "minority_material" in pc().validate(response_for([s],["Rayon Polyester Knit"]),[s],facts,{"slots":{}})["rejected"][s["id"]]


def test_review_can_name_a_pairing_without_claiming_own_material():
    s=slot("review","items.0.text",target="section")
    facts=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    assert s["id"] in pc().validate(response_for([s],["데님과 함께 입기 좋아요"]),[s],facts,{"slots":{}})["accepted"]


def test_spandex_requires_actual_blend_and_is_not_polyester_alias():
    assert pc()._material_words("폴리우레탄")=={"스판"}
    f=pc().facts_bundle({"name":"블라우스"},{"materials":[{"name":"폴리에스터","ratio":100}]},[])
    assert not any("스판" in m["name"] for m in f["materials"])
    f=pc().facts_bundle({"name":"티셔츠"},{"materials":[{"name":"면","ratio":95},{"name":"스판","ratio":5}]},[])
    assert any("스판" in m["name"] for m in f["materials"])


@pytest.mark.parametrize("name,text",[("트위드 재킷","손에 닿는 느낌이 도톰해요"),("레더 재킷","겉면이 보슬보슬해요"),("폴리에스터 셔츠","찰랑이며 떨어져요")])
def test_fabric_feel_no_longer_requires_allowed_table_entry(name,text):
    s=slot("fabric","body",target="section")
    f=pc().facts_bundle({"name":name},{"materials":[{"name":"폴리에스터","ratio":100}]},[s])
    assert s["id"] in pc().validate(response_for([s],[text]),[s],f,{"slots":{}})["accepted"]


def test_long_review_is_one_sentence_even_when_rendered_on_two_lines():
    s=slot("review","items.0.text",target="section");s.update(width=139,maxWidth=691,fontSize=32)
    f=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    text="마음에 드는 아이보리 색이라서 오늘도 이 색을 골랐습니다"
    assert s["id"] in pc().validate(response_for([s],[text]),[s],f,{"slots":{}})["accepted"]


def test_feature_sixty_character_cap_is_not_single_physical_line():
    s=slot(target="feature")
    text="어깨에서 잡은 주름이 소매로 이어지고 앞면의 배색 로고가 가운데에 자리하고 있어요"
    assert len(text)<=60
    f=pc().facts_bundle(PRODUCT,ANALYSIS,[s])
    assert s["id"] in pc().validate(response_for([s],[text]),[s],f,{"slots":{}})["accepted"]




@pytest.mark.parametrize("template", ["emo", "show"])
def test_part_plans_never_invent_viewing_angles(template):
    names = ["오픈 카라", "뒤쪽 골지 조직", "리본 끈", "후드", "소매", "밑단", "옆 트임", "지퍼 여밈"]
    slots = [dict(slot(template=template), id=f"P{i}", point=name) for i, name in enumerate(names)]
    facts = {"clothingType": "셔츠", "axes": {"length": "기본"}, "faqEvidence": {"sleeve": "긴소매"},
             "parts": [{"name": name, "evidence": "뒤쪽 마감" if i < 2 else "지퍼 여밈"} for i, name in enumerate(names)]}
    allowed = {"묶기와 풀어 두기", "조여 입기", "쓰고 내려 두기", "넣어 두기", "여닫기",
               "열어 입기와 잠가 입기", "팔을 걷을 때", "넣어 입기", "움직일 때"}
    entries = pc().plans(1, slots, facts)["slots"]
    for s in slots:
        entry = entries[s["id"]]
        assert set(entry) <= {"angle", "evidenceOnly", "omit"}
        if template == "show" or s["point"] in names[:2]:
            assert "angle" not in entry
        else:
            assert "angle" not in entry


def test_recommendation_plan_has_no_assigned_axes():
    slots = [dict(slot("for_you", f"items.{i}", target="section"), id=f"R{i}") for i in range(4)]
    facts = pc().facts_bundle(PRODUCT, ANALYSIS, slots)
    for seed in range(30):
        for entry in pc().plans(seed, slots, facts)["slots"].values():
            assert "axis" not in entry


def test_review_touch_topic_does_not_require_fabric_feel_words():
    slots = [dict(slot("review", f"items.{i}.text", target="section"), id=f"R{i}") for i in range(3)]
    selected = set()
    for seed in range(30):
        empty = pc().plans(seed, slots, {"fabricFeel": {"allowed": []}})
        selected.update(p["topic"] for p in empty["slots"].values())
        present = pc().plans(seed, slots, {"fabricFeel": {"allowed": ["부드러운"]}})
        selected.update(p["topic"] for p in present["slots"].values())
    assert "촉감" in selected


@pytest.mark.parametrize("size,passed,deviation", [(4,False,-4),(5,True,-3),(8,True,0),(12,True,0),(15,True,3),(16,False,4),(34,False,22),(35,False,23)])
def test_review_range_tolerance_and_logged_distance(size, passed, deviation):
    s = slot("review", "items.0.text", target="section"); s.update(width=5000, maxWidth=5000)
    text = ("옷 " * 20)[:size-1] + "굿"
    plan = {"slots": {s["id"]: {"length": [8,12]}}}
    result = pc().validate(response_for([s], [text]), [s], {}, plan)
    assert (s["id"] in result["accepted"]) is passed
    assert result["rows"][0]["reviewLength"] == {"characters":size, "planned":[8,12], "deviation":deviation}
    if deviation:
        assert any(w["reason"] == "review_length_deviation" and w["deviation"] == deviation for w in result["warnings"])


@pytest.mark.parametrize("size,passed", [(23,True),(34,True),(35,False)])
def test_long_review_still_has_hard_cap_34(size, passed):
    s = slot("review", "items.0.text", target="section"); s.update(width=5000, maxWidth=5000)
    text = ("옷 " * 20)[:size-1] + "굿"
    result = pc().validate(response_for([s], [text]), [s], {}, {"slots":{s["id"]:{"length":[26,32]}}})
    assert (s["id"] in result["accepted"]) is passed


def test_review_length_spread_rejects_highest_index_regardless_of_input_order():
    slots = [dict(slot("review", f"items.{i}.text", target="section"), id=f"R{i}") for i in range(3)]
    texts = ["색이 정말 마음에 들어요", "핏이 제일 마음에 드네요", "늘 손이 가는 옷입니다"]
    assert max(map(len,texts))-min(map(len,texts)) < 8
    result = pc().validate(response_for(slots, texts), slots[::-1], {}, {"slots":{}})
    assert result["rejected"]["R2"] == ["review_length_spread"]
    assert set(result["accepted"]) == {"R0", "R1"}


@pytest.mark.parametrize("kind,role", [("review","items.0.text"),("faq","items.0.a")])
def test_review_and_faq_accept_feelings_without_allowed_table(kind, role):
    s = slot(kind, role, target="section")
    text = "살에 닿는 느낌이 부드러워요"
    for allowed, passed in [([],True),(["부드러운"],True)]:
        facts = {"fabricFeel":{"allowed":allowed}}
        assert (not pc()._slot_reasons(text, s, facts, {})) is passed


@pytest.mark.parametrize("word,allowed", [("편하게",True),("편안하게",True),("편해요",True),("편한",True),("착용감",True)])
def test_comfort_words_are_allowed_in_every_slot(word, allowed):
    s = slot();text = f"{word} 접어서 입어요"
    result = pc().validate(response_for([s],[text]), [s], {}, {"slots":{}})
    assert ("feel_not_allowed" not in result["rejected"].get(s["id"],[])) is allowed


def test_fabric_and_faq_allow_table_stretch_without_treating_it_as_care():
    for kind in ("fabric", "faq"):
        s = slot(kind,"body" if kind=="fabric" else "items.0.a",target="section")
        text = "몸을 따라 편하게 늘어나요"
        facts = {"fabricFeel":{"allowed":["몸을 따라 늘어나는"]}}
        assert not pc()._slot_reasons(text, s, facts, {})


def test_prompt_slots_are_compact_examples_unique_and_limits_authoritative(tmp_path, monkeypatch):
    from page_copy_test_support import write_page_copy_prompts
    write_page_copy_prompts(tmp_path/"prompts")
    monkeypatch.setattr(pc(), "PROMPT_DIR", tmp_path/"prompts")
    # A fixture envelope tests substitution without coupling to owner-written prose.
    (pc().PROMPT_DIR/"gpt_v1.md").write_text("{{rules}}\n---USER---\n{\"facts\":{{facts}},\"plans\":{{plans}},\"examples\":{{examples}},\"slots\":{{slots}}}")
    example = {"slot":"faq", "template":"emo", "before":"before", "after":"after"}
    (pc().PROMPT_DIR/"examples_v1.json").write_text(json.dumps({"examples":[example,dict(example)]}))
    slots = [slot("faq","items.0.q",target="section"),slot("faq","items.0.a",target="section"),slot()]
    request = pc().build_request(slots, {}, {"seed":"same","slots":{}}, "gpt", "test-model")
    payload = json.loads(request["prompt"])
    assert payload["examples"] == [example]
    allowed = {"id","kind","role","template","target","point","maxChars","maxLines","maxLineChars","draft"}
    assert all(set(s) <= allowed for s in payload["slots"])
    assert [(s["maxChars"],s["maxLines"]) for s in payload["slots"]] == [(20,1),(56,2),(44,2)]
    assert [s["target"] for s in payload["slots"] if s["kind"]=="points"] == ["part"]
    assert request["slots"] == slots  # The validator's geometry stays internal.


def test_sections_part_copy_is_discarded_and_logged_without_hiding_correct_part(monkeypatch):
    s = slot();good="깃을 눕혀 펼쳐 입어요"
    response = response_for([s],[good])
    response["sections"] = [{"key":s["key"],"texts":[{"role":s["role"],"text":"잘못된 위치의 설명"}]}]
    checked = pc().validate(response,[s],{}, {"slots":{}})
    assert checked["accepted"] == {s["id"]:good}
    assert any(w["reason"]=="part_in_sections_ignored" and w["slotId"]==s["id"] for w in checked["warnings"])
    monkeypatch.setattr(pc(),"slots_for_page",lambda *args:[s])
    async def fake(request, timeout):return {"response":response,"usage":{}}
    out=asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"caller":fake}))
    assert out["copyResults"] == [{"blockId": "block", "texts": []}] and out["partDescriptionCopy"] == [{"point":s["point"],"desc":good}]
    assert any(w["reason"]=="part_in_sections_ignored" for w in out["log"]["calls"][0]["validation"]["warnings"])


@pytest.mark.parametrize("text", ["온수에 담가도 몸을 따라 늘어나요", "표백해도 몸을 따라 늘어나요", "뜨겁게 다려도 몸을 따라 늘어나요"])
def test_allowed_stretch_does_not_bypass_care_instruction_validation(text):
    s=slot("faq","items.0.a",target="section")
    facts=pc().facts_bundle({"name":"골지 니트"},{"materials":[{"name":"면","ratio":95},{"name":"스판","ratio":5}]},[s])
    assert "unsupported_care" in pc().validate(response_for([s],[text]),[s],facts,{"slots":{}})["rejected"][s["id"]]


def test_review_deviation_is_present_in_call_and_final_logs(monkeypatch):
    s=slot("review","items.0.text",target="section")
    monkeypatch.setattr(pc(),"slots_for_page",lambda *args:[s])
    monkeypatch.setattr(pc(),"plans",lambda *args:{"seed":"test","slots":{s["id"]:{"length":[8,12]}}})
    text="살에 닿는 느낌이 순하네요"
    assert len(text)==14
    async def fake(request,timeout):return {"response":response_for([s],[text]),"usage":{}}
    out=asyncio.run(pc().generate(make_settings(),[],PRODUCT,ANALYSIS,seed=1,timeout_seconds=3,overrides={"caller":fake}))
    assert out["log"]["slots"][0]["reviewLength"]["deviation"]==2
    assert out["log"]["calls"][0]["validation"]["rows"][0]["reviewLength"]["deviation"]==2
