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
from PIL import Image, ImageDraw, ImageFilter

from .. import image_usage
from .gemini_image import run_cpu_bound
from .image_cost import estimate_cost

SUNBURST_MODEL = "gpt-image-2.5-sunburst"
PROMPT_HEAD = "Use case: precise-object-edit. This is a surgical photo restoration, not a new photograph. The input is a square crop of an existing studio fashion photograph. Keep the exact crop, subject placement, scale, geometry and camera view. Only repair the damaged neck/collar compositing inside the transparent mask.\n\n"
PROMPT_TAIL = "\n\nCopy every intact feature exactly. Preserve the person's identity, face, jawline, hair, expression, pose, body proportions, clothing, lighting, texture, sharpness and neutral background. Match existing skin and cloth texture; no beauty retouch, no smoothing, no new detail or design. Eliminate abrupt cutouts, discontinuous edges, duplicated cloth fragments and pasted-skin seams. Make this look like the same unedited photograph with only the neck compositing defects corrected. All opaque mask areas must remain unchanged. Return a single square photograph, same framing. No text or borders."
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


@dataclass(frozen=True)
class RepairCrop:
    current: Image.Image
    box: tuple[int, int, int]
    face_box: tuple[float, float, float, float]
    final_size: tuple[int, int]
    crop_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
    base: Image.Image | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RepairPlan:
    has_defect: bool
    instruction: str
    damage_polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    composition_polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    align: bool = True
    feather: int = 7
    observations: list[str] = field(default_factory=list)
    neck_collar_gap: bool = False


@dataclass(frozen=True)
class RepairMasks:
    api_mask_rgba: Image.Image
    composition_mask: np.ndarray
    support_mask: np.ndarray


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


def _clamp_square(left: float, top: float, side: float, width: int, height: int) -> tuple[int, int, int]:
    side_i = max(16, min(int(round(side)), width, height))
    return (int(np.clip(round(left), 0, width - side_i)), int(np.clip(round(top), 0, height - side_i)), side_i)


def repair_crop_box(final_size, plan, crop_pad=None):
    pad_l, pad_t, *_ = _pad_ltrb(crop_pad)
    bx, by, bw, bh = (float(v) for v in plan.box)
    bx -= pad_l
    by -= pad_t
    side = max(3.2 * bw, 2.8 * bh)
    return _clamp_square(bx + bw / 2 - side / 2, by - .1 * bh, side, *final_size)


def capture_repair_context(original, plan, *, crop_pad=None, references=None, model_dir=None, neck_offset=None):
    """Retain only the aligned neck ROI; never retain the full original image."""
    pl, pt, pr, pb = _pad_ltrb(crop_pad)
    box = repair_crop_box((original.width - pl - pr, original.height - pt - pb), plan, crop_pad)
    x, y, side = box
    base = original.crop((x + pl, y + pt, x + pl + side, y + pt + side)).convert("RGB")
    return dict(plan=plan, crop_pad=crop_pad, references=references, model_dir=model_dir,
                neck_offset=neck_offset, base_crop=base, base_crop_box=box)


def prepare_repair_crop(qwen: Image.Image, context: FaceSeamContext) -> RepairCrop:
    left, top, side = repair_crop_box(qwen.size, context.plan, context.crop_pad)
    pad_l, pad_t, *_ = _pad_ltrb(context.crop_pad)
    bx, by, bw, bh = (float(v) for v in context.plan.box)
    base = context.base_crop
    if base is not None and (context.base_crop_box != (left, top, side) or base.size != (side, side)):
        raise SeamRepairUnavailable("base_crop_mismatch")
    return RepairCrop(qwen.convert("RGB").crop((left, top, left + side, top + side)),
                      (left, top, side), (bx - pad_l - left, by - pad_t - top, bw, bh),
                      qwen.size, _pad_ltrb(context.crop_pad), base)



def _face_protected_mask(crop: RepairCrop) -> np.ndarray:
    fx, fy, fw, fh = crop.face_box
    mask = Image.new("L", crop.current.size, 0)
    box = (fx + 0.12 * fw, fy - 0.06 * fh, fx + 0.88 * fw, fy + 1.03 * fh)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    protected = np.asarray(mask) > 0
    protected[:max(0, int(fy + .55 * fh)), :] = True
    return protected


def _raster(size: tuple[int, int], polygons: list[list[tuple[float, float]]]) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), np.uint8)
    for poly in polygons:
        points = np.asarray([[(int(round(x)), int(round(y))) for x, y in poly]], dtype=np.int32)
        cv2.fillPoly(mask, points, 255)
    return mask > 0


