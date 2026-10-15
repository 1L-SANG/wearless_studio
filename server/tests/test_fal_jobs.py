import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.services import fal_api, matching_cutout, matching_flatlay
from app.services.sam_client import SamUnavailable
from app.workers import sam_preprocess_job
from test_canonical_pipeline import _async, _job, _job_app
from test_fal_cutout import MemoryR2, png, wire_fal
from test_matching_cutout_job import _job_dict, _run, _wire_worker


def r2_for_jobs():
    image = Image.new("RGB", (100, 100), (250, 250, 250))
    ImageDraw.Draw(image).rectangle((20, 20, 79, 79), fill=(10, 80, 150))
    r2 = MemoryR2(png(image))
    for key in _job_dict()["payload"]["sourceKeys"]:
        r2.objects[key] = r2.objects["source"]
    return r2


def test_cutout_provider_is_part_of_downstream_source_identity():
    assert matching_cutout.source_fingerprint(["h"], provider="fal") != matching_cutout.source_fingerprint(["h"])


@pytest.mark.parametrize("failure", [None, "unavailable", "no_mask"])
def test_matching_job_fal_route_retains_existing_fallback(monkeypatch, failure):
    wire_fal(monkeypatch, fail=failure == "unavailable")
    if failure == "no_mask":
        async def empty(*args):
            return {"masks": [], "scores": []}
        monkeypatch.setattr(fal_api, "run_model", empty)
    app, r2, calls = _wire_worker(monkeypatch, r2=r2_for_jobs())
    app.state.settings.matching_cutout_provider = "fal"
    app.state.settings.fal_key = "test"
    app.state.settings.sam_service_url = None
    app.state.settings.sam_internal_token = None
    _run(app, _job_dict())
    assert calls["segment"] == [], "fal path must work without SAM2"
    if failure:
        assert calls["swap"] is None
        assert calls["finalize"][0] == "done"
        assert calls["finalize"][1]["state"] == ("unavailable" if failure == "unavailable" else "failed")
    else:
        assert calls["finalize"][1]["state"] == "ready"
        for asset in calls["assets"]:
            assert asset["metadata"]["producer"] == "fal-sam3"
            assert asset["metadata"]["algorithmVersion"] == "fal-sam3-v1"
        assert calls["swap"][1] != matching_cutout.derived_asset_id(
            role="thumb", matching_item_id="custom_x", source_hash=calls["assets"][0]["metadata"]["sourceHash"])


@pytest.mark.parametrize("failure", [None, "unavailable", "no_mask"])
def test_product_job_uses_fal_and_existing_canonical_recorder(monkeypatch, failure):
    wire_fal(monkeypatch, fail=failure == "unavailable")
    if failure == "no_mask":
        async def empty(*args):
            return {"masks": [], "scores": []}
        monkeypatch.setattr(fal_api, "run_model", empty)
    app, finished = _job_app(monkeypatch, configured=False)
    app.state.r2 = r2_for_jobs()
    monkeypatch.setattr(sam_preprocess_job, "load_settings", lambda: SimpleNamespace(
        sam_preprocess_provider="fal", fal_key="test"))
    monkeypatch.setattr(sam_preprocess_job.mannequin, "base_color_images",
                        lambda _p: [("Front", "front"), ("Back", "back"), ("Detail", "detail")])
    monkeypatch.setattr(sam_preprocess_job.repo, "get_asset_for_user",
                        lambda *args: _async({"r2_key": "source"}))
    recorded = []

    async def record(conn, **kw):
        recorded.append(kw)
        return {"id": "asset-" + kw["view"]}

    monkeypatch.setattr(sam_preprocess_job.canonical_reference, "record", record)
    asyncio.run(sam_preprocess_job.run_sam_preprocess_job(app, _job()))
    assert finished["result"]["state"] == {
        None: "ready", "unavailable": "unavailable", "no_mask": "failed"}[failure]
    assert finished["status"] == ("error" if failure == "no_mask" else "done")
    if failure is None:
        assert [r["view"] for r in recorded] == ["Front", "Back"]
        assert all(r["result"].algorithm_version == "fal-sam3-v1" for r in recorded)


@pytest.mark.parametrize("qwen_fails", [False, True])
def test_qwen_full_job_uses_raw_alpha_and_keeps_cutout_on_failure(monkeypatch, qwen_fails):
    from app.services import fal_cutout
    from test_qwen_flatlay import cutout

    app, r2, calls = _wire_worker(monkeypatch, cut_png=cutout())
    app.state.settings.matching_flatlay = "full"
    app.state.settings.matching_flatlay_provider = "qwen"
    app.state.settings.fal_key = "test"
    captured = {}
    real_prepare = fal_api.prepare_image

    async def run(settings, model, payload):
        if qwen_fails:
            raise SamUnavailable("down")
        return {"images": [{"url": "https://fake/qwen.png"}]}

    def prepare(data):
        captured["canvas"] = data
        return real_prepare(data)

    async def segment(*args):
        return cutout()

    async def fetch(*args):
        return png(Image.new("RGB", (100, 100), (65, 80, 110)))

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_api, "prepare_image", prepare)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)
    _run(app, _job_dict())
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["finalize"][1]["flatlay"] is (not qwen_fails)
    assert calls["finalize"][1]["flatlayGrid"] is (not qwen_fails)
    metadata = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    if not qwen_fails:
        thumb = metadata[calls["swap"][1]]
        assert thumb["producer"] == "fal-qwen-matching-flatlay"
        assert thumb["algorithmVersion"] == "matching-flatlay-v3-qwen"
        assert metadata[calls["swap"][2]]["flatlayModel"] == "fal-ai/qwen-image-edit-2511"
    # White canvas corners prove the RGBA cutout, rather than the grey composite, was supplied.
    canvas = Image.open(io.BytesIO(captured["canvas"]))
    assert canvas.getpixel((0, 0)) == (255, 255, 255)
    fp = matching_cutout.source_fingerprint(["h" + k for k in _job_dict()["payload"]["sourceKeys"]])
    qwen_id = matching_flatlay.derived_asset_id(matching_item_id="custom_x", source_hash=fp,
                                               provider="qwen")
    assert qwen_id in metadata[calls["swap"][2]]["sourceAssetIds"]
