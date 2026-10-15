"""Qwen 2511 정돈과 옷 영역의 부드러운 Lab 중앙값 보정."""
from __future__ import annotations

import asyncio
import io

import cv2
import numpy as np
from PIL import Image

from . import fal_api, fal_cutout, matching_cutout

PROMPT = (
    "Lay this garment out evenly on pure white with soft even light, smoothing creases "
    "and filling the small gaps left by the hanger, clips and hands with the same fabric. "
    "Keep every detail of this garment exactly as in the photo. Do not add, remove or change anything."
)
NEGATIVE_PROMPT = (
    "another garment, extra clothing, person, body, mannequin, hanger, clip, hands, price tag, watermark"
)


def prepare_canvas(cutout_png: bytes) -> bytes:
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
    canvas.paste(cut, ((768 - cut.width) // 2, (1024 - cut.height) // 2), cut)
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def white_background_mask(rgb: np.ndarray) -> np.ndarray:
    white = np.all(rgb >= 245, axis=2).astype(np.uint8)
    # 흰색 패딩으로 네 테두리에 닿는 배경을 한 번의 flood fill 로 모두 연결한다.
    padded = np.pad(white, 1, constant_values=1)
    cv2.floodFill(padded, None, (0, 0), 2)
    return padded[1:-1, 1:-1] != 2


def soft_color_correct(reference_png: bytes, rgb: np.ndarray,
                       mask: np.ndarray) -> np.ndarray:
    with Image.open(io.BytesIO(reference_png)) as image:
        ref = np.array(image.convert("RGBA"))
    reference_mask = cv2.erode((ref[:, :, 3] > 127).astype(np.uint8),
                               np.ones((7, 7), np.uint8)).astype(bool)
    if not reference_mask.any() or not mask.any():
        raise ValueError("empty color reference or garment")
    ref_lab = cv2.cvtColor(ref[:, :, :3].astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    delta = np.median(ref_lab[reference_mask], axis=0) - np.median(lab[mask], axis=0)
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
    canvas = await asyncio.to_thread(prepare_canvas, cutout_png)
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
    try:
        segmented = await fal_cutout.cutout_png(settings, generated)
        alpha = await asyncio.to_thread(_alpha, segmented, (width, height))
        if not (alpha > 127).any():
            raise ValueError("empty generated mask")
    except Exception:  # SAM 실패는 테두리에 연결된 거의 흰 배경만 제거하는 방식으로 대체.
        mask = await asyncio.to_thread(white_background_mask, rgb)
        alpha = mask.astype(np.uint8) * 255
    return await asyncio.to_thread(_composite, cutout_png, rgb, alpha)
