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

_POINT_SCHEMA = {
    "type": "array",
    "items": {"type": "number", "minimum": 0, "maximum": 1024},
    "minItems": 2,
    "maxItems": 2,
}

_POLYGON_SCHEMA = {
    "type": "array",
    "items": _POINT_SCHEMA,
    "minItems": 3,
    "maxItems": 32,
}

_POLYGONS_SCHEMA = {
    "type": "array",
    "items": _POLYGON_SCHEMA,
    "maxItems": 8,
}

_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "garment": {"type": "string", "minLength": 1, "maxLength": 220},
        "defects": {"type": "array", "items": _DEFECT_SCHEMA, "maxItems": 12},
        "edit_instruction": {"type": "string", "minLength": 1, "maxLength": 900},
        "neck_collar_gap": {"type": "boolean"},
        "damage_polygons": _POLYGONS_SCHEMA,
    },
    "required": ["garment", "defects", "edit_instruction", "neck_collar_gap", "damage_polygons"],
}

_COMPOSITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "composition_polygons": _POLYGONS_SCHEMA,
    },
    "required": ["composition_polygons"],
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
- damage_polygons: normalized 0..1024 crop coordinates for the local redraw unit needed to repair the defects. Return up to 8 polygons,
  each with 3..32 [x,y] vertices. Include the defect plus nearby undamaged connecting context required to redraw a continuous collar, binding,
  placket, neckline edge, neck silhouette, or skin/cloth contact. A local background-colored missing wedge between neck silhouette and fixed collar
  may be included when that is the defect. Use separate small polygons when intact central ribbing or garment detail should stay untouched; use one
  connected neck/clavicle/binding unit when a duplicated band spans that whole unit. Preserve natural asymmetric perspective and do not force symmetry.
  Keep polygons local to the neck/garment boundary; do not mark intact upper face, broad shoulder/garment areas, crop edges, or unrelated background.
  Return [] only when no local repair region is discernible.
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

COMPOSITION_PROMPT = """You inspect two same-frame neck repair crops. The generated repair may have small drift from the input crop.
Image 1: the current crop before repair.
Image 2: the generated repair candidate.

Return only composition_polygons: normalized 0..1024 crop coordinates for one continuous repair unit or a small set of continuous units to composite from image 2.
Coordinates are in the input crop coordinate system. The region must enclose the original damage_polygons supplied in the text context when a repair is visible, extend only far enough to place the boundary in intact matching skin or cloth texture, and avoid fragmented skin masks.
Prefer a continuous neck/clavicle/collar or binding unit when the generated repair changed that unit together. Use separate small polygons only when intact fabric detail between repairs should remain from image 1.
Preserve unaffected clothing, garment opening shape, collar or binding width, rib/stitch/fold pattern, natural asymmetric perspective, shoulders, face, jawline, normal placket openings, shadows, and crop edges.
Return [] if no reliable continuous composition region is discernible. Return strict JSON only."""


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


def _validate_polygons(raw: Any) -> list[list[list[float]]]:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, list) or len(raw) > 8:
        raise SeamRepairUnavailable("vision_bad_response") from None
    out: list[list[list[float]]] = []
    for poly in raw:
        if not isinstance(poly, list) or not (3 <= len(poly) <= 32):
            raise SeamRepairUnavailable("vision_bad_response") from None
        clean_poly: list[list[float]] = []
        for point in poly:
            if not isinstance(point, list) or len(point) != 2:
                raise SeamRepairUnavailable("vision_bad_response") from None
            x, y = point
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not float("-inf") < float(x) < float("inf")
                or not float("-inf") < float(y) < float("inf")
                or x < 0
                or y < 0
                or x > 1024
                or y > 1024
            ):
                raise SeamRepairUnavailable("vision_bad_response") from None
            clean_poly.append([float(x), float(y)])
        out.append(clean_poly)
    return out


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


def _make_plan(*, instruction: str, observations: list[str], neck_collar_gap: bool, garment: str, damage_polygons: list):
    from .face_seam_repair import RepairPlan

    return RepairPlan(
        instruction=instruction,
        observations=observations,
        neck_collar_gap=neck_collar_gap,
        garment=garment,
        damage_polygons=damage_polygons,
    )


