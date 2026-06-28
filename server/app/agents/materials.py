"""소재 인식 → 렌더링 가이드 블록 (documents/material_prompt_blocks.md 구현). 순수 함수, IO 없음.

원칙(정본 §1):
- Reference-first: 블록은 원본 사진을 거스르지 않고 '섬유 거동 prior'만 보강(모든 블록에 reference 가드).
- No ratio math: 이미지 모델은 %를 계산 못함 → 비율은 '어떤 단어/순서를 쓸지'만 결정.
- Construction(데님·레더·기모·니트…) > fiber ratio.
- Elastane은 표면 아닌 '핏 거동'만.

보안(기획자 판단): 가이드 본문은 **canonical 영문 키**로만 구성 — 셀러 자유텍스트(소재 name)를
프롬프트 지시에 끼우지 않는다(인젝션 안전 + 영어가 이미지 모델에 정확). 한국어 원문은
_product_block의 sanitize된 'Material:' 줄에만 남는다.
"""

# ── §2.1 한국어 소재명 → canonical key (alias) ───────────────────────────────
_ALIASES = {
    "cotton": ["코튼", "면", "순면", "오가닉코튼", "cotton"],
    "polyester": ["폴리에스터", "폴리에스테르", "폴리", "polyester", "pe"],
    "nylon": ["나일론", "nylon", "폴리아미드"],
    # 부드러운 셀룰로오스 드레이프는 렌더가 사실상 동일 → rayon 한 키로 통합(모달·텐셀·리오셀). '인견' 제거(니치).
    "rayon": ["레이온", "비스코스", "비스코스레이온", "viscose", "rayon",
              "모달", "modal", "텐셀", "리오셀", "라이오셀", "tencel", "lyocell"],
    "linen": ["린넨", "리넨", "linen"],  # '마' 제거 — 1글자라 '겉면→면' 류 오인식 유발
    "wool": ["울", "양모", "wool"],  # '모'(1글자 오인식)·'메리노'(울 하위종) 제거
    "cashmere": ["캐시미어", "캐시미어울", "cashmere"],
    "acrylic": ["아크릴", "acrylic"],
    "silk": ["실크", "견", "silk"],
    "acetate": ["아세테이트", "acetate"],
    "elastane": ["스판", "스판덱스", "폴리우레탄", "엘라스탄", "엘라스테인", "elastane", "spandex"],
    # 직조/마감 신호(섬유 아님) — materials[]엔 fallback으로만
    "denim": ["데님", "청"],
    "leather": ["레더", "가죽", "합성가죽", "인조가죽", "페이크레더", "pu레더"],
    "brushed": ["기모", "기모안감", "fleece"],
    "knit": ["니트", "knit"],
    "seersucker": ["시어서커", "seersucker"],
    "chiffon": ["시폰", "chiffon"],
    "gauze": ["거즈", "더블거즈", "gauze"],
    "mesh": ["메쉬", "아일렛", "mesh", "eyelet"],
    "summerknit": ["썸머니트", "썸머 니트", "여름니트", "여름 니트", "summer knit"],
}
_ALIAS_TO_KEY = {a.lower(): k for k, al in _ALIASES.items() for a in al}
_CONSTRUCTION = {"denim", "leather", "brushed", "knit", "seersucker", "chiffon", "gauze", "mesh", "summerknit"}
_GUARD = ("Keep this subordinate to the actual product reference image if it shows a different weave, "
          "weight, or finish. Do not interpolate or calculate visual properties from the percentages.")

