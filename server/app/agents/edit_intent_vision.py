"""Edit Intent 의미 관찰 — baseline 과 편집 결과의 **차이만** 구조화해 돌려준다.

이 모듈은 판정하지 않는다. `decision`·`pass`·`reject`·`review` 같은 필드는 스키마가
**거부**한다(무시가 아니라 거부다 — 무시하면 다음 사람이 그 필드를 쓰기 시작한다).
최종 판정은 services.edit_intent_qc.decide 가 정량 측정과 함께 만든다.

관찰 불가는 false 가 아니라 **null** 이다. 이 구분이 이 모듈의 존재 이유다: false 는
"확인했고 안 바뀌었다"는 뜻이라 판정기가 통과 근거로 쓴다. 가려서 못 본 것을 false 로
적으면 잠긴 항목이 바뀌었는데도 통과한다.
"""

import os
import time

from ..config import Settings
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

PROMPT_VERSION = "edit_intent_qc_v1"
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "edit_intent_qc_v1.txt")

# 관찰 항목 — Decision Engine 이 아는 이름과 1:1 이다. 여기 없는 필드는 스키마가 거부한다.
OBSERVATION_FIELDS = (
    "requestedChangeApplied",
    "collarChanged", "sleevesChanged", "buttonsChanged", "pocketsChanged",
    "patternChanged", "logoChanged",
    "poseChanged", "cameraChanged", "framingChanged",
    "backgroundChanged", "lightingChanged", "mannequinIdentityChanged",
)

# 응답에 있으면 **거부**하는 이름. 판정을 모델에게서 받아들이는 경로를 만들지 않는다.
FORBIDDEN_FIELDS = ("decision", "verdict", "pass", "reject", "review", "approved",
                    "score", "recommendation", "regenerationInstructions", "action")

_EVIDENCE_MAX = 4
_EVIDENCE_LEN = 100


def schema() -> dict:
    """strict-호환 관찰 스키마. bool|null 만 받는다 — "모르겠다"가 1급 값이다."""
    props: dict = {f: {"type": ["boolean", "null"]} for f in OBSERVATION_FIELDS}
    # 제약을 스키마에도 적는다. _to_gemini_schema 가 minimum/maximum 같은 키를 변환에서
    # 버리므로(image_qc 와 같은 관례) 이건 GPT 쪽 강제이자 문서일 뿐 — **최종 방어선은
    # validate() 다**. 스키마만 믿으면 provider 를 바꾸는 순간 조용히 뚫린다.
    props["confidence"] = {"type": "number", "minimum": 0, "maximum": 1}
    props["uncertainFields"] = {"type": "array",
                                "items": {"type": "string",
                                          "enum": list(OBSERVATION_FIELDS)}}
    props["evidence"] = {"type": "array", "maxItems": _EVIDENCE_MAX,
                         "items": {"type": "string", "maxLength": _EVIDENCE_LEN}}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": [*OBSERVATION_FIELDS, "confidence", "uncertainFields", "evidence"],
    }


def build_prompt(*, edit_type: str, adjustments: dict, allowed_scope: dict,
                 source_ref_count: int = 0) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        text = f.read()
    changes = [f"{k} {v:+d} step" for k, v in sorted((adjustments or {}).items()) if v]
    source_note = (
        f"IMAGE 3..{2 + source_ref_count} = PRODUCT SOURCE PHOTOS — supporting evidence for"
        " what the garment actually looks like."
        if source_ref_count else "")
    reference_note = (
        "The product photos are reference only. Never compare IMAGE 2 against them for"
        " composition, pose, framing or background — those questions are about IMAGE 1"
        " versus IMAGE 2 only."
        if source_ref_count else "")
    return (text
            .replace("${editType}", edit_type)
            .replace("${requestedChange}", ", ".join(changes) or "(see edit type)")
            .replace("${allowedScope}",
                     ", ".join(allowed_scope.get("allowed") or ()) or "(nothing)")
            .replace("${forbiddenScope}",
                     ", ".join(allowed_scope.get("forbidden") or ()) or "(nothing)")
            .replace("${sourceNote}", source_note)
            .replace("${referenceNote}", reference_note))


