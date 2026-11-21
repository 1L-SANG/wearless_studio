import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

import numpy as np
import pytest
from PIL import Image

from app.services import garment_color_target as tone, garment_color_target_finish as finish
from app.services.garment_color_consistency import linear_to_oklab
from app.workers import detail_page_job as worker
from conftest import fake_worker_app, make_settings, worker_job
from storyboard_v4_cases import v4_blocks

ZERO = tone.ZERO_SLIDERS
SLIDERS = {**ZERO, "lightness": 50}


def test_solver_changes_only_selected_axes_and_caps_both_distances():
    median = tone.srgb8_to_linear([100, 100, 100])
    params, capped = finish.solve(median, np.array([1, 1, 1]), SLIDERS)
    assert capped
    assert params["lightness"] > 0
    assert params["chroma"] == params["warmth"] == 0
    lab_delta = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    assert lab_delta[0] == pytest.approx(.10)

    params, capped = finish.solve(median, np.array([.8, .25, .025]), {**ZERO, "chroma": 50})
    assert capped
    assert params["lightness"] == 0
    assert params["warmth"] != 0
    lab_delta = linear_to_oklab(tone.tone_linear(median, **params)) - linear_to_oklab(median)
    assert np.linalg.norm(lab_delta[1:]) == pytest.approx(.08)
    assert abs(lab_delta[0]) <= .10


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


@pytest.mark.parametrize("enabled,has_target,crash", [(False, True, False), (True, False, False), (True, True, True)])
def test_worker_finishes_when_color_stage_is_disabled_empty_or_raises(monkeypatch, enabled, has_target, crash):
    settings = make_settings(gemini_api_key="x", r2_bucket="b", garment_color_target_enabled=enabled)
    app = fake_worker_app(settings)
    product = {"name": "상품", "clothing_type": "top",
               "colors": [{"id": "c", "isBase": True, "images": [{"id": "a", "slot": "Front"}]}]}
    storyboard = v4_blocks([{"id": "p", "source": "ai", "cutType": "product"}])
    analysis = {tone.SLIDERS_KEY: {"version": 1, "colors": {"c": SLIDERS if has_target else ZERO}}}
    original = {"asset_id": "cut", "key": "p"}
    results = [{"blockId": "p", "imageUrl": "/v1/assets/cut/file"}]
    finalized = AsyncMock(return_value={"editor_blocks": [], "available": 0})
    failed = AsyncMock()
    stage = AsyncMock(side_effect=RuntimeError("segmentation unavailable") if crash else None)
    monkeypatch.setattr(worker.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(worker.repo, "get_product", AsyncMock(return_value=product))
    monkeypatch.setattr(worker.repo, "get_storyboard", AsyncMock(return_value=storyboard))
    monkeypatch.setattr(worker.repo, "get_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(worker.repo, "get_asset_for_user", AsyncMock(return_value={
        "id": "a", "r2_key": "a", "mime_type": "image/png"}))
    monkeypatch.setattr(worker.page_part_copy, "generate", AsyncMock(return_value={}))
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
    asyncio.run(worker.run_detail_page_job(app, worker_job()))
    assert stage.await_count == int(crash)
    if crash:
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
