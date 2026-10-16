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
ILLUM_MIN_BACKGROUND_FRAC = .01
ILLUM_EDGE_BAND = 20
ILLUM_MIN_EDGE_PIXELS = 2000
ILLUM_MIN_EDGE_ENERGY = .05
ILLUM_AGREE_FULL = .7
ILLUM_NEUTRAL_NORM = .03
ILLUM_MOMENT = 6
ILLUM_GAUSSIAN_SIGMA = 2
ILLUM_MIN_GAIN = .75
ILLUM_MAX_GAIN = 1.3
EXPOSURE_BACKGROUND_TARGET = .9
EXPOSURE_GARMENT_LIMIT = .95
EXPOSURE_MAX_GAIN = 1.3
EXPOSURE_REF_MIN_LUMINANCE = .6
EXPOSURE_REF_MAX_CHROMA = 12
EXPOSURE_REF_MIN_FRAC = .01
EXPOSURE_REF_MIN_PIXELS = 300
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
    edge_energy = None
    agreement_cos = None
    weight = 0.
    exposure_ref_frac = 0.
    reason = ""
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
        cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        dilated = cv2.dilate(garment, cross, iterations=ILLUM_BACKGROUND_DILATION)
        background = valid & (dilated == 0)
        background_pixels = int(np.count_nonzero(background))
        if (background_pixels < ILLUM_MIN_BACKGROUND_PIXELS
                or background_pixels < ILLUM_MIN_BACKGROUND_FRAC * background.size):
            raise ValueError("too few background pixels")
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            sog = np.mean(linear[background] ** ILLUM_MOMENT, axis=0) ** (1 / ILLUM_MOMENT)
            if not np.isfinite(sog).all() or (sog <= 0).any():
                raise ValueError("invalid illumination estimate")
            sog /= sog[1]
            smooth = cv2.GaussianBlur(linear, (0, 0), ILLUM_GAUSSIAN_SIGMA,
                                      borderType=cv2.BORDER_REFLECT)
            if not np.isfinite(smooth).all() or (smooth < 0).any():
                raise ValueError("invalid smoothed illumination")
            gx = cv2.Sobel(smooth, cv2.CV_64F, 1, 0, borderType=cv2.BORDER_REFLECT)
            gy = cv2.Sobel(smooth, cv2.CV_64F, 0, 1, borderType=cv2.BORDER_REFLECT)
            magnitude = np.hypot(gx, gy)
            # 옷 윤곽과 무늬가 추정에 섞이지 않게 넓은 띠를 제외한다.
            band = cv2.dilate(garment, cross, iterations=ILLUM_EDGE_BAND)
            bg_ge = valid & (band == 0)
            ge = None
            if np.count_nonzero(bg_ge) >= ILLUM_MIN_EDGE_PIXELS:
                mag_lum = magnitude @ LUMINANCE_WEIGHTS
                edge_energy = float(np.mean(mag_lum[bg_ge] ** ILLUM_MOMENT)
                                    ** (1 / ILLUM_MOMENT))
                if edge_energy >= ILLUM_MIN_EDGE_ENERGY:
                    edge = np.mean(magnitude[bg_ge] ** ILLUM_MOMENT, axis=0) ** (1 / ILLUM_MOMENT)
                    if np.isfinite(edge).all() and (edge > 0).all():
                        ge = edge / edge[1]
            if ge is not None:
                u = np.log(sog)
                u -= u.mean()
                v = np.log(ge)
                v -= v.mean()
                nu, nv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
                agreement_cos = (1. if min(nu, nv) < ILLUM_NEUTRAL_NORM
                                 else float(np.dot(u, v) / (nu * nv)))
                weight = float(np.clip(agreement_cos / ILLUM_AGREE_FULL, 0, 1))
                illumination = np.clip(np.exp(weight * (np.log(sog) + np.log(ge)) / 2),
                                       ILLUM_MIN_GAIN, ILLUM_MAX_GAIN)
                reason = "both"
            else:
                # 단색 배경만으로는 벽 색과 조명 색을 구분할 수 없다.
                reason = "sog_only"
            garment_linear = _srgb_to_linear(ref[:, :, :3][ref[:, :, 3] > 127])
            g99 = float(np.percentile(garment_linear @ LUMINANCE_WEIGHTS, 99))
            if not np.isfinite(g99) or g99 <= 0:
                raise ValueError("invalid illumination estimate")
            # 보정 전 배경에서 밝고 무채색인 부분만 노출 기준으로 쓴다.
            lum_bg = luminance[background]
            bright = lum_bg >= EXPOSURE_REF_MIN_LUMINANCE
            if bright.any():
                bg_rgb = small[:, :, :3][background][bright].astype(np.float32) / 255
                bg_lab = cv2.cvtColor(bg_rgb.reshape(-1, 1, 3), cv2.COLOR_RGB2Lab)[:, 0]
                refpix = bright.copy()
                refpix[bright] = np.hypot(bg_lab[:, 1], bg_lab[:, 2]) <= EXPOSURE_REF_MAX_CHROMA
                exposure_ref_frac = float(refpix.mean())
                if (exposure_ref_frac >= EXPOSURE_REF_MIN_FRAC
                        and np.count_nonzero(refpix) >= EXPOSURE_REF_MIN_PIXELS):
                    q = float(np.percentile(lum_bg[refpix], 90))
                    # 밝은 옷을 보호하되 사진을 어둡게 만들지는 않는다.
                    exposure = max(1., min(EXPOSURE_MAX_GAIN, EXPOSURE_GARMENT_LIMIT / g99,
                                           EXPOSURE_BACKGROUND_TARGET / q))
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
    except Exception as exc:
        target = original.copy()
        illumination = np.ones(3)
        exposure = 1.
        capped = False
        reason = str(exc)
    log.info(json.dumps({"illumination_gain": np.round(illumination, 3).tolist(),
                         "exposure_gain": round(exposure, 3),
                         "background_pixels": background_pixels, "chroma_capped": capped,
                         "edge_energy": round(edge_energy, 4) if edge_energy is not None else None,
                         "agreement_cos": round(agreement_cos, 3) if agreement_cos is not None else None,
                         "weight": round(weight, 3),
                         "exposure_ref_frac": round(exposure_ref_frac, 4), "reason": reason}))
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
