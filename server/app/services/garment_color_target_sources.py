"""Read the first Front photograph and measure its sold garment."""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import uuid

import numpy as np
from PIL import Image

from .. import repo
from ..agents.garment_color_evidence import _frame
from ..agents.gemini_image import run_cpu_bound
from . import fal_api, garment_color_target as tone, garment_color_tune, qwen_flatlay
from .garment_color_consistency import linear_to_oklab

MAX_CUTOUT_EDGE = 640


def fronts_by_color(product: dict) -> dict[str, str | None]:
    return {
        str(color["id"]): next(
            (str(image["id"]) for image in color.get("images") or []
             if isinstance(image, dict) and image.get("slot") == "Front" and image.get("id")),
            None,
        )
        for color in product.get("colors") or []
        if isinstance(color, dict) and color.get("id") is not None
    }


async def read_front(conn, r2, *, user_id, project_id, front_id) -> bytes | None:
    try:
        asset_id = str(uuid.UUID(front_id))
    except (ValueError, TypeError, AttributeError):
        return None
    assets = await repo.get_uploaded_assets_for_project(conn, user_id, project_id, [asset_id])
    if not assets:
        return None
    try:
        return await asyncio.to_thread(r2.get_bytes, assets[0]["r2_key"])
    except Exception:
        return None


def rgba_pixels(data: bytes, max_edge=None) -> np.ndarray:
    image, _ = _frame(data, max_edge)
    return np.asarray(image)


def median_srgb(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    pixels = rgb[alpha >= 200]
    if not pixels.size:
        raise ValueError("empty garment")
    return np.median(pixels, axis=0)


def _auto(rgba: np.ndarray, alpha: np.ndarray, source: np.ndarray) -> dict | None:
    target_ab = qwen_flatlay._color_target(np.dstack((rgba[..., :3], alpha)), alpha >= 200,
                                         direction_only=True)
    if target_ab is None or not np.isfinite(target_ab).all():
        return None
    grid = np.arange(-100, 101)
    colors = tone.tone_linear(tone.srgb8_to_linear(source), chroma=grid[:, None], warmth=grid)
    distance = np.sum((linear_to_oklab(colors)[..., 1:] - target_ab) ** 2, axis=-1)
    chroma, warmth = np.unravel_index(np.argmin(distance), distance.shape)
    return {"chroma": int(grid[chroma]), "lightness": 0, "warmth": int(grid[warmth])}


class SourceUnavailable(ValueError):
    pass


def _cutout(rgba: np.ndarray, alpha: np.ndarray) -> tuple[Image.Image, np.ndarray]:
    image = Image.fromarray(rgba)
    alpha_image = Image.fromarray(alpha)
    bbox = alpha_image.getbbox()
    if bbox is None:
        raise ValueError("empty garment")
    image.putalpha(alpha_image)
    image = image.crop(bbox)
    image.thumbnail((MAX_CUTOUT_EDGE, MAX_CUTOUT_EDGE), Image.Resampling.LANCZOS)
    pixels = np.array(image)
    pixels[pixels[..., 3] == 0, :3] = 0
    return Image.fromarray(pixels), median_srgb(pixels[..., :3], pixels[..., 3])


async def measure(settings, data: bytes | None, *, clothing_type, sub_category=None):
    try:
        rgba = await run_cpu_bound(rgba_pixels, data, fal_api.MAX_PX) if data else None
        if rgba is None:
            raise ValueError
    except Exception as exc:
        raise SourceUnavailable("invalid_image") from exc
    try:
        alpha = await asyncio.wait_for(
            garment_color_tune.segment_seller_garment(
                settings, data, rgba, clothing_type=clothing_type, sub_category=sub_category,
                protect_matching=False),
            timeout=fal_api.TIMEOUT_S,
        )
        image, source = await run_cpu_bound(_cutout, rgba, alpha)
    except Exception as exc:
        raise SourceUnavailable("mask_failed") from exc
    return image, source, rgba, alpha


def _encode_preview(image, source, rgba, alpha) -> dict:
    out = BytesIO()
    image.save(out, "PNG")
    try:
        auto = _auto(rgba, alpha, source)
    except Exception:
        auto = None
    return {"status": "ready", "reason": None,
            "cutoutPng": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii"),
            "sourceSrgb": source.tolist(), "auto": auto}


async def preview(settings, data, *, clothing_type, sub_category=None) -> dict:
    try:
        measured = await measure(settings, data, clothing_type=clothing_type, sub_category=sub_category)
    except SourceUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc), "cutoutPng": None,
                "sourceSrgb": None, "auto": None}
    return await run_cpu_bound(_encode_preview, *measured)
