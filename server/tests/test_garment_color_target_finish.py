import asyncio
from io import BytesIO
from unittest.mock import ANY, AsyncMock, Mock

import numpy as np
import pytest
from PIL import Image

from app.services import garment_color_target as tone, garment_color_target_finish as finish
from app.services.garment_color_consistency import _oklab_to_linear, linear_to_oklab
from app.workers import detail_page_job as worker
from conftest import fake_worker_app, make_settings, worker_job
from storyboard_v4_cases import v4_blocks

ZERO = tone.ZERO_SLIDERS
SLIDERS = {**ZERO, "lightness": 50}


def test_solver_keeps_both_caps_after_limiting_seller_movement():
    median = np.full(3, .8)
    sliders = {**ZERO, "lightness": 100}
    target = tone.tone_linear(median, **sliders)
    params, capped, seller_shift, move = finish.solve(median, median, target, sliders)
    assert capped
    assert params["lightness"] > 0
    assert params["chroma"] == params["warmth"] == 0
    assert move[0] == pytest.approx(seller_shift[0])
    lab_delta = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    assert lab_delta[0] == pytest.approx(.10)

    median = np.array([0., 1., 0.])
    sliders = {**ZERO, "chroma": -100}
    target = tone.tone_linear(median, **sliders)
    params, capped, seller_shift, move = finish.solve(median, median, target, sliders)
    assert capped
    assert params["lightness"] == 0
    np.testing.assert_allclose(move[1:], seller_shift[1:])
    lab_delta = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    assert np.linalg.norm(lab_delta[1:]) == pytest.approx(.08)
    assert abs(lab_delta[0]) <= .10


def test_brightening_does_not_darken_an_already_brighter_cut():
    source = np.full(3, .4)
    median = np.full(3, .6)
    sliders = {**ZERO, "lightness": 15}
    target = tone.tone_linear(source, **sliders)
    params, capped, seller_shift, move = finish.solve(median, source, target, sliders)
    assert seller_shift[0] > 0
    np.testing.assert_array_equal(move, np.zeros(3))
    assert params is None and not capped


@pytest.mark.parametrize(("lightness", "median_value"), [(10, .1), (-10, .7)])
def test_large_original_gap_does_not_amplify_small_seller_change(lightness, median_value):
    source = np.full(3, .4)
    median = np.full(3, median_value)
    sliders = {**ZERO, "lightness": lightness}
    target = tone.tone_linear(source, **sliders)
    params, capped, seller_shift, move = finish.solve(median, source, target, sliders)
    assert abs(move[0]) == pytest.approx(abs(seller_shift[0]))
    assert np.sign(move[0]) == np.sign(lightness)
    assert not capped
    assert params["chroma"] == params["warmth"] == 0
    actual_move = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    np.testing.assert_allclose(actual_move, move, atol=1e-9)


def test_solver_applies_only_the_half_the_cut_has_not_reached():
    source = np.full(3, .4)
    target = tone.tone_linear(source, **SLIDERS)
    median = _oklab_to_linear((linear_to_oklab(source) + linear_to_oklab(target)) / 2)
    params, capped, seller_shift, move = finish.solve(median, source, target, SLIDERS)
    assert move[0] == pytest.approx(seller_shift[0] / 2)
    assert not capped
    actual_move = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    np.testing.assert_allclose(actual_move, move, atol=1e-9)


def test_chroma_adjustment_leaves_perpendicular_hue_difference_out_of_move():
    source = tone.srgb8_to_linear([130, 80, 180])
    sliders = {**ZERO, "chroma": 40}
    target = tone.tone_linear(source, **sliders)
    target_lab = linear_to_oklab(target)
    shift = target_lab - linear_to_oklab(source)
    ab = shift[1:]
    perpendicular = np.array([-ab[1], ab[0]]) / np.linalg.norm(ab)
    median_lab = target_lab - np.array([.02, *(ab / 2 + .04 * perpendicular)])
    median = _oklab_to_linear(median_lab)

    params, capped, seller_shift, move = finish.solve(median, source, target, sliders)
    assert not capped and params["lightness"] == 0
    np.testing.assert_allclose(seller_shift, shift, atol=1e-9)
    assert move[0] == 0
    np.testing.assert_allclose(move[1:], ab / 2, atol=1e-9)
    assert move[1:] @ perpendicular == pytest.approx(0, abs=1e-9)
    corrected_lab = linear_to_oklab(tone.tone_linear(median, **params))
    assert np.linalg.norm(corrected_lab - (median_lab + move)) < .002


