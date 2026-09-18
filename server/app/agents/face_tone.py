from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from . import face_identity as fi

CAP_UP = 12.0
CAP_DOWN = 4.0
CAP_AB = 2.0
MIN_REF_RATIO = 0.05
MIN_SAMPLE_PIXELS = 200


@dataclass(frozen=True)
class ToneContext:
    plan: Any
    raw_changed: np.ndarray = field(repr=False)
    target_lab: tuple[float, float, float] | None
    reference: str
    body_ratio: float

    @property
    def source(self) -> str:
        return self.reference


def _rgb(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), np.uint8)


def _lab_median(image: Image.Image, mask: np.ndarray) -> tuple[float, float, float] | None:
    if not bool(mask.any()):
        return None
    lab = cv2.cvtColor(_rgb(image), cv2.COLOR_RGB2LAB).astype(np.float32)
    return tuple(float(np.median(lab[..., c][mask])) for c in range(3))


def _geo_mask(shape: tuple[int, int], plan) -> np.ndarray:
    x, y, w, h = (float(v) for v in plan.box)
    H, W = shape
    geo = np.zeros((H, W), bool)

    y0 = int(max(0, y - 0.2 * h))
    y1 = int(min(H, y + h))
    x0 = int(max(0, x - 0.3 * w))
    x1 = int(min(W, x + 1.3 * w))
    geo[y0:y1, x0:x1] = True

    y0 = int(max(0, y + h))
    y1 = int(min(H, y + 1.55 * h))
    x0 = int(max(0, x - 0.1 * w))
    x1 = int(min(W, x + 1.1 * w))
    geo[y0:y1, x0:x1] = True
    return geo


def _erode(mask: np.ndarray, size: int) -> np.ndarray:
    return cv2.erode(mask.astype(np.uint8), np.ones((size, size), np.uint8)).astype(bool)


def _blur_changed(mask: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3) > 0.25


def skin_mask(image: Image.Image, plan) -> np.ndarray:
    arr = _rgb(image).astype(np.float32)
    x, y, side = (int(v) for v in plan.crop)
    crop = image.convert("RGB").crop((x, y, x + side, y + side)).resize((fi.CROP, fi.CROP), Image.LANCZOS)
    ref = fi._skin_reference(np.asarray(crop, np.float32), plan)
    if ref is None:
        return np.zeros(arr.shape[:2], bool)
    return fi._skinness(arr, ref) > 0.5


def prepare_tone_context(original: Image.Image, current: Image.Image, plan) -> ToneContext:
    orig = original.convert("RGB")
    cur = current.convert("RGB")
    if orig.size != cur.size:
        raise ValueError("tone images must share coordinates")

    sk_o = skin_mask(orig, plan)
    changed = np.abs(_rgb(cur).astype(np.float32) - _rgb(orig).astype(np.float32)).max(axis=2) > 6
    x, y, w, h = (float(v) for v in plan.box)
    below = np.zeros(sk_o.shape, bool)
    below[int(min(sk_o.shape[0], y + 1.6 * h)):, :] = True
    body = _erode(sk_o & below, 9)
    body_ratio = float(body.sum()) / max(1.0, w * h)

    if body_ratio >= MIN_REF_RATIO:
        target = _lab_median(orig, body)
        reference = "body"
    else:
        face_neck = _erode(sk_o & _geo_mask(sk_o.shape, plan), 7)
        target = _lab_median(orig, face_neck)
        reference = "face_neck" if target is not None else "none"

    return ToneContext(plan=plan, raw_changed=changed, target_lab=target, reference=reference, body_ratio=round(body_ratio, 6))


def _changed_with_repair_crop(final: Image.Image, context: ToneContext, repair_crop) -> np.ndarray:
    changed = np.array(context.raw_changed, dtype=bool, copy=True)
    if repair_crop is None:
        return _blur_changed(changed)
    base = getattr(repair_crop, "base", None)
    box = getattr(repair_crop, "box", None)
    if base is None or box is None or len(box) < 3:
        return _blur_changed(changed)
    left, top, side = (int(v) for v in box[:3])
    if side <= 0:
        return _blur_changed(changed)
    right = min(final.width, left + side)
    bottom = min(final.height, top + side)
    left = max(0, left)
    top = max(0, top)
    if right <= left or bottom <= top:
        return _blur_changed(changed)
    final_crop = final.convert("RGB").crop((left, top, right, bottom))
    base_crop = base.convert("RGB")
    if base_crop.size != final_crop.size:
        base_crop = base_crop.resize(final_crop.size, Image.LANCZOS)
    local = np.abs(_rgb(final_crop).astype(np.float32) - _rgb(base_crop).astype(np.float32)).max(axis=2) > 6
    changed[top:bottom, left:right] = local
    return _blur_changed(changed)


def _empty_support(size: tuple[int, int]) -> np.ndarray:
    return np.zeros((size[1], size[0]), bool)


def _noop(final: Image.Image, context: ToneContext, reason: str, support: np.ndarray | None = None) -> tuple[Image.Image, dict, np.ndarray]:
    if support is None:
        support = _empty_support(final.size)
    return final, {
        "ok": False,
        "reason": reason,
        "reference": context.reference,
        "body_ratio": context.body_ratio,
        "raw": None,
        "applied": [0.0, 0.0, 0.0],
        "support_pixels": int(support.sum()),
    }, support


def apply_tone(final: Image.Image, context: ToneContext, repair_crop) -> tuple[Image.Image, dict, np.ndarray]:
    final_rgb = final.convert("RGB")
    if context.target_lab is None:
        return _noop(final_rgb, context, "missing_reference")

    changed = _changed_with_repair_crop(final_rgb, context, repair_crop)
    sk_f = skin_mask(final_rgb, context.plan)
    geo = _geo_mask(sk_f.shape, context.plan)
    support = sk_f & changed & geo
    sample = _erode(support, 7)
    if int(sample.sum()) < MIN_SAMPLE_PIXELS:
        return _noop(final_rgb, context, "empty_support", support)

    current_lab = _lab_median(final_rgb, sample)
    if current_lab is None:
        return _noop(final_rgb, context, "empty_support", support)
    raw = np.asarray(context.target_lab, np.float32) - np.asarray(current_lab, np.float32)
    shift = raw.copy()
    shift[0] = float(np.clip(shift[0], -CAP_DOWN, CAP_UP))
    shift[1:] = np.clip(shift[1:], -CAP_AB, CAP_AB)

    alpha = cv2.GaussianBlur(support.astype(np.float32), (0, 0), 6)[..., None]
    alpha[~support] = 0.0
    lab = cv2.cvtColor(_rgb(final_rgb), cv2.COLOR_RGB2LAB).astype(np.float32)
    fixed_lab = np.clip(lab + shift * alpha, 0, 255).astype(np.uint8)
    converted = cv2.cvtColor(fixed_lab, cv2.COLOR_LAB2RGB)
    out = _rgb(final_rgb).copy()
    out[support] = converted[support]
    meta = {
        "ok": True,
        "reference": context.reference,
        "body_ratio": context.body_ratio,
        "raw": [round(float(v), 3) for v in raw],
        "applied": [round(float(v), 3) for v in shift],
        "support_pixels": int(support.sum()),
        "sample_pixels": int(sample.sum()),
    }
    return Image.fromarray(out), meta, support
