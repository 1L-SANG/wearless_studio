import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main, repo
from app.services import sam_autoscale
from app.workers import dispatcher as dispatcher_mod
from conftest import make_settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, dispatcher_mod._KINDS),
        ("", dispatcher_mod._KINDS),
        ("all", dispatcher_mod._KINDS),
        ("detail_page", ("detail_page",)),
        ("-detail_page", tuple(k for k in dispatcher_mod._KINDS if k != "detail_page")),
    ],
)
def test_job_kind_partition_is_backward_compatible(raw, expected):
    assert dispatcher_mod.configured_job_kinds(raw) == expected


@pytest.mark.parametrize("raw", ["missing", "detail_page,-analyze", "-missing"])
def test_job_kind_partition_fails_fast_on_unsafe_config(raw):
    with pytest.raises(ValueError, match="JOB_KINDS"):
        dispatcher_mod.configured_job_kinds(raw)


def test_dispatcher_uses_configured_kinds(monkeypatch):
    monkeypatch.setenv("JOB_KINDS", "detail_page")
    dispatcher = dispatcher_mod.JobDispatcher(SimpleNamespace())
    assert dispatcher.kinds == ("detail_page",)


def test_stale_recovery_is_scoped_to_the_dispatcher_kinds(monkeypatch):
    seen = []

    class Conn:
        async def commit(self):
            pass

    class Pool:
        def connection(self):
            class Context:
                async def __aenter__(self):
                    return Conn()

                async def __aexit__(self, *_args):
                    return False

            return Context()

    async def recover(_conn, timeout, kinds):
        seen.append((timeout, kinds))

    async def unsettled(_conn):
        return []

    monkeypatch.setattr(dispatcher_mod.repo, "recover_stale_leases", recover)
    monkeypatch.setattr(dispatcher_mod.repo, "list_unsettled_errored_jobs", unsettled)
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(facemarket_enabled=False)))
    dispatcher = dispatcher_mod.JobDispatcher(app, kinds=("detail_page",))

    asyncio.run(dispatcher._recover_stale(SimpleNamespace(job_lease_timeout_seconds=900), Pool()))

    assert seen == [(900, ("detail_page",))]


class _Pool:
    opened = False
    closed = False

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True


def test_detail_worker_lifespan_starts_only_its_dispatcher(monkeypatch):
    events = []
    pool = _Pool()

    class Dispatcher:
        def __init__(self, app, *, kinds=None):
            events.append(("dispatcher_init", kinds))

        async def start(self):
            events.append("dispatcher_start")

        async def stop(self):
            events.append("dispatcher_stop")

    class Forbidden:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("API-only background service started in detail worker")

    monkeypatch.setenv("JOB_KINDS", "detail_page")
    monkeypatch.setattr(main, "create_pool", lambda _url: pool)
    monkeypatch.setattr(main, "R2Client", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main, "JobDispatcher", Dispatcher)
    monkeypatch.setattr(main, "DraftAssetReclaimer", Forbidden)
    monkeypatch.setattr(main, "FaceVcRevocationReconciler", Forbidden)
    monkeypatch.setattr(main, "SamRetryPusher", Forbidden)
    monkeypatch.setattr(main, "SamAutoscaleAdapter", Forbidden)
    monkeypatch.setattr(main, "SamAutoscaler", Forbidden)
    monkeypatch.setattr(main.sam_client, "install_prewarm_hook", lambda _hook: None)

    settings = make_settings(
        database_url="postgresql://test",
        openai_api_key="sk-test",
        r2_bucket="wearless",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_endpoint="https://r2.test",
    )
    app = main.create_app(settings)

    with TestClient(app):
        pass

    assert events == [
        ("dispatcher_init", ("detail_page",)),
        "dispatcher_start",
        "dispatcher_stop",
    ]
    assert pool.opened and pool.closed


class _Cursor:
    def __init__(self):
        self.query = None
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, query, params=None):
        self.query = query
        self.params = params

    async def fetchone(self):
        return {
            "active": 2,
            "last_finished": datetime(2026, 8, 26, tzinfo=timezone.utc),
        }


def test_detail_worker_demand_counts_pending_and_running_jobs():
    cursor = _Cursor()
    conn = SimpleNamespace(cursor=lambda: cursor)
    snapshot = asyncio.run(repo.detail_worker_demand_snapshot(conn))

    assert snapshot.active_sam_jobs == 2
    assert snapshot.last_sam_finished_at == datetime(2026, 8, 26, tzinfo=timezone.utc)
    assert "status in ('pending', 'running')" in cursor.query
    assert cursor.params == ("detail_page",)


def test_autoscale_adapter_uses_runtime_region_and_copilot_tags(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("COPILOT_APPLICATION_NAME", "wearless")
    monkeypatch.setenv("COPILOT_ENVIRONMENT_NAME", "prod")

    assert sam_autoscale.aws_region() == "us-east-1"
    adapter = sam_autoscale.SamAutoscaleAdapter(
        SimpleNamespace(detail_worker_autoscale="on"),
        service="detail-worker",
        enabled_attr="detail_worker_autoscale",
        ecs=object(),
        sns=object(),
    )
    assert adapter._required_tags == {
        "copilot-application": "wearless",
        "copilot-environment": "prod",
        "copilot-service": "detail-worker",
    }
