import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from app import repo, routes
from app.workers.draft_asset_reclaimer import DraftAssetReclaimer


ASSET_ID = "11111111-1111-4111-8111-111111111111"
KEY = f"users/u1/projects/draft-slot/uploads/{ASSET_ID}.jpg"


class SqlConnection:
    def __init__(self):
        self.queries = []

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, query, params):
        self.queries.append((" ".join(query.split()), params))

    async def fetchall(self):
        return []


def test_soft_delete_records_retry_marker_in_the_same_update():
    conn = SqlConnection()
    asyncio.run(repo.soft_delete_unreferenced_draft_assets(conn, "u1", [ASSET_ID]))

    assert len(conn.queries) == 1
    query, params = conn.queries[0]
    assert "draft_cleanup_pending" in query
    assert "metadata =" in query
    assert "deleted_at =" in query
    assert "a.deleted_at is null" in query  # old unmarked tombstones are not enrolled
    assert params == ([ASSET_ID], "u1")


def test_reclaimer_retries_marked_tombstones_and_keeps_all_reference_guards():
    conn = SqlConnection()
    asyncio.run(repo.reclaim_stale_unreferenced_draft_assets(conn, older_than_minutes=10080))

    query, params = conn.queries[0]
    assert "a.deleted_at is not null" in query
    assert "a.metadata->>'draft_cleanup_pending' = 'true'" in query
    assert "a.deleted_at is null" in query
    assert "a.created_at < now() - (%s * interval '1 minute')" in query
    assert params == (10080, 200)
    for table in (
        "matching_items", "mannequin_cuts", "wardrobe_images", "exports",
        "projects", "profiles", "draft_slots", "products",
    ):
        assert f"from {table} " in query
    assert "ds.payload->'product'" in query and "else ds.payload end" in query


def test_reclaimer_keeps_the_grace_period_for_young_pending_uploads():
    conn = SqlConnection()
    asyncio.run(repo.reclaim_stale_unreferenced_draft_assets(conn, older_than_minutes=10080))

    query, _params = conn.queries[0]
    # The age cutoff applies to both alternatives, not only still-live assets.
    assert (
        "and a.created_at < now() - (%s * interval '1 minute') "
        "and (a.deleted_at is null or (a.deleted_at is not null "
        "and a.metadata->>'draft_cleanup_pending' = 'true'))"
    ) in query


def install_cleanup_case(monkeypatch, *, failure=None):
    state = {"pending": True, "connections": 0, "events": [], "commits": 0}

    class Connection:
        async def commit(self):
            state["commits"] += 1

    class Pool:
        @contextlib.asynccontextmanager
        async def connection(self):
            state["connections"] += 1
            try:
                yield Connection()
            finally:
                state["connections"] -= 1

    class R2:
        def __init__(self):
            self.present = True
            self.failure = failure

        def step(self, stage):
            assert state["connections"] == 0
            state["events"].append(stage)
            if self.failure == stage:
                self.failure = None
                raise RuntimeError(f"synthetic {stage} failure")

        def delete(self, key):
            assert key == KEY
            self.step("delete")
            self.present = False

        def head(self, key):
            assert key == KEY
            self.step("head")
            return {"size": 1} if self.present else None

        def purge_public_cache(self, keys):
            assert keys == [KEY]
            self.step("purge")

    async def reclaim(_conn, *, older_than_minutes):
        assert older_than_minutes == 7 * 24 * 60
        return [{"id": ASSET_ID, "r2_key": KEY}] if state["pending"] else []

    async def claim_outputs(_conn):
        return []

    async def complete(_conn, asset_id, r2_key):
        assert (asset_id, r2_key) == (ASSET_ID, KEY)
        state["events"].append("complete")
        state["pending"] = False

    monkeypatch.setattr(repo, "reclaim_stale_unreferenced_draft_assets", reclaim)
    monkeypatch.setattr(repo, "claim_unpublished_ai_output_cleanup_intents", claim_outputs)
    monkeypatch.setattr(repo, "complete_draft_asset_cleanup", complete, raising=False)
    r2 = R2()
    app = SimpleNamespace(state=SimpleNamespace(pool=Pool(), r2=r2))
    return state, r2, app


@pytest.mark.parametrize("failure", ["delete", "head", "purge"])
def test_cleanup_failure_is_retried_after_worker_restart(monkeypatch, failure):
    state, r2, app = install_cleanup_case(monkeypatch, failure=failure)
    asyncio.run(DraftAssetReclaimer(app)._sweep_once())
    assert state["pending"] is True

    asyncio.run(DraftAssetReclaimer(app)._sweep_once())
    assert state["pending"] is False
    assert r2.present is False
    assert state["events"][-4:] == ["delete", "head", "purge", "complete"]

    completed_events = list(state["events"])
    asyncio.run(DraftAssetReclaimer(app)._sweep_once())
    assert state["events"] == completed_events


def test_cleanup_does_not_finish_when_origin_still_contains_the_object(monkeypatch):
    state, r2, app = install_cleanup_case(monkeypatch)
    monkeypatch.setattr(r2, "delete", lambda key: state["events"].append("delete"))

    asyncio.run(DraftAssetReclaimer(app)._sweep_once())

    assert state["pending"] is True
    assert state["events"] == ["delete", "head"]


def test_fast_route_cleanup_leaves_confirmation_to_the_hourly_worker(monkeypatch):
    state, r2, app = install_cleanup_case(monkeypatch, failure="delete")

    asyncio.run(routes._cleanup_draft_slot_r2(r2, [{"id": ASSET_ID, "r2_key": KEY}]))

    assert state["pending"] is True
    assert r2.present is False
    assert state["events"] == ["delete", "delete"]

    asyncio.run(DraftAssetReclaimer(app)._sweep_once())

    assert state["pending"] is False
    assert state["events"][-4:] == ["delete", "head", "purge", "complete"]


def test_cleanup_completion_cannot_clear_a_revived_or_replaced_asset_marker():
    conn = SqlConnection()
    complete = getattr(repo, "complete_draft_asset_cleanup", None)
    assert complete is not None, "confirmed deletion must clear only the matching tombstone"
    asyncio.run(complete(conn, ASSET_ID, KEY))

    query, params = conn.queries[0]
    assert "metadata - 'draft_cleanup_pending'" in query
    assert "id = %s" in query and "r2_key = %s" in query
    assert "deleted_at is not null" in query
    assert params == (ASSET_ID, KEY)
