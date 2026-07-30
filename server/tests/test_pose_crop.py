import asyncio
from io import BytesIO

from PIL import Image

from app.agents import pose_crop
from app.agents.vision_llm import VisionError
from conftest import make_settings


def _png(width=1200, height=1800):
    output = BytesIO()
    Image.new("RGB", (width, height), (80, 120, 160)).save(output, format="PNG")
    return output.getvalue()


def _size(data):
    with Image.open(BytesIO(data)) as image:
        return image.size


def test_top_landmark_crop_uses_head_to_hip_bounds(monkeypatch):
    landmarks = {"head_top": 90, "waist": 600, "hip": 900, "feet_bottom": 1740}

    async def fake_detect(settings, image_bytes, mime):
        assert mime == "image/png"
        return landmarks

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fake_detect)
    result, mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(), "image/png"
    ))

    assert pose_crop.landmark_crop_box(1200, 1800, landmarks) == (306, 54, 894, 936)
    assert _size(result) == (640, 960)
    assert mime == "image/png"


def test_bottom_landmark_crop_uses_waist_to_feet_bounds(monkeypatch):
    landmarks = {"head_top": 90, "waist": 600, "hip": 900, "feet_bottom": 1740}

    async def fake_detect(*_args, **_kwargs):
        return landmarks

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fake_detect)
    result, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(), "image/png", "bottom"
    ))

    assert pose_crop.landmark_crop_box(
        1200, 1800, landmarks, "bottom"
    ) == (196, 564, 1004, 1776)
    assert _size(result) == (808, 1212)


def test_landmark_failure_uses_deterministic_ratio_fallback(monkeypatch):
    async def fail_detect(*_args, **_kwargs):
        raise VisionError("vision unavailable")

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fail_detect)
    source = _png()
    first, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), source, "image/png"
    ))
    second, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), source, "image/png"
    ))

    assert pose_crop.fallback_crop_box(1200, 1800) == (252, 0, 948, 1044)
    assert _size(first) == (696, 1044)
    assert first == second


def test_bottom_landmark_failure_uses_lower_body_ratio_fallback(monkeypatch):
    async def fail_detect(*_args, **_kwargs):
        raise VisionError("vision unavailable")

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fail_detect)
    result, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(), "image/png", "bottom"
    ))

    assert pose_crop.fallback_crop_box(1200, 1800, "bottom") == (228, 648, 972, 1764)
    assert _size(result) == (744, 1116)


def test_ratio_fallback_upscales_to_minimum_medium_resolution(monkeypatch):
    async def fail_detect(*_args, **_kwargs):
        raise VisionError("vision unavailable")

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fail_detect)
    result, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(300, 450), "image/png"
    ))

    assert _size(result) == (640, 960)
