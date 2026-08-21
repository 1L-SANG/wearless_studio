import asyncio
import types

import pytest
from fastapi import HTTPException

from app import facemarket, facemarket_enrollment, personalization, repo
from app.workers import fm_model_asset_job, personalization_purge_job
from test_facemarket_biometric_enrollment import (  # noqa: F401
    auth,
    enrollment_client,
    enrollment_store,
    fake_pool,
    fake_r2,
    fake_rekognition,
    fake_sts,
)
from test_facemarket_licenses import biometric_fm  # noqa: F401


class _WorkerCursor:
    def __init__(self, store):
        self.store = store
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.store["sql"].append(query)
        self.rows = []
        job = self.store["job"]
        if (
            query.startswith("select payload")
            and "from jobs" in query
            and "for update" in query
        ):
            job_id, lease = params
            if job["id"] == job_id and job["locked_by"] == lease and job["status"] == "running":
                self.rows = [{"payload": dict(job["payload"])}]
        elif query.startswith("select id from jobs"):
            job_id, lease = params
            if job["id"] == job_id and job["locked_by"] == lease and job["status"] == "running":
                self.rows = [{"id": job_id}]
        elif query.startswith("select id::text as id from personalization_profiles"):
            self.rows = [{"id": profile_id} for profile_id in self.store["profile_ids"]]
        elif query.startswith("select id::text as id from fm_biometric_purge_receipts"):
            self.rows = [{"id": "receipt-1"}] if self.store["receipt"] else []
        elif query.startswith("update jobs set status = 'done'"):
            result, job_id = params
            assert job_id == job["id"]
            job.update(status="done", result=getattr(result, "obj", result))
        elif query.startswith("insert into job_events"):
            payload = params[-1]
            self.store["events"].append(getattr(payload, "obj", payload))
        elif query.startswith("insert into personalization_audit_log"):
            self.store["audits"].append(params)
        elif query.startswith("with locked as") and "update jobs j set status='pending'" in query:
            job.update(status="pending", locked_by=None, locked_at=None)
            self.rows = [{"id": job["id"]}]
        else:
            raise AssertionError(f"unhandled worker SQL: {query}")

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class _WorkerConn:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def cursor(self):
        return _WorkerCursor(self.store)

    async def commit(self):
        self.store["commits"] += 1

    async def rollback(self):
        self.store["rollbacks"] += 1


class _WorkerPool:
    def __init__(self, store):
        self.store = store

    def connection(self):
        return _WorkerConn(self.store)


def _worker_app(store):
    return types.SimpleNamespace(state=types.SimpleNamespace(pool=_WorkerPool(store)))


def _claimed_purge_store(*, payload_reason):
    return {
        "job": {
            "id": "job-1",
            "user_id": "user-1",
            "locked_by": "lease-1",
            "status": "running",
            "payload": {"reason": payload_reason},
            "result": None,
        },
        "profile_ids": ["profile-1"],
        "receipt": True,
        "events": [],
        "audits": [],
        "sql": [],
        "commits": 0,
        "rollbacks": 0,
    }


def _job_arg(reason):
    return {
        "id": "job-1",
        "user_id": "user-1",
        "lease_token": "lease-1",
        "payload": {"reason": reason},
    }


@pytest.mark.parametrize("initial_payload", ["withdrawal"])
def test_purge_worker_reloads_account_delete_upgrade_before_shared_purge(
    monkeypatch, initial_payload
):
    store = _claimed_purge_store(payload_reason="account_delete")
    calls = []

    async def noop(*_args, **_kwargs):
        return None

    async def fake_purge(_app, *, user_id, reason, source_job_id=None, **_kwargs):
        calls.append((user_id, reason, source_job_id))
        return types.SimpleNamespace(
            target_count=0,
            confirmed_absent_count=0,
            model_count=1,
            profile_count=1,
            enrollment_count=0,
            asset_count=0,
        )

    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_personalization_writers", noop)
    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_user_facemarket_writers", noop)
    monkeypatch.setattr(personalization_purge_job, "purge_biometric_scope", fake_purge)

    asyncio.run(
        personalization_purge_job.run_personalization_purge_job(
            _worker_app(store), _job_arg(initial_payload)
        )
    )

    assert calls == [("user-1", "account_delete", "job-1")]
    assert store["job"]["result"]["outcome"] == "ready_for_identity_delete"
    assert store["job"]["result"]["receiptId"] == "receipt-1"
    assert all(event["outcome"] != "biometric_purged" for event in store["events"])


def test_purge_worker_loops_when_account_delete_upgrade_arrives_after_withdrawal_purge(
    monkeypatch,
):
    store = _claimed_purge_store(payload_reason="withdrawal")
    calls = []

    async def noop(*_args, **_kwargs):
        return None

    async def fake_purge(_app, *, user_id, reason, source_job_id=None, **_kwargs):
        calls.append((user_id, reason, source_job_id))
        if reason == "withdrawal":
            store["job"]["payload"] = {"reason": "account_delete"}
        return types.SimpleNamespace(
            target_count=0,
            confirmed_absent_count=0,
            model_count=1,
            profile_count=1,
            enrollment_count=0,
            asset_count=0,
        )

    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_personalization_writers", noop)
    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_user_facemarket_writers", noop)
    monkeypatch.setattr(personalization_purge_job, "purge_biometric_scope", fake_purge)

    asyncio.run(
        personalization_purge_job.run_personalization_purge_job(
            _worker_app(store), _job_arg("withdrawal")
        )
    )

    assert calls == [
        ("user-1", "withdrawal", None),
        ("user-1", "account_delete", "job-1"),
    ]
    assert store["job"]["result"]["outcome"] == "ready_for_identity_delete"
    assert store["job"]["result"]["receiptId"] == "receipt-1"
    assert store["events"] == [store["job"]["result"]]