def test_neutral_source_has_no_direction_when_only_chroma_is_changed():
    source = np.full(3, .4)
    median = tone.srgb8_to_linear([60, 140, 200])
    sliders = {**ZERO, "chroma": 100}
    target = tone.tone_linear(source, **sliders)
    params, capped, _, move = finish.solve(median, source, target, sliders)
    np.testing.assert_array_equal(move, np.zeros(3))
    assert params is None and not capped


def test_stage_reports_no_change_and_skips_render_for_subthreshold_move(monkeypatch):
    app = fake_worker_app(make_settings(garment_color_target_enabled=True))
    sliders = {**ZERO, "lightness": 1}
    sample = np.full(3, 100.)
    source = tone.srgb8_to_linear(sample)
    measure = AsyncMock(return_value=(None, sample, None, None))
    render = Mock(return_value=(None, sample.tolist()))
    monkeypatch.setattr(finish.sources, "read_front", AsyncMock(return_value=b"front"))
    monkeypatch.setattr(finish.sources, "measure", measure)
    monkeypatch.setattr(finish, "_measure_cut", AsyncMock(return_value=((b"cut", None, None, sample), None)))
    monkeypatch.setattr(finish, "render", render)
    pending, report = asyncio.run(finish.stage(
        app, worker_job(), sliders={"c": sliders}, fronts={"c": "a"}, block_colors={"b": "c"},
        assets=[{"asset_id": "cut"}], results=[{"blockId": "b", "imageUrl": "/v1/assets/cut/file"}],
        clothing_type="top", sub_category=None, stored_outputs=[],
    ))
    assert not pending
    render.assert_not_called()
    measure.assert_awaited_once()
    assert report["c"]["cuts"]["b"] == {
        "status": "skipped", "reason": "no_change", "beforeSrgb": sample.tolist(), "afterSrgb": sample.tolist(),
    }
    expected_shift = linear_to_oklab(tone.tone_linear(source, **sliders)) - linear_to_oklab(source)
    assert 0 < np.linalg.norm(report["c"]["move"]) < .002
    np.testing.assert_allclose(report["c"]["sellerShift"], expected_shift)
    np.testing.assert_allclose(report["c"]["move"], [expected_shift[0], 0, 0])
    assert report["c"]["params"] is None and not report["c"]["capped"]


def test_render_preserves_outside_bytes_and_relative_light_between_cuts():
    rgb = np.full((16, 16, 4), 255, dtype=np.uint8)
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    mask[1:15, 1:15] = 255
    q = {**ZERO, "lightness": 60}
    before, after = [], []
    for level in (90, 160):
        rgb[mask > 0, :3] = level
        rgb[3, 3, :3] = (250, 40, 40)
        rgba = rgb.copy()
        png, center = finish.render(rgba, mask, np.full(3, level), q)
        pixels = np.asarray(Image.open(BytesIO(png)))
        assert np.array_equal(pixels[mask == 0], rgba[mask == 0])
        assert np.array_equal(pixels[3, 3], rgba[3, 3])
        before.append(tone.srgb8_to_linear([level] * 3))
        after.append(tone.srgb8_to_linear(center))
    np.testing.assert_allclose(after[1] / after[0], before[1] / before[0], rtol=.02)


