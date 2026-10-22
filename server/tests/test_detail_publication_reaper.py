"""Retention and durable R2/CDN deletion retries without network calls."""

import calendar
import asyncio
from functools import wraps
import contextlib
import copy
import importlib
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import repo
from test_detail_publications import FakeConn, FakeCursor, FakeR2, USER, PROJECT


NOW = datetime.now(timezone.utc)


def run_async(test):
    @wraps(test)
    def wrapped(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return wrapped


class RetentionCursor(FakeCursor):
    async def execute(self, sql, args=()):
        q = " ".join(sql.lower().split())
        rows = self.store["publications"]
        if q.startswith("update detail_publications set status = 'deleted'") and "not exists" in q:
            self.store["sql"].append((q, args))
            pub_id, user_id, now, cutoff = args
            assert "deleted_at is null" in q and "interval '30 minutes'" in q
            row = rows.get(pub_id)
            eligible = row and row["user_id"] == user_id and row["deleted_at"] is None and (
                row["status"] == "deleted"
                or row["status"] == "ready" and row["expires_at"] is not None
                and row["expires_at"] <= now and self.store["plan"] == "free"
                or row["status"] == "pending" and row["created_at"] < cutoff - timedelta(minutes=30))
            self.result = []
            if eligible:
                row["status"] = "deleted"
                self.result = [copy.deepcopy(row)]
            self.rowcount = len(self.result)
            return
        if q.startswith("update detail_publications") and "from profiles" in q:
            self.store["sql"].append((q, args))
            assert "interval '6 months'" in q and "status = 'ready'" in q
            now = args["now"]
            changed = 0
            for row in rows.values():
                if row["status"] != "ready":
                    continue
                current = row["expires_at"]
                expiry = current
                if self.store["plan"] != "free":
                    expiry = None
                elif current is None:
                    month = now.month + 6
                    year, month = now.year + (month - 1) // 12, (month - 1) % 12 + 1
                    expiry = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
                row["expires_at"] = expiry
                changed += current != expiry
            self.rowcount = changed
            return
        if q.startswith("select") and "from detail_publications" in q and "where" in q and "user_id = %s" not in q:
            self.store["sql"].append((q, args))
            if "status = 'pending'" in q:
                minutes = args[0]
                self.result = [copy.deepcopy(r) for r in rows.values() if r["status"] == "pending" and
                    (r["created_at"] < NOW - timedelta(minutes=minutes) or r["expires_at"] is not None and r["expires_at"] <= NOW)]
            else:
                now, limit = args
                assert "deleted_at is null" in q and "status = 'deleted'" in q
                current_plan_guard = "not exists" in q and "from profiles" in q and "p.plan <> 'free'" in q
                self.result = [copy.deepcopy(r) for r in rows.values() if
                    r["status"] == "ready" and r["expires_at"] is not None and r["expires_at"] <= now
                    and (not current_plan_guard or self.store["plan"] == "free")
                    or r["status"] == "deleted" and r["deleted_at"] is None][:limit]
            self.rowcount = len(self.result)
            return
        await super().execute(sql, args)


class RetentionConn(FakeConn):
    def cursor(self):
        return RetentionCursor(self.store)


def setup_reaper(plan="free"):
    assert importlib.util.find_spec("app.workers.detail_publication_reaper") is not None
    module = importlib.import_module("app.workers.detail_publication_reaper")
    store = {"publications": {}, "assets": {}, "plan": plan, "sql": [], "real": False, "rate": 0}

    @contextlib.asynccontextmanager
    async def connection():
        conn = RetentionConn(store)
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise

    r2 = FakeR2()
    app = SimpleNamespace(state=SimpleNamespace(r2=r2, pool=SimpleNamespace(connection=connection)))
    return module.DetailPublicationReaper(app), store, r2


def seed(store, *, status="ready", expires_at=None, created_at=None):
    pub_id = str(uuid.uuid4())
    key = f"pub/{USER}/{pub_id}/01.jpg"
    asset_id = str(uuid.uuid4())
    row = dict(id=pub_id, user_id=USER, project_id=PROJECT, version=1, status=status,
               preset="smartstore", width=860, slice_count=1, total_bytes=100,
               product_name="옷", editor_revision=7, expires_at=expires_at,
               files=[dict(index=1, r2_key=key, bytes=100, width=860, height=1000, asset_id=asset_id)],
               created_at=created_at or NOW - timedelta(days=1), deleted_at=None, ready_at=NOW)
    store["publications"][pub_id] = row
    store["assets"][asset_id] = dict(id=asset_id, source="export", visibility="public",
                                    metadata={"publication_id": pub_id}, deleted_at=None)
    return row


@run_async
async def test_recompute_sets_expiry_for_free_user():
    assert hasattr(repo, "recompute_detail_publication_expiry")
    _worker, store, _r2 = setup_reaper()
    row = seed(store)
    now = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    assert await repo.recompute_detail_publication_expiry(RetentionConn(store), now=now) == 1
    assert row["expires_at"] == datetime(2026, 7, 31, 12, tzinfo=timezone.utc)


@run_async
async def test_recompute_clears_expiry_when_plan_returns():
    assert hasattr(repo, "recompute_detail_publication_expiry")
    _worker, store, _r2 = setup_reaper(plan="pro")
    row = seed(store, expires_at=NOW + timedelta(days=1))
    assert await repo.recompute_detail_publication_expiry(RetentionConn(store), now=NOW) == 1
    assert row["expires_at"] is None


@run_async
async def test_sweep_deletes_expired_and_purges():
    worker, store, r2 = setup_reaper()
    row = seed(store, expires_at=NOW - timedelta(days=1))
    await worker._sweep_once()
    assert r2.deleted == r2.purged == [f"pub/{USER}/{row['id']}/01.jpg"]
    assert row["status"] == "deleted" and row["deleted_at"] is not None
    assert all(a["deleted_at"] is not None for a in store["assets"].values())


@run_async
async def test_sweep_keeps_row_when_r2_delete_fails():
    worker, store, r2 = setup_reaper()
    row = seed(store, expires_at=NOW - timedelta(days=1))
    r2.fail_delete = True
    await worker._sweep_once()
    assert row["status"] == "deleted" and row["deleted_at"] is None
    assert all(a["deleted_at"] is None for a in store["assets"].values())
    r2.fail_delete = False
    await worker._sweep_once()
    assert row["status"] == "deleted"


@run_async
async def test_sweep_cleans_stale_pending():
    worker, store, r2 = setup_reaper()
    old = seed(store, status="pending", created_at=NOW - timedelta(minutes=31))
    fresh = seed(store, status="pending", created_at=NOW - timedelta(minutes=1), expires_at=NOW - timedelta(minutes=1))
    await worker._sweep_once()
    assert r2.deleted == [f"pub/{USER}/{old['id']}/01.jpg"]
    assert old["status"] == "deleted" and fresh["status"] == "pending"


@run_async
async def test_sweep_retries_soft_deleted_publication():
    worker, store, r2 = setup_reaper(plan="pro")
    row = seed(store, status="deleted", expires_at=NOW - timedelta(hours=1))
    await worker._sweep_once()
    assert row["deleted_at"] is not None
    assert r2.deleted == r2.purged == [f"pub/{USER}/{row['id']}/01.jpg"]


@run_async
async def test_sweep_skips_invalid_files_and_continues(caplog):
    worker, store, r2 = setup_reaper()
    bad = seed(store, expires_at=NOW - timedelta(days=1))
    good = seed(store, expires_at=NOW - timedelta(days=1))
    bad["files"] = []
    await worker._sweep_once()
    assert bad["deleted_at"] is None and good["deleted_at"] is not None
    assert r2.deleted == r2.purged == [f"pub/{USER}/{good['id']}/01.jpg"]
    assert f"detail publication cleanup deferred: {bad['id']}" in caplog.text


@run_async
async def test_sweep_preserves_resubscribed_user_between_daily_recomputations():
    worker, store, r2 = setup_reaper(plan="pro")
    row = seed(store, expires_at=NOW - timedelta(days=1))
    worker._last_expiry_recompute = NOW
    await worker._sweep_once()
    assert r2.deleted == [] and r2.purged == []
    assert row["status"] == "ready" and row["deleted_at"] is None
    assert all(a["deleted_at"] is None for a in store["assets"].values())


@run_async
async def test_sweep_retries_cdn_failure_before_finalizing():
    worker, store, r2 = setup_reaper()
    row = seed(store, expires_at=NOW - timedelta(days=1))
    normal_purge = r2.purge_public_cache
    r2.purge_public_cache = lambda _keys: (_ for _ in ()).throw(RuntimeError("CDN unavailable"))
    await worker._sweep_once()
    assert row["status"] == "deleted" and row["deleted_at"] is None
    r2.purge_public_cache = normal_purge
    await worker._sweep_once()
    assert row["status"] == "deleted" and row["deleted_at"] is not None


@run_async
async def test_sweep_recomputes_expiry_only_daily():
    worker, store, _r2 = setup_reaper()
    await worker._sweep_once()
    await worker._sweep_once()
    assert len([sql for sql, _ in store["sql"] if sql.startswith("update detail_publications") and "from profiles" in sql]) == 1


@run_async
async def test_sweep_preserves_renewal_committed_after_batch_listing(monkeypatch):
    worker, store, r2 = setup_reaper()
    row = seed(store, expires_at=NOW - timedelta(days=1))
    worker._last_expiry_recompute = NOW
    original = repo.list_expired_detail_publications

    async def list_then_renew(conn, **kwargs):
        candidates = await original(conn, **kwargs)
        store["plan"] = "pro"
        return candidates

    monkeypatch.setattr(repo, "list_expired_detail_publications", list_then_renew)
    await worker._sweep_once()
    assert r2.deleted == r2.purged == []
    assert row["status"] == "ready" and row["deleted_at"] is None
    assert all(a["deleted_at"] is None for a in store["assets"].values())


@run_async
async def test_claimed_expiry_retries_after_paid_renewal_and_storage_failure():
    worker, store, r2 = setup_reaper()
    row = seed(store, expires_at=NOW - timedelta(days=1))
    original = r2.delete

    def renew_and_fail(_key):
        assert store["publications"][row["id"]]["status"] == "deleted"
        store["plan"] = "pro"
        raise RuntimeError("storage unavailable after expiry claim")

    r2.delete = renew_and_fail
    await worker._sweep_once()
    assert store["plan"] == "pro"
    assert row["status"] == "deleted" and row["deleted_at"] is None
    assert all(a["deleted_at"] is None for a in store["assets"].values())
    r2.delete = original
    await worker._sweep_once()
    assert row["deleted_at"] is not None
    assert r2.deleted == r2.purged == [f"pub/{USER}/{row['id']}/01.jpg"]


@run_async
async def test_expiry_claim_update_skips_paid_user():
    _worker, store, _r2 = setup_reaper(plan="pro")
    row = seed(store, expires_at=NOW - timedelta(days=1))
    conn = RetentionConn(store)
    assert await repo.claim_detail_publication_deletion(
        conn, user_id=USER, publication_id=row["id"], now=NOW) is None
    assert row["status"] == "ready"
    assert len(store["sql"]) == 1
    sql, args = store["sql"][0]
    assert sql.startswith("update detail_publications set status = 'deleted'")
    assert "not exists" in sql and "p.plan <> 'free'" in sql
    assert "for update" not in sql
    assert args == (row["id"], USER, NOW, NOW)
    store["plan"] = "free"
    claimed = await repo.claim_detail_publication_deletion(
        conn, user_id=USER, publication_id=row["id"], now=NOW)
    assert claimed["status"] == row["status"] == "deleted"
    assert store["sql"] == [(sql, args), (sql, args)]
