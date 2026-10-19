"""fal 후보 생성만 대역으로 두고 기존 톤 판정과 저장 계약을 검증한다."""
import asyncio
import importlib
import importlib.util
import io
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.services import editor_garment_mask as egm, fal_api, sam_retry
from app.services.segmentation_types import SamUnavailable
from conftest import make_settings
from test_tone_mask_rules import _mask, _cut_bytes, SHAPE, PRODUCT_BOX, COORD_BOX


@pytest.fixture
def tone():
    assert importlib.util.find_spec("app.services.tone_mask") is not None, "API tone rules are missing"
    return importlib.import_module("app.services.tone_mask")


def images():
    return (_cut_bytes(SHAPE, top_box=PRODUCT_BOX, bottom_box=COORD_BOX),
            _cut_bytes(SHAPE))


class MemoryR2:
    def __init__(self):
        generated, base = images()
        self.objects = {"cut": generated, "base": base, "product": generated}
        self.puts = []

    def get_bytes(self, key):
        return self.objects[key]

    def head(self, key):
        return {"size": len(self.objects[key])} if key in self.objects else None

    def put_bytes(self, key, data, mime):
        self.objects[key] = data
        self.puts.append(key)


def wire_fal(monkeypatch, masks, *, fail=False):
    calls = []
    payloads = {}
    for i, mask in enumerate(masks):
        rgb = np.zeros((*mask.shape, 3), np.uint8)
        rgb[mask] = (0, 80, 0)  # R=0, max>0 이므로 컬러 마스크여야 한다.
        out = io.BytesIO()
        Image.fromarray(rgb).save(out, "PNG")
        payloads[f"mask:{i}"] = out.getvalue()

    async def run(settings, model, payload):
        calls.append((model, payload))
        if fail:
            raise SamUnavailable("fal temporary outage")
        return {"masks": [{"url": key} for key in payloads], "scores": [.99] * len(payloads)}

    async def fetch(url):
        return payloads[url]

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    return calls


@pytest.mark.parametrize("case", ["product", "fallback", "matching_only", "background", "empty"])
def test_candidate_selection_preserves_product_pixels_and_fallback_guard(tone, monkeypatch, case):
    product, pants = _mask(SHAPE, PRODUCT_BOX), _mask(SHAPE, COORD_BOX)
    candidates = {"product": [pants, product, product],
                  "fallback": [product | pants, _mask(SHAPE, (70, 116, 36, 84))],
                  "matching_only": [pants],
                  "background": [product | pants, _mask(SHAPE, (70, 116, 4, 116))],
                  "empty": []}[case]
    generated, base = images()
    if case in ("matching_only", "background", "empty"):
        with pytest.raises(tone.NoGarmentCandidate):
            tone.produce(generated, base, candidates=candidates, clothing_type="top", matching_side="bottom")
    else:
        result = tone.produce(generated, base, candidates=candidates, clothing_type="top", matching_side="bottom")
        chosen = np.array(Image.open(io.BytesIO(result.png))) > 127
        expected = product if case == "product" else _mask(SHAPE, (70, 116, 36, 84))
        assert np.array_equal(chosen, expected)
        assert result.selected_rank == 0
        assert result.vetoed_attempts == 0
        assert result.match_share == 0.0


def test_top_pick_is_not_held_to_the_fallback_background_ceiling(tone, monkeypatch):
    """2026-10-19 운영 재현: 흰 배경 위 연회색 티를 figure 추정이 놓쳐 1등 후보가 몸 밖 45%로 보였다.

    운영 SAM2는 1등의 정제 마스크를 배경 문턱에서 뺐다(worn_garment.py 622-626). 정제가 없는
    이식판에서는 1등 후보 자체가 그 자리다.
    """
    generated, base = images()
    product = _mask(SHAPE, PRODUCT_BOX)
    monkeypatch.setattr(tone, "figure_silhouette", lambda img: np.zeros(img.shape[:2], bool))
    out = tone.produce(generated, base, candidates=[product], clothing_type="top",
                       matching_side="bottom")
    assert out.selected_rank == 0 and out.area_frac > 0
    # 대체 후보의 문턱은 위 동일성 시험의 "background" 경우가 지킨다.


@pytest.mark.parametrize("category,sub,prompt", [("top", None, "top"), ("bottom", "pants", "pants"),
    ("bottom", "skirt", "skirt"), ("dress", None, "dress"), ("outer", None, "outerwear"),
    (None, None, "top")])
