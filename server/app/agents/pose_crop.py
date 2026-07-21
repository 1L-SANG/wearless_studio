"""pose-only medium 결과의 결정적 body-landmark crop.

Apple Vision은 운영 Linux 서버에 없으므로 기존 ``vision_llm``에는 정수 y 좌표만
요청한다. 좌표 호출이나 검증이 실패하면 동일 이미지에 고정 비율 폴백을 적용한다.
이미지 재생성·보정 프롬프트는 하지 않으며 all/bg 경로에서는 이 모듈을 호출하지 않는다.
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image

from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback


log = logging.getLogger("wearless.pose_crop")

MEDIUM_BOTTOM_RATIO = 0.62
MEDIUM_MIN_WIDTH = 640
MEDIUM_MIN_HEIGHT = 960

_LANDMARK_SCHEMA = {
    "type": "object",
    "properties": {
        "head_top": {"type": "integer"},
        "waist": {"type": "integer"},
        "hem": {"type": "integer"},
        "upper_thigh": {"type": "integer"},
    },
    "required": ["head_top", "waist", "hem", "upper_thigh"],
    "additionalProperties": False,
}

_LANDMARK_PROMPT = """Inspect this single full-body fashion image and return only integer pixel y
coordinates measured from the image top. head_top is the top of the hair/head; waist is the natural
waist; hem is the lowest visible edge of the untucked upper garment; upper_thigh is a horizontal
cut line across the upper thighs above both knees. Do not describe the image and do not estimate
hidden anatomy. The required JSON keys are head_top, waist, hem, upper_thigh."""


async def _detect_landmarks(settings, image_bytes: bytes, mime: str) -> dict:
    raw, _provider = await analyze_with_fallback(
        settings,
        _LANDMARK_PROMPT,
        [InlineImage(mime, image_bytes)],
        _LANDMARK_SCHEMA,
        thinking_level="low",
    )
    return raw


def _validated_landmarks(raw: dict, height: int) -> dict[str, int]:
    keys = ("head_top", "waist", "hem", "upper_thigh")
    values = {key: raw.get(key) for key in keys} if isinstance(raw, dict) else {}
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values.values()):
        raise ValueError("pose_crop_invalid_landmark_type")
    if not (
        0 <= values["head_top"] < values["waist"]
        <= values["hem"] < values["upper_thigh"] <= height
    ):
        raise ValueError("pose_crop_invalid_landmark_order")
    return values


def _portrait_crop_box(width: int, height: int, top: int, bottom: int) -> tuple[int, int, int, int]:
    top = max(0, min(int(top), height - 1))
    bottom = max(top + 1, min(int(bottom), height))
    span = bottom - top
    crop_width = min(width, int(span * 2 / 3))
    crop_width -= crop_width % 2
    if crop_width < 2:
        raise ValueError("pose_crop_region_too_small")
    crop_height = crop_width * 3 // 2
    crop_bottom = bottom
    crop_top = crop_bottom - crop_height
    if crop_top < top:
        crop_top = top
        crop_bottom = crop_top + crop_height
    if crop_bottom > height:
        crop_bottom = height
        crop_top = crop_bottom - crop_height
    left = max(0, (width - crop_width) // 2)
    return left, crop_top, left + crop_width, crop_bottom


def landmark_crop_box(width: int, height: int, landmarks: dict) -> tuple[int, int, int, int]:
    values = _validated_landmarks(landmarks, height)
    head_margin = max(2, round(height * 0.02))
    return _portrait_crop_box(
        width,
        height,
        values["head_top"] - head_margin,
        values["upper_thigh"],
    )


def fallback_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    """전신 세로 이미지의 상단부터 약 62%를 쓰는 보수적 2:3 중앙 크롭."""
    return _portrait_crop_box(width, height, 0, round(height * MEDIUM_BOTTOM_RATIO))


def _encode(image: Image.Image, mime: str) -> tuple[bytes, str]:
    output = BytesIO()
    if mime == "image/jpeg":
        image.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
    elif mime == "image/webp":
        image.save(output, format="WEBP", quality=95, method=6)
    else:
        mime = "image/png"
        image.save(output, format="PNG", optimize=True)
    return output.getvalue(), mime


async def crop_pose_medium(settings, image_bytes: bytes, mime: str) -> tuple[bytes, str]:
    with Image.open(BytesIO(image_bytes)) as source:
        source.load()
        image = source.copy()
    try:
        landmarks = await _detect_landmarks(settings, image_bytes, mime)
        box = landmark_crop_box(image.width, image.height, landmarks)
    except (VisionError, ValueError, TypeError) as exc:
        log.warning("pose medium landmark crop unavailable; using ratio fallback: %s", exc)
        box = fallback_crop_box(image.width, image.height)

    cropped = image.crop(box)
    scale = max(
        1.0,
        MEDIUM_MIN_WIDTH / cropped.width,
        MEDIUM_MIN_HEIGHT / cropped.height,
    )
    if scale > 1.0:
        cropped = cropped.resize(
            (round(cropped.width * scale), round(cropped.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return _encode(cropped, mime)
