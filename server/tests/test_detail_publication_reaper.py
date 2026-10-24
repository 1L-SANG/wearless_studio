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
        if q.startswith("select id from detail_publications") and q.endswith("for update"):
            self.store["sql"].append((q, args))
            self.result = [{"id": r["id"]} for r in rows.values() if r["status"] == "ready"]
            return
        if q.startswith("select user_id from profiles"):
            self.store["sql"].append((q, args))
            self.result = [{"user_id": USER}]
            return
        if q.startswith("update detail_publications set status = 'deleted'") and "not exists" in q:
            self.store["sql"].append((q, args))
            pub_id, user_id, now, cutoff, cleanup_now = args
            row = rows.get(pub_id)
            eligible = row and row["user_id"] == user_id and (
                row["status"] == "deleted" and row["cleanup_after"] <= cleanup_now
                or row["status"] == "ready" and row["expires_at"] is not None
                and row["expires_at"] <= now and self.store["plan"] == "free"
                or row["status"] == "pending" and row["created_at"] < cutoff - timedelta(minutes=30))
            self.result = []
            if eligible:
                row["status"] = "deleted"
                row["cleanup_generation"] += 1
                self.result = [copy.deepcopy(row)]
            self.rowcount = len(self.result)
            return
        if q.startswith("update detail_publications") and "from profiles" in q:
            self.store["sql"].append((q, args))
            assert "interval '6 months'" in q and "status = 'ready'" in q
            now = args["now"]
            changed = 0
            for row in rows.values():
                if row["status"] != "ready" or row["id"] not in args["publication_ids"]:
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
        if q.startswith(("select", "(select")) and "from detail_publications" in q and "where" in q and "user_id = %s" not in q:
            self.store["sql"].append((q, args))
            if "status = 'pending'" in q:
                minutes = args[0]
                self.result = [copy.deepcopy(r) for r in rows.values() if r["status"] == "pending" and
                    (r["created_at"] < NOW - timedelta(minutes=minutes) or r["expires_at"] is not None and r["expires_at"] <= NOW)]
            else:
                now, limit, cleanup_now, cleanup_limit = args
                current_plan_guard = "not exists" in q and "from profiles" in q and "p.plan <> 'free'" in q
                expired = [copy.deepcopy(r) for r in rows.values() if
                    r["status"] == "ready" and r["expires_at"] is not None and r["expires_at"] <= now
                    and (not current_plan_guard or self.store["plan"] == "free")][:limit]
                deleted = sorted([copy.deepcopy(r) for r in rows.values() if r["status"] == "deleted"
                    and r["cleanup_after"] <= cleanup_now], key=lambda r: (r["cleanup_after"], r["id"]))[:cleanup_limit]
                self.result = expired + deleted
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
               created_at=created_at or NOW - timedelta(days=1), deleted_at=None, ready_at=NOW,
               cleanup_after=(created_at or NOW - timedelta(days=1)) + timedelta(minutes=65), cleanup_generation=0)
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
    seed(store)
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
    store["plan"] = "free"
    claimed = await repo.claim_detail_publication_deletion(
        conn, user_id=USER, publication_id=row["id"], now=NOW)
    assert claimed["status"] == row["status"] == "deleted"


@run_async
async def test_sweep_waits_for_live_upload_cleanup_window():
    worker, store, r2 = setup_reaper()
    row = seed(store, status="deleted")
    row["cleanup_after"] = NOW + timedelta(minutes=65)
    key = f"pub/{USER}/{row['id']}/01.jpg"
    r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
    await worker._sweep_once()
    assert row["deleted_at"] is None
    assert key in r2.objects
    assert r2.deleted == r2.purged == []
    row["cleanup_after"] = NOW - timedelta(seconds=1)
    await worker._sweep_once()
    assert row["deleted_at"] is not None
    assert key not in r2.objects


@run_async
async def test_absent_finalized_tombstone_rechecks_without_repurging():
    worker, store, r2 = setup_reaper()
    row = seed(store, status="deleted")
    row["deleted_at"] = NOW - timedelta(days=2)
    row["cleanup_after"] = NOW - timedelta(days=1)
    await worker._sweep_once()
    assert r2.deleted == r2.purged == []
    assert row["cleanup_after"] > NOW


@run_async
async def test_recreated_tombstone_purge_failure_retries_even_after_object_is_gone():
    worker, store, r2 = setup_reaper()
    row = seed(store, status="deleted")
    row["deleted_at"] = NOW - timedelta(days=2)
    row["cleanup_after"] = NOW - timedelta(days=1)
    key = f"pub/{USER}/{row['id']}/01.jpg"
    r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
    normal_purge = r2.purge_public_cache
    r2.purge_public_cache = lambda _keys: (_ for _ in ()).throw(RuntimeError("CDN unavailable"))
    await worker._sweep_once()
    assert key not in r2.objects
    assert store["publications"][row["id"]]["deleted_at"] is None
    r2.purge_public_cache = normal_purge
    await worker._sweep_once()
    assert r2.purged == [key]
    assert store["publications"][row["id"]]["deleted_at"] is not None


@run_async
async def test_old_absence_check_cannot_acknowledge_new_cdn_retry_intent():
    worker, store, r2 = setup_reaper()
    row = seed(store, status="deleted")
    row["deleted_at"] = NOW - timedelta(days=2)
    row["cleanup_after"] = NOW - timedelta(days=1)
    original_head = r2.head
    key = f"pub/{USER}/{row['id']}/01.jpg"

    def other_cleanup_deletes_but_fails_cdn(object_key):
        # The real reaper already holds an independent copy of the finalized row.
        # Another DELETE or reaper clears its completion marker, deletes R2, then
        # fails its CDN purge. An older HEAD-only result cannot acknowledge that work.
        store["publications"][row["id"]]["deleted_at"] = None
        store["publications"][row["id"]]["cleanup_generation"] += 1
        return None

    r2.head = other_cleanup_deletes_but_fails_cdn
    await worker._sweep_once()
    assert store["publications"][row["id"]]["deleted_at"] is None
    assert store["publications"][row["id"]]["cleanup_after"] <= NOW
    r2.head = original_head
    await worker._sweep_once()
    assert r2.purged == [key]
    assert store["publications"][row["id"]]["deleted_at"] is not None


@run_async
async def test_old_full_cleanup_cannot_acknowledge_new_cdn_retry_intent():
    worker, store, r2 = setup_reaper()
    row = seed(store, status="deleted")
    row["cleanup_after"] = NOW - timedelta(days=1)
    row["cleanup_generation"] = 0
    key = f"pub/{USER}/{row['id']}/01.jpg"
    normal_purge = r2.purge_public_cache

    def later_cleanup_fails_after_this_purge(keys):
        normal_purge(keys)
        # A late PUT followed by B's delete succeeds, but B's CDN purge fails.
        # Both A and B observed deleted_at=NULL, so a timestamp-only CAS is unsafe.
        r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
        r2.delete(key)
        store["publications"][row["id"]]["deleted_at"] = None
        store["publications"][row["id"]]["cleanup_generation"] += 1

    r2.purge_public_cache = later_cleanup_fails_after_this_purge
    await worker._sweep_once()
    assert store["publications"][row["id"]]["deleted_at"] is None
    assert store["publications"][row["id"]]["cleanup_after"] <= NOW
    r2.purge_public_cache = normal_purge
    await worker._sweep_once()
    assert r2.purged == [key, key]
    assert store["publications"][row["id"]]["deleted_at"] is not None
