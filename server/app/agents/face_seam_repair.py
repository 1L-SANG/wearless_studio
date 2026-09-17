from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

from .. import image_usage
from .gemini_image import run_cpu_bound
from .image_cost import estimate_cost

SUNBURST_MODEL = "gpt-image-2.5-sunburst"
PROMPT_HEAD = "Use case: precise-object-edit. This is a surgical photo restoration, not a new photograph. The input is a square crop of an existing studio fashion photograph. Keep the exact crop, subject placement, scale, geometry and camera view. Only repair the damaged neck/collar compositing inside the transparent mask.\n\n"
PROMPT_TAIL = "Copy every intact feature exactly. Preserve the person's identity, face, jawline, hair, expression, pose, body proportions, clothing, lighting, texture, sharpness and neutral background. Match existing skin and cloth texture; no beauty retouch, no smoothing, no new detail or design. Eliminate abrupt cutouts, discontinuous edges, duplicated cloth fragments and pasted-skin seams. Make this look like the same unedited photograph with only the neck compositing defects corrected. All opaque mask areas must remain unchanged. Return a single square photograph, same framing. No text or borders."
log = logging.getLogger(__name__)


class SeamRepairUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FaceSeamContext:
    plan: Any
    crop_pad: Any = None
    references: Any = None
    model_dir: str | None = None
    neck_offset: float | None = None
    base_crop: Image.Image | None = field(default=None, repr=False)
    base_crop_box: tuple[int, int, int] | None = None
    band_bounds: tuple[int, int, int, int] | None = None
    tone_context: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class RepairCrop:
    current: Image.Image
    box: tuple[int, int, int]
    face_box: tuple[float, float, float, float]
    final_size: tuple[int, int]
    crop_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
    base: Image.Image | None = field(default=None, repr=False)
    skin: np.ndarray | None = field(default=None, repr=False)
    edit_mask: Image.Image | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RepairPlan:
    instruction: str
    observations: list[str] = field(default_factory=list)
    neck_collar_gap: bool = False
    garment: str = ""
    damage_polygons: list = field(default_factory=list)
    composition_polygons: list = field(default_factory=list)


@dataclass(frozen=True)
class SunburstResult:
    image: Image.Image
    usage: dict | None
    latency_ms: int


def _pad_ltrb(crop_pad) -> tuple[int, int, int, int]:
    if isinstance(crop_pad, dict):
        return tuple(int(crop_pad.get(k, 0) or 0) for k in ("left", "top", "right", "bottom"))
    if isinstance(crop_pad, (list, tuple)) and len(crop_pad) >= 4:
        return tuple(int(v or 0) for v in crop_pad[:4])
    return (0, 0, 0, 0)