# ── §4 단일 섬유 블록 (영어) ─────────────────────────────────────────────────
FIBER_BLOCK = {
    "cotton": "cotton with a fairly matte, natural surface and a fine woven or jersey grain by default, plus soft irregular wrinkles and creases at bends; medium body as a starting point. Follow the photo for body, drape, opacity, stretch and sheen — cotton can also be smooth and lustrous (mercerized/Pima, sateen) or glossy (glazed/chintz), so render the finish shown; it still reads as a natural fabric.",
    "polyester": "smooth synthetic, typically cleaner and more uniform than cotton, with strong wrinkle resistance and controlled folds as the default. Let the photo set sheen, opacity, drape and stretch: the surface can range from matte (spun, brushed, microfiber) through a soft satin/charmeuse sheen that looks bright and near-liquid — match whatever the reference shows, including high-gloss satin, rather than capping it.",
    "nylon": "lightweight synthetic with a smooth, tightly-woven surface, good shape recovery, crease resistance and a clean springy hand. Sheen, drape, stretch and opacity vary widely by weave/finish — from matte technical taffeta/tricot to bright trilobal luster or fluid glossy nylon satin — so defer to the photo for luster, gloss and how the folds fall.",
    "rayon": "soft cellulosic drape (rayon/viscose/modal/tencel family): tends to a fluid, soft drape with rounded folds that hang close to the body and a smooth, cool-touch hand. Let the photo set sheen, texture, opacity and stretch — from matte challis/crepe to glossy satin or cupro, or crisp poplin/taffeta if shown — and preserve any creases, crinkle or wrinkles visible in the garment.",
    "linen": "linen tends to read as a dry, breathable fabric with a slightly coarse, slubby surface, a soft natural sheen (mostly matte but not dead-flat), and relaxed wrinkles or creases. Follow the photo for surface smoothness, luster, drape/weight, stretch, and how pressed it looks — fine, lustrous, stretch-blend, or freshly pressed linen are all legitimate.",
    "wool": "warm wool with natural body and springy, rounded folds; surface and sheen follow the photo — often soft and matte with a faint fuzzy halo (woolen, flannel, tweed), but smooth and clean with a subtle-to-crisp luster on worsted, gabardine, sharkskin and mohair blends. Heavy/felted wool reads weighty in deep structured folds; thin wools (challis, tropical) drape fluid and close.",
    "cashmere": "soft, fine cashmere with a gentle, even surface halo and a subtle natural low sheen; lightweight with a soft, fluid drape by default. Follow the photo for sheen, surface (smooth to brushed/bloomed), any pilling, opacity, drape stiffness, and knit-vs-woven structure.",
    "acrylic": "synthetic knit with soft bulk and a springy, resilient hand; lighter and loftier than wool, with rounded, soft folds and moderate volume. Tends matte and slightly plush, but follow the photo for sheen and surface (smooth, lightly fuzzy, brushed-halo, or pilled) — acrylic can read anywhere from soft and cashmere-like to glossy depending on finish.",
    "silk": "fine silk with a natural sheen and, by default, a fluid drape that follows the figure plus soft irregular wrinkles. Match luster, surface texture, opacity and body to the photo: a bright liquid/satin sheen where the reference shows a satin/charmeuse or taffeta weave, low-sheen and matte for crepe/twill, a soft glow for habotai; if it shows a crisp/structured silk (dupioni, taffeta, shantung, organza), let it hold its shape and texture.",
    "acetate": "smooth, lightweight cellulosic fabric that usually carries a soft satin-like sheen and a fluid, graceful drape; let the photo set the actual gloss (glossy satin vs matte crepe) and how crisp vs flowing the garment looks.",
}
# elastane은 표면 아닌 modifier (§4 마지막)
_ELASTANE_FIBER = ("elastane affects fit, not surface: the garment sits closer to the body and recovers smoothly, "
                   "with gentle tension lines at stress points (shoulders, bust, waist, hips, knees, seat) and "
                   "smoother, less-broken wrinkles. Take sheen, texture and opacity from the photo — stretch fabrics "
                   "range from fully matte to glossy or wet-look.")

# ── §2.4 elastane 밴드 ───────────────────────────────────────────────────────
def _elastane_mod(ratio: float) -> str:
    if ratio < 2:
        return ""
    if ratio <= 4:
        return "Spandex modifier: subtle stretch recovery; fabric sits closer to the body with light tension."
    if ratio <= 8:
        return ("Spandex modifier: noticeable stretch; smoother body contour; fewer sharp wrinkles; "
                "gentle pull lines at stress points.")
    return ("Spandex modifier: high-stretch with firm recovery; close body-skimming fit; tension lines at bends. "
            "Reads as a stretch textile — take any sheen from the photo.")

