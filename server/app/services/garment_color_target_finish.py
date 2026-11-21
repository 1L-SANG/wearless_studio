"""Apply one seller-selected tone to the completed cuts of each color."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image, ImageOps

from ..agents.gemini_image import InlineImage, run_cpu_bound
from ..r2 import IMMUTABLE_CACHE, PRIVATE_NO_STORE
from . import fal_api, garment_color as storage, garment_color_target as tone
from . import garment_color_target_sources as sources, garment_color_tune
from .garment_color_consistency import _encode_srgb, linear_to_oklab

MAX_LIGHTNESS_SHIFT = 0.10
MAX_CHROMA_SHIFT = 0.08


def _best_exposure(lab, target, *, color):
    if color:
        numerator = np.sum(lab * target, axis=-1)
        denominator = np.sum(lab * lab, axis=-1)
    else:
        numerator, denominator = target[0], lab[..., 0]
    gain = np.divide(numerator, denominator, out=np.ones_like(denominator), where=denominator > 0)
    return np.clip(gain, 2.0 ** -6, 2.0 ** 6)


def solve(median: np.ndarray, target: np.ndarray, sliders: dict) -> tuple[dict, bool]:
    """Fit active Oklab coordinates, then scale all parameters by the same factor."""
    light = bool(sliders["lightness"])
    color = bool(sliders["chroma"] or sliders["warmth"])
    target_lab = linear_to_oklab(target)
    center_lab = linear_to_oklab(median)
    params = {axis: 0.0 for axis in tone.ZERO_SLIDERS}
    if color:
        # Nonnegative saturation and channel gains. Refine a broad grid in place.
        bounds = ((-250.0, 750.0), (-1000.0, 1000.0))
        chroma, warmth = 0.0, 0.0
        widths = [max(abs(low), abs(high)) for low, high in bounds]
        for _ in range(7):
            chromas, warmths = (
                np.unique(np.append(np.linspace(max(low, center - width), min(high, center + width), 21), center))
                for (low, high), center, width in zip(bounds, (chroma, warmth), widths)
            )
            lab = linear_to_oklab(tone.tone_linear(median, chroma=chromas[:, None], warmth=warmths))
            if light:
                lab *= _best_exposure(lab, target_lab, color=True)[..., None]
            active = slice(None) if light else slice(1, None)
            errors = np.sum((lab[..., active] - target_lab[active]) ** 2, axis=-1)
            # Prefer smaller adjustments if the source cannot distinguish candidates.
            errors += 1e-13 * (chromas[:, None] ** 2 + warmths ** 2)
            i, j = np.unravel_index(np.argmin(errors), errors.shape)
            chroma, warmth = float(chromas[i]), float(warmths[j])
            widths = [max(np.diff(chromas), default=0) * 2, max(np.diff(warmths), default=0) * 2]
        params.update(chroma=chroma, warmth=warmth)
    if light:
        lab = linear_to_oklab(tone.tone_linear(median, **params))
        # Exposure multiplies all linear channels equally. Oklab scales by its cube root.
        params["lightness"] = float(600 * np.log2(_best_exposure(lab, target_lab, color=color)))

    def within_cap(scale):
        shifted = tone.tone_linear(median, **{axis: value * scale for axis, value in params.items()})
        delta = linear_to_oklab(shifted) - center_lab
        return abs(delta[0]) <= MAX_LIGHTNESS_SHIFT and np.linalg.norm(delta[1:]) <= MAX_CHROMA_SHIFT

    if within_cap(1):
        return params, False
    low, high = 0.0, 1.0
    for _ in range(34):
        mid = (low + high) / 2
        if within_cap(mid):
            low = mid
        else:
            high = mid
    return {axis: value * low for axis, value in params.items()}, True


def _png(pixels: np.ndarray) -> bytes:
    out = BytesIO()
    Image.fromarray(pixels).save(out, "PNG")
    return out.getvalue()


def render(rgba: np.ndarray, alpha: np.ndarray, median: np.ndarray, params: dict) -> tuple[bytes | None, list]:
    if rgba.dtype != np.uint8 or alpha.dtype != np.uint8 or alpha.shape != rgba.shape[:2] or rgba.shape[-1] != 4:
        raise ValueError("invalid garment frame")
    center = linear_to_oklab(tone.srgb8_to_linear(median))
    if center[0] <= 0:
        return None, median.tolist()
    center_chroma = center[1:] / center[0]
    result = rgba.copy()
    # Bound work arrays when closeups contain millions of eligible pixels.
    rows = max(1, 65536 // rgba.shape[1])
    for start in range(0, rgba.shape[0], rows):
        mask = alpha[start:start + rows]
        inside = mask > 0
        pixels = result[start:start + rows, :, :3]
        linear = tone.srgb8_to_linear(pixels[inside])
        lab = linear_to_oklab(linear)
        chroma = np.divide(lab[:, 1:], lab[:, :1], out=np.zeros_like(lab[:, 1:]),
                           where=lab[:, :1] > 0)
        distance = np.linalg.norm(chroma - center_chroma, axis=-1) * center[0]
        protection = np.clip((0.08 - distance) / (0.08 - 0.04), 0, 1)
        weight = (mask[inside] / 255 * protection)[:, None]
        corrected = linear + weight * (tone.tone_linear(linear, **params) - linear)
        if not np.isfinite(corrected).all():
            raise ValueError("nonfinite garment pixels")
        pixels[inside] = np.rint(_encode_srgb(np.clip(corrected, 0, 1))).astype(np.uint8)
    after = sources.median_srgb(result[..., :3], alpha).tolist()
    if np.array_equal(rgba, result):
        return None, after
    png = _png(result)
    with Image.open(BytesIO(png)) as image:
        checked = np.asarray(image)
        if (checked.shape != rgba.shape or not np.array_equal(checked, result)
                or not np.array_equal(checked[alpha == 0], rgba[alpha == 0])
                or not np.array_equal(checked[..., 3], rgba[..., 3])):
            raise ValueError("garment self-check failed")
    return png, after


def _read_cut_pixels(data: bytes) -> np.ndarray:
    rgba = sources.rgba_pixels(data)
    with Image.open(BytesIO(data)) as image:
        if image.info.get("icc_profile") or image.mode not in {"RGB", "RGBA", "L", "LA", "P", "1"}:
            original = np.asarray(ImageOps.exif_transpose(image).convert("RGBA"))
            if not np.array_equal(rgba, original):
                raise ValueError("encoded color cannot be preserved")
    return rgba


def _cut_record(status, reason=None, before=None, after=None):
    return {"status": status, "reason": reason, "beforeSrgb": before, "afterSrgb": after}


async def _measure_cut(app, job, asset, *, clothing_type, sub_category, slots):
    metadata = asset.get("metadata") or {}
    recipe = (metadata.get("generation") or {}).get("recipe") or {}
    worn = metadata.get("cut_type") != "product"
    require_matching = False
    if worn and recipe:
        try:
            side = await garment_color_tune._matching_side(app.state.pool, job, recipe, clothing_type)
            require_matching = side is not None and recipe.get("shot") == "full"
        except Exception:
            return None, "matching_unverified"
    async with slots:
        try:
            data = await asyncio.to_thread(app.state.r2.get_bytes, asset["key"])
            rgba = await run_cpu_bound(_read_cut_pixels, data)
        except Exception:
            return None, "invalid_image"
        try:
            alpha = await asyncio.wait_for(
                garment_color_tune.segment_seller_garment(
                    app.state.settings, data, rgba, clothing_type=clothing_type,
                    sub_category=sub_category, protect_matching=worn, require_matching=require_matching,
                    detail=bool(metadata.get("detail_recipe"))),
                timeout=fal_api.TIMEOUT_S,
            )
            center = await run_cpu_bound(sources.median_srgb, rgba[..., :3], alpha)
            return (data, rgba, alpha, center), None
        except garment_color_tune.MatchingUnverified:
            return None, "matching_unverified"
        except Exception:
            return None, "mask_failed"


@dataclass
class StagedCut:
    asset: dict
    masters: list[dict]
    color_id: str
    block_ids: list[str]
    before: list
    after: list
    metrics: dict | None


async def _stage_cut(app, job, original, original_data, corrected, alpha, params, *, stored_outputs):
    cut_type = (original.get("metadata") or {}).get("cut_type")
    original_master = await storage.stage_output(
        app, job, InlineImage(original["mime"], original_data),
        metadata={"internalGarmentColorTarget": "original", "cut_type": cut_type},
        cache=PRIVATE_NO_STORE, stored_outputs=stored_outputs,
    )
    mask_png = await run_cpu_bound(_png, alpha)
    mask = await storage.stage_output(
        app, job, InlineImage("image/png", mask_png),
        metadata={"internalGarmentColorTarget": "mask", "cut_type": cut_type},
        cache=PRIVATE_NO_STORE, stored_outputs=stored_outputs,
    )
    metadata = {**(original.get("metadata") or {}), "colorTarget": {
        "originalAssetId": original_master["asset_id"], "maskAssetId": mask["asset_id"],
        "params": params, "toneVersion": tone.TONE_VERSION,
    }}
    final = await storage.stage_output(
        app, job, InlineImage("image/png", corrected), metadata=metadata,
        cache=IMMUTABLE_CACHE, stored_outputs=stored_outputs,
    )
    # Storyboard ancestry keeps the generated cut ID. The preview key expires separately.
    return {**original, **final, "asset_id": original["asset_id"]}, [original_master, mask]


async def stage(app, job, *, sliders, fronts, block_colors, assets, results, clothing_type, sub_category,
                stored_outputs, measure_contrast=None):
    by_url = {f"/v1/assets/{asset['asset_id']}/file": asset for asset in assets}
    mappings = defaultdict(lambda: defaultdict(list))
    for result in results:
        color_id = block_colors.get(str(result.get("blockId")))
        asset = by_url.get(result.get("imageUrl"))
        if asset is not None:
            mappings[asset["asset_id"]][color_id].append(str(result["blockId"]))
    report = {color_id: {"target": None, "median": None, "params": None, "capped": False, "cuts": {}}
              for color_id in sliders}
    grouped = defaultdict(list)
    for asset in assets:
        color_blocks = mappings[asset["asset_id"]]
        for color_id, blocks in color_blocks.items():
            if color_id not in sliders:
                continue
            if (asset.get("metadata") or {}).get("facemarket_real_derived") is True:
                reason = "real_model"
            elif len(color_blocks) > 1:
                reason = "conflicting_color"
            else:
                grouped[color_id].append((asset, blocks))
                continue
            report[color_id]["cuts"].update({block: _cut_record("skipped", reason) for block in blocks})

    pending = {}
    settings = app.state.settings
    slots = asyncio.Semaphore(getattr(settings, "detail_cut_concurrency", 0) or 3)
    for color_id, candidates in grouped.items():
        group = report[color_id]
        cuts = group["cuts"]
        try:
            async with app.state.pool.connection() as conn:
                data = await sources.read_front(conn, app.state.r2, user_id=job["user_id"],
                                                project_id=job["project_id"], front_id=fronts[color_id])
            measured_source = await sources.measure(settings, data, clothing_type=clothing_type,
                                                     sub_category=sub_category)
            target = tone.tone_linear(tone.srgb8_to_linear(measured_source[1]), **sliders[color_id])
            del measured_source, data
            group["target"] = target.tolist()
        except Exception as exc:
            reason = str(exc) if isinstance(exc, sources.SourceUnavailable) else "source_unavailable"
            cuts.update({block: _cut_record("skipped", reason) for _, blocks in candidates for block in blocks})
            continue
        measurements = await asyncio.gather(*(
            _measure_cut(app, job, asset, clothing_type=clothing_type, sub_category=sub_category, slots=slots)
            for asset, _ in candidates
        ))
        centers = [sample[3] for sample, _ in measurements if sample is not None]
        params = None
        if centers:
            try:
                median = tone.srgb8_to_linear(np.median(centers, axis=0))
                params, capped = await run_cpu_bound(solve, median, target, sliders[color_id])
                group.update(median=median.tolist(), params=params, capped=capped)
            except Exception:
                pass
        for (original, blocks), (sample, reason) in zip(candidates, measurements, strict=True):
            before = after = None
            if sample is None or params is None:
                status = "skipped"
                reason = reason or "calculation_failed"
            else:
                original_data, rgba, alpha, center = sample
                before = center.tolist()
                try:
                    png, after = await run_cpu_bound(render, rgba, alpha, center, params)
                    if png is None:
                        status, reason = "skipped", "no_change"
                    else:
                        metrics = await measure_contrast(png, blocks) if measure_contrast else None
                        final, masters = await _stage_cut(app, job, original, original_data, png, alpha, params,
                                                          stored_outputs=stored_outputs)
                        if metrics is not None:
                            final.update(width=metrics["width"], height=metrics["height"])
                        pending[original["asset_id"]] = StagedCut(
                            final, masters, color_id, blocks, before, after, metrics,
                        )
                        continue
                except Exception:
                    status, reason, after = "reverted", "render_or_storage_failed", before
            cuts.update({block: _cut_record(status, reason, before, after) for block in blocks})
    return pending, report


async def persist(conn, job, pending, report, cut_assets, cut_results):
    for index, original in enumerate(cut_assets):
        staged = pending.get(original["asset_id"])
        if staged is None:
            continue
        try:
            async with conn.transaction():
                await storage.persist_masters(conn, job=job, outputs=[{"internal_masters": staged.masters}])
        except Exception:
            status = _cut_record("reverted", "internal_storage_failed", staged.before, staged.before)
        else:
            cut_assets[index] = staged.asset
            if staged.metrics is not None:
                for result in cut_results:
                    if str(result.get("blockId")) not in staged.block_ids:
                        continue
                    for key in ("hookTitleColor", "overlayTextColors", "overlayTextLines",
                                "overlayTextPositions", "overlayTextPlates"):
                        result.pop(key, None)
                    result.update(staged.metrics)
            status = _cut_record("applied", before=staged.before, after=staged.after)
        report[staged.color_id]["cuts"].update({block: status for block in staged.block_ids})
