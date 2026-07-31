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


def test_landmark_crop_uses_head_margin_and_upper_thigh_boundary(monkeypatch):
    landmarks = {"head_top": 90, "waist": 600, "hem": 850, "upper_thigh": 1050}

    async def fake_detect(settings, image_bytes, mime):
        assert mime == "image/png"
        return landmarks

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fake_detect)
    result, mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(), "image/png"
    ))

    assert pose_crop.landmark_crop_box(1200, 1800, landmarks) == (268, 54, 932, 1050)
    assert _size(result) == (664, 996)
    assert mime == "image/png"


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

    assert pose_crop.fallback_crop_box(1200, 1800) == (228, 0, 972, 1116)
    assert _size(first) == (744, 1116)
    assert first == second


def test_ratio_fallback_upscales_to_minimum_medium_resolution(monkeypatch):
    async def fail_detect(*_args, **_kwargs):
        raise VisionError("vision unavailable")

    monkeypatch.setattr(pose_crop, "_detect_landmarks", fail_detect)
    result, _mime = asyncio.run(pose_crop.crop_pose_medium(
        make_settings(), _png(300, 450), "image/png"
    ))

    assert _size(result) == (640, 960)
