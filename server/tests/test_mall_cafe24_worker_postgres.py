"""Opt-in worker fencing on a disposable local PostgreSQL, no Cafe24 or R2."""

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import uuid

import httpx
import pytest
from psycopg.types.json import Json

from app import repo
from app.auth import require_user
from app.services import cafe24_tokens
from app.services.cafe24_client import Cafe24Client
from app.services.mall_catalog import REQUIRED_SCOPES
from app.services.mall_registration_contract import (
    freeze_registration, normalize_register_draft, registration_request_hash,
)
from test_mall_connect_postgres import mall_pg
from test_mall_cafe24_worker import CONNECTION, JOB, LEASE, PROJECT, PUBLICATION, USER
from test_multimall_routes import draft, facts, source


@pytest.fixture
def worker_pg(mall_pg):
    # This input belongs only to the existing worker body, which changes in A03.
    app, admin, pool = mall_pg
    admin.execute("""alter table jobs
        add column id uuid primary key,
        add column project_id uuid,
        add column idempotency_key text,
        add column progress integer not null default 0,
        add column steps jsonb not null default '[]',
        add column metadata jsonb not null default '{}',
        add column credits_reserved integer not null default 0,
        add column credits_charged integer not null default 0,
        add column error_message text,
        add column locked_by text,
        add column locked_at timestamptz,
        add column started_at timestamptz,
        add column created_at timestamptz not null default now(),
        add column updated_at timestamptz not null default now(),
        add column finished_at timestamptz""")
    admin.execute("create table job_events (job_id uuid, event_type text, payload jsonb)")
    admin.execute("create table fm_cutover_batches (status text)")
    admin.execute("create table personalization_profiles (id uuid, status text)")
    admin.execute("create table fm_models (id uuid, user_id uuid)")
    app.state.settings = replace(app.state.settings, multimall_register_enabled=True, detail_publish_enabled=True, cafe24_enabled=True,
        cafe24_client_id="fixture-id", cafe24_client_secret="fixture-private")
    payload = dict(mall="cafe24", mallId="myshop", connectionId=CONNECTION, publicationId=PUBLICATION,
        width=860, productName="니트", price=39000, supplyPrice=39000, display="T", selling="T", taxType="A")
    admin.execute("""insert into mall_connections (id, user_id, mall, shop_id, refresh_expires_at)
        values (%s, %s, 'cafe24', 'myshop', now() + interval '1 day')""", (CONNECTION, USER))
    admin.execute("""insert into jobs (id, project_id, user_id, kind, status, payload, locked_by, locked_at)
        values (%s, %s, %s, 'mall_register', 'running', %s, %s, now() - interval '1 hour')""",
        (JOB, PROJECT, USER, Json(payload), LEASE))
    return app, admin, pool


