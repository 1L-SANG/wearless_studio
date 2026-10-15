"""SAM 3 컬러 마스크를 기존 SAM2의 RGBA·R2·SamViewResult 계약으로 바꾼다."""
from __future__ import annotations

import asyncio
import hashlib
import io
import math

import cv2
import numpy as np
from PIL import Image

from . import fal_api
from .sam_client import SamUnavailable, SamViewResult, VIEWS

ALGORITHM_VERSION = "fal-sam3-v1"
MODEL = fal_api.SAM_MODEL


class NoGarmentMask(ValueError):
    """유효한 옷 후보가 없다. 원본을 유지한다."""


def _mask_from_color(data: bytes, source_rgb: np.ndarray) -> np.ndarray:
    height, width = source_rgb.shape[:2]
    with Image.open(io.BytesIO(data)) as image:
        rgb = np.array(image.convert("RGB").resize((width, height), Image.Resampling.NEAREST))
    mask = (rgb.max(axis=2) > 0).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    # 테두리에 이어진 배경은 제외하고, 작은 구멍이나 원본이 어두운 구멍만 복구한다.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(1 - mask, 8)
    border_labels = set(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    mask_area = int(mask.sum())
    source_max = source_rgb.max(axis=2)
    for label in range(1, count):
        if label in border_labels:
            continue
        x, y, w, h, area = stats[label]
        hole = labels[y:y + h, x:x + w] == label
        if (area < mask_area * .005
                or np.median(source_max[y:y + h, x:x + w][hole]) <= 30):
            region = mask[y:y + h, x:x + w]
            region[hole] = 1
    return mask.astype(bool)


def select_mask(candidates: list[tuple[bytes, float]],
                source_rgb: np.ndarray) -> np.ndarray:
    height, width = source_rgb.shape[:2]
    eligible = []
    for data, score in candidates:
        if not math.isfinite(score) or score < .5:
            continue
        mask = _mask_from_color(data, source_rgb)
        area = int(mask.sum())
        if area / (width * height) < .03:
            continue
        center = bool(mask[int(height * .4):max(int(height * .4) + 1, math.ceil(height * .6)),
                           int(width * .4):max(int(width * .4) + 1, math.ceil(width * .6))].any())
        eligible.append((mask, area, score, center))
    if not eligible:
        raise NoGarmentMask("no_garment_mask")
    central = [c for c in eligible if c[3]]
    pool = central or eligible
    largest = max(c[1] for c in pool)
    mask = max((c for c in pool if c[1] >= largest * .95), key=lambda c: c[2])[0]
    # 전체 옷 면적의 1% 미만인 떨어진 조각만 제거한다.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    keep = np.zeros(count, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= mask.sum() * .01
    return keep[labels]


def to_cutout_png(source: Image.Image, mask: np.ndarray) -> bytes:
    # sam_service.segmentation.to_cutout_png 와 같은 안쪽 2px feather·PNG 바이트 계약.
    alpha = cv2.GaussianBlur(mask.astype(np.uint8) * 255, (5, 5), 0)
    alpha[~mask] = 0
    rgba = np.dstack((np.array(source.convert("RGB")), alpha))
    height, width = rgba.shape[:2]
    scale = min(1., 2048 / max(height, width))
    if scale < 1:
        rgba = cv2.resize(rgba, (max(1, int(width * scale)), max(1, int(height * scale))),
                          interpolation=cv2.INTER_AREA)
    out = io.BytesIO()
    Image.fromarray(rgba).save(out, "PNG", compress_level=6)
    return out.getvalue()


async def cutout_png(settings, source: bytes) -> bytes:
    prepared = await asyncio.to_thread(fal_api.prepare_image, source)
    body = await fal_api.run_model(settings, MODEL, {
        "image_url": prepared.url, "prompt": "clothing", "return_multiple_masks": True,
        "max_masks": 3, "include_scores": True, "include_boxes": True,
        "output_format": "png"})
    # 응답 필드와 각 마스크의 크기는 실연동 전 가정이다. Claude가 실연동 확인.
    masks, scores = body.get("masks"), body.get("scores")
    if not isinstance(masks, list):
        raise SamUnavailable("fal SAM returned invalid masks")
    candidates = []
    for index, item in enumerate(masks[:3]):
        try:
            url = item["url"]
            score = (scores[index] if isinstance(scores, list) else item["score"])
            if not isinstance(url, str) or not url or isinstance(score, bool):
                raise ValueError
            score = float(score)
        except (KeyError, IndexError, TypeError, ValueError):
            raise SamUnavailable("fal SAM returned invalid mask scores or URL") from None
        candidates.append((await fal_api.fetch_image(url), score))
    try:
        source_rgb = np.array(prepared.image.convert("RGB"))
        mask = await asyncio.to_thread(select_mask, candidates, source_rgb)
    except NoGarmentMask:
        raise
    except (OSError, ValueError):
        raise SamUnavailable("fal SAM returned invalid mask image") from None
    return await asyncio.to_thread(to_cutout_png, prepared.image, mask)


def _result(view: str, data: bytes, source_hash: str, key: str, *, cached=False) -> SamViewResult:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        alpha = np.array(image.getchannel("A"))
    return SamViewResult(view=view, ready=True, cutout_key=key, source_hash=source_hash,
        model_version=MODEL, algorithm_version=ALGORITHM_VERSION,
        checksum=hashlib.sha256(data).hexdigest(), width=width, height=height,
        area_frac=float((alpha > 0).mean()), byte_size=len(data), cached=cached)


async def segment_garment(settings, r2, views: dict[str, str]) -> dict[str, SamViewResult]:
    if not fal_api.configured(settings):
        raise SamUnavailable("fal is not configured (FAL_KEY)")
    results = {}
    for view, source_key in views.items():
        if view not in VIEWS or not source_key:
            continue
        try:
            source = await asyncio.to_thread(r2.get_bytes, source_key)
            source_hash = hashlib.sha256(source).hexdigest()
            key = (f"derived/canonical-cutout/{ALGORITHM_VERSION}/{MODEL.replace('/', '_')}"
                   f"/{view}/{source_hash}.png")
            if await asyncio.to_thread(r2.head, key):
                cached = await asyncio.to_thread(r2.get_bytes, key)
                results[view] = await asyncio.to_thread(_result, view, cached, source_hash,
                                                       key, cached=True)
                continue
            data = await cutout_png(settings, source)
            await asyncio.to_thread(r2.put_bytes, key, data, "image/png")
            results[view] = await asyncio.to_thread(_result, view, data, source_hash, key)
        except SamUnavailable:
            # 한 뷰의 통신 장애가 다른 뷰의 성공을 버리거나 실행을 막지 않게 한다.
            results[view] = SamViewResult(view=view, ready=False, code="unavailable")
        except NoGarmentMask:
            results[view] = SamViewResult(view=view, ready=False, code="no_garment_mask")
        except Exception:  # 디코드·저장 실패도 한 뷰의 기존 실패 계약을 따른다.
            results[view] = SamViewResult(view=view, ready=False, code="cutout_failed")
    return results
