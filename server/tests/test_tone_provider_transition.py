"""Provider switches preserve queue identity, usable masks and bounded recovery."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import routes
from app.services import editor_garment_mask as egm, sam_client
from app.workers import editor_garment_mask_job as worker, mannequin_job
from conftest import FakePool, fake_worker_app, worker_job


async def value(result):
    return result


@pytest.mark.parametrize("payload_provider", [None, "sam2"])
def test_old_tone_job_skips_without_sam2_configuration(monkeypatch, payload_provider):
    settings = SimpleNamespace(tone_mask_provider="fal", mannequin_tone_editor="on",
                               fal_key="test", sam_service_url=None, sam_internal_token=None)
    assert not sam_client.configured(settings)
    assert worker.fal_api.configured(settings)
    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    finished = {}

    async def forbidden(*args, **kwargs):
        raise AssertionError("unconfigured SAM2 jobs must skip before reading assets or calling providers")

    async def finalize(conn, **kwargs):
        finished.update(kwargs)

    monkeypatch.setattr(worker.repo, "get_mannequin_cut_asset", forbidden)
    monkeypatch.setattr(worker.sam_client, "segment_worn_garment", forbidden)
    monkeypatch.setattr(worker.tone_mask, "segment_worn_garment", forbidden)
    monkeypatch.setattr(worker.repo, "finalize_uncharged_job", finalize)
    payload = {"cutId": "A-1"}
    if payload_provider:
        payload["toneMaskProvider"] = payload_provider
    job = worker_job(payload, credits_reserved=0)
    asyncio.run(worker.run_editor_garment_mask_job(fake_worker_app(settings), job))
    assert finished["status"] == "done"
    assert finished["lease_token"] == job["lease_token"]
    assert finished["result"] == {"state": "skipped", "reason": "sam_not_configured",
                                  "cutId": "A-1", "toneMaskProvider": "sam2"}


@pytest.mark.parametrize("local_provider,payload_provider,key_provider,expected", [
    ("sam2", "fal", "fal", "fal"),
    ("fal", "sam2", "sam2", "sam2"),
    ("sam2", None, "fal", "sam2"),
    ("fal", None, "sam2", "sam2"),
    ("fal", None, None, "sam2"),
])
def test_worker_uses_producer_identity_and_legacy_sam2(
        monkeypatch, local_provider, payload_provider, key_provider, expected):
    settings = SimpleNamespace(tone_mask_provider=local_provider, mannequin_tone_editor="on",
        base_mannequin_women_asset_id="base", base_mannequin_men_asset_id="base")
    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    monkeypatch.setattr(worker.fal_api, "configured", lambda s: True)
    monkeypatch.setattr(worker.sam_client, "configured", lambda s: True)
    monkeypatch.setattr(worker.repo, "get_mannequin_cut_asset",
                        lambda *a: value({"id": "cut", "r2_key": "cut"}))
    monkeypatch.setattr(worker.repo, "get_product", lambda *a: value({"clothing_type": "top"}))
    monkeypatch.setattr(worker.repo, "get_analysis", lambda *a: value({}))
    monkeypatch.setattr(worker.repo, "get_asset_for_user", lambda *a: value({"r2_key": "base"}))
    monkeypatch.setattr(egm, "matching_side_for_project", lambda *a, **kw: value(None))
    calls, finished = [], {}
    async def segment_sam(s, **kw):
        calls.append("sam2")
        return sam_client.WornGarmentResult(ready=False, code="no_garment_candidate")
    async def segment_fal(s, r2, **kw):
        calls.append("fal")
        return sam_client.WornGarmentResult(ready=False, code="no_garment_candidate")
    async def finalize(conn, **kw):
        finished.update(kw)
    monkeypatch.setattr(worker.sam_client, "segment_worn_garment", segment_sam)
    monkeypatch.setattr(worker.tone_mask, "segment_worn_garment", segment_fal)
    monkeypatch.setattr(worker.repo, "finalize_uncharged_job", finalize)
    payload = {"cutId": "A-1"}
    if payload_provider:
        payload["toneMaskProvider"] = payload_provider
    job = worker_job(payload)
    if key_provider:
        job["idempotency_key"] = egm.mask_job_key("p", "A-1", provider=key_provider)
    asyncio.run(worker.run_editor_garment_mask_job(fake_worker_app(settings), job))
    assert calls == [expected]
    assert finished["result"].get("toneMaskProvider") == expected


@pytest.mark.parametrize("provider", ["sam2", "fal"])
def test_both_producers_bind_provider_in_payload(monkeypatch, provider):
    monkeypatch.setenv("TONE_MASK_PROVIDER", provider)
    monkeypatch.setenv("FAL_KEY", "test")
    created = []
    async def create(conn, **kw):
        created.append(kw)
        return {"id": "job"}, True
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", lambda *a: value(None))
    monkeypatch.setattr(routes.repo, "create_job", create)
    async def scenario():
        for product in (None, "product"):
            await routes._enqueue_tone_mask_generations(None, user_id="u", project_id="p",
                cut_id="A-1", product_key=product, metadata={})
        await mannequin_job._enqueue_editor_garment_mask(FakePool(),
            SimpleNamespace(mannequin_tone_editor="on", tone_mask_provider=provider),
            user_id="u", project_id="p", cuts=[{"id": "A-1"}], cut_metadata={"profileGender": "women"})
    asyncio.run(scenario())
    assert len(created) == 3
    assert [job["payload"].get("toneMaskProvider") for job in created] == [provider] * 3


@pytest.mark.parametrize("state,algorithm", [
    ("failed", None), ("ready", egm.ALGORITHM_VERSION), ("skipped", None),
])
def test_wrong_provider_completion_recovers_once_then_stops(monkeypatch, state, algorithm):
    monkeypatch.setenv("TONE_MASK_PROVIDER", "fal")
    monkeypatch.setenv("FAL_KEY", "test")
    key = egm.mask_job_key("p", "A-1")
    previous = {"status": "done", "payload": {"cutId": "A-1"},
        "finished_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        "result": {"state": state, "algorithmVersion": algorithm, "code": "no_garment_candidate"}}
    if state == "skipped":
        previous["result"] = {"state": "skipped", "reason": "sam_not_configured"}
    created = []
    async def create(conn, **kw):
        created.append(kw)
        return {"status": "pending", "payload": kw["payload"]}, True
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", lambda *a: value(previous))
    monkeypatch.setattr(routes.repo, "create_job", create)
    def enqueue():
        return asyncio.run(routes._enqueue_tone_mask_generations(None, user_id="u", project_id="p",
            cut_id="A-1", metadata={}))
    assert routes._tone_state_with_job({"status": "processing"}, previous)["status"] == "processing"
    assert enqueue()[0] is True
    assert created[0]["idempotency_key"] == key + ":r1"
    assert created[0]["payload"] == {"cutId": "A-1", "toneMaskProvider": "fal", "retry": 1}
    previous["payload"] = created[0]["payload"]
    previous["result"] = {"state": "failed", "toneMaskProvider": "fal", "code": "no_garment_candidate"}
    assert enqueue()[0] is False
    previous["result"] = {"state": "ready", "toneMaskProvider": "sam2", "algorithmVersion": egm.ALGORITHM_VERSION}
    previous["payload"]["retry"] = egm.TONE_MASK_MAX_RETRIES
    assert enqueue()[0] is False
    assert routes._tone_state_with_job({"status": "processing"}, previous)["status"] == "failed"
    assert len(created) == 1


def test_wrong_provider_recovery_waits_for_backoff(monkeypatch):
    monkeypatch.setenv("TONE_MASK_PROVIDER", "fal")
    monkeypatch.setenv("FAL_KEY", "test")
    previous = {"status": "done", "idempotency_key": egm.mask_job_key("p", "A-1"),
        "payload": {"cutId": "A-1"}, "result": {"state": "failed"},
        "finished_at": datetime.now(timezone.utc)}
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", lambda *a: value(previous))
    async def forbidden(*a, **kw):
        raise AssertionError("backoff must prevent a new generation")
    monkeypatch.setattr(routes.repo, "create_job", forbidden)
    assert asyncio.run(routes._enqueue_tone_mask_generations(None, user_id="u", project_id="p",
        cut_id="A-1", metadata={}))[0] is False
    assert routes._tone_state_with_job({"status": "processing"}, previous)["status"] == "processing"


def test_repeated_provider_switches_reselect_older_rows_without_jobs(client, make_token, monkeypatch):
    from test_product_reference_mask import _tone_editor_state, _meta
    original_find = egm.find_for_cut
    jobs = []
    _tone_editor_state(monkeypatch, mask_meta=_meta(), product_cutout_key="product", jobs=jobs)
    monkeypatch.setattr(egm, "find_for_cut", original_find)
    rows = [
        {"id": "fal-mask", "metadata": _meta(algorithm=egm.FAL_ALGORITHM_VERSION, product_key="product")},
        {"id": "sam-mask", "metadata": _meta(algorithm=egm.ALGORITHM_VERSION, product_key="product")},
    ]
    monkeypatch.setattr(egm, "list_for_project", lambda *a, **kw: value(rows))
    for provider, mask_id in [("sam2", "sam-mask"), ("fal", "fal-mask"), ("sam2", "sam-mask"), ("fal", "fal-mask")]:
        monkeypatch.setenv("TONE_MASK_PROVIDER", provider)
        monkeypatch.setenv("FAL_KEY", "test")
        response = client.get("/v1/projects/p1/mannequins/A-1/tone-editor",
            headers={"Authorization": f"Bearer {make_token()}"})
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["maskAssetId"] == mask_id
    assert jobs == []


def test_selection_prefers_current_reference_and_skips_unsafe_rows(monkeypatch):
    from test_product_reference_mask import _meta
    monkeypatch.setenv("TONE_MASK_PROVIDER", "sam2")
    rows = [
        {"id": "unsafe", "metadata": {**_meta(product_key="product"), "matchGuardVersion": "old"}},
        {"id": "other-cut", "metadata": {**_meta(product_key="product"), "sourceCutId": "B-1"}},
        {"id": "old-reference", "metadata": _meta(product_key="old")},
        {"id": "cached-current", "metadata": _meta(product_key="product")},
    ]
    monkeypatch.setattr(egm, "list_for_project", lambda *a, **kw: value(rows))
    monkeypatch.setattr(egm, "matching_side_for_project", lambda *a, **kw: value("bottom"))
    mask, side, upgrade = asyncio.run(egm.current_mask_for_cut(None, user_id="u", project_id="p",
        cut_id="A-1", product_key="product"))
    assert mask is not None
    assert mask["id"] == "cached-current" and side == "bottom" and upgrade is None
    rows[:] = [rows[2]]
    mask, _, upgrade = asyncio.run(egm.current_mask_for_cut(None, user_id="u", project_id="p",
        cut_id="A-1", product_key="product"))
    assert mask["id"] == "old-reference" and upgrade == "reference"


def test_state_pixels_and_apply_use_the_same_current_reference(client, make_token, monkeypatch):
    from test_product_reference_mask import _tone_editor_state, _meta
    original_find = egm.find_for_cut
    _tone_editor_state(monkeypatch, mask_meta=_meta(), product_cutout_key="product", jobs=[])
    monkeypatch.setattr(egm, "find_for_cut", original_find)
    rows = [{"id": "newer-old-ref", "r2_key": "masks/old.png", "metadata": _meta(product_key="old")},
            {"id": "older-current-ref", "r2_key": "masks/current.png", "metadata": _meta(product_key="product")}]
    monkeypatch.setattr(egm, "list_for_project", lambda *a, **kw: value(rows))
    monkeypatch.setattr(routes, "_r2", lambda req: SimpleNamespace(get_bytes=lambda key: key.encode()))
    monkeypatch.setattr(routes.repo, "get_asset_for_user", lambda *a: value({"r2_key": "rendered.png"}))
    monkeypatch.setattr(routes.mannequin_display, "build", lambda *a: None)
    monkeypatch.setattr(routes.mannequin_tone_render, "clear_for_cut", lambda *a, **kw: value(None))
    recorded = {}
    async def record(conn, **kw):
        recorded.update(kw)
    monkeypatch.setattr(routes.mannequin_tone_render, "record", record)
    headers = {"Authorization": f"Bearer {make_token()}"}
    url = "/v1/projects/p1/mannequins/A-1/tone-editor"
    state = client.get(url, headers=headers)
    pixels = client.get(url + "/mask", headers=headers)
    assert state.status_code == pixels.status_code == 200
    assert state.json()["maskAssetId"] == "older-current-ref"
    assert pixels.content == b"masks/current.png"
    applied = client.post(url + ":apply", headers=headers,
        json={"assetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "saturation": 5, "exposure": 0})
    assert applied.status_code == 200
    assert recorded["mask_asset_id"] == state.json()["maskAssetId"]