def test_fal_prompts_png_and_cache(tone, monkeypatch, category, sub, prompt):
    calls = wire_fal(monkeypatch, [_mask(SHAPE, PRODUCT_BOX)])
    r2 = MemoryR2()
    settings = SimpleNamespace(fal_key="test")
    async def run():
        first = await tone.segment_worn_garment(settings, r2, source_key="cut", base_key="base",
            clothing_type=category, sub_category=sub, product_key="product")
        second = await tone.segment_worn_garment(settings, r2, source_key="cut", base_key="base",
            clothing_type=category, sub_category=sub, product_key="product")
        return first, second
    first, second = asyncio.run(run())
    assert first.ready and second.ready and second.cached
    assert first.mask_key == second.mask_key and len(r2.puts) == 1
    assert [p["prompt"] for _, p in calls] == [prompt, "clothing"]
    for model, payload in calls:
        assert model == "fal-ai/sam-3/image"
        assert payload["return_multiple_masks"] is True
        assert payload["max_masks"] == 3 and payload["include_scores"] is True
        assert "box_prompts" not in payload
    with Image.open(io.BytesIO(r2.objects[first.mask_key])) as image:
        assert image.mode == "L" and image.size == (120, 200)
        assert set(np.unique(np.array(image))) <= {0, 255}
    assert first.algorithm_version == "editor-worn-garment-fal-sam3-v2"
    assert first.m2m is False and first.grid == 0
    assert second.selected_rank is None and second.product_match is None
    meta = egm.metadata_for(first, cut_id="A-1", source_asset_id="cut", category=category,
                            sub_category=sub, product_key="product")
    assert meta["type"] == "editorGarmentMask"
    assert meta["producer"] == "fal-sam3-worn-garment" and meta["productKey"] == "product"


@pytest.mark.parametrize("case,state", [("empty", "failed"), ("matching", "failed"),
    ("outage", "unavailable"), ("ready", "ready"), ("reference_outage", "unavailable")])
def test_fal_worker_retains_status_and_guard(tone, monkeypatch, case, state):
    from app.workers import editor_garment_mask_job as worker
    from conftest import fake_worker_app, worker_job
    masks = [] if case == "empty" else [_mask(SHAPE, COORD_BOX if case == "matching" else PRODUCT_BOX)]
    wire_fal(monkeypatch, masks, fail=case == "outage")
    settings = SimpleNamespace(fal_key="test", mannequin_tone_editor="on",
        base_mannequin_women_asset_id="base",
        base_mannequin_men_asset_id="base")
    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    async def value(v):
        return v
    monkeypatch.setattr(worker.repo, "get_mannequin_cut_asset", lambda *a: value({"id": "cut", "r2_key": "cut"}))
    monkeypatch.setattr(worker.repo, "get_product", lambda *a: value({"clothing_type": "top"}))
    monkeypatch.setattr(worker.repo, "get_analysis", lambda *a: value({}))
    monkeypatch.setattr(worker.repo, "get_asset_for_user", lambda *a: value({"r2_key": "base"}))
    monkeypatch.setattr(worker, "_product_reference_key", lambda *a, **kw: value("product"))
    monkeypatch.setattr(egm, "matching_side_for_project", lambda *a, **kw: value("bottom"))
    finished, recorded = {}, {}
    async def finalize(conn, **kw):
        finished.update(kw)
    async def record(conn, **kw):
        recorded.update(kw)
        return {"id": "mask"}
    monkeypatch.setattr(worker.repo, "finalize_uncharged_job", finalize)
    monkeypatch.setattr(egm, "record", record)
    r2 = MemoryR2()
    if case == "reference_outage":
        del r2.objects["product"]
    app = fake_worker_app(settings, r2=r2)
    asyncio.run(worker.run_editor_garment_mask_job(app,
        worker_job({"cutId": "A-1", "toneMaskProvider": "fal"})))
    assert finished["status"] == "done" and finished["result"]["state"] == state
    assert finished["result"]["toneMaskProvider"] == "fal"
    assert sam_retry.job_is_retryable(finished) is (case in ("outage", "reference_outage"))
    if state == "ready":
        assert recorded["product_key"] == "product"
        assert recorded["result"].algorithm_version == tone.ALGORITHM_VERSION
        assert recorded["match_share"] <= .25
    elif case == "reference_outage":
        assert recorded["result"].ready and recorded["product_key"] is None
        assert finished["result"]["maskAssetId"] == "mask"
    else:
        assert not recorded


