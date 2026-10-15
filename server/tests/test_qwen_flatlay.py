import asyncio
import io
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from app import image_usage
from app.services import fal_api, fal_cutout, matching_flatlay, qwen_flatlay
from app.services.sam_client import SamUnavailable
from test_fal_cutout import png


PROMPT = ("Lay this garment out evenly on pure white with soft even light, smoothing creases "
          "and filling the small gaps left by the hanger, clips and hands with the same fabric. "
          "Keep every detail of this garment exactly as in the photo. Do not add, remove or change anything.")
NEGATIVE = "another garment, extra clothing, person, body, mannequin, hanger, clip, hands, price tag, watermark"


def cutout(color=(40, 80, 120)):
    a = np.zeros((100, 100, 4), np.uint8)
    a[10:90, 25:75, :3] = color
    a[10:90, 25:75, 3] = 255
    return png(Image.fromarray(a))


def test_canvas_centers_visible_garment_at_88_percent():
    canvas, area_fraction = qwen_flatlay.prepare_canvas(cutout())
    a = np.array(Image.open(io.BytesIO(canvas)))
    assert a.shape == (1024, 768, 3)
    ys, xs = np.where(np.any(a < 240, axis=2))
    assert abs((ys.min() + ys.max()) / 2 - 511.5) <= 1
    assert abs((xs.min() + xs.max()) / 2 - 383.5) <= 1
    assert 898 <= ys.max() - ys.min() + 1 <= 902
    assert tuple(a[0, 0]) == (255, 255, 255)
    assert area_fraction == pytest.approx(563 * 901 / (768 * 1024))


def test_soft_lab_translation_moves_only_garment_and_lightness_by_70_percent():
    reference = cutout((110, 120, 140))
    result = np.full((100, 100, 3), 255, np.uint8)
    result[10:90, 25:75] = (60, 85, 120)
    mask = np.zeros((100, 100), bool)
    mask[10:90, 25:75] = True
    corrected = qwen_flatlay.soft_color_correct(reference, result, mask)
    lab_ref = cv2.cvtColor(np.float32([[[110, 120, 140]]]) / 255, cv2.COLOR_RGB2Lab)[0, 0]
    lab_old = cv2.cvtColor(result.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[50, 50]
    lab_new = cv2.cvtColor(corrected.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[50, 50]
    assert lab_new[0] - lab_old[0] == pytest.approx((lab_ref[0] - lab_old[0]) * .7, abs=.5)
    assert lab_new[1:] == pytest.approx(lab_ref[1:], abs=.8)
    assert np.array_equal(corrected[~mask], result[~mask])


@pytest.mark.parametrize("mask_fails", [False, True])
def test_qwen_exact_payload_safe_fallback_grey_composite_and_stage(monkeypatch, mask_fails):
    calls = []
    generated = np.full((1024, 768, 3), 255, np.uint8)
    generated[100:924, 200:568] = (250, 250, 250) if mask_fails else (65, 90, 125)
    if mask_fails:
        generated[300:330, 300:400] = 30  # 흰 옷 몸통이 아닌 로고만 남으면 안 된다.

    async def run(settings, model, payload):
        calls.append((model, payload, image_usage._ctx.get().stage))
        return {"images": [{"url": "https://fake/qwen.png", "width": 768, "height": 1024}]}

    async def fetch(url):
        assert url == "https://fake/qwen.png"
        return png(Image.fromarray(generated))

    async def segment(settings, data):
        assert data == png(Image.fromarray(generated))
        if mask_fails:
            raise SamUnavailable("down")
        rgba = Image.fromarray(generated).convert("RGBA")
        rgba.putalpha(Image.fromarray((np.any(generated < 245, axis=2) * 255).astype(np.uint8)))
        return png(rgba)

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)
    settings = SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test")
    with image_usage.job_scope(job_id="j", stage="matching_cutout"):
        result = asyncio.run(matching_flatlay.render_thumbnail(None, settings=settings,
                             cutout_png=cutout((250, 250, 250)) if mask_fails else cutout(),
                             clothing_type="bottom"))
        assert image_usage._ctx.get().stage == "matching_cutout"
    if mask_fails:
        assert result is None, "누끼 실패는 기존 누끼를 유지해야 한다"
        return
    assert result is not None and result.model == "fal-ai/qwen-image-edit-2511"
    model, payload, stage = calls[0]
    assert stage == "matching_flatlay"
    assert payload == {"image_urls": [payload["image_urls"][0]], "prompt": PROMPT,
                       "negative_prompt": NEGATIVE, "guidance_scale": 4.5,
                       "num_inference_steps": 28, "acceleration": "regular", "seed": 7,
                       "num_images": 1, "output_format": "png",
                       "image_size": {"width": 768, "height": 1024}}
    a = np.array(Image.open(io.BytesIO(result.raw)))
    assert tuple(a[0, 0]) == (232, 232, 230)
    assert tuple(a[500, 300]) != (65, 90, 125), "garment was color corrected"
    thumb = Image.open(io.BytesIO(result.image))
    assert thumb.format == "JPEG" and max(thumb.size) == 512


def test_qwen_failure_is_absorbed_and_provider_identities_are_separate(monkeypatch):
    async def down(*args):
        raise SamUnavailable("down")

    monkeypatch.setattr(fal_api, "run_model", down)
    result = asyncio.run(matching_flatlay.render_thumbnail(None,
        settings=SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test"),
        cutout_png=cutout(), clothing_type=None))
    assert result is None
    assert matching_flatlay.flatlay_fingerprint("h", provider="qwen") != matching_flatlay.flatlay_fingerprint("h")
    assert matching_flatlay.derived_asset_id(matching_item_id="i", source_hash="h", provider="qwen") != matching_flatlay.derived_asset_id(matching_item_id="i", source_hash="h")
    meta = matching_flatlay.metadata_for(source_hash="h", source_asset_id="s",
        matching_item_id="i", model="fal-ai/qwen-image-edit-2511", provider="qwen")
    assert meta["producer"] == "fal-qwen-matching-flatlay"
    assert meta["algorithmVersion"] == "matching-flatlay-v3-qwen"


@pytest.mark.parametrize("output_size,mask_width,accepted", [
    ((768, 1024), 30, False),  # 로고만 남음
    ((768, 1024), 337, False),  # 입력 옷 면적의 50% 바로 아래
    ((768, 1024), 338, True),   # 정확히 50%는 허용
    ((384, 512), 169, True),   # 해상도가 작아도 같은 비율이면 허용
])
@pytest.mark.parametrize("input_size", [(100, 100), (2000, 2000)])
def test_result_area_is_compared_in_prepared_canvas_coordinates(
        monkeypatch, output_size, mask_width, accepted, input_size):
    reference = png(Image.new("RGBA", input_size, (60, 80, 100, 255)))
    width, height = output_size
    rgb = Image.new("RGB", output_size, (65, 90, 125))
    # 정사각형 입력은 768x1024 캔버스에서 676x676 크기로 배치된다.
    alpha = np.zeros((height, width), np.uint8)
    alpha[:676 * height // 1024, :mask_width] = 255
    segmented = rgb.convert("RGBA")
    segmented.putalpha(Image.fromarray(alpha))

    async def run(*args):
        return {"images": [{"url": "https://fake/generated.png"}]}

    async def fetch(*args):
        return png(rgb)

    async def segment(*args):
        return png(segmented)

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)
    result = asyncio.run(matching_flatlay.render_thumbnail(None,
        settings=SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test"),
        cutout_png=reference, clothing_type=None))
    assert (result is not None) is accepted
