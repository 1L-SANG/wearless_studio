"""조건부 SAM 폴백 — 베이스라인 우선, 니트 계열만, QC 가 재시도를 요구할 때만.

이 파일이 지키는 건 "폴백이 동작하는가"보다 **폴백이 함부로 발화하지 않는가**다:

  * 니트라도 베이스라인이 통과하면 두 번째 Gemini 호출은 없다 (비용 정책)
  * 블라우스는 캐노니컬이 있어도 절대 이 경로에 들어오지 않는다 (실측 회귀 사례)
  * 1차 라우팅은 그대로다 — 니트의 primary 는 여전히 RAW
  * 폴백이 폴백을 부르지 않는다
"""
import pathlib
import re

from app.services import generation_input_strategy as gis
from app.services import sam_fallback


def _refs(*slots):
    from app.agents.product_reference import ProductReference
    from app.agents.vision_llm import InlineImage
    return {s: ProductReference(slot=s, asset_id=f"a-{s}",
                                image=InlineImage("image/png", b"x")) for s in slots}


BOTH = _refs("CanonicalFront", "CanonicalBack")
KNIT = ({"name": "회색 니트", "clothing_type": "상의"}, {"subCategory": "knit"})


def _decide(product, analysis, *, retry=True, refs=BOTH, used=False):
    return sam_fallback.decide(product=product, analysis=analysis, qc_says_retry=retry,
                               canonical_refs=refs, already_used=used)


# ── 계열 판정 ────────────────────────────────────────────────────────────────

def test_knit_family_is_recognised_from_existing_metadata():
    for analysis in ({"subCategory": "knit"}, {"subCategory": "cardigan"},
                     {"customCategory": "터틀넥 니트"}, {"suggestedName": "베이직 스웨터"}):
        assert sam_fallback.is_knit_family({}, analysis), analysis
    assert sam_fallback.is_knit_family({"name": "울 니트"}, {})


def test_a_knit_blouse_is_still_a_blouse():
    """드레이프 의존 블라우스는 캐노니컬 증거로 회귀한 실측 사례다 — 니트라 불려도 제외."""
    assert not sam_fallback.is_knit_family({}, {"subCategory": "blouse"})
    assert not sam_fallback.is_knit_family({}, {"suggestedName": "니트 블라우스"})
    assert not sam_fallback.is_knit_family({"name": "블라우스"}, {"subCategory": "knit"})


def test_ordinary_tops_are_not_knit_family():
    for analysis in ({"subCategory": "shirt"}, {"subCategory": "tshirt"},
                     {"subCategory": "jeans"}, {}):
        assert not sam_fallback.is_knit_family({"clothing_type": "상의"}, analysis), analysis


# ── 발화 조건 ────────────────────────────────────────────────────────────────

def test_knit_with_failing_baseline_and_ready_canonical_triggers_once():
    v = _decide(*KNIT)
    assert v["augment"] and v["samFallbackTriggered"]
    assert v["samFallbackReason"] == "baseline_qc"
    assert v["canonicalFront"] == "READY" and v["canonicalBack"] == "READY"


def test_a_passing_baseline_never_triggers_a_second_generation():
    """비용 정책의 핵심 — 베이스라인이 통과하면 SAM Gemini 호출은 없다."""
    v = _decide(*KNIT, retry=False)
    assert not v["augment"]
    assert v["samFallbackReason"] == "baseline_accepted"
    assert v["samFallbackEligible"] is True      # 자격은 있으나 발화하지 않는다


def test_missing_canonical_does_not_trigger_and_does_not_wait():
    """PENDING·FAILED·MISSING 은 여기서 구분되지 않는다 — 로더가 READY 만 준다."""
    v = _decide(*KNIT, refs={})
    assert not v["augment"]
    assert v["samFallbackReason"] == "canonical_missing"
    assert v["canonicalFront"] == "MISSING" and v["canonicalBack"] == "MISSING"


def test_front_only_canonical_still_triggers():
    v = _decide(*KNIT, refs=_refs("CanonicalFront"))
    assert v["augment"]
    assert v["canonicalFront"] == "READY" and v["canonicalBack"] == "MISSING"


def test_back_only_canonical_still_triggers():
    v = _decide(*KNIT, refs=_refs("CanonicalBack"))
    assert v["augment"]
    assert v["canonicalBack"] == "READY" and v["canonicalFront"] == "MISSING"


def test_the_fallback_cannot_trigger_itself():
    v = _decide(*KNIT, used=True)
    assert not v["augment"]
    assert v["samFallbackReason"] == "already_used"


def test_blouse_with_failing_baseline_and_canonical_present_is_not_augmented():
    v = _decide({"clothing_type": "상의"}, {"subCategory": "blouse"})
    assert not v["augment"]
    assert v["samFallbackReason"] == "ineligible_family"


