from __future__ import annotations

import re
import time
from io import BytesIO
from typing import Any

from PIL import Image

from .gemini_image import InlineImage, run_cpu_bound

FALLBACK_INSTRUCTION = (
    "Inside the editable area, repair any visible compositing seam, notch, step, torn-looking fragment, stray fabric shard or misaligned collar/neckline edge where the neck meets the garment, so the neck outline, skin and garment edge are continuous and natural. Preserve the same garment design, collar or neckline shape, stripes or pattern, knit, rib or denim texture, stitching, neck proportions and shadows."
)

_DEFECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "what": {"type": "string", "minLength": 1, "maxLength": 220},
        "where": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "required": ["what", "where"],
}

_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "garment": {"type": "string", "minLength": 1, "maxLength": 220},
        "defects": {"type": "array", "items": _DEFECT_SCHEMA, "maxItems": 12},
        "edit_instruction": {"type": "string", "minLength": 1, "maxLength": 900},
        "neck_collar_gap": {"type": "boolean"},
    },
    "required": ["garment", "defects", "edit_instruction", "neck_collar_gap"],
}

_QC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "remaining_defects": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 220}, "maxItems": 8},
        "garment_geometry_preserved": {"type": "boolean"},
        "skin_texture_preserved": {"type": "boolean"},
        "seamless": {"type": "boolean"},
        "uncertain": {"type": "boolean"},
    },
    "required": ["remaining_defects", "garment_geometry_preserved", "skin_texture_preserved", "seamless", "uncertain"],
}

LOOK_PROMPT = """You inspect fashion catalog photos for AI compositing damage.
Image 1: close crop around the neck of a catalog photo in which the head and neck were replaced by an AI model.
Image 2: the same crop from the original photo BEFORE replacement. The person differs, but the garment in image 2 is the ground truth.

Replacement often damages the place where the neck meets the garment: pointed fabric shards or scraps sticking out beside the neck,
angular notches or steps in the neck outline, torn-looking fragments, a collar or strap edge that is cut, doubled or misaligned,
a gap or wedge in a placket or strap, skin showing where fabric should be, or fabric covering where skin should be.
Compare image 1 against image 2 and against how a real garment would sit on a real neck.

Return:
- garment: short English description of the garment around the neck (type, neckline or collar, color, material, pattern).
- defects: every visible damage in image 1 near the neck/garment boundary. "what" = precise visual description, "where" = location using
  IMAGE left/right (viewer side) and front/back. Look at both sides and the rear collar, especially denim collars and skin seams below the jaw.
  Do not list the face itself, normal fabric folds, normal open chest/placket, normal white cloth fragments, or normal shadows.
- neck_collar_gap: true only for a background-colored gap or missing wedge between the NECK skin edge and the FIXED collar/crewneck edge that should touch it.
  Do not mark white cloth fragments, natural open chest/placket, ordinary shadows, skin visible in a normal neckline, or floating fabric as neck_collar_gap.
- edit_instruction: 2-3 English sentences for an image editor, written like these examples:
  "Repair the broken raised fabric scraps on both sides of the neck and make the striped shirt collar and skin contact continuous. Preserve the same open pointed shirt collar, fine stripes, neck shape, and shadows."
  "Repair the jagged torn-looking artificial fragments around the denim collar opening, including the rear right collar sticking up behind the neck, left inner collar and dark triangular fragments at the front neck. Reconstruct a continuous natural open denim shirt collar, matching its existing faded denim and stitching."
  "Remove the pointed grey fabric shard protruding at the left neck and the unnatural angular notch on the right neck. Restore a smooth continuous circular ribbed crewneck with natural contact against skin. Preserve its original grey knit and rib texture, round neck opening, and neck proportions."
  Name each defect and its location, say what the correct garment edge should look like (use image 2), and what to preserve.
  When neck_collar_gap is true, instruct the editor to extend only the neck skin edge to meet the existing collar naturally.
  Explicitly keep the face, chin, jawline, garment, collar opening, ribbed band and both shoulder lines fixed; do not raise or reshape the garment to close the gap.
  The editor sees ONLY image 1: never mention "image 1" or "image 2" in edit_instruction; describe the correct edge in words.
  If you truly see no damage, still describe the boundary to keep continuous; never tell the editor to leave it as is."""