def _rgba_from_damage(damage: np.ndarray) -> Image.Image:
    alpha = np.where(damage, 0, 255).astype(np.uint8)
    rgba = Image.new("RGBA", (damage.shape[1], damage.shape[0]), (255, 255, 255, 255))
    rgba.putalpha(Image.fromarray(alpha, "L"))
    return rgba


def build_repair_masks(crop: RepairCrop, plan: RepairPlan) -> RepairMasks:
    if not plan.has_defect:
        empty = np.zeros((crop.current.height, crop.current.width), bool)
        return RepairMasks(Image.new("RGBA", crop.current.size, (255, 255, 255, 255)), empty, empty)
    comp = _raster(crop.current.size, plan.composition_polygons)
    damage = _raster(crop.current.size, plan.damage_polygons)
    face = _face_protected_mask(crop)
    comp &= ~face
    damage &= ~face
    if not damage.any() or not comp.any():
        raise SeamRepairUnavailable("empty_mask")
    if (damage & ~comp).any():
        raise SeamRepairUnavailable("damage_outside_composition")
    api_mask = _rgba_from_damage(damage)
    api_damage = np.asarray(api_mask.getchannel("A")) == 0
    if (api_damage & ~comp).any():
        raise SeamRepairUnavailable("damage_outside_composition")
    return RepairMasks(api_mask, comp, comp | damage)


def _dis_flow(current: np.ndarray, generated: np.ndarray) -> np.ndarray:
    cur = cv2.cvtColor(current, cv2.COLOR_RGB2GRAY)
    gen = cv2.cvtColor(generated, cv2.COLOR_RGB2GRAY)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    return dis.calc(cur, gen, None)