def test_purge_worker_retries_before_biometric_purge_when_user_writers_not_drained(
    monkeypatch,
):
    store = _claimed_purge_store(payload_reason="account_delete")
    calls = []

    async def noop(*_args, **_kwargs):
        return None

    async def blocked(*_args, **_kwargs):
        raise personalization_purge_job.facemarket_cutover.CutoverBlocked(
            "writers_not_drained"
        )

    async def fake_purge(*_args, **_kwargs):
        calls.append(_kwargs)
        raise AssertionError("purge must wait for user-scoped writer fences")

    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_personalization_writers", noop)
    monkeypatch.setattr(personalization_purge_job.facemarket_cutover, "quiesce_user_facemarket_writers", blocked)
    monkeypatch.setattr(personalization_purge_job, "purge_biometric_scope", fake_purge)

    asyncio.run(
        personalization_purge_job.run_personalization_purge_job(
            _worker_app(store), _job_arg("account_delete")
        )
    )

    assert calls == []
    assert store["job"]["status"] == "pending"
    assert store["events"] == []
    assert store["audits"] == []
    assert not any("fm_biometric_purge_receipts" in query for query in store["sql"])


class _ClosedCursor:
    def __init__(self, jobs):
        self.jobs = jobs
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        if "status in ('pending', 'running')" not in query:
            raise AssertionError(f"helper did not close active purge jobs: {query}")
        if "result->>'outcome' = 'ready_for_identity_delete'" not in query:
            raise AssertionError(f"helper did not require final outcome: {query}")
        if "user_id = %s" not in query:
            raise AssertionError(f"helper did not use server-owned user: {query}")
        (user_id,) = params
        closed = any(
            job["user_id"] == user_id
            and job["kind"] == "personalization_purge"
            and (
                job["status"] in {"pending", "running"}
                or (
                    job["status"] == "done"
                    and job.get("reason") == "account_delete"
                    and job.get("outcome") == "ready_for_identity_delete"
                )
            )
            for job in self.jobs
        )
        self.rows = [{"closed": closed}]

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class _ClosedConn:
    def __init__(self, jobs):
        self.jobs = jobs

    def cursor(self):
        return _ClosedCursor(self.jobs)


@pytest.mark.parametrize(
    ("job", "closed"),
    [
        ({"status": "pending", "reason": "withdrawal", "outcome": None}, True),
        ({"status": "running", "reason": "account_delete", "outcome": None}, True),
        ({"status": "done", "reason": "withdrawal", "outcome": "biometric_purged"}, False),
        (
            {
                "status": "done",
                "reason": "account_delete",
                "outcome": "ready_for_identity_delete",
            },
            True,
        ),
    ],
)
def test_repo_user_account_purge_closed_blocks_active_purges_and_completed_account_delete_only(job, closed):
    job = {"user_id": "user-1", "kind": "personalization_purge", **job}
    assert asyncio.run(repo.user_account_purge_closed(_ClosedConn([job]), "user-1")) is closed
    assert asyncio.run(repo.user_account_purge_closed(_ClosedConn([job]), "other-user")) is False


class _NoWriteConn:
    def cursor(self):
        raise AssertionError("closed accounts must not recreate profile rows")


def test_personalization_ensure_profile_rejects_closed_account_before_insert(monkeypatch):
    async def closed(_conn, _user_id):
        return True

    monkeypatch.setattr(repo, "user_account_purge_closed", closed, raising=False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(personalization._ensure_profile(_NoWriteConn(), "user-1"))

    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "account_closed"


def test_facemarket_enrollment_create_rejects_closed_account_before_rows(
    enrollment_client, auth, enrollment_store, monkeypatch
):
    async def closed(_conn, _user_id):
        return True

    monkeypatch.setattr(repo, "user_account_purge_closed", closed, raising=False)

    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "device-id-with-at-least-32-characters",
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v1"},
        },
        headers=auth(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_closed"
    assert enrollment_store.enrollments == []


def test_facemarket_license_create_rejects_closed_account_before_license_or_holder(
    biometric_fm, make_token, monkeypatch
):
    client, store, _r2 = biometric_fm

    async def closed(_conn, _user_id):
        return True

    monkeypatch.setattr(repo, "user_account_purge_closed", closed, raising=False)

    response = client.post(
        "/v1/facemarket/licenses",
        json={"enrollmentId": "22222222-2222-2222-2222-222222222222"},
        headers={"Authorization": f"Bearer {make_token(sub='user-1')}"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_closed"
    assert store["licenses"] == []
    assert store["revocations"] == {}


def test_account_closed_producer_scan_covers_personalization_and_facemarket_writers():
    personalization_source = personalization.__loader__.get_source(personalization.__name__)
    facemarket_source = facemarket.__loader__.get_source(facemarket.__name__)
    enrollment_source = facemarket_enrollment.__loader__.get_source(facemarket_enrollment.__name__)
    asset_worker_source = fm_model_asset_job.__loader__.get_source(fm_model_asset_job.__name__)

    assert personalization_source.count("_assert_account_open(") >= 6
    assert facemarket_source.count("_assert_account_open(") >= 4
    assert enrollment_source.count("_assert_account_open(") >= 6
    assert asset_worker_source.count("_assert_account_open(") >= 1
    assert "user_account_purge_closed" in personalization_source
    assert "user_account_purge_closed" in facemarket_source
    assert "user_account_purge_closed" in enrollment_source
    assert "user_account_purge_closed" in asset_worker_source
