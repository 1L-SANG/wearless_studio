"""Canonical mannequin Frame Lock observations; policy lives in services."""

import hashlib
import os
import time

from ..config import Settings
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

PROMPT_VERSION = "mannequin_frame_qc_v1"
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_frame_qc_v1.txt")

VIEW_FAMILIES = (
    "front", "three_quarter_left", "three_quarter_right",
    "profile_left", "profile_right", "back", "unknown",
)
BOOLEAN_FIELDS = (
    "orientationMatches", "cameraYawMatches", "framingMatches", "fullBodyVisible",
    "backgroundMatches", "lightingMatches", "shadowMatches",
)
OBSERVATION_FIELDS = ("canonicalViewFamily", "resultViewFamily", *BOOLEAN_FIELDS)
FORBIDDEN_FIELDS = (
    "decision", "verdict", "pass", "reject", "review", "approved", "score",
    "recommendation", "regenerationInstructions", "action",
)
_EVIDENCE_MAX = 4
_EVIDENCE_LEN = 120


def schema() -> dict:
    properties = {
        "canonicalViewFamily": {"type": "string", "enum": list(VIEW_FAMILIES)},
        "resultViewFamily": {"type": "string", "enum": list(VIEW_FAMILIES)},
        **{field: {"type": ["boolean", "null"]} for field in BOOLEAN_FIELDS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "uncertainFields": {
            "type": "array",
            "items": {"type": "string", "enum": list(OBSERVATION_FIELDS)},
        },
        "evidence": {
            "type": "array", "maxItems": _EVIDENCE_MAX,
            "items": {"type": "string", "maxLength": _EVIDENCE_LEN},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": list(properties),
    }


def build_prompt() -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as file:
        return file.read()


def template_sha256() -> str:
    with open(_PROMPT_FILE, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise VisionError("frame 관찰 응답이 객체가 아니에요.")
    lowered = {str(key).lower() for key in raw}
    leaked = [field for field in FORBIDDEN_FIELDS if field.lower() in lowered]
    if leaked:
        raise VisionError(f"frame 관찰 응답에 판정 필드가 포함됨: {sorted(leaked)}")
    allowed = set(OBSERVATION_FIELDS) | {"confidence", "uncertainFields", "evidence"}
    unknown = set(raw) - allowed
    if unknown:
        raise VisionError(f"frame 관찰 응답에 알 수 없는 필드: {sorted(unknown)}")

    out = {}
    for field in ("canonicalViewFamily", "resultViewFamily"):
        value = raw.get(field)
        if value not in VIEW_FAMILIES:
            raise VisionError(f"frame view family 오류: {field}")
        out[field] = value
    for field in BOOLEAN_FIELDS:
        if field not in raw:
            raise VisionError(f"frame 관찰 필드 누락: {field}")
        value = raw[field]
        if value is not None and not isinstance(value, bool):
            raise VisionError(f"frame 관찰 필드 타입 오류: {field}")
        out[field] = value

    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VisionError("frame confidence 가 숫자가 아니에요.")
    if not 0.0 <= float(confidence) <= 1.0:
        raise VisionError("frame confidence 가 0~1 범위를 벗어났어요.")
    out["confidence"] = float(confidence)

    uncertain = raw.get("uncertainFields")
    if not isinstance(uncertain, list) or any(
            not isinstance(value, str) or value not in OBSERVATION_FIELDS
            for value in uncertain):
        raise VisionError("frame uncertainFields 계약 위반")
    inferred = {field for field in BOOLEAN_FIELDS if out[field] is None}
    inferred |= {field for field in ("canonicalViewFamily", "resultViewFamily")
                 if out[field] == "unknown"}
    out["uncertainFields"] = sorted(set(uncertain) | inferred)

    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        raise VisionError("frame evidence 계약 위반")
    out["evidence"] = [item.strip()[:_EVIDENCE_LEN] for item in evidence
                       if item.strip()][:_EVIDENCE_MAX]
    return out


async def observe(settings: Settings, *, canonical: InlineImage,
                  candidate: InlineImage) -> tuple[dict, dict]:
    """Canonical first, candidate second. The model observes; it never decides."""
    prompt = build_prompt()
    started = time.perf_counter()
    raw, provider = await analyze_with_fallback(
        settings, prompt, [canonical, candidate], schema())
    observation = validate(raw)
    return observation, {
        "provider": provider,
        "promptVersion": PROMPT_VERSION,
        "templateSha256": template_sha256(),
        "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "imageCount": 2,
        "status": "ok",
    }


def failure_meta(exc: BaseException) -> dict:
    name = type(exc).__name__
    status = "timeout" if "Timeout" in name else (
        "provider_error" if isinstance(exc, VisionError) else "unexpected_error")
    return {
        "provider": None, "promptVersion": PROMPT_VERSION,
        "templateSha256": template_sha256(), "promptSha256": None,
        "latencyMs": None, "imageCount": 2, "status": status, "errorType": name,
    }
