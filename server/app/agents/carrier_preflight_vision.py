"""Observation-only Vision contract for a pre-projection garment carrier."""

from __future__ import annotations

import hashlib
import os
import time

from ..config import Settings
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

PROMPT_VERSION = "hybrid_carrier_preflight_vision_v1"
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "hybrid_carrier_preflight_vision_v1.txt")

SILHOUETTES = ("shirt", "cape", "poncho", "slab", "other", "unknown")
BOOLEAN_FIELDS = (
    "hemPlausible",
    "sleevesPlausible",
    "lowerBodyPresent",
    "matchingGarmentPresent",
    "mannequinFramePreserved",
    "garmentCategoryMatches",
)
OBSERVATION_FIELDS = ("shirtSilhouette", *BOOLEAN_FIELDS)
FORBIDDEN_FIELDS = (
    "decision", "verdict", "pass", "reject", "review", "approved", "score",
    "recommendation", "regenerationInstructions", "action",
)
_EVIDENCE_MAX = 5
_EVIDENCE_LEN = 120


def schema() -> dict:
    properties = {
        "shirtSilhouette": {"type": "string", "enum": list(SILHOUETTES)},
        **{field: {"type": ["boolean", "null"]} for field in BOOLEAN_FIELDS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "uncertainFields": {
            "type": "array",
            "items": {"type": "string", "enum": list(OBSERVATION_FIELDS)},
        },
        "evidence": {
            "type": "array",
            "maxItems": _EVIDENCE_MAX,
            "items": {"type": "string", "maxLength": _EVIDENCE_LEN},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }

def build_prompt(*, product_count: int, matching_expected: bool) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as file:
        template = file.read()
    return (template
            .replace("${productCount}", str(max(1, int(product_count))))
            .replace("${matchingExpected}", "YES" if matching_expected else "NO"))


def template_sha256() -> str:
    with open(_PROMPT_FILE, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise VisionError("carrier preflight observation is not an object")
    lowered = {str(key).lower() for key in raw}
    leaked = [field for field in FORBIDDEN_FIELDS if field.lower() in lowered]
    if leaked:
        raise VisionError(f"carrier preflight leaked decision fields: {sorted(leaked)}")
    allowed = set(OBSERVATION_FIELDS) | {"confidence", "uncertainFields", "evidence"}
    unknown = set(raw) - allowed
    if unknown:
        raise VisionError(f"carrier preflight unknown fields: {sorted(unknown)}")

    silhouette = raw.get("shirtSilhouette")
    if silhouette not in SILHOUETTES:
        raise VisionError("carrier preflight invalid shirtSilhouette")
    out = {"shirtSilhouette": silhouette}
    for field in BOOLEAN_FIELDS:
        if field not in raw:
            raise VisionError(f"carrier preflight missing field: {field}")
        value = raw[field]
        if value is not None and not isinstance(value, bool):
            raise VisionError(f"carrier preflight invalid field type: {field}")
        out[field] = value

    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VisionError("carrier preflight confidence is not numeric")
    if not 0.0 <= float(confidence) <= 1.0:
        raise VisionError("carrier preflight confidence outside 0..1")
    out["confidence"] = float(confidence)

    uncertain = raw.get("uncertainFields")
    if not isinstance(uncertain, list) or any(
            not isinstance(value, str) or value not in OBSERVATION_FIELDS
            for value in uncertain):
        raise VisionError("carrier preflight uncertainFields contract violation")
    inferred = {field for field in BOOLEAN_FIELDS if out[field] is None}
    if silhouette == "unknown":
        inferred.add("shirtSilhouette")
    out["uncertainFields"] = sorted(set(uncertain) | inferred)

    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        raise VisionError("carrier preflight evidence contract violation")
    out["evidence"] = [item.strip()[:_EVIDENCE_LEN] for item in evidence
                       if item.strip()][:_EVIDENCE_MAX]
    return out


async def observe(
    settings: Settings,
    *,
    canonical: InlineImage,
    product_sources: list[InlineImage],
    matching_garment: InlineImage | None,
    candidate: InlineImage,
) -> tuple[dict, dict]:
    """Observe canonical, originals, optional match, then the carrier (last)."""
    sources = list(product_sources[:3])
    images = [canonical, *sources]
    if matching_garment is not None:
        images.append(matching_garment)
    images.append(candidate)
    prompt = build_prompt(
        product_count=len(sources), matching_expected=matching_garment is not None)
    started = time.perf_counter()
    raw, provider = await analyze_with_fallback(settings, prompt, images, schema())
    observation = validate(raw)
    return observation, {
        "provider": provider,
        "promptVersion": PROMPT_VERSION,
        "templateSha256": template_sha256(),
        "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "imageCount": len(images),
        "status": "ok",
    }


def failure_meta(exc: BaseException, *, image_count: int) -> dict:
    name = type(exc).__name__
    status = "timeout" if "Timeout" in name else (
        "provider_error" if isinstance(exc, VisionError) else "unexpected_error")
    return {
        "provider": None,
        "promptVersion": PROMPT_VERSION,
        "templateSha256": template_sha256(),
        "promptSha256": None,
        "latencyMs": None,
        "imageCount": image_count,
        "status": status,
        "errorType": name,
    }