def validate(raw: dict) -> dict:
    """정규화 — Decision Engine 입력 스키마 하나로 고정. 위반은 VisionError.

    관대하게 고쳐 쓰지 않는다: 타입이 어긋난 관찰은 "모르겠다"보다 나쁘다(모델이 무엇을
    봤는지 알 수 없는데 판정기는 값을 받는다). null 로 눕히지 않고 거부한다.
    """
    if not isinstance(raw, dict):
        raise VisionError("edit intent 관찰 응답이 객체가 아니에요.")
    leaked = [k for k in raw if k.lower() in {f.lower() for f in FORBIDDEN_FIELDS}]
    if leaked:
        raise VisionError(f"관찰 응답에 판정 필드가 포함됨: {sorted(leaked)}")
    unknown = set(raw) - set(OBSERVATION_FIELDS) - {"confidence", "uncertainFields",
                                                    "evidence"}
    if unknown:
        raise VisionError(f"관찰 응답에 알 수 없는 필드: {sorted(unknown)}")
    out: dict = {}
    for f in OBSERVATION_FIELDS:
        if f not in raw:
            raise VisionError(f"관찰 필드 누락: {f}")
        v = raw[f]
        if v is not None and not isinstance(v, bool):
            raise VisionError(f"관찰 필드 타입 오류: {f}")
        out[f] = v
    conf = raw.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise VisionError("confidence 가 숫자가 아니에요.")
    # 범위 밖은 **클램프하지 않는다**. 1.7 을 1.0 으로 접으면 "매우 확신"이라는 잘못된
    # 신호가 되고, 계약을 못 지킨 응답을 통과시킨 사실도 사라진다.
    if not 0.0 <= float(conf) <= 1.0:
        raise VisionError("confidence 가 0~1 범위를 벗어났어요.")
    out["confidence"] = float(conf)
    uncertain = raw.get("uncertainFields")
    if not isinstance(uncertain, list):
        raise VisionError("uncertainFields 가 배열이 아니에요.")
    unknown_uncertain = [u for u in uncertain
                         if not isinstance(u, str) or u not in OBSERVATION_FIELDS]
    if unknown_uncertain:
        # 조용히 버리면 "모델이 무엇을 모른다고 했는지"가 사라진다. 필드명을 못 맞춘
        # 응답은 관찰 자체를 신뢰할 수 없다는 신호다.
        raise VisionError(f"uncertainFields 에 알 수 없는 값: {unknown_uncertain[:3]}")
    out["uncertainFields"] = sorted(set(uncertain))
    # 모델이 빠뜨려도 null 인 항목은 불확실 목록에 넣는다 — 두 신호가 갈라지면 안 된다.
    out["uncertainFields"] = sorted(
        set(out["uncertainFields"]) | {f for f in OBSERVATION_FIELDS if out[f] is None})
    evidence = raw.get("evidence")
    if not isinstance(evidence, list):
        raise VisionError("evidence 가 배열이 아니에요.")
    if any(not isinstance(e, str) for e in evidence):
        raise VisionError("evidence 에 문자열이 아닌 값이 있어요.")
    # 개수·길이는 **bounded normalize** 다(거부 아님) — 설명이 길거나 많은 것은 계약 위반이
    # 아니라 수다스러움이고, 그것 때문에 관찰 전체를 버릴 이유가 없다. 잘린다는 사실은
    # 여기 문서와 테스트에 고정돼 있다.
    out["evidence"] = [e.strip()[:_EVIDENCE_LEN] for e in evidence
                       if e.strip()][:_EVIDENCE_MAX]
    return out


async def observe(
    settings: Settings, *, baseline: InlineImage, edited: InlineImage,
    edit_type: str, adjustments: dict, allowed_scope: dict,
    source_refs: list[InlineImage] | None = None,
) -> tuple[dict, dict]:
    """→ (정규화 관찰, 계측 메타). 실패는 VisionError — 호출자가 review 로 처리한다.

    이미지 순서는 **고정**이다: baseline(1), edited(2), 그 뒤에 상품 원본. 프롬프트가 이
    번호로 질문하므로 순서가 흔들리면 관찰 자체가 다른 질문에 답한 것이 된다.
    """
    refs = list(source_refs or ())
    images = [baseline, edited, *refs]
    prompt = build_prompt(edit_type=edit_type, adjustments=adjustments,
                          allowed_scope=allowed_scope, source_ref_count=len(refs))
    t0 = time.perf_counter()
    raw, provider = await analyze_with_fallback(settings, prompt, images, schema())
    observation = validate(raw)
    meta = {
        "provider": provider,
        "promptVersion": PROMPT_VERSION,
        "latencyMs": int((time.perf_counter() - t0) * 1000),
        "imageCount": len(images),
        "status": "ok",
    }
    return observation, meta


def failure_meta(exc: BaseException) -> dict:
    """실패 계측 — **원문을 남기지 않는다**. provider 응답에는 URL·본문이 들어 있다."""
    name = type(exc).__name__
    category = "timeout" if "Timeout" in name or "timeout" in str(exc).lower() else (
        "provider_error" if isinstance(exc, VisionError) else "unexpected_error")
    return {"provider": None, "promptVersion": PROMPT_VERSION, "latencyMs": None,
            "status": category, "errorType": name}
