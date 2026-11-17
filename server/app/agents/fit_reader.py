"""상품 사진의 핏 길이 읽기. 기존 분석 요청과 별개인 Gemini 부가 호출이다."""

import logging
from pathlib import Path
import time

from .vision_llm import analyze_with_fallback


log = logging.getLogger("wearless.fit_reader")
_PROMPT = Path(__file__).resolve().parents[2] / "prompts/fit_reader_v1.txt"
_PHOTO_TYPES = ("flat", "hanger", "worn")
_SLEEVE_TYPES = ("sleeveless", "short", "long")
_VALUES = {
    "topLength": ("crop", "slight_crop", "semi_crop", "basic", "semi_long"),
    "hemLength": ("mini", "above_knee", "knee", "midi", "long", "floor"),
    "pantsLength": ("above_ankle", "ankle", "ankle_cover", "below_ankle"),
    "sleeve": ("cap", "cap_short", "short", "above_elbow", "elbow", "above_wrist", "wrist", "back_of_hand", "fingers"),
    "frontHem": ("straight", "curved", "back_longer"),
}
_SLEEVE_GROUPS = {"short": _VALUES["sleeve"][:5], "long": _VALUES["sleeve"][5:]}
_SLOT_LABELS = {
    "Front": "front view of the whole product",
    "Back": "back view of the whole product",
    "Detail": "front-side close-up, usually only part of the product",
    "BackDetail": "back-side close-up, usually only part of the product",
}


def enabled(settings):
    return getattr(settings, "fit_reader_mode", "on") == "on"


def model_name(settings):
    return (getattr(settings, "model_text_gemini_fit_reader", "")
            or settings.model_text_gemini_analysis or settings.model_text_gemini)


def build_prompt(slots=None):
    manifest = ""
    if slots:
        lines = ["IMAGE MANIFEST (attached in this exact order):"]
        lines.extend(f"{index}. {_SLOT_LABELS.get(slot, 'product view') if isinstance(slot, str) else 'product view'}"
                     for index, slot in enumerate(slots, 1))
        manifest = "\n".join(lines)
    return _PROMPT.read_text().replace("${imageManifest}", manifest)


def _object_schema(properties):
    return {"type": "object", "additionalProperties": False, "x-propertyOrdering": True,
            "properties": properties, "required": list(properties)}


def response_schema():
    properties = {"photoType": {"type": "string", "enum": list(_PHOTO_TYPES)},
                  "sleeveType": {"type": ["string", "null"], "enum": list(_SLEEVE_TYPES)}}
    for name, values in _VALUES.items():
        properties[name] = _object_schema({
            "value": {"type": ["string", "null"], "enum": list(values)},
            "confident": {"type": "boolean"},
        })
    return _object_schema(properties)


def normalize_fit_read(raw):
    """허용 토큰과 사진·소매의 일관성을 검증한다. 입력은 바꾸지 않는다."""
    if not isinstance(raw, dict) or not isinstance(raw.get("photoType"), str) or raw["photoType"] not in _PHOTO_TYPES:
        return None
    sleeve_type = raw.get("sleeveType")
    sleeve_type = sleeve_type if isinstance(sleeve_type, str) and sleeve_type in _SLEEVE_TYPES else None
    result = {"photoType": raw["photoType"], "sleeveType": sleeve_type}
    for name, allowed in _VALUES.items():
        item = raw.get(name)
        item = item if isinstance(item, dict) else {}
        value = item.get("value")
        value = value if isinstance(value, str) and value in allowed else None
        result[name] = {"value": value, "confident": value is not None and item.get("confident") is True}
    if result["sleeve"]["value"] not in _SLEEVE_GROUPS.get(sleeve_type, ()):
        result["sleeve"] = {"value": None, "confident": False}
    if sleeve_type == "long" and result["photoType"] != "worn":
        result["sleeve"]["confident"] = False
    if result["photoType"] == "hanger":
        result["topLength"]["confident"] = False
    if result["photoType"] != "worn":
        result["pantsLength"]["confident"] = False
    return result


def request_metadata(settings, provider):
    if provider is None:
        return {}
    return {"fitReaderProvider": provider, "fitReaderModel": model_name(settings), "fitReaderPromptVersion": "v1"}


async def read(settings, images, *, slots=None):
    if not enabled(settings) or not images:
        return None, None
    model = model_name(settings)
    started = time.perf_counter()
    provider, accepted = None, False
    try:
        raw, provider = await analyze_with_fallback(
            settings, build_prompt(slots), images, response_schema(), models={"gemini": model},
            order=["gemini"], thinking_level="low", require_complete_envelope=True,
            timeout_seconds=settings.analysis_aux_timeout_seconds,
        )
        result = normalize_fit_read(raw)
        accepted = result is not None
        return result, provider
    finally:
        log.info("FIT-READ provider=%s model=%s prompt_version=v1 elapsed_ms=%d accepted=%s",
                 provider, model, round((time.perf_counter() - started) * 1000), accepted)