def test_a_plain_top_with_failing_baseline_is_not_augmented():
    v = _decide({"clothing_type": "상의"}, {"subCategory": "tshirt"})
    assert not v["augment"] and v["samFallbackReason"] == "ineligible_family"


def test_the_verdict_carries_every_observability_field():
    v = _decide(*KNIT)
    for key in ("samFallbackEligible", "canonicalFront", "canonicalBack",
                "samFallbackTriggered", "samFallbackReason"):
        assert key in v
    assert "token" not in str(v).lower() and "secret" not in str(v).lower()


# ── 1차 라우팅 불변 ──────────────────────────────────────────────────────────

def test_primary_routing_for_knit_is_still_raw():
    """폴백은 resolve() 밖에서 일어난다. 니트의 1차 전략은 그대로 RAW 여야 한다."""
    st = gis.resolve({"status": "approved"}, {"name": "회색 니트"}, canonical_available=True)
    assert st.strategy == gis.STRATEGY_RAW
    assert not st.use_canonical


def test_blouse_primary_routing_unchanged():
    st = gis.resolve(None, {"name": "셔링 블라우스"}, canonical_available=True)
    assert st.strategy == gis.STRATEGY_RAW and st.category == gis.CATEGORY_BLOUSE


def test_existing_shirt_augmented_behaviour_is_untouched():
    """셔츠의 기존 AUGMENTED 경로는 이 밀스톤과 무관하게 그대로 살아 있어야 한다."""
    st = gis.resolve(None, {"name": "옥스포드 셔츠"}, canonical_available=True)
    assert st.strategy == gis.STRATEGY_AUGMENTED and st.category == gis.CATEGORY_SHIRT
    without = gis.resolve(None, {"name": "옥스포드 셔츠"}, canonical_available=False)
    assert without.strategy == gis.STRATEGY_RAW      # canonical 없으면 여전히 RAW


def test_resolve_never_consults_the_new_fallback_module():
    src = pathlib.Path("app/services/generation_input_strategy.py").read_text(encoding="utf-8")
    assert "sam_fallback" not in src


# ── 오케스트레이션 배선 ──────────────────────────────────────────────────────

MJ = pathlib.Path("app/workers/mannequin_job.py").read_text(encoding="utf-8")


def _func_source(name: str) -> str:
    """이름으로 함수 본문만 잘라낸다 — 문자 오프셋 창은 리팩터링에 쉽게 깨진다."""
    import ast
    tree = ast.parse(MJ)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(MJ, node) or ""
    raise AssertionError(f"{name} not found in mannequin_job")


FALLBACK_BLOCK = _func_source("_maybe_augment_with_canonical")
RETRY_BRANCH = _func_source("_run_candidate")


def test_the_fallback_is_wired_into_the_existing_qc_retry_branch():
    """새 오케스트레이션을 만들지 않는다 — 기존 retry 분기 안에서 입력만 바꾼다."""
    assert 'final_decision(s, qc_scores) == "retry"' in RETRY_BRANCH
    assert "_build_retry_feedback" in RETRY_BRANCH
    assert "_maybe_augment_with_canonical" in RETRY_BRANCH
    assert "sam_fallback.decide" in FALLBACK_BLOCK


def test_the_fallback_consumes_no_extra_provider_budget():
    """추가 예약을 만들지 않는다 — 이미 승인된 재생성의 입력을 바꿀 뿐이다."""
    assert "_require_image_slot" not in FALLBACK_BLOCK
    assert "REQUEST_GENERATION" not in FALLBACK_BLOCK


def test_the_fallback_is_guarded_against_recursion():
    assert "sam_fallback_used = False" in RETRY_BRANCH      # 후보 시작 시 리셋
    assert "already_used" in FALLBACK_BLOCK                 # 헬퍼가 직접 막고
    assert "already_used=sam_fallback_used" in RETRY_BRANCH  # 호출부가 상태를 넘긴다


def test_the_fallback_never_calls_the_sam_service_at_generation_time():
    """생성 시점에는 저장된 캐노니컬만 읽는다. SAM 추론은 sam_preprocess 소유다."""
    for forbidden in ("sam_client", "segment_garment"):
        assert forbidden not in MJ


def test_raw_references_are_extended_not_replaced():
    code = _code_only(FALLBACK_BLOCK)
    assert "(*images, *extra)" in code           # RAW 뒤에 덧붙이기만
    assert "extra.append(" in code
    assert "images = [" not in code              # 목록을 새로 만들지 않는다
    assert "images.clear()" not in code


def test_the_fallback_skips_the_edit_path():
    """편집 경로는 image 1 계약이 따로 있다 — 캐노니컬을 끼워 넣으면 그 계약이 깨진다."""
    assert 'generation_path == "edit"' in FALLBACK_BLOCK
    assert "return None" in FALLBACK_BLOCK


