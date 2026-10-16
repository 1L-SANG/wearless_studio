import asyncio
import io
import json
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

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


def white_sleeve_cutout():
    image = Image.new("RGBA", (400, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(100, 60), (20, 120), (0, 230), (100, 250)],
                 fill=(250, 250, 250, 255))
    draw.polygon([(300, 60), (380, 120), (399, 230), (300, 250)],
                 fill=(250, 250, 250, 255))
    draw.rectangle((100, 60, 300, 480), fill=(40, 80, 120, 255))
    return png(image)


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
def test_qwen_exact_payload_safe_fallback_grey_composite_and_stage(monkeypatch, caplog, mask_fails):
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
    with caplog.at_level("INFO"), image_usage.job_scope(job_id="j", stage="matching_cutout"):
        result = asyncio.run(matching_flatlay.render_thumbnail(None, settings=settings,
                             cutout_png=cutout((250, 250, 250)) if mask_fails else cutout(),
                             clothing_type="bottom"))
        assert image_usage._ctx.get().stage == "matching_cutout"
    assert len(calls) == 1, "SAM 성공과 가용성 오류에는 BiRefNet을 부르지 않는다"
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
    events = [json.loads(r.message) for r in caplog.records if r.name == "wearless.qwen_flatlay"]
    assert len(events) == 1 and events[0]["mask_source"] == "sam3"
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


@pytest.mark.parametrize("reference", [cutout((110, 75, 40)), white_sleeve_cutout()],
                         ids=["brown_garment", "white_sleeves"])
def test_no_garment_mask_and_empty_birefnet_keep_successful_cutout(monkeypatch, reference):
    generated, _ = qwen_flatlay.prepare_canvas(reference)

    async def run(settings, model, payload):
        if model == fal_api.QWEN_MODEL:
            return {"images": [{"url": "https://fake/generated.png"}]}
        if model == fal_api.SAM_MODEL:
            return {"masks": [], "scores": []}
        assert model == "fal-ai/birefnet/v2"
        return {}

    async def fetch(url):
        assert url == "https://fake/generated.png"
        return generated

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    result = asyncio.run(matching_flatlay.render_thumbnail(None,
        settings=SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test"),
        cutout_png=reference, clothing_type="top"))
    assert result is None, "마스크가 없으면 흰 배색까지 보존한 기존 누끼를 유지한다"


def _generated_result(monkeypatch, rgb, error):
    async def run(*args):
        return {"images": [{"url": "https://fake/generated.png"}]}

    async def fetch(url):
        return png(Image.fromarray(rgb))

    async def segment(*args):
        raise error

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)


@pytest.mark.parametrize("error", [SamUnavailable("down"), ValueError("invalid mask image")])
def test_second_segmentation_errors_propagate(monkeypatch, error):
    generated = np.full((100, 100, 3), 255, np.uint8)
    generated[10:90, 10:90] = (65, 90, 125)
    _generated_result(monkeypatch, generated, error)
    with pytest.raises(type(error)) as exc:
        asyncio.run(qwen_flatlay.render(SimpleNamespace(fal_key="test"), cutout()))
    assert exc.value is error


@pytest.fixture
def birefnet_response(monkeypatch):
    """실제 fal 경계를 유지하고 HTTP 응답만 합성 이미지로 대체한다."""
    def setup(mask, *, body=None, source_area=None):
        generated = png(Image.new("RGB", (100, 100), (65, 90, 125)))
        generated_url = fal_api.image_input_url(generated, "image/png")
        mask_url = fal_api.image_input_url(mask, "image/png")
        calls = []

        def handler(request):
            calls.append((request.url.path, json.loads(request.content)))
            if request.url.path == "/fal-ai/qwen-image-edit-2511":
                return httpx.Response(200, json={"images": [{"url": generated_url}]})
            if request.url.path == "/fal-ai/sam-3/image":
                return httpx.Response(200, json={"masks": [], "scores": []})
            assert request.url.path == "/fal-ai/birefnet/v2"
            return httpx.Response(200, json=body if body is not None else {
                "image": {"url": mask_url, "content_type": "image/png"}})

        real_client = httpx.AsyncClient
        monkeypatch.setattr(fal_api.httpx, "AsyncClient", lambda **kw: real_client(
            transport=httpx.MockTransport(handler), **kw))
        if source_area is not None:
            monkeypatch.setattr(qwen_flatlay, "prepare_canvas", lambda _: (generated, source_area))
        return calls
    return setup