def _validate_plan(raw: Any):
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, dict) or set(raw.keys()) != set(_PLAN_SCHEMA["required"]):
        raise SeamRepairUnavailable("vision_bad_response") from None
    garment = _clean_string(raw["garment"], field="observation")
    observations = _defect_observations(raw["defects"])
    neck_collar_gap = _validate_bool(raw["neck_collar_gap"])
    damage_polygons = _validate_polygons(raw["damage_polygons"])
    instruction = FALLBACK_INSTRUCTION if not observations and not neck_collar_gap else _strip_image_refs(_clean_string(raw["edit_instruction"], field="instruction"))
    return _make_plan(
        instruction=instruction,
        observations=observations,
        neck_collar_gap=neck_collar_gap,
        garment=garment,
        damage_polygons=damage_polygons,
    )


def _validate_composition(raw: Any) -> list[list[list[float]]]:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, dict) or set(raw.keys()) != set(_COMPOSITION_SCHEMA["required"]):
        raise SeamRepairUnavailable("vision_bad_response") from None
    return _validate_polygons(raw["composition_polygons"])


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


def _protected_context(crop, *, field: str) -> str:
    image = getattr(crop, "current", None)
    face_box = getattr(crop, "face_box", None)
    if not isinstance(image, Image.Image) or image.width <= 0 or image.height <= 0:
        return "Avoid crop edges and broad intact areas."
    if not isinstance(face_box, (tuple, list)) or len(face_box) < 4:
        return "Avoid crop edges and broad intact areas."
    try:
        fx, fy, fw, fh = (float(v) for v in face_box[:4])
    except (TypeError, ValueError):
        return "Avoid crop edges and broad intact areas."
    vals = (fx, fy, fw, fh)
    if not all(float("-inf") < v < float("inf") for v in vals) or fw <= 0 or fh <= 0:
        return "Avoid crop edges and broad intact areas."
    sx = 1024.0 / image.width
    sy = 1024.0 / image.height
    protected_top = max(0.0, fy + 0.55 * fh) * sy
    jaw_skin_top = max(0.0, fy + 0.95 * fh) * sy
    face_left = max(0.0, fx - 0.05 * fw) * sx
    face_right = min(float(image.width), fx + 1.05 * fw) * sx
    return (
        f"Protected boundary context: Do not place {field} above y={protected_top:.0f}. "
        f"Never include jaw/face skin above y={jaw_skin_top:.0f} within face x={face_left:.0f}..{face_right:.0f}. "
        "Avoid crop edges and broad intact areas."
    )


def _plan_prompt(crop) -> str:
    return f"{LOOK_PROMPT}\n\n{_protected_context(crop, field='damage_polygons')}"


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
            _plan_prompt(crop),
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


async def plan_composition(settings, crop, generated: Image.Image, repair_plan, *, metadata: dict | None = None):
    from . import vision_llm
    from .face_seam_repair import SeamRepairUnavailable

    model = (getattr(settings, "face_seam_vision_model", None) or "gpt-5.4").strip() or "gpt-5.4"
    damage_polygons = _validate_polygons(getattr(repair_plan, "damage_polygons", []))
    prompt = (
        f"{COMPOSITION_PROMPT}\n\n"
        f"Context: original damage_polygons={damage_polygons!r}. "
        f"Garment={_clean_string(getattr(repair_plan, 'garment', '') or 'garment', field='observation')}. "
        "Choose composition_polygons only for the continuous repair unit visible in image 2.\n\n"
        f"{_protected_context(crop, field='composition_polygons')}"
    )
    call_meta: dict = {}
    started = time.perf_counter()
    try:
        raw = await vision_llm._call_gpt(
            settings,
            model,
            prompt,
            await run_cpu_bound(_inline_images, crop.current, generated),
            _COMPOSITION_SCHEMA,
            180.0,
            metadata=call_meta,
            reasoning_effort="high",
            image_detail="high",
        )
    except Exception:
        raise SeamRepairUnavailable("vision_provider_error") from None
    finally:
        _copy_call_metadata(metadata, call_meta, started=started, requested_model=model)
    return _validate_composition(raw)


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
