"""QC를 지난 다른 색상 컷의 작은 색 오차만 맞춘다. 실패하면 AI 결과를 그대로 쓴다."""
from __future__ import annotations

import asyncio
import hashlib
import io

import numpy as np
from PIL import Image, ImageOps

from .. import repo
from ..agents import garment_color_evidence
from ..agents.gemini_image import InlineImage, run_cpu_bound
from . import fal_api, garment_color_consistency as consistency, matching, tone_mask

# fal HTTP 호출의 기존 60초 상한을 준비, 전체 호출, CPU 계산을 포함한 단계 전체에 적용한다.
TIMEOUT_S = fal_api.TIMEOUT_S
MIN_CONFIDENCE = 0.5
MAX_OVERLAP_FRAC = 0.02
_MATCHING_PROMPTS = {"top": "top", "bottom": "lower body garment"}


class MatchingUnverified(ValueError):
    """The generated outfit cannot be separated using the current mask policy."""


async def _matching_side(pool, job, recipe: dict, clothing_type: str) -> str | None:
    slots = (recipe.get("outfitSelectionsSnapshot") or {}).get("slots") or {}
    if (slots.get("inner") or {}).get("itemId"):
        # Inner layers can share the same area as the main garment.
        raise MatchingUnverified
    expected = matching.complementary_type(clothing_type)
    ids = recipe.get("matchIds") or []
    if not ids:
        return None
    if expected is None:
        raise MatchingUnverified
    async with pool.connection() as conn:
        for item_id in dict.fromkeys(ids):
            item = await repo.get_matching_item_metadata(conn, item_id, job["user_id"], job["project_id"])
            if (item or {}).get("clothing_type") != expected:
                raise MatchingUnverified
    return expected


class ColorProfileUnverified(ValueError):
    """RGB outside the garment must be suitable for verbatim preservation in the saved PNG."""