def _smooth_unknown_flow(flow: np.ndarray, mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    known = ~cv2.dilate(mask.astype(np.uint8), np.ones((17, 17), np.uint8)).astype(bool)
    small_flow = cv2.resize(np.clip(flow, -48, 48), (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    small_known = cv2.resize(known.astype(np.uint8), (small_flow.shape[1], small_flow.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
    cur = small_flow.copy()
    cur[~small_known] = 0
    fixed = cur.copy()
    for _ in range(1600):
        blurred = cv2.blur(cur, (3, 3))
        cur[~small_known] = blurred[~small_known]
        cur[small_known] = fixed[small_known]
    up = cv2.resize(cur, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(up, (0, 0), 5)


def _sample_bilinear(src: np.ndarray, flow: np.ndarray) -> np.ndarray:
    h, w = src.shape[:2]
    yy, xx = np.indices((h, w), dtype=np.float32)
    sx = np.clip(xx + flow[..., 0], 0, w - 1)
    sy = np.clip(yy + flow[..., 1], 0, h - 1)
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    wx = (sx - x0)[..., None]
    wy = (sy - y0)[..., None]
    top = src[y0, x0].astype(np.float32) * (1.0 - wx) + src[y0, x1].astype(np.float32) * wx
    bottom = src[y1, x0].astype(np.float32) * (1.0 - wx) + src[y1, x1].astype(np.float32) * wx
    sampled = top * (1.0 - wy) + bottom * wy
    return np.clip(np.floor(sampled + 0.5), 0, 255).astype(np.uint8)


def align_generated_crop(current: Image.Image, generated: Image.Image, composition_mask: np.ndarray, *, enabled: bool) -> Image.Image:
    raw = np.asarray(generated.convert("RGB"), np.uint8)
    side = current.size[0]
    gen = cv2.resize(raw, (side, side), interpolation=cv2.INTER_LANCZOS4)
    if not enabled:
        return Image.fromarray(gen)
    cur = np.asarray(current.convert("RGB"), np.uint8)
    flow = _smooth_unknown_flow(_dis_flow(cur, gen), composition_mask.astype(bool))
    return Image.fromarray(_sample_bilinear(gen, flow))


def composite_repair(qwen: Image.Image, crop: RepairCrop, plan: RepairPlan, generated: Image.Image) -> tuple[Image.Image, dict]:
    masks = build_repair_masks(crop, plan)
    aligned = align_generated_crop(crop.current, generated, masks.composition_mask, enabled=plan.align)
    binary = masks.composition_mask.astype(np.uint8) * 255
    soft = np.asarray(Image.fromarray(binary).filter(ImageFilter.GaussianBlur(plan.feather)), np.float32)
    alpha = np.where(masks.composition_mask, np.clip((soft - 128.0) / 110.0, 0.0, 1.0), 0.0)
    cur = np.asarray(crop.current.convert("RGB"), np.float32)
    gen = np.asarray(aligned.convert("RGB"), np.float32)
    repaired_crop = Image.fromarray(np.clip(cur * (1.0 - alpha[..., None]) + gen * alpha[..., None] + 0.5, 0, 255).astype(np.uint8))
    out = qwen.convert("RGB").copy()
    left, top, side = crop.box
    out.paste(repaired_crop, (left, top, left + side, top + side))
    full_support = np.zeros((qwen.height, qwen.width), bool)
    full_support[top:top + side, left:left + side] = masks.composition_mask
    diff = np.abs(np.asarray(qwen.convert("RGB"), np.int16) - np.asarray(out, np.int16)).max(axis=2) > 0
    outside = int((diff & ~full_support).sum())
    if outside:
        raise SeamRepairUnavailable("outside_changed")
    return out, {"crop": list(crop.box), "feather": int(plan.feather), "align": bool(plan.align), "changed_pixels": int(diff.sum()), "outside_changed": outside}


async def call_sunburst_edit(settings, prompt: str, crop: RepairCrop, masks: RepairMasks, *, http_post=None, timeout: float = 180.0) -> SunburstResult:
    if getattr(settings, "face_seam_repair_model", SUNBURST_MODEL) != SUNBURST_MODEL:
        raise SeamRepairUnavailable("unsupported_model")
    key = getattr(settings, "openai_api_key", None)
    if not key:
        raise SeamRepairUnavailable("openai_key_missing")
    data = {"model": SUNBURST_MODEL, "prompt": prompt, "size": "1024x1024", "quality": "high", "output_format": "png", "n": "1"}
    files = [("image[]", ("neck_crop.png", _png_bytes(crop.current), "image/png")), ("mask", ("neck_mask.png", _png_bytes(masks.api_mask_rgba, "RGBA"), "image/png"))]
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
    allowed = {"mode", "attempted", "accepted", "reason", "crop", "feather", "align", "changed_pixels", "outside_changed", "latency_ms", "usage", "cost_usd", "identity_before", "identity_after", "prompt_sha", "neck_offset", "planning", "visual_check", "neck_collar_gap"}
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
    return FaceSeamContext(raw["plan"], raw.get("crop_pad"), raw.get("references"), raw.get("model_dir"), raw.get("neck_offset"), raw.get("base_crop"), raw.get("base_crop_box"))


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
    try:
        qwen = await run_cpu_bound(lambda: Image.open(BytesIO(result.image)).convert("RGB"))
        crop = await run_cpu_bound(prepare_repair_crop, qwen, context)
        meta["neck_offset"] = context.neck_offset
        from .face_seam_vision import plan_repair, verify_repair
        planning = {}
        meta["planning"] = planning
        plan = await plan_repair(settings, crop, metadata=planning)
        meta["neck_collar_gap"] = plan.neck_collar_gap
        masks = await run_cpu_bound(build_repair_masks, crop, plan)
        prompt = PROMPT_HEAD + plan.instruction.strip() + PROMPT_TAIL
        meta["prompt_sha"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        meta["attempted"] = True
        sunburst = await (edit or call_sunburst_edit)(settings, prompt, crop, masks)
        repaired, cmeta = await run_cpu_bound(composite_repair, qwen, crop, plan, sunburst.image)
        meta.update(cmeta, latency_ms=sunburst.latency_ms, usage=sunburst.usage)
        cost = estimate_cost(SUNBURST_MODEL, "1024x1024", sunburst.usage, has_image=True)
        if cost.usd is not None:
            meta["cost_usd"] = cost.usd
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
        if not await verify_repair(settings, crop, candidate_crop, metadata=visual_check):
            meta["reason"] = "visual_reject"
            return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
        encoded = await run_cpu_bound(_png_roundtrip_bytes, repaired)
        if mode == "shadow":
            meta["reason"] = "shadow"
            return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
        meta.update(accepted=True, reason="ok")
        return fi.FacePassResult(encoded, "image/png", True, {**base_meta, "face_seam": _safe_meta(meta)}, getattr(result, "context", None))
    except Exception:
        meta["reason"] = "repair_unavailable"
        return replace(result, meta={**base_meta, "face_seam": _safe_meta(meta)})
