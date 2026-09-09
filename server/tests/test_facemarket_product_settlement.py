"""상품별 7일 정산 계약. 실제 생성 워커와 정산 함수를 FakeChain/DB로 함께 실행한다."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import facemarket
from app.workers import editor_image_job as eij
from test_detail_page_license_face import (
    _app, _license_row, _patch_inputs, _patch_snapshot_success, _snapshot_job, dpj,
)
from test_facemarket_settlement import FakeChain, SlowFirstChain, _FakePool


@pytest.fixture()
def product_settlement(monkeypatch):
    store = {
        "settlements": [], "intents": [], "jobs": {}, "signer_locked": False,
        "now": datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
    }
    chain = FakeChain()
    captured = {}
    row = _license_row()
    _patch_inputs(
        monkeypatch, captured, project={"copywriting": False},
        storyboard=[
            {"id": f"b{i}", "source": "ai", "cutType": "horizon", "shot": "full",
             "pose": f"pose-{i}"}
            for i in range(8)
        ],
    )
    _patch_snapshot_success(monkeypatch, row)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return store["now"].astimezone(tz)

    async def noop(*args, **kwargs):
        return None

    async def no_cuts(*args, **kwargs):
        return []

    async def editor_success(conn, **kwargs):
        captured["editor"] = kwargs
        return {"available": 99}

    async def editor_failure(conn, **kwargs):
        captured["failure"] = kwargs

    monkeypatch.setattr(facemarket, "datetime", Clock)
    monkeypatch.setattr(dpj.repo, "lock_facemarket_writer_boundary", noop)
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", noop)
    monkeypatch.setattr(dpj.repo, "clear_ai_output_cleanup_intent", noop)
    monkeypatch.setattr(dpj.repo, "list_mannequin_cuts", no_cuts)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", editor_success)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", editor_failure)

    def make_app():
        app, _ = _app(row)
        app.state.pool = _FakePool(store, max_size=3)
        app.state.fm_chain = chain
        return app

    async def run(job_id, project_id="p1", *, editor=False, app=None):
        job = _snapshot_job(reserved=8)
        job.update(id=job_id, project_id=project_id)
        store["jobs"][job_id] = project_id
        if editor:
            job["payload"].update(mode="new", cutType="horizon", shot="full")
            await eij.run_editor_image_job(app or make_app(), job)
        else:
            await dpj.run_detail_page_job(app or make_app(), job)
        assert "failure" not in captured, captured.get("failure")

    return SimpleNamespace(store=store, chain=chain, captured=captured, run=run,
                           make_app=make_app, unit_price=row["unit_price"])


def test_eight_real_cuts_record_one_unit_price(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))

    assert len(ctx.captured["cut_assets"]) == 8
    assert all(c["metadata"]["facemarket_real_derived"] for c in ctx.captured["cut_assets"])
    assert len(ctx.store["settlements"]) == 1
    assert ctx.store["settlements"][0]["total_amount"] == ctx.unit_price
    assert ctx.chain.record_calls == ["product:p1:20260901"]


def test_detail_regeneration_within_seven_days_reuses_product(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))
    ctx.store["now"] += timedelta(days=6)
    asyncio.run(ctx.run("detail-2"))

    assert len(ctx.store["settlements"]) == 1
    assert ctx.chain.record_calls == ["product:p1:20260901"]


def test_editor_real_face_within_seven_days_reuses_product(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))
    ctx.store["now"] += timedelta(days=1)
    asyncio.run(ctx.run("editor-1", editor=True))

    assert ctx.captured["editor"]["image"]["metadata"]["facemarket_real_derived"] is True
    assert len(ctx.store["settlements"]) == 1
    assert ctx.chain.record_calls == ["product:p1:20260901"]


def test_detail_after_eight_days_starts_new_window(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))
    ctx.store["now"] += timedelta(days=6)
    asyncio.run(ctx.run("detail-2"))
    ctx.store["now"] += timedelta(days=2)
    asyncio.run(ctx.run("detail-3"))

    assert len(ctx.store["settlements"]) == 2
    assert ctx.chain.record_calls == ["product:p1:20260901", "product:p1:20260909"]
    assert [r["total_amount"] for r in ctx.store["settlements"]] == [ctx.unit_price] * 2


def test_other_product_records_separately(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))
    asyncio.run(ctx.run("detail-2", "p2"))

    assert len(ctx.store["settlements"]) == 2
    assert ctx.chain.record_calls == ["product:p1:20260901", "product:p2:20260901"]


@pytest.mark.parametrize("extra_seconds, count", [(0, 1), (1, 2)])
def test_seven_day_boundary(product_settlement, extra_seconds, count):
    ctx = product_settlement
    asyncio.run(ctx.run("detail-1"))
    ctx.store["now"] += timedelta(days=7, seconds=extra_seconds)
    asyncio.run(ctx.run("detail-2"))

    assert len(ctx.store["settlements"]) == len(ctx.chain.record_calls) == count


def test_editor_can_start_product_window(product_settlement):
    ctx = product_settlement
    asyncio.run(ctx.run("editor-1", editor=True))
    ctx.store["now"] += timedelta(days=1)
    asyncio.run(ctx.run("detail-1"))

    assert ctx.captured["editor"]["image"]["metadata"]["facemarket_real_derived"] is True
    assert len(ctx.store["settlements"]) == 1
    assert ctx.store["settlements"][0]["total_amount"] == ctx.unit_price
    assert ctx.chain.record_calls == ["product:p1:20260901"]


@pytest.mark.parametrize("payment_key", ["job:old-detail", "product:p1:20260831"])
def test_recovered_broadcast_reuses_product_window(product_settlement, payment_key):
    ctx = product_settlement
    ctx.store["jobs"]["old-detail"] = "p1"
    # RPC succeeded before a worker died, but the settlement has not yet been mirrored.
    ctx.chain.record_settlement(payment_key=payment_key, model_uuid="old-model", total=10000)
    ctx.store["intents"].append({
        "payment_id": payment_key, "license_id": "old-license", "job_id": "old-detail",
        "credit_ledger_id": None, "model_id": "old-model", "total_amount": 10000,
        "status": "broadcasting", "attempted_at": ctx.store["now"],
    })

    asyncio.run(ctx.run("detail-1"))

    assert len(ctx.store["settlements"]) == 1
    assert ctx.chain.record_calls == [payment_key]
    assert ctx.store["intents"][0]["status"] == "confirmed"
    assert len(ctx.store["intents"]) == 1


def test_legacy_job_settlement_counts_towards_product_window(product_settlement):
    ctx = product_settlement
    ctx.store["jobs"]["old-detail"] = "p1"
    asyncio.run(facemarket.record_license_settlement(
        ctx.make_app(), payment_key="job:old-detail", license_id="old-license",
        model_id="old-model", total=10000, job_id="old-detail",
    ))
    ctx.store["now"] += timedelta(days=1)
    asyncio.run(ctx.run("detail-1"))

    assert len(ctx.store["settlements"]) == 1
    assert ctx.chain.record_calls == ["job:old-detail"]


def test_queued_product_intent_keeps_original_payload_on_new_job(product_settlement):
    ctx = product_settlement
    payment_key = "product:p1:20260901"
    ctx.store["jobs"]["old-detail"] = "p1"
    ctx.store["intents"].append({
        "payment_id": payment_key, "license_id": "old-license", "job_id": "old-detail",
        "credit_ledger_id": None, "model_id": "old-model", "total_amount": 7000,
        "status": "queued", "attempted_at": None,
    })

    class PayloadChain(FakeChain):
        def record_settlement(self, *, payment_key, model_uuid, total):
            self.payload = (model_uuid, total)
            return super().record_settlement(
                payment_key=payment_key, model_uuid=model_uuid, total=total,
            )

    app = ctx.make_app()
    chain = app.state.fm_chain = PayloadChain()
    asyncio.run(ctx.run("new-detail", app=app))

    assert chain.payload == ("old-model", 7000)
    assert chain.record_calls == [payment_key]
    assert len(ctx.store["settlements"]) == len(ctx.store["intents"]) == 1
    row = ctx.store["settlements"][0]
    assert (row["license_id"], row["job_id"], row["total_amount"]) == (
        "old-license", "old-detail", 7000,
    )
    assert ctx.store["intents"][0]["status"] == "confirmed"


@pytest.mark.parametrize("cross_midnight", [False, True])
def test_concurrent_product_jobs_record_once(product_settlement, cross_midnight):
    ctx = product_settlement
    chain = SlowFirstChain()
    app_a, app_b = ctx.make_app(), ctx.make_app()
    app_a.state.fm_chain = app_b.state.fm_chain = chain
    ctx.store["now"] = datetime(2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc)

    async def race():
        owner = asyncio.create_task(ctx.run("detail-1", app=app_a))
        assert await asyncio.to_thread(chain.started.wait, 0.5)
        if cross_midnight:
            ctx.store["now"] += timedelta(seconds=2)
        waiter = asyncio.create_task(ctx.run("editor-1", editor=True, app=app_b))
        await asyncio.sleep(0.03)
        chain.release.set()
        await asyncio.gather(owner, waiter)

    asyncio.run(race())

    assert len(ctx.store["settlements"]) == 1
    assert chain.record_calls == ["product:p1:20260901"]
    assert len(ctx.store["intents"]) == 1
    assert ctx.store["intents"][0]["status"] == "confirmed"
