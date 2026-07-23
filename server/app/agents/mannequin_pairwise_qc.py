"""T2 파일럿 — 조정 축의 **인과 검증용 pairwise 심판**.

두 마네킹 컷(LEFT/RIGHT)을 동시에 넣고 "어느 쪽이 더 <중립 비교속성>인가"만 묻는다.
기대 방향은 프롬프트에 **숨기고**(judge 편향 방지), 채점은 외부(`expected_more_side`)에서 매핑한다.
axis_qc(절대 적합성)와 달리 이건 **변화 방향**을 잰다 → treatment vs control 로 인과효과 계산(P2).

provider **고정**(Gemini 단독, vision_llm._call_gemini 직접 호출) — 기본 폴백 순서(config
analysis_model_order)를 따르면 실행 간 평가자가 달라질 수 있어 명시 고정한다.

순수 함수(_ORDINAL·expected_more_side·score_pair·build_prompt·schema)는 DB·네트워크 없음."""

from .fit_axes import AXIS_OBSERVABLES  # noqa: F401  (문서적 — 어휘 정본)
from .gemini_image import InlineImage
from .vision_llm import VisionError, _call_gemini

# ── 축별 중립 비교속성 (기대 방향 미노출) ─────────────────────────────────────
_COMPARATIVE = {
    "length": "the garment hem sits LOWER on the body (i.e. it is longer)",
    "fit": "the garment is LOOSER / more oversized, with more ease around the body",
    "cut": "the legs/hem are WIDER (more volume around the legs)",
    "silhouette": "the outline FLARES OUT more toward the hem",
}

# ── (category, axis) → 값의 비교속성 오름차순 서열 (파일럿 근사) ──────────────
# 주의: cut/silhouette 은 단일 1차원이 아니라 근사 순위다(파일럿 한정). 극단쌍부터 검증.
_ORDINAL = {
    ("top", "length"): {"ultra_crop": 0, "crop": 1, "basic": 2, "long": 3},
    ("outer", "length"): {"crop_short": 0, "basic": 1, "long": 2},
    ("pants", "length"): {"above_ankle": 0, "ankle": 1, "below_ankle": 2},
    ("skirt", "length"): {"mini": 0, "midi": 1, "long": 2},
    ("dress", "length"): {"mini": 0, "midi": 1, "long": 2},
    ("top", "fit"): {"tight": 0, "slim": 1, "regular": 2, "semi_over": 3, "over": 4},
    ("outer", "fit"): {"slim": 0, "regular": 1, "semi_over": 2, "over": 3},
    ("pants", "cut"): {  # '다리/밑단 폭' 근사
        "skinny": 0, "slim": 1, "tapered": 2, "straight": 2,
        "relaxed": 3, "semi_wide": 4, "bootcut": 4, "wide": 5},
    ("skirt", "silhouette"): {"h_line": 0, "a_line": 1, "mermaid": 2},
    ("dress", "silhouette"): {"h_line": 0, "a_line": 1, "fit_and_flare": 1, "mermaid": 2},
}

_ABSTAIN = {"similar", "unclear"}


def comparative(axis: str) -> str | None:
    return _COMPARATIVE.get(axis)


def expected_more_side(category: str, axis: str, value_left, value_right) -> str | None:
    """LEFT/RIGHT 축값 → 기대 'more' 방향. 'left'|'right'|'equal', 비교불가/미지값이면 None.
    순수 함수 — judge 출력 채점의 정답 매핑(외부, 프롬프트 미노출)."""
    order = _ORDINAL.get((category, axis))
    if not order:
        return None
    a, b = order.get(value_left), order.get(value_right)
    if a is None or b is None:
        return None
    if a == b:
        return "equal"
    return "left" if a > b else "right"


def build_prompt(axis: str) -> str:
    """LEFT/RIGHT 비교 프롬프트 — 기대 방향 미노출. 의류 종류·색·정체성은 무시하고 축만 본다."""
    comp = _COMPARATIVE.get(axis)
    if not comp:
        raise ValueError(f"pairwise 미지원 축: {axis}")
    return (
        "You are comparing two e-commerce studio photos of the SAME garment on the SAME mannequin, "
        "shown side by side. The FIRST image is LEFT, the SECOND image is RIGHT.\n"
        f"Question: on which side is it clearer that {comp}?\n"
        "Judge ONLY this single property. Ignore color, print, logo, fabric, pose, background, and "
        "every other difference. Do NOT assume either side is a target or 'correct' version.\n"
        'Answer moreSide = "left" or "right". If the two sides look the same on this property, answer '
        '"similar". If you genuinely cannot tell (occluded, ambiguous), answer "unclear". '
        "Give a one-sentence visual reason citing a landmark (hem line, leg width, shoulder seam, flare)."
    )


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "moreSide": {"type": "string", "enum": ["left", "right", "similar", "unclear"]},
            "reason": {"type": "string"},
        },
        "required": ["moreSide", "reason"],
    }


def validate(raw: dict) -> dict:
    side = raw.get("moreSide")
    if side not in ("left", "right", "similar", "unclear"):
        raise VisionError(f"pairwise: 잘못된 moreSide={side!r}")
    return {"moreSide": side, "reason": str(raw.get("reason") or "")[:300]}


def score_pair(verdict: dict, category: str, axis: str, value_left, value_right) -> dict:
    """judge 출력 + 외부 정답 매핑 → directional 채점. abstain(similar/unclear)은 pass/fail 아님.
    directionalPass: True/False, 또는 None(abstain). expected='equal'이면 'similar'가 정답."""
    observed = verdict.get("moreSide")
    expected = expected_more_side(category, axis, value_left, value_right)
    if observed in _ABSTAIN:
        # equal 기대인데 similar 면 정답, 그 외 abstain 은 미채점
        if expected == "equal" and observed == "similar":
            return {"directionalPass": True, "expected": expected, "observed": observed, "abstain": False}
        return {"directionalPass": None, "expected": expected, "observed": observed, "abstain": True}
    if expected in ("left", "right"):
        return {"directionalPass": observed == expected, "expected": expected,
                "observed": observed, "abstain": False}
    # expected == 'equal' 인데 방향 답 → 오답. None(비교불가)이면 미채점.
    if expected == "equal":
        return {"directionalPass": False, "expected": expected, "observed": observed, "abstain": False}
    return {"directionalPass": None, "expected": expected, "observed": observed, "abstain": True}


async def judge(
    settings, left: bytes, right: bytes, axis: str, *,
    model: str | None = None, mime: str = "image/png", timeout: float = 60.0,
) -> dict:
    """LEFT/RIGHT 두 컷을 Gemini(고정)로 pairwise 판정 → validate 된 verdict.
    provider 고정: analysis_model_order 무시하고 Gemini 만 호출(평가자 재현성)."""
    prompt = build_prompt(axis)
    images = [InlineImage(mime, left), InlineImage(mime, right)]
    model = model or settings.model_text_gemini
    raw = await _call_gemini(settings, model, prompt, images, schema(), timeout, thinking_level="low")
    return validate(raw)