# ── §5 조합 블록 (영어) — 상위 2섬유 frozenset 키 ────────────────────────────
COMBO_BLOCK = {
    frozenset(["cotton", "polyester"]): "cotton-polyester blend: a cotton-forward base made smoother, more uniform and more wrinkle-resistant by polyester, with relaxed (less-crushed) folds and typically medium body; sheen tends matte-to-soft. Follow the photo for the actual finish — it can range from flat-matte jersey/percale to a glossy poly-cotton satin/sateen — and for drape, stretch (knits cling), weight and opacity.",
    frozenset(["cotton", "elastane"]): "cotton-elastane: usually a soft, mostly matte cotton-like surface with elastane stretch — sits a little closer to the body with gentle pull lines and softer, controlled wrinkles. Follow the photo for sheen, weave texture, weight and opacity: a sateen/satin-weave or mercerized cotton-elastane can look lustrous, and knits can cling or read lighter.",
    frozenset(["polyester", "elastane"]): "polyester-elastane: a synthetic stretch blend that conforms to the body with good elastic recovery, generally crisp and wrinkle-resistant. Surface and sheen vary widely by knit and finish — from matte/brushed (peachskin, crepe) through soft sheen to high-gloss wet-look — and weight from soft drapey jersey to thick structured double-knit (scuba/ponte); match what the photo shows.",
    frozenset(["nylon", "elastane"]): "nylon-elastane knit: smooth, compact, fine-knit face with elastic stretch and quick recovery; tends to fit close and form springy, recovering folds with tension lines at bends. Sheen and opacity are image-conditional — a subtle sporty/semi-matte luster by default, but follow the photo if it shows a glossy, satin, wet-look, foiled or fully matte/brushed finish. Reads as a stretch knit textile.",
    frozenset(["rayon", "polyester"]): "rayon-polyester: a soft fabric with rayon-like fluid drape and polyester-added stability, giving longer, cleaner folds than cotton; matte-to-light sheen and a smooth surface by default. Follow the photo for weight, body, sheen (including full satin gloss), surface texture (including slubs), opacity and stretch — including crisp/structured fabrics.",
    frozenset(["linen", "cotton"]): "linen-cotton: a breathable natural blend tending to a dry, lightly textured surface with subtle slubs, relaxed (not harsh) creasing, and airy, softly-structured folds — crisper than fluid fibers like rayon. Soft matte-to-low-luster and a relaxed fit by default, but follow the photo for sheen (sateen/mercerized/waxed can be lustrous), stretch/fit, drape, weight and opacity.",
    frozenset(["linen", "rayon"]): "linen-rayon: a natural summer cellulosic — linen slub texture softened by rayon's fluid drape, breathable, with relatively soft wrinkles and a soft subtle luster by default (rayon can push it brighter). Match the exact sheen, weave, body and opacity shown — it can range from drapey-soft to crisp, matte to glossy.",
    frozenset(["rayon", "cotton"]): "soft cellulosic-cotton blend (rayon/viscose/modal/tencel + cotton): tends smooth-handed with a slightly more fluid, body-skimming drape and softer, lower-contrast wrinkling than pure cotton. Sheen, crispness, opacity, stretch and weave (matte challis to soft-sheen sateen, drapey knit to crisp poplin/twill) vary widely — follow the photo for all of these.",
    frozenset(["wool", "nylon"]): "wool-nylon blend: typically a warm, soft wool surface with a faint fiber halo, good shape retention and a cleaner, more durable hand than pure wool. Let the photo set thickness (thin tropical knit up to heavy suiting), fold character (rounded or crisp), stretch, and sheen — matte and fuzzy for woolen/flannel, smooth and subtly-to-notably lustrous for worsted or sharkskin weaves.",
    frozenset(["wool", "polyester"]): "wool-polyester blend: typically warm and soft with good shape retention and body, falling in rounded, structured folds; surface ranges from matte (woolen/flannel) to a soft sheen (worsted/sharkskin or higher-polyester weaves) and may show a faint fiber halo — match the photo's sheen, texture, weight and any stretch.",
    frozenset(["wool", "cashmere"]): "wool-cashmere: a fine, premium hand with soft drape, lightweight warmth, and a gently matte, refined surface by default. Follow the photo for construction (knit or woven coating), surface finish (smooth vs brushed halo), sheen, body, and any natural pilling or fiber halo.",
}
# 3섬유 슈팅 블렌드(폴리+레이온+스판) — 별도 매칭(§5)
_SUITING = ("polyester-rayon-spandex suiting: a smooth, opaque, light-to-medium-weight fabric with soft rayon-led "
            "drape, polyester shape retention and wrinkle resistance, and slight give at fitted areas. Surface is "
            "usually matte to lightly lustrous — match the exact sheen, weave (including any satin/sateen gloss) and "
            "texture shown. Reads as structured woven suiting; keep its coverage and weight as shown.")