def _decode(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        rgb = np.array(ImageOps.exif_transpose(image).convert("RGB"))
        if not (image.info.get("icc_profile") or "A" in image.getbands() or "transparency" in image.info):
            return rgb
    normalized, _ = garment_color_evidence._frame(data)
    if normalized.getchannel("A").getextrema()[0] < 255 or not np.array_equal(rgb, np.array(normalized.convert("RGB"))):
        raise ColorProfileUnverified
    return rgb


def _plausible_masks(masks: list[np.ndarray]) -> list[np.ndarray]:
    return [mask for mask in masks if tone_mask.plausible(tone_mask.candidate_stats(mask))[0]]


def _overlap(mask: np.ndarray, protected: list[np.ndarray]) -> np.ndarray:
    overlap = np.zeros_like(mask)
    for other in protected:
        overlap |= mask & other
    return overlap


def _target_color(rgb: np.ndarray, candidates: list[np.ndarray]) -> tuple[np.ndarray | None, dict]:
    plausible = tone_mask.dedupe(_plausible_masks(candidates))
    if len(plausible) != 1:
        return None, {"status": "skipped", "reason": "target_ambiguous" if plausible else "target_no_garment_mask"}
    mask, _, _ = consistency.trim_specks(plausible[0])
    return _measure_target_color(rgb, mask)


def _measure_target_color(rgb: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray | None, dict]:
    region = consistency._region(rgb, mask)
    codes = consistency.judge_mask_quality(consistency._quality(region, mask.size))
    if codes:
        return None, {"status": "skipped", "reason": "target_" + codes[0]}
    measure = consistency._measure(region)
    return mask, {"target_measure": measure, "target_ab": measure["center"][1:],
                                   "algorithm_version": consistency.TARGET_ALGORITHM_VERSION}


def _finish(master: InlineImage, output: InlineImage, rgb: np.ndarray, candidates: list,
            target_ab: list[float], clothing_type, matching_side, protected: list) -> tuple[bytes, str, dict]:
    # Check both before ranking and after mask cleanup, which can fill holes.
    candidates = tone_mask.dedupe(_plausible_masks(candidates))
    separated = []
    for mask in candidates:
        overlap = _overlap(mask, protected)
        if overlap.sum() <= MAX_OVERLAP_FRAC * mask.sum():
            separated.append((mask & ~overlap, overlap))
    if len(separated) != 1:
        raise tone_mask.NoGarmentCandidate("the product mask could not be separated")
    mask, trimmed = separated[0]
    selected = tone_mask.produce(output.data, master.data, candidates=[mask],
                                 clothing_type=clothing_type, matching_side=matching_side)
    with Image.open(io.BytesIO(selected.png)) as image:
        mask = np.array(image.convert("L")) > 127
    overlap = _overlap(mask, protected)
    if overlap.sum() > MAX_OVERLAP_FRAC * mask.sum():
        raise tone_mask.NoGarmentCandidate("product mask overlaps the other garment")
    mask &= ~overlap
    result, record = consistency.match_to_target_color(rgb, mask, target_ab)
    overlap_trimmed_px = int(np.count_nonzero(trimmed | overlap))
    if overlap_trimmed_px:
        record["overlap_trimmed_px"] = overlap_trimmed_px
    if result is rgb:
        return output.data, output.mime, record
    encoded = io.BytesIO()
    Image.fromarray(result).save(encoded, "PNG")
    return encoded.getvalue(), "image/png", record


async def tune(settings, *, master: InlineImage, target: InlineImage, output: InlineImage,
               clothing_type, sub_category=None, matching_side=None, pool=None,
               job=None, recipe=None) -> tuple[bytes, str, dict]:
    """취소 신호 말고는 예외를 내보내지 않는다. 옮기지 않은 사진은 디코딩 여부와 무관하게 원래 바이트를 돌려준다."""
    stage = "matching"
    matching_record = {}

    async def execute():
        nonlocal stage
        side = matching_side
        selected_matching = False
        if recipe and pool is not None and job is not None:
            side = await _matching_side(pool, job, recipe, clothing_type)
            selected_matching = side is not None
        if clothing_type not in tone_mask.CATEGORY_PROMPTS:
            return output.data, output.mime, {"status": "skipped", "reason": "unknown_garment_type"}
        category_prompt, _ = tone_mask.prompts_for(clothing_type, sub_category)
        other_prompt = _MATCHING_PROMPTS.get(side or matching.complementary_type(clothing_type))

        async def masks(image: fal_api.PreparedImage, pixels: np.ndarray, prompt: str) -> list[np.ndarray]:
            return await tone_mask.candidates_for_prepared_image(settings, image, pixels,
                (prompt,), min_score=MIN_CONFIDENCE)

        stage = "decode"
        rgb = await run_cpu_bound(_decode, output.data)
        await run_cpu_bound(_decode, master.data)
        target_input = await run_cpu_bound(fal_api.prepare_image, target.data, color_managed=True)
        target_rgb = await run_cpu_bound(lambda: np.array(target_input.image.convert("RGB")))
        stage = "target_candidates"
        target_masks = await masks(target_input, target_rgb, category_prompt)
        stage = "target_measure"
        if target_input.image.mode == "RGBA":
            alpha = np.asarray(target_input.image.getchannel("A")) > 127
            target_masks = [mask & alpha for mask in target_masks]
        target_mask, target_record = await run_cpu_bound(_target_color, target_rgb, target_masks)
        if target_mask is None:
            return output.data, output.mime, target_record
        target_ab = target_record["target_ab"]
        # 거의 무채색 목표에는 결과 윤곽을 따는 두 호출도 필요 없다.
        if float(np.hypot(*target_ab)) < consistency.TARGET_ACHROMATIC_CHROMA:
            return output.data, output.mime, {**target_record, "status": "untouched",
                                             "reason": "target_achromatic"}
        if other_prompt:
            stage = "target_candidates"
            target_others = await masks(target_input, target_rgb, other_prompt)
            overlap = await run_cpu_bound(_overlap, target_mask, target_others)
            overlap_px = int(np.count_nonzero(overlap))
            if overlap_px > MAX_OVERLAP_FRAC * target_mask.sum():
                return output.data, output.mime, {"status": "skipped", "reason": "target_ambiguous"}
            if overlap_px:
                stage = "target_measure"
                target_mask, target_record = await run_cpu_bound(_measure_target_color, target_rgb,
                                                                target_mask & ~overlap)
                if target_mask is None:
                    return output.data, output.mime, target_record
                target_ab = target_record["target_ab"]
                if float(np.hypot(*target_ab)) < consistency.TARGET_ACHROMATIC_CHROMA:
                    return output.data, output.mime, {**target_record, "status": "untouched",
                                                     "reason": "target_achromatic"}
        stage = "output_candidates"
        output_input = await run_cpu_bound(fal_api.prepare_image, output.data)
        candidates = await masks(output_input, rgb, category_prompt)
        if not candidates:
            return output.data, output.mime, {**target_record, "status": "skipped", "reason": "no_candidate"}
        protected = []
        if other_prompt:
            protected = await masks(output_input, rgb, other_prompt)
            matching_record["matching_found"] = bool(await run_cpu_bound(_plausible_masks, protected))
            if selected_matching and recipe.get("shot") == "full" and not matching_record["matching_found"]:
                return output.data, output.mime, {**target_record, "status": "skipped",
                                                 "reason": "matching_unverified", "matching_found": False}
        stage = "apply"
        data, mime, record = await run_cpu_bound(_finish, master, output, rgb, candidates,
                                                target_ab, clothing_type, side, protected)
        return data, mime, {**target_record, **matching_record, **record}

    try:
        if not getattr(settings, "garment_color_tune_enabled", False):
            return output.data, output.mime, {"status": "disabled"}
        if not fal_api.configured(settings):
            return output.data, output.mime, {"status": "skipped", "reason": "fal_unconfigured"}
        return await asyncio.wait_for(execute(), timeout=TIMEOUT_S)
    except TimeoutError:
        reason = "timeout"
    except tone_mask.NoGarmentCandidate:
        reason = "no_candidate"
    except MatchingUnverified:
        reason = "matching_unverified"
    except ColorProfileUnverified:
        reason = "encoded_color_unverified"
    except Exception:
        # URL, 사진 바이트, provider 예외 문자열은 기록하지 않는다.
        reason = {"matching": "matching_unverified", "decode": "invalid_image", "target_candidates": "fal_failed",
                  "output_candidates": "fal_failed"}.get(stage, "calculation_failed")
    return output.data, output.mime, {"status": "skipped", "reason": reason, "stage": stage, **matching_record}


async def after_color_check(settings, *, master: InlineImage, target: InlineImage,
                            output: InlineImage, clothing_type, color_execution: dict,
                            warnings: list, sub_category=None, matching_side=None,
                            pool=None, job=None, recipe=None, real_model=False) -> tuple[bytes, str]:
    """다섯 생성 자리의 기록, 해시, 경고 배선. QC가 본 사진의 해시는 그대로 둔다."""
    if color_execution["colorCheck"]["status"] == "failed":
        return output.data, output.mime
    if real_model:
        data, mime, record = output.data, output.mime, {"status": "skipped", "reason": "real_model"}
    else:
        data, mime, record = await tune(settings, master=master, target=target, output=output,
            clothing_type=clothing_type, sub_category=sub_category, matching_side=matching_side,
            pool=pool, job=job, recipe=recipe)
    color_execution["colorTune"] = {"algorithm_version": consistency.TARGET_ALGORITHM_VERSION, **record,
        "inputOutputSha256": hashlib.sha256(output.data).hexdigest(),
        "outputSha256": hashlib.sha256(data).hexdigest()}
    if record.get("reason") == "over_cap":
        warnings.append({"code": "color_tune_over_cap"})
    return data, mime
