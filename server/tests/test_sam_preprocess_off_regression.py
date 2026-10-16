"""PRODUCT_TIDY=off preserves the worker contract from origin/main 2cdc98db.

Expected payloads below are snapshots of that worker, including the SAM2/fal
per-view unavailable distinction. Provider/storage boundaries stay offline.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app import repo
from app.services import sam_client, sam_retry
from app.workers import sam_preprocess_job as worker


@pytest.mark.parametrize("provider", ["sam2", "fal"])
@pytest.mark.parametrize("case", [
    "unconfigured", "no_assets", "outage", "both_ready", "back_failed",
    "back_unavailable", "both_failed", "both_unavailable", "record_collision",
])
def test_off_matches_2cdc98db_finish_recording_and_retry_contract(monkeypatch, provider, case):
    events, finalized, records, segmented = [], [], [], []
    settings = SimpleNamespace(product_tidy="off", sam_preprocess_provider=provider)
    job = dict(id="j", project_id="p", user_id="u", lease_token="lease")

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def commit(self):
            events.append("commit")

    app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=Connection), r2=object()))

    async def product(*_):
        events.append("product")
        return {"colors": [{"isBase": True, "images": [
            {"slot": "Front", "id": "src-f"}, {"slot": "Back", "id": "src-b"}]}]}

    async def asset(_conn, user_id, aid):
        assert user_id == "u"
        events.append(aid)
        return None if case == "no_assets" else {"r2_key": f"uploads/{aid}.jpg"}

    async def segment(*args):
        views = args[-1]
        assert len(args) == (3 if provider == "fal" else 2)
        if provider == "fal":
            assert args[1] is app.state.r2
        segmented.append(views)
        events.append("segment")
        if case == "outage":
            raise sam_client.SamUnavailable("provider down")
        out = {}
        for view in ("Front", "Back"):
            code = None
            if case == "both_failed" or (case in ("back_failed", "record_collision") and view == "Back"):
                code = "no_garment_candidate"
            elif case == "both_unavailable" or (case == "back_unavailable" and view == "Back"):
                code = "unavailable"
            out[view] = sam_client.SamViewResult(
                view=view, ready=code is None, code=code,
                cutout_key=f"canonical/{view}.png" if code is None else None,
                source_hash=f"hash-{view}", cached=view == "Front")
        return out

    async def record(_conn, **kw):
        records.append({k: kw[k] for k in ("user_id", "project_id", "view", "source_asset_id")})
        events.append(f"record-{kw['view']}")
        assert kw["result"].source_hash == f"hash-{kw['view']}"
        return None if case == "record_collision" else {"id": f"cut-{kw['view']}"}

    async def finish(_conn, **kw):
        finalized.append(kw)
        events.append("finish")

    async def forbidden(*_a, **_kw):
        pytest.fail("off must never enter product tidy lookup or production")

    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    monkeypatch.setattr(worker.sam_client, "configured", lambda _: case != "unconfigured")
    monkeypatch.setattr(worker.fal_api, "configured", lambda _: case != "unconfigured")
    monkeypatch.setattr(worker.sam_client, "segment_garment", segment)
    monkeypatch.setattr(worker.fal_cutout, "segment_garment", segment)
    monkeypatch.setattr(worker.canonical_reference, "record", record)
    monkeypatch.setattr(worker.product_tidy, "load_front", forbidden)
    monkeypatch.setattr(worker.product_tidy, "produce", forbidden)
    monkeypatch.setattr(worker.product_tidy, "bind_source", forbidden)
    monkeypatch.setattr(repo, "get_product", product)
    monkeypatch.setattr(repo, "get_asset_for_user", asset)
    monkeypatch.setattr(repo, "finalize_uncharged_job", finish)
    asyncio.run(worker.run_sam_preprocess_job(app, job))

    expected_status, retryable = "done", False
    if case == "unconfigured":
        detail = {"state": "skipped", "reason": "sam_not_configured"}
        expected_events = ["finish", "commit"]
    elif case == "no_assets":
        detail = {"state": "skipped", "reason": "no_eligible_source_assets"}
        expected_events = ["product", "src-f", "src-b", "finish", "commit"]
    elif case == "outage":
        detail = {"state": "unavailable", "reason": "provider down"}
        retryable = True
        expected_events = ["product", "src-f", "src-b", "segment", "finish", "commit"]
    else:
        front = {"assetId": "cut-Front", "cutoutKey": "canonical/Front.png", "cached": True}
        back = {"assetId": "cut-Back", "cutoutKey": "canonical/Back.png", "cached": False}
        detail = {
            "both_ready": {"state": "ready", "recorded": {"Front": front, "Back": back}, "failed": {}},
            "back_failed": {"state": "partial", "recorded": {"Front": front}, "failed": {"Back": "no_garment_candidate"}},
            "back_unavailable": {"state": "unavailable" if provider == "fal" else "partial",
                                 "recorded": {"Front": front}, "failed": {"Back": "unavailable"}},
            "both_failed": {"state": "failed", "recorded": {},
                            "failed": {"Front": "no_garment_candidate", "Back": "no_garment_candidate"}},
            "both_unavailable": {"state": "unavailable" if provider == "fal" else "failed",
                                 "recorded": {}, "failed": {"Front": "unavailable", "Back": "unavailable"}},
            "record_collision": {"state": "failed", "recorded": {}, "failed": {"Back": "no_garment_candidate"}},
        }[case]
        expected_status = "error" if case in ("both_failed", "record_collision") or (
            case == "both_unavailable" and provider == "sam2") else "done"
        retryable = provider == "fal" and case in ("back_unavailable", "both_unavailable")
        ready_views = {"both_ready": ["Front", "Back"], "back_failed": ["Front"],
                       "back_unavailable": ["Front"], "record_collision": ["Front"]}.get(case, [])
        assert records == [dict(user_id="u", project_id="p", view=view,
                                source_asset_id="src-f" if view == "Front" else "src-b") for view in ready_views]
        expected_events = ["product", "src-f", "src-b", "segment",
                           *(f"record-{view}" for view in ready_views), "commit", "finish", "commit"]
    assert finalized == [dict(job_id="j", lease_token="lease", status=expected_status, result=detail,
                             product_tidy_capable=False)]
    assert events == expected_events
    assert sam_retry.job_is_retryable({"status": expected_status, "result": detail}) is retryable
    assert segmented == ([] if case in ("unconfigured", "no_assets") else [
        {"Front": "uploads/src-f.jpg", "Back": "uploads/src-b.jpg"}])
    if case in ("unconfigured", "no_assets", "outage"):
        assert records == []