def test_deep_shadows_keep_full_tone_and_different_chromaticity_is_protected():
    center = np.array([180, 115, 80], dtype=np.uint8)
    shadow = np.rint(finish._encode_srgb(tone.srgb8_to_linear(center) * .045)).astype(np.uint8)
    rgba = np.full((12, 12, 4), 255, dtype=np.uint8)
    rgba[..., :3] = center
    rgba[2, 2, :3] = shadow
    rgba[2, 3, :3] = (160, 30, 20)
    rgba[2, 4, :3] = 0
    mask = np.full(rgba.shape[:2], 255, dtype=np.uint8)

    png, _ = finish.render(rgba, mask, center, SLIDERS)
    result = np.asarray(Image.open(BytesIO(png)))
    expected = np.rint(finish._encode_srgb(tone.tone_linear(tone.srgb8_to_linear(shadow), **SLIDERS)))
    np.testing.assert_array_equal(result[2, 2, :3], expected)
    np.testing.assert_array_equal(result[2, 3:5], rgba[2, 3:5])


@pytest.mark.parametrize(("enabled", "has_target", "owns_lease"), [
    (False, True, True), (True, False, True), (True, True, False), (True, True, True),
])
def test_worker_checks_lease_before_color_stage_and_keeps_originals_on_stage_failure(
    monkeypatch, enabled, has_target, owns_lease,
):
    settings = make_settings(gemini_api_key="x", r2_bucket="b", garment_color_target_enabled=enabled)
    app = fake_worker_app(settings)
    product = {"name": "상품", "clothing_type": "top",
               "colors": [{"id": "c", "isBase": True, "images": [{"id": "a", "slot": "Front"}]}]}
    storyboard = v4_blocks([{"id": "p", "source": "ai", "cutType": "product"}])
    analysis = {tone.SLIDERS_KEY: {"version": 1, "colors": {"c": SLIDERS if has_target else ZERO}}}
    original = {"asset_id": "cut", "key": "p"}
    results = [{"blockId": "p", "imageUrl": "/v1/assets/cut/file"}]
    finalized = AsyncMock(return_value={"editor_blocks": [], "available": 0} if owns_lease else None)
    failed = AsyncMock()
    renew = AsyncMock(return_value=owns_lease)
    renewals_at_stage_start = []

    async def fail_stage(*args, **kwargs):
        renewals_at_stage_start.append(renew.await_count)
        raise RuntimeError("segmentation unavailable")

    stage = AsyncMock(side_effect=fail_stage)
    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product))
    monkeypatch.setattr(worker.repo, "get_storyboard", AsyncMock(return_value=storyboard))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(worker.repo, "get_asset_for_user", AsyncMock(return_value={
        "id": "a", "r2_key": "a", "mime_type": "image/png"}))
    monkeypatch.setattr(worker.page_copy, "has_provider_key", lambda _settings: False)
    monkeypatch.setattr(worker.page_assembler, "assemble", lambda *_a, **_kw: [])
    monkeypatch.setattr(worker, "_emit", AsyncMock())
    async def generate(*args, **kwargs):
        analysis[tone.SLIDERS_KEY]["colors"]["c"] = ZERO
        product["colors"][0]["images"][0]["id"] = "replacement"
        return results, [original], 0, [], None, []

    monkeypatch.setattr(worker, "_gen_cuts", generate)
    monkeypatch.setattr(worker.repo, "finalize_detail_page_success", finalized)
    monkeypatch.setattr(worker.repo, "finalize_detail_page_failure", failed)
    monkeypatch.setattr(finish, "stage", stage)
    monkeypatch.setattr(worker.repo, "renew_job_lease", renew)
    job = worker_job()
    asyncio.run(worker.run_detail_page_job(app, job))
    if enabled and has_target:
        renew.assert_awaited_once_with(ANY, job["id"], job["lease_token"])
    else:
        renew.assert_not_awaited()
    should_stage = enabled and has_target and owns_lease
    assert stage.await_count == int(should_stage)
    if should_stage:
        assert renewals_at_stage_start == [1]
        assert stage.await_args.kwargs["sliders"] == {"c": SLIDERS}
        assert stage.await_args.kwargs["fronts"] == {"c": "a"}
    finalized.assert_awaited_once()
    assert finalized.await_args.kwargs["cut_assets"] == [original]
    failed.assert_not_awaited()


