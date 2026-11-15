"""QC를 지난 다른 색상 컷의 작은 색 오차만 맞춘다. 실패하면 AI 결과를 그대로 쓴다."""
from __future__ import annotations

import asyncio
import hashlib
import io

import numpy as np
from PIL import Image

from ..agents.gemini_image import InlineImage, run_cpu_bound
from . import fal_api, fal_cutout, garment_color_consistency as consistency, tone_mask

# fal HTTP 호출의 기존 60초 상한을 준비, 전체 호출, CPU 계산을 포함한 단계 전체에 적용한다.
TIMEOUT_S = fal_api.TIMEOUT_S


def _decode(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert("RGB"))


def _target_color(cutout: bytes) -> tuple[list[float] | None, dict]:
    with Image.open(io.BytesIO(cutout)) as image:
        rgba = np.array(image.convert("RGBA"))
    mask, _, _ = consistency.trim_specks(rgba[..., 3] > 127)
    region = consistency._region(rgba[..., :3], mask)
    codes = consistency.judge_mask_quality(consistency._quality(region, mask.size))
    if codes:
        return None, {"status": "skipped", "reason": "target_" + codes[0]}
    measure = consistency._measure(region)
    return measure["center"][1:], {"target_measure": measure, "target_ab": measure["center"][1:],
                                   "algorithm_version": consistency.TARGET_ALGORITHM_VERSION}


def _finish(master: InlineImage, output: InlineImage, rgb: np.ndarray, candidates: list,
            target_ab: list[float], clothing_type, matching_side) -> tuple[bytes, str, dict]:
    # produce는 base의 신체를 검사하지 않고 차분을 채점에만 쓴다. 같은 그림의 색 편집에도 그대로 쓴다.
    selected = tone_mask.produce(output.data, master.data, candidates=candidates,
                                 clothing_type=clothing_type, matching_side=matching_side)
    with Image.open(io.BytesIO(selected.png)) as image:
        mask = np.array(image.convert("L")) > 127
    result, record = consistency.match_to_target_color(rgb, mask, target_ab)
    if result is rgb:
        return output.data, output.mime, record
    encoded = io.BytesIO()
    Image.fromarray(result).save(encoded, "PNG")
    return encoded.getvalue(), "image/png", record


async def tune(settings, *, master: InlineImage, target: InlineImage, output: InlineImage,
               clothing_type, sub_category=None, matching_side=None) -> tuple[bytes, str, dict]:
    """취소 신호 말고는 예외를 내보내지 않는다. 옮기지 않은 사진은 디코딩 여부와 무관하게 원래 바이트를 돌려준다."""
    stage = "decode"

    async def execute():
        nonlocal stage
        # 깨진 입력에 유료 호출을 쓰지 않는다. 후보 선택에도 해독 가능한 master가 필요하다.
        rgb = await run_cpu_bound(_decode, output.data)
        await run_cpu_bound(_decode, master.data)
        await run_cpu_bound(_decode, target.data)
        stage = "target_cutout"
        cutout = await fal_cutout.cutout_png(settings, target.data)
        stage = "target_measure"
        target_ab, target_record = await run_cpu_bound(_target_color, cutout)
        if target_ab is None:
            return output.data, output.mime, target_record
        # 거의 무채색 목표에는 결과 윤곽을 따는 두 호출도 필요 없다.
        if float(np.hypot(*target_ab)) < consistency.TARGET_ACHROMATIC_CHROMA:
            return output.data, output.mime, {**target_record, "status": "untouched",
                                             "reason": "target_achromatic"}
        stage = "output_candidates"
        candidates = await tone_mask.generate_candidates(settings, output.data,
            clothing_type=clothing_type, sub_category=sub_category)
        if not candidates:
            return output.data, output.mime, {**target_record, "status": "skipped", "reason": "no_candidate"}
        stage = "apply"
        data, mime, record = await run_cpu_bound(_finish, master, output, rgb, candidates,
                                                target_ab, clothing_type, matching_side)
        return data, mime, {**target_record, **record}

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
    except fal_cutout.NoGarmentMask:
        reason = "target_no_garment_mask"
    except Exception:
        # URL, 사진 바이트, provider 예외 문자열은 기록하지 않는다.
        reason = {"decode": "invalid_image", "target_cutout": "fal_failed",
                  "output_candidates": "fal_failed"}.get(stage, "calculation_failed")
    return output.data, output.mime, {"status": "skipped", "reason": reason, "stage": stage}


async def after_color_check(settings, *, master: InlineImage, target: InlineImage,
                            output: InlineImage, clothing_type, color_execution: dict,
                            warnings: list, sub_category=None, matching_side=None) -> tuple[bytes, str]:
    """다섯 생성 자리의 기록, 해시, 경고 배선. QC가 본 사진의 해시는 그대로 둔다."""
    if color_execution["colorCheck"]["status"] == "failed":
        return output.data, output.mime
    data, mime, record = await tune(settings, master=master, target=target, output=output,
        clothing_type=clothing_type, sub_category=sub_category, matching_side=matching_side)
    color_execution["colorTune"] = {"algorithm_version": consistency.TARGET_ALGORITHM_VERSION, **record,
        "inputOutputSha256": hashlib.sha256(output.data).hexdigest(),
        "outputSha256": hashlib.sha256(data).hexdigest()}
    if record.get("reason") == "over_cap":
        warnings.append({"code": "color_tune_over_cap"})
    return data, mime
