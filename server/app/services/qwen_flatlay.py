"""Qwen 2511 정돈과 옷 영역의 부드러운 Lab 중앙값 보정."""
from __future__ import annotations

import asyncio
import io
import json
import logging

import cv2
import numpy as np
from PIL import Image

from . import fal_api, fal_cutout, matching_cutout

log = logging.getLogger("wearless.qwen_flatlay")

ILLUM_MAX_SIDE = 1024
ILLUM_MIN_LUMINANCE = .02
ILLUM_MAX_CHANNEL = .97
ILLUM_BACKGROUND_DILATION = 5
ILLUM_MIN_BACKGROUND_PIXELS = 500
ILLUM_MOMENT = 6
ILLUM_GAUSSIAN_SIGMA = 2
ILLUM_MIN_GAIN = .75
ILLUM_MAX_GAIN = 1.3
EXPOSURE_BACKGROUND_TARGET = .9
EXPOSURE_GARMENT_LIMIT = .95
EXPOSURE_MAX_GAIN = 1.3
MAX_CHROMA_SHIFT = 10.
# 조명 추정이 갈색 나무 배경 등에 끌려 과하게 고치는 일을 줄이려고 색 이동은 70%만 쓴다(10/16 실사진 비교).
CHROMA_SHIFT_STRENGTH = .7
LUMINANCE_WEIGHTS = np.array([.2126, .7152, .0722])

PROMPT = (
    "Lay this garment out evenly on pure white with soft even light, smoothing creases "
    "and filling the small gaps left by the hanger, clips and hands with the same fabric. "
    "Keep every detail of this garment exactly as in the photo. Do not add, remove or change anything."
)
NEGATIVE_PROMPT = (
    "another garment, extra clothing, person, body, mannequin, hanger, clip, hands, price tag, watermark"
)


def prepare_canvas(cutout_png: bytes) -> tuple[bytes, float]:
    with Image.open(io.BytesIO(cutout_png)) as image:
        cut = image.convert("RGBA")
        bbox = cut.getchannel("A").getbbox()
        if bbox is None:
            raise ValueError("empty cutout")
        cut = cut.crop(bbox)
    scale = min(768 * .88 / cut.width, 1024 * .88 / cut.height)
    cut = cut.resize((max(1, round(cut.width * scale)), max(1, round(cut.height * scale))),
                     Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (768, 1024), "white")
    # 촬영 해상도와 빈 여백이 아니라 실제 모델 입력에서 옷이 차지하는 비율이다.
    area_fraction = float((np.array(cut.getchannel("A")) > 127).sum()) / (768 * 1024)
    canvas.paste(cut, ((768 - cut.width) // 2, (1024 - cut.height) // 2), cut)
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue(), area_fraction


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    srgb = rgb.astype(np.float64) / 255
    return np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)


def _color_target(ref: np.ndarray, reference_mask: np.ndarray) -> np.ndarray:
    # 원본 중앙값을 유지해 추정 실패 시 기존 보정과 정확히 같은 목표를 쓴다.
    ref_lab = cv2.cvtColor(ref[:, :, :3].astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    original = np.median(ref_lab[reference_mask], axis=0)
    target = original.copy()
    illumination = np.ones(3)
    exposure = 1.
    background_pixels = 0
    capped = False
    try:
        scale = min(1., ILLUM_MAX_SIDE / max(ref.shape[:2]))
        small = ref
        if scale < 1:
            small = cv2.resize(ref, (max(1, round(ref.shape[1] * scale)),
                                     max(1, round(ref.shape[0] * scale))),
                               interpolation=cv2.INTER_AREA)
        linear = _srgb_to_linear(small[:, :, :3])
        luminance = linear @ LUMINANCE_WEIGHTS
        valid = ((luminance > ILLUM_MIN_LUMINANCE)
                 & (linear < ILLUM_MAX_CHANNEL).all(axis=2))
        garment = (small[:, :, 3] > 127).astype(np.uint8)
        # scipy 기본 binary_dilation과 같은 십자 커널을 5회 적용한다.
        dilated = cv2.dilate(garment, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)),
                             iterations=ILLUM_BACKGROUND_DILATION)
        background = valid & (dilated == 0)
        if np.count_nonzero(background) < ILLUM_MIN_BACKGROUND_PIXELS:
            background = valid
        background_pixels = int(np.count_nonzero(background))
        if not background_pixels or not valid.any():
            raise ValueError("no valid illumination pixels")
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            sog = np.mean(linear[background] ** ILLUM_MOMENT, axis=0) ** (1 / ILLUM_MOMENT)
            smooth = cv2.GaussianBlur(linear, (0, 0), ILLUM_GAUSSIAN_SIGMA,
                                      borderType=cv2.BORDER_REFLECT)
            if not np.isfinite(smooth).all() or (smooth < 0).any():
                raise ValueError("invalid smoothed illumination")
            gx = cv2.Sobel(smooth, cv2.CV_64F, 1, 0, borderType=cv2.BORDER_REFLECT)
            gy = cv2.Sobel(smooth, cv2.CV_64F, 0, 1, borderType=cv2.BORDER_REFLECT)
            magnitude = np.hypot(gx, gy)
            edge = np.mean(magnitude[valid] ** ILLUM_MOMENT, axis=0) ** (1 / ILLUM_MOMENT)
            garment_linear = _srgb_to_linear(ref[:, :, :3][ref[:, :, 3] > 127])
            q = float(np.percentile(luminance[background], 90))
            g99 = float(np.percentile(garment_linear @ LUMINANCE_WEIGHTS, 99))
            estimates = np.array([sog, edge])
            if (not np.isfinite(estimates).all() or (estimates <= 0).any()
                    or not np.isfinite([q, g99]).all() or min(q, g99) <= 0):
                raise ValueError("invalid illumination estimate")
            estimates /= estimates[:, 1:2]
            illumination = np.clip(np.exp(np.log(estimates).mean(axis=0)),
                                   ILLUM_MIN_GAIN, ILLUM_MAX_GAIN)
            # 밝은 옷의 상한이 1보다 작아져도 사진을 어둡게 만들지는 않는다.
            exposure = max(1., min(EXPOSURE_MAX_GAIN, EXPOSURE_GARMENT_LIMIT / max(g99, 1e-3),
                                   max(1., EXPOSURE_BACKGROUND_TARGET / q)))
            corrected = np.clip(_srgb_to_linear(ref[:, :, :3][reference_mask])
                                / illumination * exposure, 0, 1)
            srgb = np.where(corrected <= .0031308, corrected * 12.92,
                            1.055 * corrected ** (1 / 2.4) - .055)
            lab = cv2.cvtColor(srgb.astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_RGB2Lab)
            target = np.median(lab[:, 0], axis=0)
            if not np.isfinite(target).all():
                raise ValueError("invalid color target")
            shift = (target[1:] - original[1:]) * CHROMA_SHIFT_STRENGTH
            distance = float(np.linalg.norm(shift))
            capped = distance > MAX_CHROMA_SHIFT
            if capped:
                shift *= MAX_CHROMA_SHIFT / distance
            target[1:] = original[1:] + shift
    except Exception:
        target = original.copy()
        illumination = np.ones(3)
        exposure = 1.
        capped = False
    log.info(json.dumps({"illumination_gain": np.round(illumination, 3).tolist(),
                         "exposure_gain": round(exposure, 3),
                         "background_pixels": background_pixels, "chroma_capped": capped}))
    return target


