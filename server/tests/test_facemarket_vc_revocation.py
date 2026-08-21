import asyncio
import contextlib
import types
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app import holder_client, main
from app.workers import fm_vc_revocation_reconciler as reconciler_module
from app.workers.fm_vc_revocation_reconciler import FaceVcRevocationReconciler
from conftest import make_settings


class _Response:
    def __init__(self, status_code, payload=None, *, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class _MemoryReconciler(FaceVcRevocationReconciler):
    def __init__(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(settings=types.SimpleNamespace(
            opendid_holder_url="http://holder",
            opendid_holder_hmac_secret="shared-secret",
        )))
        super().__init__(app)
        self.job = {
            "id": "job-1",
            "license_id": "lic-1",
            "model_id": "11111111-1111-1111-1111-111111111111",
            "vc_id": "vc-1",
            "attempts": 0,
            "lease_token": None,
            "status": "pending",
        }

    async def _claim_one(self):
        if self.job["status"] not in {"pending", "retry"}:
            return None
        self.job.update(status="processing", lease_token="lease-1")
        return dict(self.job)

    async def _mark_retry(self, job, code):
        assert job["lease_token"] == self.job["lease_token"]
        self.job.update(
            status="retry",
            attempts=self.job["attempts"] + 1,
            lease_token=None,
            last_error_code=code,
        )

    async def _mark_revoked(self, job):
        assert job["lease_token"] == self.job["lease_token"]
        self.job.update(status="revoked", lease_token=None)


def _patch_holder(monkeypatch, results):
    calls = []
    results = iter(results)

    async def fake_post(client, **kwargs):
        calls.append({**kwargs, "timeout": client.timeout.read})
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(holder_client, "post", fake_post)
    return calls


def test_reconciler_signed_verify_revoke_verify_then_marks_revoked(monkeypatch):
    reconciler = _MemoryReconciler()
    calls = _patch_holder(monkeypatch, [
        _Response(200, {"verified": True, "status": "valid", "onChain": True}),
        _Response(200, {"revoked": True, "status": "revoked", "txId": "tx-1"}),
        _Response(200, {"verified": False, "status": "revoked", "onChain": True}),
    ])

    asyncio.run(reconciler._sweep_once())

    assert reconciler.job["status"] == "revoked"
    assert calls == [
        {
            "base_url": "http://holder",
            "secret": "shared-secret",
            "path": "/holder/vc/verify",
            "payload": {"vcId": "vc-1"},
            "timeout": 5.0,
        },
        {
            "base_url": "http://holder",
            "secret": "shared-secret",
            "path": "/holder/models/11111111-1111-1111-1111-111111111111/revoke-vc",
            "payload": {"vcId": "vc-1"},
            "timeout": 180.0,
        },
        {
            "base_url": "http://holder",
            "secret": "shared-secret",
            "path": "/holder/vc/verify",
            "payload": {"vcId": "vc-1"},
            "timeout": 5.0,
        },
    ]


def test_already_revoked_job_skips_duplicate_revoke(monkeypatch):
    reconciler = _MemoryReconciler()
    calls = _patch_holder(monkeypatch, [
        _Response(200, {"verified": False, "status": "revoked", "onChain": True}),
    ])

    asyncio.run(reconciler._sweep_once())

    assert reconciler.job["status"] == "revoked"
    assert [call["path"] for call in calls] == ["/holder/vc/verify"]


def test_holder_sequence_has_one_210_second_operation_budget(monkeypatch):
    reconciler = _MemoryReconciler()
    _patch_holder(monkeypatch, [
        _Response(200, {"verified": False, "status": "revoked", "onChain": True}),
    ])
    budgets = []

    @contextlib.asynccontextmanager
    async def capture_timeout(seconds):
        budgets.append(seconds)
        yield

    monkeypatch.setattr(reconciler_module.asyncio, "timeout", capture_timeout)

    asyncio.run(reconciler._sweep_once())

    assert budgets == [210]


@pytest.mark.parametrize(
    ("responses", "code"),
    [
        ([httpx.ConnectError("holder down")], "transport"),
        ([_Response(503, {})], "http_status"),
        ([_Response(200, [])], "invalid_body"),
        ([
            _Response(200, {"verified": True, "status": "valid"}),
            _Response(200, {"revoked": False, "status": "revoked"}),
        ], "invalid_body"),
        ([
            _Response(200, {"verified": True, "status": "valid"}),
            _Response(200, {"revoked": True, "status": "revoked"}),
            _Response(200, {"verified": True, "status": "valid"}),
        ], "not_revoked"),
    ],
)
def test_reconciler_persists_only_bounded_failure_codes(monkeypatch, responses, code):
    reconciler = _MemoryReconciler()
    _patch_holder(monkeypatch, responses)

    asyncio.run(reconciler._sweep_once())

    assert reconciler.job["status"] == "retry"
    assert reconciler.job["attempts"] == 1
    assert reconciler.job["last_error_code"] == code


def test_cancellation_leaves_processing_job_for_lease_recovery(monkeypatch):
    reconciler = _MemoryReconciler()
    started = asyncio.Event()

    async def blocked_post(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(holder_client, "post", blocked_post)

    async def scenario():
        task = asyncio.create_task(reconciler._sweep_once())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert reconciler.job["status"] == "processing"
    assert reconciler.job["lease_token"] == "lease-1"


def test_stop_cancels_and_awaits_a_stuck_sweep(monkeypatch):
    started = asyncio.Event()
    terminated = asyncio.Event()

    class BlockingReconciler(FaceVcRevocationReconciler):
        async def _sweep_once(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                terminated.set()

    monkeypatch.setattr(reconciler_module, "_STOP_TIMEOUT_SECONDS", 0.001)
    reconciler = BlockingReconciler(types.SimpleNamespace(state=types.SimpleNamespace()))

    async def scenario():
        await reconciler.start()
        await started.wait()
        await reconciler.stop()

    asyncio.run(scenario())

    assert terminated.is_set()
    assert reconciler._task is None


class _SqlCursor:
    def __init__(self, calls):
        self.calls = calls
        self.one = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        params = params or ()
        self.calls.append((normalized, params))
        if normalized.startswith("with candidate"):
            self.one = {
                "id": "job-1",
                "license_id": "lic-1",
                "model_id": "model-1",
                "vc_id": "vc-1",
                "attempts": 3,
                "lease_token": params[0],
                "status": "processing",
            }
        else:
            self.one = None

    async def fetchone(self):
        return self.one


class _SqlConn:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _SqlCursor(self.calls)

    async def commit(self):
        self.calls.append(("commit", ()))


class _SqlPool:
    def __init__(self):
        self.calls = []
        self.active_connections = 0

    def connection(self):
        @contextlib.asynccontextmanager
        async def connection():
            self.active_connections += 1
            try:
                yield _SqlConn(self.calls)
            finally:
                self.active_connections -= 1

        return connection()


def _sql_reconciler():
    pool = _SqlPool()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pool=pool,
        settings=types.SimpleNamespace(
            opendid_holder_url="http://holder",
            opendid_holder_hmac_secret="shared-secret",
        ),
    ))
    return FaceVcRevocationReconciler(app), pool


def test_claim_recovers_stale_leases_and_uses_skip_locked_single_claim():
    reconciler, pool = _sql_reconciler()

    job = asyncio.run(reconciler._claim_one())

    sql = "\n".join(call[0] for call in pool.calls)
    assert "status = 'processing'" in sql and "lease_expires_at <= now()" in sql
    assert "for update skip locked" in sql
    assert "limit 1" in sql
    assert "interval '240 seconds'" in sql
    uuid.UUID(job["lease_token"])
    assert pool.calls[-1][0] == "commit"


def test_reconciler_releases_database_connection_before_holder_io(monkeypatch):
    reconciler, pool = _sql_reconciler()

    async def holder_post(_client, **_kwargs):
        assert pool.active_connections == 0
        return _Response(200, {"verified": False, "status": "revoked"})

    monkeypatch.setattr(holder_client, "post", holder_post)

    asyncio.run(reconciler._sweep_once())

    assert pool.active_connections == 0
    assert any(
        "set status = 'revoked'" in sql
        for sql, _params in pool.calls
    )


def test_mark_updates_are_lease_fenced_and_retry_is_exponential():
    reconciler, pool = _sql_reconciler()
    job = {
        "id": "job-1",
        "lease_token": "lease-1",
        "attempts": 3,
    }

    asyncio.run(reconciler._mark_retry(job, "transport"))
    asyncio.run(reconciler._mark_revoked(job))

    updates = [call for call in pool.calls if call[0].startswith("update")]
    retry_sql, retry_params = updates[0]
    success_sql, success_params = updates[1]
    for sql in (retry_sql, success_sql):
        assert "where id = %s and status = 'processing' and lease_token = %s" in sql
    assert "attempts = attempts + 1" in retry_sql
    assert "power(2, least(attempts + 1, 9))" in retry_sql
    assert "make_interval(secs =>" in retry_sql
    assert retry_params == ("transport", "job-1", "lease-1")
    assert success_params == ("job-1", "lease-1")


def test_mark_retry_never_persists_an_unbounded_error_value():
    reconciler, pool = _sql_reconciler()

    asyncio.run(reconciler._mark_retry(
        {"id": "job-1", "lease_token": "lease-1"},
        "response body with vc-1",
    ))

    update = next(call for call in pool.calls if call[0].startswith("update"))
    assert update[1][0] == "transport"


def test_main_starts_mandatory_reconciler_and_quiesces_it_before_pool_close(monkeypatch):
    events = []

    class Pool:
        async def open(self):
            events.append("pool.open")

        async def close(self):
            assert events[-1] == "reconciler.stop.done"
            events.append("pool.close")

    class Reconciler:
        def __init__(self, _app):
            events.append("reconciler.init")

        async def start(self):
            events.append("reconciler.start")

        async def stop(self):
            events.append("reconciler.stop.begin")
            await asyncio.sleep(0)
            events.append("reconciler.stop.done")

    monkeypatch.setattr(main, "create_pool", lambda _url: Pool())
    monkeypatch.setattr(main, "FaceVcRevocationReconciler", Reconciler)
    app = main.create_app(make_settings(
        database_url="postgresql://unused",
        fm_vc_required=True,
        opendid_holder_url="http://holder",
        opendid_holder_hmac_secret="shared-secret",
        job_dispatcher_enabled=False,
    ))

    with TestClient(app):
        pass

    assert events == [
        "pool.open",
        "reconciler.init",
        "reconciler.start",
        "reconciler.stop.begin",
        "reconciler.stop.done",
        "pool.close",
    ]


def test_main_does_not_start_reconciler_when_vc_is_optional(monkeypatch):
    class Pool:
        async def open(self):
            return None

        async def close(self):
            return None

    class ForbiddenReconciler:
        def __init__(self, _app):
            raise AssertionError("optional VC must not start reconciler")

    monkeypatch.setattr(main, "create_pool", lambda _url: Pool())
    monkeypatch.setattr(main, "FaceVcRevocationReconciler", ForbiddenReconciler)
    app = main.create_app(make_settings(
        database_url="postgresql://unused",
        fm_vc_required=False,
        job_dispatcher_enabled=False,
    ))

    with TestClient(app):
        pass