def test_fal_cache_separates_product_references(tone, monkeypatch):
    calls = wire_fal(monkeypatch, [_mask(SHAPE, PRODUCT_BOX)])
    r2 = MemoryR2()
    r2.objects["new-product"] = r2.objects["product"]
    async def run():
        results = []
        for key in (None, "product", "new-product", "product"):
            results.append(await tone.segment_worn_garment(SimpleNamespace(fal_key="test"), r2,
                source_key="cut", base_key="base", clothing_type="top", product_key=key))
        return results
    a, b, c, again = asyncio.run(run())
    assert all(r.ready for r in (a, b, c, again))
    assert len({a.mask_key, b.mask_key, c.mask_key}) == 3
    assert again.cached and again.mask_key == b.mask_key
    assert len(calls) == 6


@pytest.mark.parametrize("product", [None, b"corrupt image"])
def test_unusable_product_is_not_recorded_as_used(tone, monkeypatch, product):
    wire_fal(monkeypatch, [_mask(SHAPE, PRODUCT_BOX)])
    r2 = MemoryR2()
    if product is None:
        del r2.objects["product"]
    else:
        r2.objects["product"] = product
    result = asyncio.run(tone.segment_worn_garment(SimpleNamespace(fal_key="test"), r2,
        source_key="cut", base_key="base", clothing_type="top", product_key="product"))
    assert result.ready and result.product_key is None
    assert "/noref/" in result.mask_key


@pytest.mark.parametrize("failure,code", [("source", "source_error"), ("store", "mask_store_failed"),
    ("malformed", "unavailable"), ("download", "unavailable")])
def test_fal_failures_keep_retryable_contract(tone, monkeypatch, failure, code):
    wire_fal(monkeypatch, [_mask(SHAPE, PRODUCT_BOX)])
    r2 = MemoryR2()
    if failure == "source":
        del r2.objects["base"]
    if failure == "store":
        def broken(*a):
            raise OSError("store failed")
        r2.put_bytes = broken
    if failure == "malformed":
        async def malformed(*a):
            return {"masks": [{"url": "mask:0"}], "scores": []}
        monkeypatch.setattr(fal_api, "run_model", malformed)
    if failure == "download":
        async def broken_fetch(*a):
            raise SamUnavailable("download failed")
        monkeypatch.setattr(fal_api, "fetch_image", broken_fetch)
    async def run():
        return await tone.segment_worn_garment(SimpleNamespace(fal_key="test"), r2,
            source_key="cut", base_key="base", clothing_type="top")
    if code == "unavailable":
        with pytest.raises(SamUnavailable):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert not result.ready and result.code == code
        assert code in egm.TONE_MASK_RETRYABLE_CODES
    assert not r2.puts




@pytest.mark.parametrize("algorithm,queued", [("editor-worn-garment-sam2-v3", True),
    ("editor-worn-garment-fal-sam3-v2", False)])