def _safe_usage(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    nested_allow = {
        "input_tokens_details": {"text_tokens", "image_tokens", "cached_tokens"},
        "output_tokens_details": {"text_tokens", "image_tokens", "cached_tokens"},
    }
    for key, allowed_keys in nested_allow.items():
        details = raw.get(key)
        if isinstance(details, dict):
            clean = {
                k: v for k, v in details.items()
                if k in allowed_keys and isinstance(v, int) and not isinstance(v, bool) and v >= 0
            }
            if clean:
                out[key] = clean
    return out or None


def _json_safe(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if _json_safe(v) is not None}
    return None


def _png_bytes(image: Image.Image, mode: str = "RGB") -> bytes:
    buf = BytesIO()
    image.convert(mode).save(buf, "PNG")
    return buf.getvalue()


def _ellipse(radius):
    r = max(0, int(round(radius)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*r + 1,) * 2)


def neck_band_bounds(original_size, plan, gen_mask, crop_pad=None):
    from .face_mask_lock import unlocked_cells
    if gen_mask is None or np.asarray(gen_mask).shape != (1024, 1024):
        raise SeamRepairUnavailable("generation_mask_missing")
    un = unlocked_cells(np.asarray(gen_mask, bool)).astype(np.uint8)
    kernel = _ellipse(56)
    band = cv2.dilate(un, kernel).astype(bool) & ~cv2.erode(un, kernel).astype(bool)
    _, fy, _, fh = plan.face_box_crop
    band[:max(0, int(fy + .55*fh))] = False
    band[int(min(1024, fy + 2*fh)):] = False
    x, y, side = plan.crop
    # Resize first, like the prototype: bbox rounding must use actual output pixels.
    band = cv2.resize(band.astype(np.uint8), (side, side), interpolation=cv2.INTER_NEAREST)
    pl, pt, pr, pb = _pad_ltrb(crop_pad)
    width, height = original_size[0] - pl - pr, original_size[1] - pt - pb
    ys, xs = np.nonzero(band)
    xs, ys = xs + x - pl, ys + y - pt
    inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    if not inside.any():
        raise SeamRepairUnavailable("empty_neck_band")
    return (int(xs[inside].min()), int(ys[inside].min()), int(xs[inside].max()), int(ys[inside].max()))


def repair_crop_box(final_size, band_bounds, margin=.3, *, min_top=0):
    if band_bounds is None:
        raise SeamRepairUnavailable("neck_band_missing")
    x0, y0, x1, y1 = band_bounds
    min_top = max(0, int(np.ceil(min_top)))
    side = min(int(max(x1-x0, y1-y0) * (1+margin)), final_size[0], final_size[1]-min_top)
    if side < 16:
        raise SeamRepairUnavailable("neck_crop_too_small")
    cx, cy = (x0+x1)//2, (y0+y1)//2
    return (int(np.clip(cx-side//2, 0, final_size[0]-side)), int(np.clip(cy-side//2, min_top, final_size[1]-side)), side)


def capture_repair_context(original, plan, *, gen_mask=None, current=None, tone_enabled=False,
                           crop_pad=None, references=None, model_dir=None, neck_offset=None):
    """Keep a neck ROI plus derived masks/statistics, never the full base photograph."""
    from . import face_identity as fi
    pl, pt, pr, pb = _pad_ltrb(crop_pad)
    size = (original.width-pl-pr, original.height-pt-pb)
    bounds = neck_band_bounds(original.size, plan, gen_mask, crop_pad)
    _, fy, _, fh = plan.box
    box = repair_crop_box(size, bounds, .6, min_top=fy-pt+.55*fh)
    x, y, side = box
    base = original.crop((x+pl, y+pt, x+pl+side, y+pt+side)).convert("RGB")
    tone_context = None
    if tone_enabled and current is not None:
        from .face_tone import prepare_tone_context
        tone_context = prepare_tone_context(fi.unpad_edges(original, crop_pad), fi.unpad_edges(current, crop_pad), unpadded_plan(plan, crop_pad, size))
    return dict(plan=plan, crop_pad=crop_pad, references=references, model_dir=model_dir,
                neck_offset=neck_offset, base_crop=base, base_crop_box=box, band_bounds=bounds, tone_context=tone_context)


def unpadded_plan(plan, crop_pad, size):
    pl, pt, *_ = _pad_ltrb(crop_pad)
    x, y, w, h = plan.box
    cx, cy, side = plan.crop
    return replace(plan, width=size[0], height=size[1], box=(x-pl, y-pt, w, h), crop=(cx-pl, cy-pt, side))


def prepare_repair_crop(qwen: Image.Image, context: FaceSeamContext, *, gap=False) -> RepairCrop:
    from .face_tone import skin_mask
    plan = unpadded_plan(context.plan, context.crop_pad, qwen.size)
    fx, fy, fw, fh = plan.box
    # A centered square can include the entire face even when its bbox is a neck band.
    left, top, side = repair_crop_box(qwen.size, context.band_bounds, .6 if gap else .3, min_top=fy+.55*fh)
    bx, by, bs = context.base_crop_box or (0, 0, 0)
    base = context.base_crop
    if base is None or base.size != (bs, bs) or not (bx <= left and by <= top and left+side <= bx+bs and top+side <= by+bs):
        raise SeamRepairUnavailable("base_crop_mismatch")
    skin = skin_mask(qwen, plan)[top:top+side, left:left+side]
    f = max(2, int(round(14*side/575)))
    skin = cv2.dilate(skin.astype(np.uint8), _ellipse(max(2, f//2))).astype(bool)
    return RepairCrop(qwen.convert("RGB").crop((left, top, left+side, top+side)),
                      (left, top, side), (fx-left, fy-top, fw, fh), qwen.size,
                      _pad_ltrb(context.crop_pad), base.crop((left-bx, top-by, left-bx+side, top-by+side)), skin)


def remap_repair_plan(plan: RepairPlan, source: RepairCrop, target: RepairCrop) -> RepairPlan:
    """Keep visual coordinates attached to source pixels when widening the neck crop."""
    sx, sy, ss = source.box
    tx, ty, ts = target.box
    polygons = [[[((x/1024*ss)+sx-tx)/ts*1024, ((y/1024*ss)+sy-ty)/ts*1024]
                 for x, y in polygon] for polygon in plan.damage_polygons]
    return replace(plan, damage_polygons=polygons, composition_polygons=[])


def prepare_edit_crop(crop: RepairCrop, plan: RepairPlan) -> RepairCrop:
    from .face_seam_geometry import api_mask
    return replace(crop, edit_mask=api_mask(crop, plan.damage_polygons))


def composite_repair(qwen: Image.Image, crop: RepairCrop, plan: RepairPlan, generated: Image.Image):
    from .face_seam_geometry import composition_alpha, align_generated
    alpha, support = composition_alpha(crop, plan.damage_polygons, plan.composition_polygons)
    aligned, alignment = align_generated(crop, generated, support)
    api = np.asarray(aligned, np.float32)
    current = np.asarray(crop.current, np.float32)
    pixels = np.clip(api*alpha[..., None]+current*(1-alpha[..., None])+.5, 0, 255).astype(np.uint8)
    out = qwen.convert("RGB").copy()
    x, y, side = crop.box
    out.paste(Image.fromarray(pixels), (x, y))
    full_support = np.zeros((qwen.height, qwen.width), bool)
    full_support[y:y+side, x:x+side] = support
    meta = pixel_change_meta(qwen, out, full_support)
    return out, dict(meta, crop=list(crop.box), region_px=int(support.sum()), alignment=alignment), full_support


def pixel_change_meta(before, after, support):
    diff = np.any(np.asarray(before.convert("RGB")) != np.asarray(after.convert("RGB")), axis=2)
    outside = int((diff & ~support).sum())
    if outside:
        raise SeamRepairUnavailable("outside_changed")
    return {"changed_pixels": int(diff.sum()), "outside_changed": outside}


def finish_repair(qwen, context, crop, plan, generated, *, tone_enabled=False):
    repaired, meta, support = composite_repair(qwen, crop, plan, generated)
    meta["seam_outside_changed"] = meta["outside_changed"]
    if tone_enabled:
        from .face_tone import apply_tone
        repaired, tone_meta, tone_support = apply_tone(repaired, context.tone_context, crop)
        meta["tone"] = tone_meta
        support |= tone_support
    meta.update(pixel_change_meta(qwen, repaired, support))
    meta["region_px"] = int(support.sum())
    return repaired, meta, support


async def call_sunburst_edit(settings, prompt: str, crop: RepairCrop, *, http_post=None, timeout: float = 180.0) -> SunburstResult:
    if getattr(settings, "face_seam_repair_model", SUNBURST_MODEL) != SUNBURST_MODEL:
        raise SeamRepairUnavailable("unsupported_model")
    key = getattr(settings, "openai_api_key", None)
    if not key:
        raise SeamRepairUnavailable("openai_key_missing")
    if crop.edit_mask is None or crop.edit_mask.mode != "RGBA" or crop.edit_mask.size != (1024, 1024):
        raise SeamRepairUnavailable("edit_mask_missing")
    data = {"model": SUNBURST_MODEL, "prompt": prompt, "size": "1024x1024", "quality": "high", "output_format": "png", "n": "1"}
    encoded = await run_cpu_bound(lambda: _png_bytes(crop.current.resize((1024, 1024), Image.LANCZOS)))
    mask = await run_cpu_bound(_png_bytes, crop.edit_mask, "RGBA")
    files = [("image[]", ("neck_crop.png", encoded, "image/png")), ("mask", ("neck_mask.png", mask, "image/png"))]
    started = time.perf_counter()
    if http_post is None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post("https://api.openai.com/v1/images/edits", headers={"Authorization": f"Bearer {key}"}, data=data, files=files)
    else:
        res = await http_post("https://api.openai.com/v1/images/edits", headers={"Authorization": f"Bearer {key}"}, data=data, files=files, timeout=timeout)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if getattr(res, "status_code", None) != 200:
        status = getattr(res, "status_code", None)
        if status == 429:
            log.warning("face seam repair image edit rejected with 429; likely rate limit or credit exhaustion")
        raise SeamRepairUnavailable(f"openai_http_{status}")
    usage = None
    raw = None
    try:
        payload = res.json()
        usage = _safe_usage(payload.get("usage"))
        rows = payload.get("data") or []
        b64 = rows[0].get("b64_json") if rows and isinstance(rows[0], dict) else None
        if not isinstance(b64, str) or not b64:
            raise ValueError("missing_image")
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise SeamRepairUnavailable("openai_bad_response") from exc
    finally:
        with image_usage.job_scope(stage="face_seam_repair"):
            image_usage.record(model=SUNBURST_MODEL, image_size="1024x1024", usage=usage, latency_ms=latency_ms, has_image=raw is not None)
    try:
        image = Image.open(BytesIO(raw)).convert("RGB")
        if image.size != (1024, 1024):
            raise ValueError("wrong_size")
    except (ValueError, TypeError, binascii.Error, OSError) as exc:
        raise SeamRepairUnavailable("openai_bad_response") from exc
    return SunburstResult(image, usage, latency_ms)


def _safe_meta(meta: dict) -> dict:
    allowed = {"mode", "attempted", "accepted", "reason", "crop", "changed_pixels", "outside_changed", "latency_ms", "usage", "cost_usd", "identity_before", "identity_after", "prompt_sha", "neck_offset", "planning", "composition_planning", "alignment", "failure_stage", "visual_check", "neck_collar_gap", "region_px", "seam_outside_changed", "tone"}
    return {k: cleaned for k, v in meta.items() if k in allowed and (cleaned := _json_safe(v)) is not None}


def _finite_score(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _png_roundtrip_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, "PNG")
    data = out.getvalue()
    decoded = Image.open(BytesIO(data)).convert("RGB")
    if not np.array_equal(np.asarray(decoded), np.asarray(image.convert("RGB"))):
        raise SeamRepairUnavailable("png_decode_mismatch")
    return data


def _identity_score(image: Image.Image, context: FaceSeamContext):
    if context.references is None:
        return None
    from . import face_identity as fi
    return fi.identity_score(image, context.references, model_dir=context.model_dir)


def _context_from_result(result, context: FaceSeamContext | None) -> FaceSeamContext | None:
    if context is not None:
        return context
    raw = getattr(result, "context", None)
    if not isinstance(raw, dict) or raw.get("plan") is None:
        return None
    return FaceSeamContext(**{key: value for key, value in raw.items() if key in FaceSeamContext.__dataclass_fields__})


async def repair_after_face_pass(settings, result, context: FaceSeamContext | None = None, *, request_context: dict | None = None, edit=None, identity_score=None):
    from . import face_identity as fi

    mode = str(getattr(settings, "face_seam_repair", "off") or "off").lower()
    mode = mode if mode in {"off", "shadow", "on"} else "off"
    meta = {"mode": mode, "attempted": False, "accepted": False}
    base_meta = dict(getattr(result, "meta", {}) or {})
    if mode == "off":
        return result
    if not getattr(result, "applied", False):
        return replace(result, meta={**base_meta, "face_seam": meta})
    context = _context_from_result(result, context)
    if context is None:
        meta["reason"] = "missing_context"
        return replace(result, meta={**base_meta, "face_seam": meta})
    stage = "crop"
    try:
        qwen = await run_cpu_bound(lambda: Image.open(BytesIO(result.image)).convert("RGB"))
        crop = await run_cpu_bound(prepare_repair_crop, qwen, context)
        meta["neck_offset"] = context.neck_offset
        from .face_seam_vision import plan_repair, plan_composition, verify_repair
        planning = {}
        meta["planning"] = planning
        stage = "planning"
        plan = await plan_repair(settings, crop, metadata=planning)
        meta["neck_collar_gap"] = plan.neck_collar_gap
        if plan.neck_collar_gap:
            wider = await run_cpu_bound(prepare_repair_crop, qwen, context, gap=True)
            plan = remap_repair_plan(plan, crop, wider)
            crop = wider
        stage = "edit_mask"
        crop = await run_cpu_bound(prepare_edit_crop, crop, plan)
        prompt = PROMPT_HEAD + plan.instruction.strip() + " " + PROMPT_TAIL
        meta["prompt_sha"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        meta["attempted"] = True
        stage = "image_edit"
        sunburst = await (edit or call_sunburst_edit)(settings, prompt, crop)
        meta.update(latency_ms=sunburst.latency_ms, usage=sunburst.usage)
        cost = estimate_cost(SUNBURST_MODEL, "1024x1024", sunburst.usage, has_image=True)
        if cost.usd is not None:
            meta["cost_usd"] = cost.usd
        stage = "composition_planning"
        composition_planning = {}
        meta["composition_planning"] = composition_planning
        polygons = await plan_composition(settings, crop, sunburst.image, plan, metadata=composition_planning)
        plan = replace(plan, composition_polygons=polygons)
        stage = "composition_alignment"
        repaired, cmeta, _ = await run_cpu_bound(finish_repair, qwen, context, crop, plan, sunburst.image,
                                               tone_enabled=getattr(settings, "face_tone_fix", "off") == "on")
        meta.update(cmeta)
        stage = "identity"
        scorer = identity_score or (lambda im: _identity_score(im, context))
        before = _finite_score(await run_cpu_bound(scorer, qwen))
        after = _finite_score(await run_cpu_bound(scorer, repaired))
        if before is not None:
            meta["identity_before"] = before
        if after is not None:
            meta["identity_after"] = after
        if before is None or after is None:
            meta["reason"] = "identity_missing"
            return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
        if round(float(before) - float(after), 6) > 0.02:
            meta["reason"] = "identity_drop"
            return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
        visual_check = {}
        meta["visual_check"] = visual_check
        x, y, side = crop.box
        candidate_crop = await run_cpu_bound(repaired.crop, (x, y, x + side, y + side))
        try:
            visual_check["passed"] = await verify_repair(settings, crop, candidate_crop, metadata=visual_check)
        except Exception:
            visual_check["reason"] = "unavailable"
        # Visual audit is observational; only pixel/identity integrity determines adoption.
        encoded = await run_cpu_bound(_png_roundtrip_bytes, repaired)
        if mode == "shadow":
            meta["reason"] = "shadow"
            return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
        meta.update(accepted=True, reason="ok")
        return fi.FacePassResult(encoded, "image/png", True, {**base_meta, "face_seam": _safe_meta(meta)}, getattr(result, "context", None))
    except Exception:
        meta["reason"] = "repair_unavailable"
        meta["failure_stage"] = stage
        return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