# ── §6 / §6.5 직조·마감 override 블록 ────────────────────────────────────────
OVERRIDE_BLOCK = {
    "denim": "denim twill: typically a sturdy cotton fabric with a structured body and a visible diagonal twill grain, holding firm angular folds and crease lines at knees/hips/waistband/pockets/hems; matte-to-low-sheen and washed indigo or black by default. Follow the photo for fiber/blend, weight and wash: a softer fluid drape for lightweight/washed/stretch/lyocell denim, and visible sheen or a smoother grain for a coated/waxed or satin-weave finish.",
    "leather": "leather-like material: an opaque hide with no woven thread structure, carrying whatever sheen and surface the photo shows — glossy/patent enamel, satin nappa, or matte velvety suede/nubuck — with highlights that follow folds and seams. Match the photo's body and fold character: thicker leathers fold in broad structured folds, while soft lambskin/chrome-tanned/stretch leathers drape fluidly and crease finely like fabric.",
    "brushed": "brushed/fleece (napped): a soft napped pile that adds loft for a slightly thicker, cozier silhouette with softened rounded folds, usually matte and light-diffusing. Follow the photo for where the nap appears (smooth jersey face with brushed interior on brushed-back/sweatshirt styles vs a fully napped/plush exterior on polar, coral, sherpa or 'teddy' fleece) and for sheen, weight, opacity and stretch; add looped terry or fur/shearling only if the reference shows it.",
    "knit": "knit: let the visible gauge and garment category drive texture (jersey, rib, cable, waffle, pointelle) before fiber ratio, and render the stitch structure the reference actually shows. Soft body-following drape with no hard ironed creases by default, and take sheen from the photo — typically matte for wool/cotton, but mercerized, rayon, silk or lurex/metallic knits can read shiny.",
    "seersucker": "seersucker: a lightweight fabric (classically cotton, also silk/poly/blends) with alternating raised crinkled stripes and flat smooth stripes forming a bumpy 3D surface that holds slightly away from the body, for an intentional rumpled look. Usually a matte, dry, airy hand, but follow the photo for sheen, drape, weight, and how subtle or pronounced the pucker is; keep the striped puckered texture rather than smoothing it flat.",
    "chiffon": "chiffon: a very lightweight, airy sheer fabric with a fine grainy/crinkled (high-twist crepe) surface that floats and ripples in soft folds and diffuses light. Sheen and opacity follow the reference — a soft matte-to-shimmer look by default, but keep a lustrous or satin-like sheen if the photo shows it, and preserve the exact lining, opacity and coverage shown.",
    "gauze": "cotton gauze: a soft, loosely/open-woven cotton with an airy, breathable, lightweight hand and a relaxed drape; a slightly crinkled, matte, low-sheen surface by default, and double-layer gauze usually reads more opaque than single. Follow the photo for sheen (mercerized or silk-blend gauze can look smoother and lustrous), smoothness (crinkle can be pressed out), and opacity (light/open-weave gauze can be a bit sheer).",
    "mesh": "mesh/eyelet: a visible regular open structure (mesh holes or embroidered eyelets) — keep the openings open and the hole pattern even, matching the reference. Follow the photo for sheen (matte cotton/poly vs lustrous nylon/tulle), drape, stretch and coverage: keep a lining/underlayer only if the reference shows one, otherwise render the openings as genuinely open; if the netting itself is sheer let the whole surface read translucent.",
    "summerknit": "summer open-gauge knit: a visible airy stitch structure with open spaces between stitches and a light, fluid drape; reads lighter and less compact than a tight winter knit and is often somewhat see-through between stitches — but follow the reference for actual opacity, yarn thickness/bulk, stitch pattern and sheen. Apply only when the reference shows an open summer knit.",
}

