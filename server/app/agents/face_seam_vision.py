from __future__ import annotations

import math
import re
import time
from io import BytesIO
from typing import Any

from PIL import Image

from .gemini_image import InlineImage, run_cpu_bound

_POINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "x": {"type": "number", "minimum": 0, "maximum": 1000},
        "y": {"type": "number", "minimum": 0, "maximum": 1000},
    },
    "required": ["x", "y"],
}

_POLYGON_SCHEMA = {
    "type": "array",
    "minItems": 3,
    "maxItems": 32,
    "items": _POINT_SCHEMA,
}

_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "observations": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 220}, "minItems": 1, "maxItems": 8},
        "neck_collar_gap": {"type": "boolean"},
        "instruction": {"type": "string", "minLength": 1, "maxLength": 900},
        "damage_polygons": {"type": "array", "items": _POLYGON_SCHEMA, "minItems": 1, "maxItems": 8},
        "composition_polygons": {"type": "array", "items": _POLYGON_SCHEMA, "minItems": 1, "maxItems": 8},
        "align": {"type": "boolean"},
    },
    "required": ["observations", "neck_collar_gap", "instruction", "damage_polygons", "composition_polygons", "align"],
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
    # Never copy provider-supplied strings, even model identifiers, into logs.
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


def _native_polygons(raw: Any, size: tuple[int, int], crop: Any) -> list[list[tuple[float, float]]]:
    from .face_seam_repair import SeamRepairUnavailable

    if not isinstance(raw, list) or not (1 <= len(raw) <= 8):
        raise SeamRepairUnavailable("vision_bad_response") from None
    width, height = size
    out: list[list[tuple[float, float]]] = []
    for poly in raw:
        if not isinstance(poly, list) or not (3 <= len(poly) <= 32):
            raise SeamRepairUnavailable("vision_bad_response") from None
        points: list[tuple[float, float]] = []
        for point in poly:
            if not isinstance(point, dict) or set(point.keys()) != {"x", "y"}:
                raise SeamRepairUnavailable("vision_bad_response") from None
            x = point["x"]
            y = point["y"]
            if type(x) not in (int, float) or type(y) not in (int, float):
                raise SeamRepairUnavailable("vision_bad_response") from None
            if not math.isfinite(float(x)) or not math.isfinite(float(y)) or not (0 <= x <= 1000) or not (0 <= y <= 1000):
                raise SeamRepairUnavailable("vision_bad_response") from None
            points.append((round(float(x) / 1000.0 * width, 4), round(float(y) / 1000.0 * height, 4)))
        _reject_gross_polygon(points, size, crop)
        out.append(points)
    return out


def _reject_gross_polygon(points: list[tuple[float, float]], size: tuple[int, int], crop: Any) -> None:
    from .face_seam_repair import SeamRepairUnavailable

    width, height = size
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    box_w = max(xs) - min(xs)
    box_h = max(ys) - min(ys)
    if box_w >= 0.92 * width and box_h >= 0.92 * height:
        raise SeamRepairUnavailable("vision_bad_response") from None
    if box_w * box_h > 0.72 * width * height:
        raise SeamRepairUnavailable("vision_bad_response") from None
    face_box = getattr(crop, "face_box", None)
    if isinstance(face_box, tuple) and len(face_box) >= 4:
        fx, fy, fw, fh = (float(v) for v in face_box[:4])
        cx = fx + fw / 2
        # Safety envelope only; the model still selects the actual repair polygons.
        if (min(xs) < max(0, cx - 1.5 * fw) or max(xs) > min(width, cx + 1.5 * fw)
                or min(ys) < max(0, fy + .55 * fh) or max(ys) > min(height, fy + 2.25 * fh)):
            raise SeamRepairUnavailable("vision_polygon_outside_neck") from None


