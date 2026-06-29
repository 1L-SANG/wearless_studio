"""소재 인식 → 렌더링 가이드 블록. 로직·구조 정본=documents/material_prompt_blocks.md ·
블록 본문은 2026-06-29 전수 감사로 개정(근거 documents/material_audit_findings.md)이라 이 파일이 텍스트 정본. 순수 함수, IO 없음.

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
    "cotton": "matte, natural cotton with a fine woven/jersey grain and soft irregular wrinkles at bends; render the surface, sheen and drape the photo shows.",
    "polyester": "smooth, uniform synthetic with clean, controlled folds that resist wrinkling; match the photo's sheen, from matte to bright satin.",
    "nylon": "lightweight nylon with a crisp, papery technical hand and springy shape recovery; take its sheen and drape from the photo.",
    "rayon": "soft cellulosic with a fluid drape that hangs close in rounded folds and a cool, smooth hand; match the photo's sheen and finish, matte challis to glossy satin.",
    "linen": "dry, breathable linen with a slubby texture and relaxed, lived-in creases; follow the photo for sheen and crispness.",
    "wool": "warm wool with natural body, soft rounded folds and a faint fuzzy halo; take sheen, weight and crease sharpness from the photo.",
    "cashmere": "soft, fine cashmere with a gentle surface halo, low natural sheen and a soft fluid drape; follow the photo for fuzz and structure.",
    "acrylic": "soft, lofty wool-like synthetic with a springy hand and rounded folds, slightly plush and usually matte; take surface and sheen from the photo.",
    "silk": "fine silk with a fluid drape that follows the figure and a natural luster; match the photo's weave — bright satin/charmeuse where shown, soft-matte for crepe/twill.",
    "acetate": "smooth, light fabric with a soft satin-like sheen and graceful drape; let the photo set the gloss and how crisp or flowing it is.",
}
# elastane은 표면 아닌 modifier (§4 마지막)
_ELASTANE_FIBER = ("elastane sets fit, not surface: it sits close to the body and recovers smoothly with gentle "
                   "tension lines at stress points; take all sheen and texture from the photo.")

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
    frozenset(["cotton", "polyester"]): "a cotton base smoothed and made wrinkle-resistant by polyester, with relaxed folds; mostly matte, but match the photo's finish, drape and stretch.",
    frozenset(["cotton", "elastane"]): "soft, mostly matte cotton that sits closer to the body with gentle stretch and pull lines; take sheen, weave and weight from the photo.",
    frozenset(["polyester", "elastane"]): "a smooth synthetic stretch fabric that conforms to the body with springy recovery; match the photo's surface and sheen, matte/brushed to wet-look.",
    frozenset(["nylon", "elastane"]): "a sleek technical-activewear stretch fabric that fits close and recovers springily; take its sheen, sporty-matte to glossy, from the photo.",
    frozenset(["rayon", "polyester"]): "rayon-like fluid drape with polyester stability and long, clean folds; match the photo's sheen, body and texture.",
    frozenset(["linen", "cotton"]): "a breathable blend with subtle slubs and relaxed creasing, crisper than rayon; mostly soft-matte, but follow the photo for sheen and drape.",
    frozenset(["linen", "rayon"]): "linen slub softened by rayon's fluid drape, breathable with soft wrinkles; match the photo's sheen and crispness.",
    frozenset(["rayon", "cotton"]): "smooth-handed with a slightly more fluid, body-skimming drape and softer wrinkles than pure cotton; follow the photo for sheen and weave.",
    frozenset(["wool", "nylon"]): "warm, soft wool with a faint halo, a cleaner surface and sharp shape recovery; take thickness, fold character and sheen from the photo.",
    frozenset(["wool", "polyester"]): "warm, soft wool with body and rounded, structured folds; match the photo's sheen, weight and any stretch.",
    frozenset(["wool", "cashmere"]): "a fine, soft cashmere-wool halo with gentle drape and a softly matte surface; follow the photo for finish, sheen and structure.",
}
# 3섬유 슈팅 블렌드(폴리+레이온+스판) — 별도 매칭(§5)
_SUITING = ("smooth, opaque suiting with a soft rayon-led drape, polyester shape retention and slight give; "
            "usually matte to lightly lustrous — match the photo's sheen and weave.")

# ── §6 / §6.5 직조·마감 override 블록 ────────────────────────────────────────
OVERRIDE_BLOCK = {
    "denim": "sturdy cotton twill with a diagonal grain and firm, structured folds holding crease lines at knees/hips/hems; match the photo's wash and surface.",
    "leather": "opaque leather with highlights that follow the folds and seams; take its sheen, from matte suede to glossy patent, and its body from the photo.",
    "brushed": "soft napped pile adding cozy loft and softened folds, usually matte; follow the photo for where the nap shows and for weight and sheen.",
    "knit": "let the visible gauge and stitch — jersey, rib, cable, waffle — drive the texture, and render only the stitch the photo shows, with a soft body-following drape.",
    "seersucker": "lightweight puckered fabric with alternating crinkled and flat stripes that lift off the body; keep the puckered stripe texture and take sheen from the photo.",
    "chiffon": "very light, airy sheer that floats and ripples in soft folds and diffuses light; follow the photo for sheen and opacity, keeping any lining and coverage it shows.",
    "gauze": "soft, open-woven fabric with an airy crinkled texture and relaxed drape, usually matte; follow the photo for sheen and opacity.",
    "mesh": "a visible regular open structure — mesh holes or eyelets — kept open with an even pattern; follow the photo for sheen, lining and how sheer it reads.",
    "summerknit": "open-gauge knit with airy gaps between stitches and a light drape, often semi-sheer; follow the photo for opacity, yarn bulk and stitch.",
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