def _png_1024(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.convert("RGB").resize((1024, 1024), Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


def _inline_images(*images):
    return [InlineImage("image/png", _png_1024(image)) for image in images]


def _safe_usage(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    nested_allow = {
        "input_tokens_details": {"text_tokens", "image_tokens", "cached_tokens"},
        "output_tokens_details": {"text_tokens", "image_tokens", "cached_tokens"},
    }
    for key, allowed in nested_allow.items():
        details = raw.get(key)
        if isinstance(details, dict):
            clean = {
                k: v for k, v in details.items()
                if k in allowed and isinstance(v, int) and not isinstance(v, bool) and v >= 0
            }
            if clean:
                out[key] = clean
    return out or None


def _copy_call_metadata(dst: dict | None, call_meta: dict, *, started: float, requested_model: str) -> None:
    if dst is None:
        return
    dst["duration_ms"] = int((time.perf_counter() - started) * 1000)
    usage = _safe_usage(call_meta.get("usage"))
    if re.fullmatch(r"gpt-[A-Za-z0-9._-]{1,64}", requested_model):
        dst["requested_model"] = requested_model
    if usage:
        dst["usage"] = usage


def _clean_string(value: Any, *, field: str) -> str:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(value, str):
        raise SeamRepairUnavailable("vision_bad_response") from None
    clean = " ".join(value.split())
    limit = 900 if field == "instruction" else 220
    if not clean or len(clean) > limit:
        raise SeamRepairUnavailable("vision_bad_response") from None
    return clean


def _validate_bool(value: Any) -> bool:
    from .face_seam_repair import SeamRepairUnavailable

    if type(value) is not bool:
        raise SeamRepairUnavailable("vision_bad_response") from None
    return value


def _defect_observations(raw: Any) -> list[str]:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, list) or len(raw) > 12:
        raise SeamRepairUnavailable("vision_bad_response") from None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, dict) or set(item.keys()) != {"what", "where"}:
            raise SeamRepairUnavailable("vision_bad_response") from None
        what = _clean_string(item["what"], field="observation")
        where = _clean_string(item["where"], field="observation")
        out.append(f"{where}: {what}")
    return out


def _strip_image_refs(instruction: str) -> str:
    return re.sub(r"\bimage\s*[12]\b", "the crop", instruction, flags=re.I)


def _make_plan(*, instruction: str, observations: list[str], neck_collar_gap: bool, garment: str):
    from .face_seam_repair import RepairPlan

    return RepairPlan(
        instruction=instruction,
        observations=observations,
        neck_collar_gap=neck_collar_gap,
        garment=garment,
    )


def _validate_plan(raw: Any):
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, dict) or set(raw.keys()) != set(_PLAN_SCHEMA["required"]):
        raise SeamRepairUnavailable("vision_bad_response") from None
    garment = _clean_string(raw["garment"], field="observation")
    observations = _defect_observations(raw["defects"])
    neck_collar_gap = _validate_bool(raw["neck_collar_gap"])
    instruction = FALLBACK_INSTRUCTION if not observations and not neck_collar_gap else _strip_image_refs(_clean_string(raw["edit_instruction"], field="instruction"))
    return _make_plan(instruction=instruction, observations=observations, neck_collar_gap=neck_collar_gap, garment=garment)


