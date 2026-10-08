import asyncio
import copy

import pytest

from app import repo


ROOT = "11111111-1111-4111-8111-111111111111"
SOURCE = "22222222-2222-4222-8222-222222222222"


def record(action="rest hands"):
    return {"version": 1, "rootAssetId": ROOT, "sourceAssetId": SOURCE,
            "plan": {"actionSummary": action}}


class Connection:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.responses.pop(0)

    async def fetchall(self):
        return self.responses.pop(0)


def test_project_source_requires_exact_owner_project_and_live_asset():
    asset = {"id": SOURCE, "project_id": "project", "metadata": {}}
    conn = Connection(asset)
    assert asyncio.run(repo.get_project_asset_for_user(conn, "user", "project", SOURCE)) == asset
    sql, params = conn.calls[0]
    assert "user_id = %s" in sql and "project_id = %s" in sql
    assert "deleted_at is null" in sql
    assert "or source" not in sql
    assert "project_id::text" in sql and "mime_type" in sql and "metadata" in sql
    assert params == (SOURCE, "user", "project")


def test_history_is_scoped_and_only_includes_reserved_or_completed_plans():
    conn = Connection([{"id": "job-1", "similar_cut": record()}])
    result = asyncio.run(repo.list_similar_cut_plans(
        conn, "user", "project", ROOT, exclude_job_id="current", limit=12))
    assert result == [{"jobId": "job-1", **record()}]
    sql, params = conn.calls[0]
    assert "user_id = %s" in sql and "project_id = %s" in sql
    assert "kind = 'editor_image'" in sql
    assert "status in ('pending', 'running', 'done')" in sql
    assert "metadata->'similar_cut'->>'rootAssetId' = %s" in sql
    assert "metadata->'similar_cut'->'version' = '1'::jsonb" in sql
    assert "jsonb_typeof(metadata->'similar_cut'->'plan') = 'object'" in sql
    assert "order by created_at desc, id desc" in sql
    assert params == ("user", "project", ROOT, "current", "current", 12)


def test_saved_job_plan_lookup_is_owner_project_scoped():
    conn = Connection({"similar_cut": record()})
    assert asyncio.run(repo.get_similar_cut_plan_for_job(conn, "user", "project", "job")) == record()
    sql, params = conn.calls[0]
    assert "user_id = %s" in sql and "project_id = %s" in sql
    assert "kind = 'editor_image'" in sql
    assert params == ("job", "user", "project")


def reserve(conn, *, expected_history=None, metadata=None):
    return asyncio.run(repo.reserve_similar_cut_plan(
        conn, user_id="user", project_id="project", job_id="job", lease_token="lease",
        metadata=metadata or record(), expected_history=expected_history or []))


def test_reserve_merges_only_similar_cut_and_fences_the_lease():
    conn = Connection({"locked": True}, {"similar_cut": None}, [], {"id": "job"})
    assert reserve(conn) == {"status": "reserved", "similar_cut": record()}
    lock_sql, lock_params = conn.calls[0]
    assert "pg_try_advisory_xact_lock" in lock_sql
    assert lock_params == (f"similar-cut:user:project:{ROOT}",)
    fenced_sql, fenced_params = conn.calls[1]
    assert "for update" in fenced_sql
    assert "status = 'running'" in fenced_sql and "locked_by = %s" in fenced_sql
    assert "user_id = %s" in fenced_sql and "project_id = %s" in fenced_sql
    assert fenced_params == ("job", "user", "project", "lease")
    sql, params = conn.calls[-1]
    assert "metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb" in sql
    assert params[0].obj == {"similar_cut": record()}
    assert params[1:] == ("job", "user", "project", "lease")


def test_concurrent_history_change_requires_replan_without_write():
    new_history = [{"id": "other-job", "similar_cut": record("look left")}]
    conn = Connection({"locked": True}, {"similar_cut": None}, new_history)
    assert reserve(conn) == {"status": "history_changed"}
    assert not any(sql.startswith("update") for sql, _ in conn.calls)


def test_matching_history_allows_reservation():
    history = [{"jobId": "previous", **record("look left")}]
    original = copy.deepcopy(history)
    conn = Connection({"locked": True}, {"similar_cut": None},
                      [{"id": "previous", "similar_cut": record("look left")}], {"id": "job"})
    assert reserve(conn, expected_history=history)["status"] == "reserved"
    assert history == original


def test_busy_does_not_read_or_write_job():
    conn = Connection({"locked": False})
    assert reserve(conn) == {"status": "busy"}
    assert len(conn.calls) == 1


def test_lost_lease_does_not_read_history_or_write_plan():
    conn = Connection({"locked": True}, None)
    assert reserve(conn) == {"status": "lease_lost"}
    assert len(conn.calls) == 2


def test_reclaimed_worker_resumes_saved_plan_before_comparing_history():
    saved = record("touch glasses")
    conn = Connection({"locked": True}, {"similar_cut": saved})
    assert reserve(conn) == {"status": "existing", "similar_cut": saved}
    assert len(conn.calls) == 2


@pytest.mark.parametrize("invalid", [None, [], {}, {**record(), "version": True},
                                    {**record(), "plan": []}, {**record(), "rootAssetId": ""}])
def test_lookup_does_not_resume_malformed_metadata(invalid):
    conn = Connection({"similar_cut": invalid})
    assert asyncio.run(repo.get_similar_cut_plan_for_job(conn, "user", "project", "job")) is None