def soft_color_correct(reference_png: bytes, rgb: np.ndarray,
                       mask: np.ndarray) -> np.ndarray:
    with Image.open(io.BytesIO(reference_png)) as image:
        ref = np.array(image.convert("RGBA"))
    reference_mask = cv2.erode((ref[:, :, 3] > 127).astype(np.uint8),
                               np.ones((7, 7), np.uint8)).astype(bool)
    if not reference_mask.any() or not mask.any():
        raise ValueError("empty color reference or garment")
    target = _color_target(ref, reference_mask)
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    delta = target - np.median(lab[mask], axis=0)
    delta[0] *= .7
    lab[mask] += delta
    lab[:, :, 0] = np.clip(lab[:, :, 0], 0, 100)
    lab[:, :, 1:] = np.clip(lab[:, :, 1:], -127, 127)
    corrected = np.clip(np.rint(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB) * 255), 0, 255).astype(np.uint8)
    # 변환 왕복의 반올림 오차도 배경 픽셀에는 적용하지 않는다.
    corrected[~mask] = rgb[~mask]
    return corrected


def _decode_rgb(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert("RGB"))


def _alpha(data: bytes, size: tuple[int, int]) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.getchannel("A").resize(size, Image.Resampling.NEAREST))


def _composite(reference: bytes, rgb: np.ndarray, alpha: np.ndarray) -> bytes:
    corrected = soft_color_correct(reference, rgb, alpha > 127)
    image = Image.fromarray(corrected).convert("RGBA")
    image.putalpha(Image.fromarray(alpha))
    out = io.BytesIO()
    image.save(out, "PNG")
    return matching_cutout.flatten_on_bg(out.getvalue())


async def render(settings, cutout_png: bytes) -> bytes:
    canvas, source_area_fraction = await asyncio.to_thread(prepare_canvas, cutout_png)
    prepared = await asyncio.to_thread(fal_api.prepare_image, canvas)
    # 지시서 3.4의 로컬 실험(run_stage5.py)과 같은 복수 필드. SAM3만 image_url을 쓴다.
    body = await fal_api.run_model(settings, fal_api.QWEN_MODEL, {
        "image_urls": [prepared.url], "prompt": PROMPT, "negative_prompt": NEGATIVE_PROMPT,
        "guidance_scale": 4.5, "num_inference_steps": 28, "acceleration": "regular",
        "seed": 7, "num_images": 1, "output_format": "png",
        "image_size": {"width": 768, "height": 1024}})
    generated = await fal_api.fetch_image(body["images"][0]["url"])
    rgb = await asyncio.to_thread(_decode_rgb, generated)
    height, width = rgb.shape[:2]
    # 마스크가 없으면 render_thumbnail이 기존 누끼를 유지한다.
    # 흰 배경과 이어진 흰 소매도 사라질 수 있어 색으로 마스크를 추측하지 않는다.
    segmented = await fal_cutout.cutout_png(settings, generated)
    alpha = await asyncio.to_thread(_alpha, segmented, (width, height))
    result_area_fraction = float((alpha > 127).mean())
    log.info(json.dumps({"provider": "fal", "model": fal_api.QWEN_MODEL,
        "mask_source": "sam3", "source_area_fraction": source_area_fraction,
        "result_area_fraction": result_area_fraction}))
    if source_area_fraction <= 0 or result_area_fraction < source_area_fraction * .5:
        raise ValueError("generated garment mask is too small")
    return await asyncio.to_thread(_composite, cutout_png, rgb, alpha)