def test_fal_upgrade_keeps_ready_mask_on_screen(client, make_token, monkeypatch, algorithm, queued):
    from test_product_reference_mask import _tone_editor_state, _meta
    monkeypatch.setenv("TONE_MASK_PROVIDER", "fal")
    monkeypatch.setenv("FAL_KEY", "test")
    jobs = []
    _tone_editor_state(monkeypatch, mask_meta=_meta(algorithm=algorithm, product_key="product"),
                       product_cutout_key="product", jobs=jobs)
    response = client.get("/v1/projects/p1/mannequins/A-1/tone-editor",
                          headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert response.json()["maskAssetId"] == "mask-1"
    assert bool(jobs) is queued
    if queued:
        assert "editor-worn-garment-fal-sam3-v2" in jobs[0]["idempotency_key"]
        assert jobs[0]["credits_reserved"] == 0


def test_reference_outage_can_retry_then_stops_after_recovery(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import routes
    monkeypatch.setenv("TONE_MASK_PROVIDER", "fal")
    monkeypatch.setenv("FAL_KEY", "test")
    key = egm.mask_job_key("p", "cut", product_key="product")
    previous = {"id": "first", "user_id": "u", "status": "done",
        "idempotency_key": key, "payload": {"cutId": "cut"},
        "finished_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        "result": {"state": "unavailable", "maskAssetId": "usable-mask"}}
    created = []
    async def latest(*args):
        return previous
    async def create(conn, **kwargs):
        created.append(kwargs)
        return {"id": "retry"}, True
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", latest)
    monkeypatch.setattr(routes.repo, "create_job", create)
    async def enqueue():
        return await routes._enqueue_tone_mask_generations(None, user_id="u", project_id="p",
            cut_id="cut", product_key="product", metadata={})
    assert asyncio.run(enqueue())[0] is True
    assert created[0]["idempotency_key"] == key + ":r1"
    assert created[0]["payload"]["retry"] == 1 and created[0]["credits_reserved"] == 0
    assert routes._tone_state_with_job({"status": "ready"}, previous)["status"] == "ready"
    # 성공하거나 예산을 소진하면 같은 상품 키로 더 실행하지 않는다.
    previous["result"]["state"] = "ready"
    previous["result"]["toneMaskProvider"] = "fal"
    assert asyncio.run(enqueue())[0] is False
    previous["result"]["state"] = "unavailable"
    previous["payload"]["retry"] = sam_retry.MAX_RETRIES
    assert asyncio.run(enqueue())[0] is False
    assert len(created) == 1


def test_fal_mask_restores_original_size_after_input_downscaling(tone, monkeypatch):
    shape = (2200, 1200)
    r2 = MemoryR2()
    r2.objects["cut"] = _cut_bytes(shape, top_box=(264, 1276, 360, 840))
    r2.objects["base"] = _cut_bytes(shape)
    # fal が縮小サイズで返した候補でも元の生成カットに対応しなければならない。
    wire_fal(monkeypatch, [_mask((1100, 600), (132, 638, 180, 420))])
    result = asyncio.run(tone.segment_worn_garment(SimpleNamespace(fal_key="test"), r2,
        source_key="cut", base_key="base", clothing_type="top"))
    assert result.ready and (result.width, result.height) == (1200, 2200)
    with Image.open(io.BytesIO(r2.objects[result.mask_key])) as image:
        assert image.mode == "L" and image.size == (1200, 2200)
        mask = np.array(image)
        assert mask[600, 600] == 255 and mask[1800, 600] == 0


@pytest.mark.parametrize("stats,expected", [
    ({"areaFrac": .0199}, (False, "too_small")),
    ({"areaFrac": .02}, (True, "")),
    ({"areaFrac": .75}, (True, "")),
    ({"areaFrac": .7501}, (False, "near_full_frame")),
    ({"areaFrac": .2, "borderTouchFrac": .5501}, (False, "border_dominated")),
    ({"areaFrac": .2, "aspectHW": 6.01}, (False, "impossible_aspect")),
    ({"areaFrac": .2, "aspectHW": .119}, (False, "impossible_aspect")),
])
def test_candidate_shape_rejection_boundaries(tone, stats, expected):
    assert tone.plausible(stats) == expected


def test_candidate_geometry_is_measured_from_pixels(tone):
    assert tone.candidate_stats(_mask((20, 30), (4, 16, 10, 20))) == {
        "areaFrac": .2, "bbox": [10, 4, 10, 12], "bboxFillFrac": 1.0,
        "borderTouchFrac": 0.0, "components": 1, "aspectHW": 1.2,
    }


def _force_candidate_order(monkeypatch, tone, masks):
    # Fix only ranking: final matching-band and background vetoes still inspect real pixels.
    real_score = tone.score_candidate
    def score(mask, *args):
        parts = real_score(mask, *args)
        rank = next(i for i, candidate in enumerate(masks) if np.array_equal(mask, candidate))
        return {**parts, "score": float(100 - rank)}
    monkeypatch.setattr(tone, "score_candidate", score)


def test_fallback_rejects_matching_and_background_before_serving_product(tone, monkeypatch):
    product = _mask(SHAPE, PRODUCT_BOX)
    pants = _mask(SHAPE, COORD_BOX)
    background = _mask(SHAPE, (70, 116, 4, 116))
    candidates = [product | pants, background, product]
    _force_candidate_order(monkeypatch, tone, candidates)
    generated, base = images()
    result = tone.produce(generated, base, candidates=candidates,
                          clothing_type="top", matching_side="bottom")
    assert result.selected_rank == 2 and result.vetoed_attempts == 1
    assert np.array_equal(np.array(Image.open(io.BytesIO(result.png))) > 127, product)


def test_fallback_never_searches_beyond_the_first_three_candidates(tone, monkeypatch):
    product = _mask(SHAPE, PRODUCT_BOX)
    pants = _mask(SHAPE, COORD_BOX)
    candidates = [product | pants,
                  _mask(SHAPE, (30, 116, 20, 48)) | pants,
                  _mask(SHAPE, (40, 116, 72, 100)) | pants,
                  product]
    assert len(tone.dedupe(candidates)) == 4
    _force_candidate_order(monkeypatch, tone, candidates)
    generated, base = images()
    with pytest.raises(tone.NoGarmentCandidate):
        tone.produce(generated, base, candidates=candidates,
                     clothing_type="top", matching_side="bottom")