def test_real_model_cut_never_goes_to_segmentation(monkeypatch):
    app = fake_worker_app(make_settings(garment_color_target_enabled=True))
    read = AsyncMock()
    segment = AsyncMock()
    monkeypatch.setattr(finish.sources, "read_front", read)
    monkeypatch.setattr(finish.garment_color_tune, "segment_seller_garment", segment)
    assets = [{"asset_id": "real-cut", "metadata": {"facemarket_real_derived": True}}]
    results = [{"blockId": "real", "imageUrl": "/v1/assets/real-cut/file"}]
    pending, report = asyncio.run(finish.stage(
        app, worker_job(), sliders={"c": SLIDERS}, fronts={"c": "a"}, block_colors={"real": "c"},
        assets=assets, results=results, clothing_type="top", sub_category=None, stored_outputs=[],
    ))
    assert not pending
    assert report["c"]["cuts"]["real"]["status"] == "skipped"
    assert report["c"]["cuts"]["real"]["reason"] == "real_model"
    read.assert_not_awaited()
    segment.assert_not_awaited()


def test_stage_updates_aliases_and_keeps_failed_internal_assets_off_page(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    images = {}
    for key, level in (("worn", 110), ("detail", 150), ("untouched", 200)):
        out = BytesIO()
        Image.new("RGB", (16, 16), (level,) * 3).save(out, "PNG")
        images[key] = out.getvalue()

    def put(key, data, mime, *, cache):
        images[key] = data

    settings = make_settings(garment_color_target_enabled=True, r2_bucket="test-bucket")
    app = fake_worker_app(settings, r2=SimpleNamespace(get_bytes=images.__getitem__, put_bytes=put))
    monkeypatch.setattr(finish.sources, "read_front", AsyncMock(return_value=b"front"))
    monkeypatch.setattr(finish.sources, "measure", AsyncMock(
        return_value=(None, np.array([190, 190, 190]), None, None)))
    segments = []

    async def segment(_settings, data, rgba, *, protect_matching, detail, **_kw):
        segments.append((data, protect_matching, detail))
        alpha = np.zeros(rgba.shape[:2], dtype=np.uint8)
        alpha[2:14, 2:14] = 255
        return alpha

    monkeypatch.setattr(finish.garment_color_tune, "segment_seller_garment", segment)
    job = worker_job()
    assets = [
        {"asset_id": "worn", "key": "worn", "mime": "image/png", "bucket": "test-bucket",
         "metadata": {"cut_type": "horizon"}, "sha256": "worn-hash", "size": len(images["worn"])},
        {"asset_id": "detail", "key": "detail", "mime": "image/png", "bucket": "test-bucket",
         "metadata": {"cut_type": "product", "detail_recipe": {"shot": "detail"}},
         "sha256": "detail-hash", "size": len(images["detail"])},
        {"asset_id": "untouched", "key": "untouched", "metadata": {}},
    ]
    results = [{"blockId": block, "imageUrl": f"/v1/assets/{asset}/file"}
               for block, asset in (("base-worn", "worn"), ("base-alias", "worn"),
                                    ("base-detail", "detail"), ("other", "untouched"))]
    results[0]["overlayTextPlates"] = {"old": True}
    contrast = AsyncMock(return_value={"width": 16, "height": 16, "hookTitleColor": "#ffffff"})
    pending, report = asyncio.run(finish.stage(
        app, job, sliders={"base": SLIDERS}, fronts={"base": "base-front"},
        block_colors={"base-worn": "base", "base-alias": "base", "base-detail": "base", "other": "other"},
        assets=assets, results=results, clothing_type="top", sub_category=None, stored_outputs=[],
        measure_contrast=contrast,
    ))
    assert sorted(segments) == sorted([(images["worn"], True, False), (images["detail"], False, True)])
    finish.sources.measure.assert_awaited_once()
    source = tone.srgb8_to_linear([190] * 3)
    expected_shift = linear_to_oklab(tone.tone_linear(source, **SLIDERS)) - linear_to_oklab(source)
    np.testing.assert_allclose(report["base"]["sellerShift"], expected_shift)
    np.testing.assert_allclose(report["base"]["move"], [expected_shift[0], 0, 0])
    assert pending["worn"].asset["metadata"]["colorTarget"]["params"] == report["base"]["params"]
    assert pending["detail"].asset["metadata"]["colorTarget"]["params"] == report["base"]["params"]
    saved = []

    @asynccontextmanager
    async def transaction():
        yield

    async def save_asset(_conn, *, asset_id, metadata, **kwargs):
        if asset_id == pending["detail"].masters[0]["asset_id"]:
            raise RuntimeError("asset insert failed")
        saved.append((asset_id, metadata["internalGarmentColorTarget"]))

    monkeypatch.setattr(finish.storage.repo, "create_asset", save_asset)
    asyncio.run(finish.persist(SimpleNamespace(transaction=transaction), job, pending, report, assets, results))
    final = assets[0]
    color_metadata = final["metadata"]["colorTarget"]
    assert final["asset_id"] == "worn"
    original_id = color_metadata["originalAssetId"]
    assert original_id != "worn"
    assert images[pending["worn"].masters[0]["key"]] == images["worn"]
    assert pending["worn"].masters[0]["key"] != "worn"
    assert color_metadata["toneVersion"] == "garment-color-v1"
    assert saved == [(original_id, "original"), (color_metadata["maskAssetId"], "mask")]
    assert results[0]["imageUrl"] == results[1]["imageUrl"] == f"/v1/assets/{final['asset_id']}/file"
    assert results[0]["hookTitleColor"] == results[1]["hookTitleColor"] == "#ffffff"
    assert "overlayTextPlates" not in results[0]
    assert assets[1]["asset_id"] == "detail" and results[2]["imageUrl"] == "/v1/assets/detail/file"
    assert assets[2]["asset_id"] == "untouched" and results[3]["imageUrl"] == "/v1/assets/untouched/file"
    cuts = report["base"]["cuts"]
    assert cuts["base-worn"] == cuts["base-alias"]
    assert cuts["base-worn"]["status"] == "applied"
    assert cuts["base-worn"]["beforeSrgb"] != cuts["base-worn"]["afterSrgb"]
    assert cuts["base-detail"]["status"] == "reverted"
    assert cuts["base-detail"]["reason"] == "internal_storage_failed"


def test_segmentation_subtracts_small_matching_overlap():
    mask = np.zeros((100, 100), dtype=bool)
    mask[10:50, 20:80] = True
    matching = np.zeros_like(mask)
    matching[49:90, 30:60] = True
    alpha = finish.garment_color_tune._select_seller_garment(
        [mask], [matching], np.full(mask.shape, 255, dtype=np.uint8), detail=False,
    )
    np.testing.assert_array_equal(alpha > 0, mask & ~matching)


@pytest.mark.parametrize("kind", ["original", "mask"])
def test_internal_assets_are_not_served_as_public_capabilities(client, monkeypatch, kind):
    from app import repo, routes
    from conftest import patch_route_db

    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_asset_public", AsyncMock(return_value={
        "r2_key": "private-object", "source": "ai", "metadata": {"internalGarmentColorTarget": kind},
    }))
    url = "/v1/assets/00000000-0000-0000-0000-000000000001"
    assert client.get(url + "/file", follow_redirects=False).status_code == 404
    assert client.get(url + "/bytes").status_code == 404


def test_source_cancellation_leaves_staged_cut_unchanged(monkeypatch):
    app = fake_worker_app(make_settings(garment_color_target_enabled=True))
    read = AsyncMock(side_effect=asyncio.CancelledError)
    segment = AsyncMock()
    monkeypatch.setattr(finish.sources, "read_front", read)
    monkeypatch.setattr(finish.garment_color_tune, "segment_seller_garment", segment)
    assets = [{"asset_id": "cut", "metadata": {}}]
    results = [{"blockId": "b", "imageUrl": "/v1/assets/cut/file"}]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(finish.stage(
            app, worker_job(), sliders={"c": SLIDERS}, fronts={"c": "a"}, block_colors={"b": "c"},
            assets=assets, results=results, clothing_type="top", sub_category=None, stored_outputs=[],
        ))
    assert assets == [{"asset_id": "cut", "metadata": {}}]
    segment.assert_not_awaited()