UNKNOWN_BLOCK = ("Render the fabric faithfully to the product reference image (its texture, sheen, drape, and "
                 "weight). Do not invent shine, stretch, or special weave that the reference does not show.")


# 부분일치용 안전 alias: 한글 ≥2자 / 라틴 ≥3자 (1자 음절 면·견·청 + 'pe' 오탐 제외), 긴 것 우선
_SUBSTR_ALIASES = sorted(
    (a for a in _ALIAS_TO_KEY if (len(a) >= 3 if a.isascii() else len(a) >= 2)),
    key=len, reverse=True,
)


def _canonical(name: str) -> str | None:
    """소재명 → canonical 키. 전체/토큰 exact 우선 → 토큰 내 안전-부분일치(긴 alias 먼저).
    '겉면 폴리에스터'처럼 라벨이 섞여도 토큰 단위라 '면'⊂'겉면' 오분류를 막는다.
    1자 음절(면·견·청 등) 부분일치는 금지 — exact일 때만 인정."""
    s = (name or "").strip().lower()
    if not s:
        return None
    if s in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[s]
    tokens = s.split() or [s]
    for tok in tokens:  # 토큰 exact ('면 혼방'→면=cotton, '겉면 폴리에스터'→폴리에스터)
        if tok in _ALIAS_TO_KEY:
            return _ALIAS_TO_KEY[tok]
    for tok in tokens:  # 토큰 내 안전 부분일치 ('캐시미어울'→cashmere), 긴 alias 우선
        for alias in _SUBSTR_ALIASES:
            if alias in tok:
                return _ALIAS_TO_KEY[alias]
    return None


def normalize(materials: list) -> list[dict]:
    """[{name,ratio}] → [{key,ratio}] (canonical 병합·정렬). 알 수 없는 키는 제외.
    합이 80~120이면 100 정규화. ratio 없으면 균등 추정."""
    agg: dict[str, float] = {}
    known_any = False
    for m in materials or []:
        if not isinstance(m, dict):
            continue
        key = _canonical(m.get("name", ""))
        if not key or key in _CONSTRUCTION:  # construction(데님·니트…)은 섬유 아님 → 정규화 제외(별도 신호)
            continue
        known_any = True
        r = m.get("ratio")
        r = max(0.0, min(100.0, float(r))) if isinstance(r, (int, float)) else 0.0
        agg[key] = agg.get(key, 0.0) + r
    if not known_any:
        return []
    total = sum(agg.values())
    if total == 0:  # ratio 미입력 → 균등
        n = len(agg)
        agg = {k: 100.0 / n for k in agg}
    elif 80 <= total <= 120 and total != 100:
        agg = {k: v * 100.0 / total for k, v in agg.items()}
    ordered = sorted(({"key": k, "ratio": v} for k, v in agg.items()), key=lambda x: -x["ratio"])
    # §2.3 trace 무시: 비-elastane <3% 제거(elastane은 ≥2% 유지). 전부 trace면 최상위 1개는 남김.
    kept = [m for m in ordered if m["ratio"] >= 3 or (m["key"] == "elastane" and m["ratio"] >= 2)]
    return kept or ordered[:1]


def _construction_keys(materials: list) -> set:
    """materials[]에 등장한 construction 키 (ratio 무관 — 0/미입력도 신호로 본다). 섬유 정규화와 분리."""
    out = set()
    for m in materials or []:
        if isinstance(m, dict):
            k = _canonical(m.get("name", ""))
            if k in _CONSTRUCTION:
                out.add(k)
    return out


def _detect_construction(clothing_type: str, sub_category: str, constr_keys: set) -> str | None:
    """hard override (denim·leather·brushed). 트리거: category/subcategory(우선) + materials construction 키."""
    ctx = f"{clothing_type or ''} {sub_category or ''}".lower()
    for ov, hints in (("denim", ["denim", "jean", "청", "데님"]),
                      ("leather", ["leather", "가죽", "레더"]),
                      ("brushed", ["기모", "fleece", "brushed"])):
        if ov in constr_keys or any(h in ctx for h in hints):
            return ov
    return None


def _cat_has_knit(clothing_type: str, sub_category: str) -> bool:
    ctx = f"{clothing_type or ''} {sub_category or ''}".lower()
    return "knit" in ctx or "니트" in ctx