def test_birefnet_recovers_second_sam_empty_mask_with_inline_payload(birefnet_response, caplog):
    # 127은 배경, 128은 옷이다. 작은 마스크를 생성 이미지 크기로 맞춘다.
    mask = np.full((50, 50), 127, np.uint8)
    mask[5:45, 5:45] = 128
    calls = birefnet_response(png(Image.fromarray(mask)))
    with caplog.at_level("INFO"):
        result = asyncio.run(matching_flatlay.render_thumbnail(None,
            settings=SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test"),
            cutout_png=cutout(), clothing_type="bottom"))
    assert result is not None
    raw = np.array(Image.open(io.BytesIO(result.raw)))
    assert tuple(raw[0, 0]) == (232, 232, 230)
    assert tuple(raw[50, 50]) != (232, 232, 230)
    assert Image.open(io.BytesIO(result.image)).format == "JPEG"
    assert [path for path, _ in calls] == [
        "/fal-ai/qwen-image-edit-2511", "/fal-ai/sam-3/image", "/fal-ai/birefnet/v2"]
    payload = calls[-1][1]
    assert payload == {"image_url": calls[1][1]["image_url"],
        "model": "General Use (Heavy)", "operating_resolution": "1024x1024",
        "output_format": "png", "mask_only": True, "refine_foreground": False,
        "sync_mode": True}
    assert payload["image_url"].startswith("data:image/jpeg;base64,")
    events = [json.loads(r.message) for r in caplog.records if r.name == "wearless.qwen_flatlay"]
    assert len(events) == 1
    assert events[0]["mask_source"] == "birefnet"
    assert events[0]["result_area_fraction"] == .64


@pytest.mark.parametrize("source_area,pixels,error", [
    (.4, 1999, "too small"),
    (.4, 2000, None),        # 정확히 원본의 0.5배
    (.4, 6400, None),        # 정확히 원본의 1.6배
    (.4, 6401, "too large"),
    (.6, 8500, None),        # 정확히 캔버스의 85%
    (.6, 8501, "too large"),
])
def test_birefnet_area_limits_include_boundaries(birefnet_response, source_area, pixels, error):
    mask = np.zeros((100, 100), np.uint8)
    mask.flat[:pixels] = 255
    birefnet_response(png(Image.fromarray(mask)), source_area=source_area)
    if error:
        with pytest.raises(ValueError, match=f"generated garment mask is {error}"):
            asyncio.run(qwen_flatlay.render(SimpleNamespace(fal_key="test"), cutout()))
    else:
        result = asyncio.run(qwen_flatlay.render(SimpleNamespace(fal_key="test"), cutout()))
        assert Image.open(io.BytesIO(result)).size == (100, 100)


@pytest.mark.parametrize("body,mask", [
    ({}, png(Image.new("L", (10, 10), 255))),
    ({"image": None}, png(Image.new("L", (10, 10), 255))),
    ({"image": []}, png(Image.new("L", (10, 10), 255))),
    ({"image": {}}, png(Image.new("L", (10, 10), 255))),
    ({"image": {"url": ""}}, png(Image.new("L", (10, 10), 255))),
    ({"image": {"url": 123}}, png(Image.new("L", (10, 10), 255))),
    (None, png(Image.new("L", (10, 10), 0))),
    (None, b"broken mask image"),
])
def test_invalid_birefnet_mask_fails_and_thumbnail_keeps_cutout(birefnet_response, body, mask):
    calls = birefnet_response(mask, body=body)
    settings = SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test")
    with pytest.raises(fal_cutout.NoGarmentMask):
        asyncio.run(qwen_flatlay.render(settings, cutout()))
    assert asyncio.run(matching_flatlay.render_thumbnail(None, settings=settings,
        cutout_png=cutout(), clothing_type="bottom")) is None
    assert sum(path == "/fal-ai/birefnet/v2" for path, _ in calls) == 2