def _code_only(text: str) -> str:
    """주석을 뺀 코드만. 산문에 등장하는 단어로 불변식을 판정하면 안 된다."""
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def test_the_fallback_rerenders_the_same_approved_prompt():
    """GARMENT-BODY CONTACT·니트 가이드는 템플릿에서 온다 — 다시 렌더할 뿐 바꾸지 않는다.

    폴백이 프롬프트 문구를 직접 조립하면 승인된 템플릿과 두 벌이 된다. 그래서 문자열로
    프롬프트를 덧붙이지 않는지, 같은 렌더러를 다시 부르는지를 코드에서 확인한다.
    """
    code = _code_only(FALLBACK_BLOCK)
    assert "render_mannequin_prompt(" in code
    assert "template, ctx, product, analysis" in code
    for invented in ("SILHOUETTE AUTHORITY", "DETAIL EVIDENCE ONLY", "base_prompt +"):
        assert invented not in code


# ── 프롬프트/QC 불변 ─────────────────────────────────────────────────────────

def test_contact_block_and_knit_guidance_are_unchanged():
    tpl = pathlib.Path("prompts/mannequin_generate_v1.txt").read_text(encoding="utf-8")
    assert "GARMENT-BODY CONTACT (mandatory)" in tpl
    assert "WHILE PRESERVING THE PRODUCT'S TRUE FIT" in tpl
    from app.agents.materials import material_guidance
    g = material_guidance([{"name": "아크릴", "ratio": 100}], "니트", "knit")
    assert "Knit yields to the body underneath" in g
    assert "keeping the product's own fit and ease exactly as they are" in g


def test_canonical_authority_wording_is_unchanged():
    for line in (gis.CANONICAL_MANIFEST_LINE, gis.CANONICAL_BACK_MANIFEST_LINE):
        assert "PROPORTION AND CONSTRUCTION EVIDENCE, NEVER A SILHOUETTE TEMPLATE" in line
        assert "do NOT trace that contour literally as the final worn silhouette" in line
        assert "SILHOUETTE AUTHORITY" not in line
        assert "DETAIL EVIDENCE ONLY" not in line


def test_qc_configuration_was_not_redefined():
    """폴백은 QC 결과를 소비할 뿐 재정의하지 않는다."""
    # 모듈 독스트링은 "임계값을 만들지 않는다"고 설명하려고 그 단어를 쓴다. 그러니 산문이
    # 아니라 코드를 본다: QC 를 임포트하지도, 숫자 임계값을 두지도 않는 것이 불변식이다.
    import ast
    src = pathlib.Path("app/services/sam_fallback.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("qc" in m or "fidelity" in m for m in imported), imported
    numbers = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert not numbers, f"fallback must not carry numeric thresholds: {numbers}"


# ── 입력 구성(그레이 니트 픽스처 시나리오) ──────────────────────────────────

def _slots_after_fallback(raw_slots, canonical_slots):
    """배선이 만드는 슬롯 순서를 그대로 재현: RAW 먼저, 캐노니컬은 뒤에 추가."""
    from app.workers.mannequin_job import _CANONICAL_SLOTS
    out = list(raw_slots)
    for slot in _CANONICAL_SLOTS:
        if slot in canonical_slots:
            out.append(slot)
    return out


def test_grey_knit_without_detail_gets_raw_plus_both_canonical():
    """실제 그레이 니트 픽스처: Detail 없음, Front/Back 캐노니컬 준비됨."""
    assert _slots_after_fallback(["Front", "Back"], BOTH) == [
        "Front", "Back", "CanonicalFront", "CanonicalBack"]


def test_a_knit_with_detail_keeps_detail_in_the_fallback():
    assert _slots_after_fallback(["Front", "Back", "Detail"], BOTH) == [
        "Front", "Back", "Detail", "CanonicalFront", "CanonicalBack"]


def test_raw_always_precedes_canonical():
    slots = _slots_after_fallback(["Front", "Back", "Detail"], BOTH)
    assert slots.index("Front") < slots.index("CanonicalFront")
    assert slots.index("Detail") < slots.index("CanonicalFront")
    assert slots.index("CanonicalFront") < slots.index("CanonicalBack")


def test_the_fallback_never_produces_a_canonical_only_input():
    for canonical in (BOTH, _refs("CanonicalFront"), _refs("CanonicalBack")):
        slots = _slots_after_fallback(["Front", "Back"], canonical)
        assert slots[:2] == ["Front", "Back"], "RAW must never be dropped"


def test_no_canonical_detail_slot_exists():
    from app.workers.mannequin_job import _CANONICAL_SLOTS, _SLOT_LABEL
    assert _CANONICAL_SLOTS == ("CanonicalFront", "CanonicalBack")
    assert not any("CanonicalDetail" in k for k in _SLOT_LABEL)