def _validate_plan(raw: Any, crop: Any):
    from .face_seam_repair import RepairPlan, SeamRepairUnavailable

    if not isinstance(raw, dict) or set(raw.keys()) != set(_PLAN_SCHEMA["required"]):
        raise SeamRepairUnavailable("vision_bad_response") from None
    observations_raw = raw["observations"]
    if not isinstance(observations_raw, list) or not (1 <= len(observations_raw) <= 8):
        raise SeamRepairUnavailable("vision_bad_response") from None
    observations = [_clean_string(v, field="observation") for v in observations_raw]
    instruction = _clean_string(raw["instruction"], field="instruction")
    if re.search(r"\bimage\s*[123]\b", instruction, re.I):
        raise SeamRepairUnavailable("vision_bad_response") from None
    damage = _native_polygons(raw["damage_polygons"], crop.current.size, crop)
    composition = _native_polygons(raw["composition_polygons"], crop.current.size, crop)
    return RepairPlan(
        True,
        instruction,
        damage,
        composition,
        _validate_bool(raw["align"]),
        7,
        observations,
        _validate_bool(raw["neck_collar_gap"]),
    )


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


def _plan_prompt(crop: Any) -> str:
    face_box = getattr(crop, "face_box", None)
    coords = ""
    if isinstance(face_box, tuple) and len(face_box) >= 4:
        fx, fy, fw, fh = (float(v) for v in face_box[:4])
        w, h = crop.current.size
        coords = (
            f" Face box in the crop, normalized 0..1000, is approximately "
            f"x={fx / w * 1000:.0f}, y={fy / h * 1000:.0f}, w={fw / w * 1000:.0f}, h={fh / h * 1000:.0f}; "
            "polygon vertices must remain in the neck/collar safety envelope: x within face center +/-1.5 face widths, y within face y+0.55h through y+2.25h, clipped to the crop."
        )
    return (
        "Create a photo-specific neck seam repair plan from two aligned 1024 crops. Image 1 is the current generated neck crop; "
        "Image 2 is the original garment/base neck crop worn by a different person. Use Image 2 only for garment geometry, collar/binding structure, "
        "scoop depth, button/opening state, fabric texture, stitching, rib/fold pattern and intact clothing continuity; do not copy that person's skin or identity."
        f"{coords} Always produce a targeted neck joining plan. If no clear defect is visible, still mark the narrow complete neck-interface region and write a local continuity instruction. "
        "Inspect the entire neck outline on BOTH sides and the FULL garment opening, including the rear collar, lower scoop, straps and placket. "
        "Describe every actual broken edge: jagged cutouts/notches, floating cloth shards, duplicated binding or rib bands, patches of skin covering fabric, and pasted-skin transitions below the jaw. "
        "Ignore normal folds/shadows and the deliberate difference in face identity. neck_collar_gap means a BACKGROUND-COLORED missing wedge separating neck skin from its fixed collar, not normal exposed chest or a dark collar shadow. "
        "When the neck is too narrow for a fixed collar, extend the neck locally into the missing wedge; do not move/redraw the intact collar or shoulders. "
        "When changing a binding or collar, repair one coherent segment; preserve binding width relative to the original perspective and do not blend incompatible widths, rib patterns, fold patterns, or force symmetry. "
        "Do not change a normal opening, button state, pose, shoulder line, collar tip, placket alignment, scoop depth, garment width, or original perspective. "
        "instruction must contain 2-3 English sentences: actual defect and precise location, desired continuous geometry, then specific intact features to preserve. "
        "The editing model receives ONLY the current crop, so never mention Image 1/2 or a second reference in instruction. "
        "Return normalized 0..1000 polygons: damage_polygons cover ALL defects plus a small repair margin; composition_polygons contain every damage polygon plus a generous feather margin on intact surroundings. "
        "Keep existing intact rib/binding out of damage when only adjacent skin is broken. If binding itself must be repaired, include the complete damaged segment in composition, with its boundary landing on continuous intact cloth/skin instead of cutting across two incompatible outlines. "
        "align=true uses intact surroundings to align the generated crop; use false if preserving a narrow binding would be distorted by alignment. "
        "Reject filename rules and generic broad masks; keep polygons tight around the neck/collar interface."
    )


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
    return _validate_plan(raw, crop)


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