@pytest.fixture
def verify_pg(worker_pg, monkeypatch):
    from app import mall_registration

    app, admin, pool = worker_pg
    app.dependency_overrides[require_user] = lambda: USER
    value = normalize_register_draft(draft(
        projectId=PROJECT, publicationId=PUBLICATION, selectedMalls=["cafe24"]))
    owned = source()
    owned["project"]["id"] = PROJECT
    owned["publication"].update(id=PUBLICATION, user_id=USER, project_id=PROJECT)
    batch_id = str(uuid.uuid4())
    payload = dict(mall="cafe24", batchId=batch_id, requestHash=registration_request_hash(value),
        connectionId=CONNECTION, shopId="myshop", publicationId=PUBLICATION, editorRevision=3,
        snapshot=freeze_registration(value, source=owned, facts=facts()))
    external_code = "wl" + uuid.UUID(JOB).hex[:16]
    admin.execute("update jobs set payload = %s, idempotency_key = %s, metadata = %s where id = %s",
        (Json(payload), f"mall-register:{USER}:{batch_id}:cafe24", Json({"externalCode": external_code}), JOB))
    admin.execute("update mall_connections set scopes = %s where id = %s", (sorted(REQUIRED_SCOPES), CONNECTION))
    calls = []

    def respond(request):
        assert request.method == "GET", "verify must never create a product or write settings"
        assert request.url.path == "/api/v2/admin/products"
        assert request.url.params["custom_product_code"] == external_code
        assert request.headers["Authorization"] == "Bearer fixture-token"
        calls.append(request)
        return httpx.Response(200, json={"products": [
            {"product_no": 42, "custom_product_code": external_code}]})

    async def with_token(app, *, user_id, connection_id, fn):
        assert (user_id, connection_id) == (USER, CONNECTION)
        return await fn("fixture-token")

    monkeypatch.setattr(mall_registration, "Cafe24Client", lambda *args, **kwargs:
        Cafe24Client(*args, **kwargs, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(mall_registration, "call_with_token", with_token)
    return app, admin, pool, calls


def test_second_lease_expiry_after_send_remains_verifiable(verify_pg):
    app, admin, pool, calls = verify_pg
    admin.execute("update jobs set metadata = metadata || %s::jsonb where id = %s",
        (Json({"create": {"sentAt": "2026-10-26T00:00:00+00:00", "attempt": 1, "leaseToken": LEASE}}), JOB))

    async def scenario():
        async with pool.connection() as conn:
            recovered = await repo.recover_stale_leases(conn, 60, ("mall_register",))
            await conn.commit()
            assert recovered[0]["status"] == "pending"
            reclaimed = await repo.claim_next_job(conn, ("mall_register",), "second-worker")
            await conn.commit()
            assert reclaimed["lease_token"] != LEASE
        admin.execute("update jobs set locked_at = now() - interval '1 hour' where id = %s", (JOB,))
        async with pool.connection() as conn:
            recovered = await repo.recover_stale_leases(conn, 60, ("mall_register",))
            await conn.commit()
            assert recovered[0]["status"] == "error"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/v1/malls/registrations/{JOB}")
            assert response.status_code == 200, response.text
            view = response.json()
            assert view["status"] == "error" and view["outcome"] == "unknown"
            assert view["retryAction"] == "verify" and view["productId"] is None
            before = admin.execute("select metadata, result from jobs where id = %s", (JOB,)).fetchone()
            assert before["metadata"]["leaseRecoveries"] == 2 and before["result"] is None
            response = await client.post(f"/v1/malls/registrations/{JOB}/verify")
            assert response.status_code == 200, response.text
            view = response.json()
            assert (view["status"], view["outcome"], view["productId"]) == ("error", "created", "42")
            assert view["setupStatus"] == "needs_attention" and view["retryAction"] == "resume"

    asyncio.run(scenario())
    row = admin.execute("select metadata, result from jobs where id = %s", (JOB,)).fetchone()
    assert row["metadata"]["leaseRecoveries"] == 2 and row["metadata"]["productId"] == "42"
    assert row["result"]["outcome"] == "created" and len(calls) == 1
    assert admin.execute("select count(*) as n from job_events").fetchone()["n"] == 1


def test_reclaimed_job_fence_blocks_stale_worker_create(worker_pg, monkeypatch):
    from app.workers import mall_register_job as worker

    app, admin, pool = worker_pg
    client_calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            client_calls.append("constructed")

        async def product_setting(self, token):
            client_calls.append("setting")
            return "P"

        async def create_product(self, *args):
            pytest.fail("stale worker must never send the create request")

    async def token(app, *, user_id, connection_id):
        assert (user_id, connection_id) == (USER, CONNECTION)
        return "fixture-token", datetime.now(timezone.utc)

    async def images(*args):
        return ["https://fake.invalid/1.jpg"]

    monkeypatch.setattr(worker, "Cafe24Client", Client)
    monkeypatch.setattr(worker, "_upload_images", images)
    monkeypatch.setattr(cafe24_tokens, "ensure_cafe24_access_token", token)
    app.state.r2 = object()

    async def scenario():
        at_create, resume = asyncio.Event(), asyncio.Event()
        patch = repo.set_job_metadata_patch

        async def pause_before_sent_at(conn, **kwargs):
            if "create" in kwargs["patch"]:
                at_create.set()
                await resume.wait()
            return await patch(conn, **kwargs)

        monkeypatch.setattr(repo, "set_job_metadata_patch", pause_before_sent_at)
        async with pool.connection() as conn:
            stale = await repo.get_job(conn, USER, JOB)
            stale["lease_token"] = LEASE
            await conn.commit()
            running = asyncio.create_task(worker.run_mall_register_job(app, stale))
            try:
                await asyncio.wait_for(at_create.wait(), timeout=5)
                # The old worker passed _recheck; reclaim before its sentAt fence executes.
                async with pool.connection() as reclaimer:
                    await reclaimer.execute("update jobs set locked_at = now() - interval '1 hour' where id = %s", (JOB,))
                    await repo.recover_stale_leases(reclaimer, 60, ("mall_register",))
                    await reclaimer.commit()
                    current = await repo.claim_next_job(reclaimer, ("mall_register",), "new-worker")
                    await reclaimer.commit()
                    assert current["lease_token"] != LEASE
                before = copy.deepcopy(admin.execute("select * from jobs where id = %s", (JOB,)).fetchone())
                resume.set()
                await asyncio.wait_for(running, timeout=5)
            finally:
                resume.set()
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            assert not await patch(conn, job_id=JOB, lease_token=LEASE, patch={"create": {"sentAt": "stale"}})
            assert not await repo.set_job_progress_fenced(conn, job_id=JOB, lease_token=LEASE, progress=90)
            assert not await repo.finalize_uncharged_job(conn, job_id=JOB, lease_token=LEASE,
                status="error", result={"outcome": "failed"})
            await conn.commit()
            # A second entry with the stale snapshot must stop before constructing a client.
            await worker.run_mall_register_job(app, stale)
            after = admin.execute("select * from jobs where id = %s", (JOB,)).fetchone()
            assert after == before
            assert client_calls == ["constructed", "setting"]

    asyncio.run(scenario())
    assert admin.execute("select count(*) as n from job_events").fetchone()["n"] == 0
    assert admin.execute("select last_registered_at from mall_connections").fetchone()["last_registered_at"] is None


@pytest.mark.parametrize("status", ["done", "error"])
def test_verify_and_late_worker_finalize_do_not_clobber_product_no(verify_pg, monkeypatch, status):
    app, admin, pool, calls = verify_pg
    admin.execute("""update jobs set status = %s, metadata = metadata || %s::jsonb, result = %s,
        finished_at = now(), locked_by = null, locked_at = null where id = %s""",
        (status, Json({"create": {"sentAt": "2026-10-26T00:00:00+00:00"}}),
         Json({"outcome": "unknown", "setupStatus": "pending"}), JOB))

    async def scenario():
        patch_ready, resume = asyncio.Event(), asyncio.Event()
        patch = repo.set_job_result_patch

        async def pause_result_patch(conn, **kwargs):
            updated = await patch(conn, **kwargs)
            patch_ready.set()
            await resume.wait()
            return updated

        monkeypatch.setattr(repo, "set_job_result_patch", pause_result_patch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            verifying = asyncio.create_task(client.post(f"/v1/malls/registrations/{JOB}/verify"))
            try:
                await asyncio.wait_for(patch_ready.wait(), timeout=5)
                # A different DB connection carries the expired worker's stale lease and unknown result.
                async with pool.connection() as late:
                    assert not await repo.finalize_uncharged_job(late, job_id=JOB, lease_token=LEASE,
                        status="done", result={"outcome": "unknown", "setupStatus": "pending"})
                    assert not await repo.set_job_metadata_patch(late, job_id=JOB, lease_token=LEASE,
                        patch={"productId": "999"})
                    await late.commit()
                resume.set()
                response = await asyncio.wait_for(verifying, timeout=5)
            finally:
                resume.set()
                if not verifying.done():
                    verifying.cancel()
                await asyncio.gather(verifying, return_exceptions=True)
            assert response.status_code == 200, response.text
            assert response.json()["productId"] == "42"
            assert response.json()["setupStatus"] == "needs_attention"
            async with pool.connection() as late:
                assert not await repo.finalize_uncharged_job(late, job_id=JOB, lease_token=LEASE,
                    status="error", result={"outcome": "failed"}, error_message="늦은 워커")
                assert not await patch(late, job_id=JOB, user_id="99999999-9999-4999-8999-999999999999",
                                       expect_status=("done", "error"), patch={"productId": "999"})
                assert not await patch(late, job_id=JOB, user_id=USER,
                                       expect_status=("running",), patch={"productId": "999"})
                await late.commit()

    asyncio.run(scenario())
    saved = admin.execute("select status, metadata, result from jobs where id = %s", (JOB,)).fetchone()
    assert saved["status"] == status
    assert saved["metadata"]["productId"] == "42"
    assert "productNo" not in saved["metadata"] and "productNo" not in saved["result"]
    assert saved["result"]["outcome"] == "created" and len(calls) == 1
    assert admin.execute("select count(*) as n from job_events").fetchone()["n"] == 0