def _validate_qc(raw: Any) -> bool:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, dict) or set(raw.keys()) != set(_QC_SCHEMA["required"]):
        raise SeamRepairUnavailable("vision_bad_response") from None
    remaining = raw["remaining_defects"]
    if not isinstance(remaining, list) or len(remaining) > 8:
        raise SeamRepairUnavailable("vision_bad_response") from None
    for item in remaining:
        _clean_string(item, field="observation")
    garment = _validate_bool(raw["garment_geometry_preserved"])
    skin = _validate_bool(raw["skin_texture_preserved"])
    seamless = _validate_bool(raw["seamless"])
    uncertain = _validate_bool(raw["uncertain"])
    return len(remaining) == 0 and garment and skin and seamless and not uncertain


def _qc_prompt() -> str:
    return (
        "Compare three aligned neck crops only: Image 1 current before repair, Image 2 candidate repaired crop, Image 3 original garment/base crop. "
        "Judge whether the candidate fixed the neck/collar seam without damaging garment geometry or skin. Check binding width relative to the original perspective, "
        "scoop depth, neck-to-collar alignment, seam continuity, rear collar continuity, double-knit/rib/fold pattern consistency, normal openings/buttons, skin texture, "
        "and whether any double band, notch, fragment, or pasted-skin seam remains. The original base has a different person; do not demand matching that face/neck identity. Fail uncertain candidates. Preserve natural perspective rather than requiring equal left/right widths. Return strict JSON only."
    )


async def plan_repair(settings, crop, *, metadata: dict | None = None):
    from . import vision_llm
    from .face_seam_repair import SeamRepairUnavailable

    base = getattr(crop, "base", None)
    if base is None:
        raise SeamRepairUnavailable("vision_base_missing") from None
    if base.size != crop.current.size:
        raise SeamRepairUnavailable("base_crop_mismatch") from None
    model = (getattr(settings, "face_seam_vision_model", None) or "gpt-5.4").strip() or "gpt-5.4"
    call_meta: dict = {}
    started = time.perf_counter()
    try:
        raw = await vision_llm._call_gpt(
            settings,
            model,
            LOOK_PROMPT,
            await run_cpu_bound(_inline_images, crop.current, base),
            _PLAN_SCHEMA,
            180.0,
            metadata=call_meta,
            reasoning_effort="high",
            image_detail="high",
        )
    except Exception:
        raise SeamRepairUnavailable("vision_provider_error") from None
    finally:
        _copy_call_metadata(metadata, call_meta, started=started, requested_model=model)
    return _validate_plan(raw)


async def verify_repair(settings, crop, repaired_crop: Image.Image, *, metadata: dict | None = None) -> bool:
    from . import vision_llm
    from .face_seam_repair import SeamRepairUnavailable

    base = getattr(crop, "base", None)
    if base is None:
        raise SeamRepairUnavailable("vision_base_missing") from None
    if base.size != crop.current.size:
        raise SeamRepairUnavailable("base_crop_mismatch") from None
    model = (getattr(settings, "face_seam_vision_model", None) or "gpt-5.4").strip() or "gpt-5.4"
    call_meta: dict = {}
    started = time.perf_counter()
    try:
        raw = await vision_llm._call_gpt(
            settings,
            model,
            _qc_prompt(),
            await run_cpu_bound(_inline_images, crop.current, repaired_crop, base),
            _QC_SCHEMA,
            180.0,
            metadata=call_meta,
            reasoning_effort="high",
            image_detail="high",
        )
    except Exception:
        raise SeamRepairUnavailable("vision_provider_error") from None
    finally:
        _copy_call_metadata(metadata, call_meta, started=started, requested_model=model)
    ok = _validate_qc(raw)
    if metadata is not None:
        metadata["garment_geometry_preserved"] = raw["garment_geometry_preserved"]
        metadata["skin_texture_preserved"] = raw["skin_texture_preserved"]
        metadata["seamless"] = raw["seamless"]
        metadata["uncertain"] = raw["uncertain"]
        metadata["remaining_defect_count"] = len(raw["remaining_defects"])
    return ok
