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
from app.services.segmentation_types import SamUnavailable
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
    ref = np.array(Image.open(io.BytesIO(reference)))
    reference_mask = cv2.erode((ref[:, :, 3] > 127).astype(np.uint8),
                               np.ones((7, 7), np.uint8)).astype(bool)
    lab_ref = qwen_flatlay._color_target(ref, reference_mask)
    lab_old = cv2.cvtColor(result.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[50, 50]
    lab_new = cv2.cvtColor(corrected.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)[50, 50]
    assert lab_new[0] - lab_old[0] == pytest.approx((lab_ref[0] - lab_old[0]) * .7, abs=.5)
    assert lab_new[1:] == pytest.approx(lab_ref[1:], abs=.8)
    assert np.array_equal(corrected[~mask], result[~mask])


def _linear_scene(background=None, garment=(.65, .4, .5), illuminant=(1, 1, 1)):
    # 강한 중립 배경 질감은 옷 경계보다 많은 조명 단서를 제공한다.
    yy, xx = np.indices((192, 192))
    grey = np.where((xx // 8 + yy // 8) % 2, .92, .15)
    linear = np.repeat(grey[:, :, None], 3, axis=2)
    if background is not None:
        linear[:] = background
    alpha = np.zeros((192, 192), np.uint8)
    alpha[72:120, 72:120] = 255
    linear[alpha > 127] = garment
    linear = np.clip(linear * illuminant, 0, 1)
    srgb = np.where(linear <= .0031308, linear * 12.92,
                    1.055 * linear ** (1 / 2.4) - .055)
    return np.dstack((np.rint(srgb * 255).astype(np.uint8), alpha))


def _reference_target(ref, caplog):
    mask = cv2.erode((ref[:, :, 3] > 127).astype(np.uint8),
                     np.ones((7, 7), np.uint8)).astype(bool)
    with caplog.at_level("INFO", logger="wearless.qwen_flatlay"):
        target = qwen_flatlay._color_target(ref, mask)
    event = json.loads(caplog.records[-1].message)
    return target, event


def _garment_lab(ref):
    lab = cv2.cvtColor(ref[:, :, :3].astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    return np.median(lab[ref[:, :, 3] > 127], axis=0)


def test_neutral_light_preserves_pink_reference(caplog):
    ref = _linear_scene()
    target, event = _reference_target(ref, caplog)
    assert event["illumination_gain"] == pytest.approx([1, 1, 1], abs=.01)
    assert event["exposure_gain"] == pytest.approx(1, abs=.01)
    assert np.linalg.norm(target - _garment_lab(ref)) < 1


def test_yellow_light_recovers_pink_chroma(caplog):
    neutral = _linear_scene()
    yellow = _linear_scene(illuminant=(1.15, 1, .8))
    target, event = _reference_target(yellow, caplog)
    assert event["edge_energy"] >= .05
    assert event["agreement_cos"] >= .7
    assert event["weight"] == 1
    ideal = _garment_lab(neutral)[1:]
    old_distance = np.linalg.norm(_garment_lab(yellow)[1:] - ideal)
    assert old_distance > 5
    assert np.linalg.norm(target[1:] - ideal) <= old_distance / 2


def test_red_wall_limits_channel_gain_and_chroma_shift(caplog):
    ref = _linear_scene(background=(.8, .08, .04))
    target, event = _reference_target(ref, caplog)
    assert all(.75 <= value <= 1.3 for value in event["illumination_gain"])
    assert np.linalg.norm(target[1:] - _garment_lab(ref)[1:]) <= 10 + 1e-5
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1


def test_chroma_shift_cap_applies_at_full_strength(monkeypatch, caplog):
    # 단색 벽은 보정하지 않으므로, 경계가 있는 강한 색조명에서 상한을 확인한다.
    monkeypatch.setattr(qwen_flatlay, "CHROMA_SHIFT_STRENGTH", 1.)
    ref = _linear_scene(illuminant=(1.6, 1, .6))
    target, event = _reference_target(ref, caplog)
    assert np.linalg.norm(target[1:] - _garment_lab(ref)[1:]) == pytest.approx(10, abs=1e-5)
    assert event["chroma_capped"] is True


@pytest.mark.parametrize("stripes,expected", [(False, 1.3), (True, .95 / .9)])
def test_exposure_restores_dark_scene_without_blowing_white_stripes(caplog, stripes, expected):
    # 밝고 무채색인 배경이 있어야 노출을 올린다.
    ref = _linear_scene(background=(.67, .67, .67))
    if stripes:
        ref[80:86, 72:120, :3] = 244  # sRGB 244의 선형 휘도는 약 0.905다.
    target, event = _reference_target(ref, caplog)
    assert event["exposure_gain"] == pytest.approx(expected, abs=.007)
    assert target[0] > _garment_lab(ref)[0]


def test_exposure_never_darkens_bright_garment(caplog):
    ref = _linear_scene(background=(.67, .67, .67), garment=(.99, .99, .99))
    _, event = _reference_target(ref, caplog)
    assert event["exposure_gain"] == 1


def test_zero_rgb_outside_alpha_keeps_original_target(caplog):
    ref = _linear_scene()
    ref[ref[:, :, 3] == 0, :3] = 0
    target, event = _reference_target(ref, caplog)
    assert np.isfinite(target).all()
    assert event["background_pixels"] == 0
    assert event["reason"] == "too few background pixels"
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1
    assert np.array_equal(target, _garment_lab(ref))


@pytest.mark.parametrize("stripes", [False, True])
def test_plain_grey_wall_preserves_pink_and_white_stripes(caplog, stripes):
    ref = _linear_scene()
    ref[ref[:, :, 3] == 0, :3] = 150
    ref[ref[:, :, 3] > 127, :3] = (230, 140, 170)
    if stripes:
        ref[80:86, 72:120, :3] = 245
    target, event = _reference_target(ref, caplog)
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1
    assert event["edge_energy"] < .05
    assert np.max(np.abs(target[1:] - _garment_lab(ref)[1:])) < .3
    assert abs(target[0] - _garment_lab(ref)[0]) < 1
    corrected = qwen_flatlay.soft_color_correct(
        png(Image.fromarray(ref)), ref[:, :, :3], ref[:, :, 3] > 127)
    assert np.max(np.abs(corrected.astype(int) - ref[:, :, :3].astype(int))) <= 1


def test_plain_beige_wall_does_not_estimate_colored_light(caplog):
    ref = _linear_scene()
    ref[ref[:, :, 3] == 0, :3] = (220, 200, 170)
    _, event = _reference_target(ref, caplog)
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1


def test_clipped_warm_white_wall_does_not_use_garment_for_estimation(caplog):
    white = qwen_flatlay._srgb_to_linear(np.array([240, 240, 240]))
    ref = _linear_scene(background=white, illuminant=(1.15, 1, .8))
    target, event = _reference_target(ref, caplog)
    assert event["reason"] == "too few background pixels"
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1
    assert np.array_equal(target, _garment_lab(ref))


@pytest.mark.parametrize("size,patch_height", [(192, 8), (640, 15)])
def test_insufficient_background_count_or_fraction_keeps_original(caplog, size, patch_height):
    ref = np.zeros((size, size, 4), np.uint8)
    ref[size // 2:size // 2 + 40, size // 2:size // 2 + 40] = (230, 140, 170, 255)
    ref[:patch_height, :50, :3] = 150
    target, event = _reference_target(ref, caplog)
    assert event["reason"] == "too few background pixels"
    assert event["background_pixels"] == patch_height * 50
    assert event["illumination_gain"] == [1, 1, 1]
    assert np.array_equal(target, _garment_lab(ref))


def test_opposing_background_estimates_disable_color_shift(caplog):
    # 배경의 평균은 따뜻하지만 무늬의 변화량은 파란 채널이 가장 크다.
    yy, xx = np.indices((192, 192))
    texture = np.where((xx // 8 + yy // 8) % 2, 1, -1)
    background = np.array([.65, .4, .3]) + texture[:, :, None] * [.01, .1, .2]
    ref = _linear_scene(background=background)
    target, event = _reference_target(ref, caplog)
    assert event["edge_energy"] >= .05
    assert event["agreement_cos"] < 0
    assert event["weight"] == 0
    assert event["illumination_gain"] == [1, 1, 1]
    assert np.max(np.abs(target - _garment_lab(ref))) < .3


def test_five_percent_bright_neutral_background_allows_exposure(caplog):
    ref = _linear_scene()
    ref[ref[:, :, 3] == 0, :3] = 150
    ref[:9, :, :3] = 215
    _, event = _reference_target(ref, caplog)
    assert event["exposure_ref_frac"] == pytest.approx(.05, abs=.005)
    assert event["exposure_gain"] == pytest.approx(1.3, abs=.005)


def test_small_edge_background_disables_grey_edge(caplog):
    ref = _linear_scene(illuminant=(1.15, 1, .8))[48:144, 48:144].copy()
    _, event = _reference_target(ref, caplog)
    assert event["background_pixels"] >= 500
    assert event["edge_energy"] is None
    assert event["weight"] == 0
    assert event["illumination_gain"] == [1, 1, 1]


@pytest.mark.parametrize("size,patch_height,patch_width", [(64, 3, 64), (640, 10, 50)])
def test_too_few_exposure_references_by_count_or_fraction(caplog, size, patch_height, patch_width):
    ref = np.full((size, size, 4), (150, 150, 150, 0), np.uint8)
    ref[size // 2:size // 2 + 16, size // 2:size // 2 + 16] = (230, 140, 170, 255)
    ref[:patch_height, :patch_width, :3] = 215
    _, event = _reference_target(ref, caplog)
    assert event["exposure_ref_frac"] > 0
    assert event["exposure_gain"] == 1


def test_bright_colored_wall_is_not_an_exposure_reference(caplog):
    ref = _linear_scene()
    ref[ref[:, :, 3] == 0, :3] = (250, 220, 180)
    _, event = _reference_target(ref, caplog)
    assert event["exposure_ref_frac"] == 0
    assert event["exposure_gain"] == 1


@pytest.mark.parametrize("invalid", [0, np.nan, np.inf, -1])
def test_invalid_estimate_keeps_legacy_target_and_output(monkeypatch, caplog, invalid):
    ref = _linear_scene()
    rgb = ref[:, :, :3].copy()
    mask = ref[:, :, 3] > 127
    rgb[mask] = (100, 125, 150)
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    delta = _garment_lab(ref) - np.median(lab[mask], axis=0)
    delta[0] *= .7
    lab[mask] += delta
    lab[:, :, 0] = np.clip(lab[:, :, 0], 0, 100)
    lab[:, :, 1:] = np.clip(lab[:, :, 1:], -127, 127)
    legacy = np.clip(np.rint(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB) * 255), 0, 255).astype(np.uint8)
    legacy[~mask] = rgb[~mask]
    # 조명 추정 직전의 실패와 수치 오류 모두 원래 Lab 목표로 복귀해야 한다.
    monkeypatch.setattr(cv2, "GaussianBlur", lambda image, *args, **kwargs:
                        np.full_like(image, invalid))
    target, event = _reference_target(ref, caplog)
    assert np.array_equal(target, _garment_lab(ref))
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1
    corrected = qwen_flatlay.soft_color_correct(png(Image.fromarray(ref)), rgb, mask)
    assert np.array_equal(corrected, legacy)


def test_estimation_exception_keeps_legacy_target(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise cv2.error("synthetic estimation failure")

    monkeypatch.setattr(cv2, "GaussianBlur", broken)
    ref = _linear_scene()
    target, event = _reference_target(ref, caplog)
    assert np.array_equal(target, _garment_lab(ref))
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1


def test_large_reference_limits_estimation_resolution(caplog):
    ref = cv2.resize(_linear_scene(), (1536, 1152), interpolation=cv2.INTER_NEAREST)
    target, event = _reference_target(ref, caplog)
    assert 700_000 < event["background_pixels"] < 1024 * 768
    assert np.linalg.norm(target - _garment_lab(ref)) < 1


@pytest.mark.parametrize("color", [(0, 0, 0), (255, 255, 255), (120, 120, 120), (120, 0, 120)])
def test_empty_valid_pixels_zero_edges_or_zero_channel_use_legacy_target(caplog, color):
    ref = np.full((64, 64, 4), (*color, 255), np.uint8)
    target, event = _reference_target(ref, caplog)
    assert np.array_equal(target, _garment_lab(ref))
    assert event["illumination_gain"] == [1, 1, 1]
    assert event["exposure_gain"] == 1


@pytest.mark.parametrize("empty_reference", [True, False])
def test_empty_color_reference_or_garment_still_raises(empty_reference):
    ref = _linear_scene()
    mask = ref[:, :, 3] > 127
    if empty_reference:
        ref[:, :, 3] = 0
    else:
        mask[:] = False
    with pytest.raises(ValueError, match="empty color reference or garment"):
        qwen_flatlay.soft_color_correct(png(Image.fromarray(ref)), ref[:, :, :3], mask)


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
    mask_events = [event for event in events if "mask_source" in event]
    assert len(mask_events) == 1 and mask_events[0]["mask_source"] == "sam3"
    illumination_events = [event for event in events if "illumination_gain" in event]
    assert len(illumination_events) == 1
    assert set(illumination_events[0]) == {"illumination_gain", "exposure_gain", "background_pixels", "chroma_capped",
                              "edge_energy", "agreement_cos", "weight", "exposure_ref_frac", "reason"}
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
    mask_events = [event for event in events if "mask_source" in event]
    assert len(mask_events) == 1
    assert mask_events[0]["mask_source"] == "birefnet"
    assert mask_events[0]["result_area_fraction"] == .64


def test_birefnet_recovery_keeps_illumination_correction_and_background(birefnet_response):
    mask = np.zeros((100, 100), np.uint8)
    mask[10:90, 10:90] = 255
    calls = birefnet_response(png(Image.fromarray(mask)))
    yellow = _linear_scene(illuminant=(1.15, 1, .8))
    ideal_chroma = _garment_lab(_linear_scene())[1:]
    old_distance = np.linalg.norm(_garment_lab(yellow)[1:] - ideal_chroma)
    assert old_distance > 5
    result = asyncio.run(matching_flatlay.render_thumbnail(None,
        settings=SimpleNamespace(matching_flatlay_provider="qwen", fal_key="test"),
        cutout_png=png(Image.fromarray(yellow)), clothing_type="bottom"))
    assert result is not None
    raw = np.array(Image.open(io.BytesIO(result.raw)))
    result_lab = cv2.cvtColor(raw.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    assert np.linalg.norm(result_lab[50, 50, 1:] - ideal_chroma) < old_distance / 2 + 1
    assert tuple(raw[0, 0]) == (232, 232, 230)
    assert [path for path, _ in calls] == [
        "/fal-ai/qwen-image-edit-2511", "/fal-ai/sam-3/image", "/fal-ai/birefnet/v2"]


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


def test_transparent_render_keeps_alpha_and_default_bytes_match_existing_composite(monkeypatch):
    generated = np.full((100, 100, 3), 255, np.uint8)
    generated[10:90, 25:75] = (65, 90, 125)
    alpha = np.zeros((100, 100), np.uint8)
    alpha[10:90, 25:75] = 255

    async def run(*_):
        return {"images": [{"url": "data:fake"}]}

    async def fetch(*_):
        return png(Image.fromarray(generated))

    async def segment(*_):
        image = Image.fromarray(generated).convert("RGBA")
        image.putalpha(Image.fromarray(alpha))
        return png(image)

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)
    reference = cutout()
    corrected = qwen_flatlay.soft_color_correct(reference, generated, alpha > 127)
    image = Image.fromarray(corrected).convert("RGBA")
    image.putalpha(Image.fromarray(alpha))
    # This is the original pre-change _composite algorithm, including the final encode.
    from app.services.matching_cutout import flatten_on_bg
    expected_default = flatten_on_bg(png(image))
    assert asyncio.run(qwen_flatlay.render(SimpleNamespace(), reference)) == expected_default
    transparent = asyncio.run(qwen_flatlay.render(SimpleNamespace(), reference, transparent=True))
    decoded = Image.open(io.BytesIO(transparent))
    assert decoded.mode == "RGBA"
    assert np.array_equal(np.array(decoded.getchannel("A")), alpha)
    assert transparent == png(image)