def _knit_cue(knit_ctx: bool) -> str:
    return ("Visible knit stitch structure faithful to the reference (rib/cable/jersey/waffle as shown)."
            if knit_ctx else "")


def _unknown() -> str:
    return f"- Material rendering guidance:\n  {UNKNOWN_BLOCK}\n  {_GUARD}"


def material_guidance(materials: list, clothing_type: str = "", sub_category: str = "") -> str | None:
    """PRODUCT CONTEXT의 'Material:' 줄 다음에 붙일 렌더링 가이드 블록(영어). 없으면 None.
    선택 로직: 정본 §2.5 (construction override > suiting > combo > dominant > blend) + §2.3 trace/§2.4 elastane.
    construction(데님·니트…)은 normalize에서 빠지고 _construction_keys로 따로 감지 — ratio 0/저비율에도 견고."""
    known = normalize(materials)  # 섬유만, trace(<3%) 제거됨, elastane은 ≥2%만
    constr = _construction_keys(materials)  # 섬유 정규화와 무관하게 construction 신호 보존
    elastane = next((m for m in known if m["key"] == "elastane"), None)
    el_mod = _elastane_mod(elastane["ratio"]) if elastane else ""

    # 1) construction override (섬유 유무와 무관하게 먼저 — denim/leather/brushed hard + 비-hard non-knit)
    ov = _detect_construction(clothing_type, sub_category, constr) or next(
        (k for k in constr if k != "knit" and k in OVERRIDE_BLOCK), None)
    if ov:
        return _wrap(OVERRIDE_BLOCK[ov], el_mod)

    solids = [m for m in known if m["key"] != "elastane"]  # known은 이미 섬유만(construction 제외됨)
    knit_ctx = ("knit" in constr) or _cat_has_knit(clothing_type, sub_category)
    if not solids:  # 섬유 불명: knit 구조만 / elastane만 / 그 외
        if "knit" in constr:
            return _wrap(OVERRIDE_BLOCK["knit"], el_mod)
        if elastane:
            return _wrap("a stretch fabric.", _ELASTANE_FIBER)
        return _unknown()

    # 2) 폴리+레이온+스판 슈팅 (특수 3섬유)
    if {"polyester", "rayon"} <= {m["key"] for m in solids} and elastane:
        return _wrap(_SUITING, _knit_cue(knit_ctx))

    # 3) 알려진 combo — 섬유 상위 2(elastane 포함 → cotton-spandex 등 매칭).
    #    비-elastane combo는 2번째 섬유 ≥20%일 때만 — 저비율 2섬유(예: 면90/레이온10)가
    #    dominant를 덮지 않게(그런 건 dominant 블록으로). elastane combo는 저비율도 핏에 의미 → 유지.
    top2 = known[:2]  # known=섬유전용이라 construction이 pair를 오염시키지 않음
    if len(top2) == 2:
        pair = frozenset(m["key"] for m in top2)
        cov = sum(m["ratio"] for m in top2)
        is_combo = ("elastane" in pair and elastane) or (cov >= 85 and top2[1]["ratio"] >= 20)
        if pair in COMBO_BLOCK and is_combo:
            extra = el_mod if (elastane and "elastane" not in pair) else ""  # elastane이 3섬유째면 보강
            return _wrap(COMBO_BLOCK[pair], extra, _knit_cue(knit_ctx))

    # 4) 강한 dominant (섬유 ≥70%): dominant 블록 + elastane modifier
    top = solids[0]
    if top["ratio"] >= 70:
        return _wrap(FIBER_BLOCK.get(top["key"], ""), el_mod, _knit_cue(knit_ctx))

    # 5) 블렌드: 상위 섬유를 한 줄로 (dominant 리드) + elastane modifier
    lead = solids[:3]
    blend = (f"{', '.join(m['key'] for m in lead)} blend — let {lead[0]['key']} lead the surface; combine the "
             f"listed fibers into one consistent fabric (not separate patches), mostly following the dominant "
             f"fiber's texture and drape.")
    return _wrap(blend, el_mod, _knit_cue(knit_ctx))


def _wrap(*parts: str) -> str:
    body = " ".join(p.strip() for p in parts if p and p.strip())
    return f"- Material rendering guidance:\n  Render as {body}\n  {_GUARD}"
