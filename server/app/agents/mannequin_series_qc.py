"""D축 시리즈 일관성 QC — 새 컷이 같은 프로젝트의 기존 컷들과 한 세트로 보이는가.

QC 4축 중 유일하게 없던 축이고, 사용자 최우선 요구("마네킹컷은 일정하게 유지")의 정확한
지점이다. A축(의류 재현)·B축(형태)·C축(품질)은 컷 하나만 보고 판정할 수 있지만, 일관성은
**비교 대상이 있어야만** 존재하는 속성이라 별도 에이전트로 둔다.

무엇을 재는가: 단일 candidate 체제(2026-07-13 전환)에서 같은 프로젝트의 기존 컷은 사실상
**재생성 버전 이력**이다. 따라서 여기서 재는 것은 *버전 간 일관성*이지 상품 간 세트
일관성이 아니다. 후자가 필요해지면 프로젝트 경계를 넘는 조회가 따로 필요하다.

설계 규율 둘:
- **provider 고정**(Gemini 단독). `analyze_with_fallback` 은 실행마다 평가자가 바뀔 수 있어
  점수 캘리브레이션이 무의미해진다(mannequin_pairwise_qc 와 같은 이유).
- **blinded**. "기존 컷에 맞춰야 한다"는 기대를 프롬프트에 노출하지 않는다. 어느 쪽이 새
  컷인지도 말하지 않는다 — 알려주면 새 컷을 관대하게 보거나 반대로 트집잡는다.
"""

from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, _call_gemini

# 비교 대상 상한. `list_mannequin_cuts` 는 프로젝트의 **전 버전을 무제한** 반환하고
# 재생성마다 version 이 누적되므로, cap 이 없으면 재생성이 잦은 프로젝트에서 이미지 수와
# 비용이 무한히 늘어난다.
MAX_REFERENCE_CUTS = 3

# 판정 항목 — 사용자가 "제각각"이라고 느끼는 실제 축들.
_ASPECTS = (
    "mannequin scale and framing (how much of the frame the mannequin fills, where it sits)",
    "camera angle and eye level",
    "background color and brightness",
    "garment placement and margins around the subject",
    "overall color cast and white balance",
    "lighting direction and shadow treatment",
)


def select_reference_cuts(cuts: list[dict], *, limit: int = MAX_REFERENCE_CUTS) -> list[dict]:
    """비교 기준으로 쓸 기존 컷 선택 — candidate 별 **최신 버전만**, 최신순 limit 개.

    전 버전을 그대로 넘기면 셀러가 이미 갈아치운 구버전에 새 컷을 맞추게 된다(나쁜 컷에
    앵커링). 최신 버전이 "사실상 확정본"이라는 것이 현재 스키마에서 가능한 최선의 근사다 —
    확정/승인 플래그가 생기면 그걸로 교체할 것.
    """
    latest: dict[str, dict] = {}
    for c in cuts or []:
        cand = c.get("candidate")
        version = c.get("version") or 0
        if cand is None:
            continue
        if cand not in latest or version > (latest[cand].get("version") or 0):
            latest[cand] = c
    ordered = sorted(latest.values(), key=lambda c: (-(c.get("version") or 0), c.get("candidate")))
    return ordered[:limit]


def build_prompt(reference_count: int) -> str:
    aspects = "\n".join(f"- {a}" for a in _ASPECTS)
    return (
        f"You are given {reference_count + 1} e-commerce studio photos of mannequins from one "
        "seller's shop. They show different garments; the garments are NOT what you are judging.\n\n"
        "Question: do these photos look like they were shot in one session with a fixed setup, "
        "or do they look assembled from different shoots?\n\n"
        "Judge only how the photos are made:\n"
        f"{aspects}\n\n"
        "Return consistency as an integer from 0 to 100. 100 means a viewer scrolling a product "
        "page would never notice a setup change between them. 50 means one or two photos visibly "
        "break the pattern. 0 means every photo looks like a different studio.\n"
        "In inconsistencies, name each concrete difference you actually see, one short phrase each "
        "(for example \"third photo has a warmer background\"). Return an empty list when the set "
        "is uniform. Do not comment on the garments themselves, the poses of the clothing, or "
        "which photo you think is correct."
    )


def schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "consistency": {"type": "integer"},
            "inconsistencies": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["consistency", "inconsistencies"],
    }


def validate(raw: dict) -> dict:
    """0-100 정수 클램핑 + 사유 정리. 범위는 스키마로 못 건다(_to_gemini_schema 가
    minimum/maximum 을 변환에서 버린다) — 여기서 강제한다."""
    raw = raw or {}
    value = raw.get("consistency")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VisionError(f"series_qc: 잘못된 consistency={value!r}")
    reasons = [r for r in (clean_text(x, 200) for x in (raw.get("inconsistencies") or [])) if r]
    return {"consistency": max(0, min(100, int(value))), "inconsistencies": reasons}


async def judge(
    settings, generated: InlineImage, references: list[InlineImage], *,
    model: str | None = None, timeout: float = 60.0,
) -> dict | None:
    """새 컷 + 기존 컷들의 일관성 판정. 기존 컷이 없으면 None(판정 스킵 — 비교 대상 부재).

    이미지 순서는 references 먼저, 새 컷이 마지막이다. 어느 것이 새 컷인지 프롬프트로
    알려주지 않는다(blinded) — 판정을 "세트로 보이는가" 하나로 묶어두기 위해서다.
    """
    if not references:
        return None
    prompt = build_prompt(len(references))
    model = model or settings.model_text_gemini
    raw = await _call_gemini(
        settings, model, prompt, [*references, generated], schema(), timeout,
        thinking_level="low")
    return validate(raw)
