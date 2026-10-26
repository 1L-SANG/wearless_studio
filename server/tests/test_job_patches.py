"""Conditional job patches keep ownership and worker lease fences."""

import asyncio

import pytest

from app import repo
from conftest import assert_query_binds


class Cursor:
    def __init__(self, *, rowcount=1, row=None, rows=None):
        self.rowcount, self.row, self.rows = rowcount, row, rows
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params):
        assert_query_binds(sql, params)
        self.calls.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, **kwargs):
        self.cur = Cursor(**kwargs)

    def cursor(self):
        return self.cur


@pytest.mark.parametrize("column", ["metadata", "result"])
@pytest.mark.parametrize("filters,clauses,bindings", [
    ({"user_id": "owner", "expect_status": ("pending",)},
     ("user_id = %s", "status = any(%s)"), ("owner", ["pending"])),
    ({"lease_token": "lease"}, ("locked_by = %s", "status = 'running'"), ("lease",)),
    ({"user_id": "owner", "expect_status": ("done", "error")},
     ("user_id = %s", "status = any(%s)"), ("owner", ["done", "error"])),
    ({"lease_token": "lease", "user_id": "owner", "expect_status": ("running",)},
     ("locked_by = %s", "status = 'running'", "user_id = %s", "status = any(%s)"),
     ("lease", "owner", ["running"])),
])
def test_metadata_patch_modes_pending_lease_and_owner_terminal(column, filters, clauses, bindings):
    fn = getattr(repo, f"set_job_{column}_patch")
    for count in (0, 1):
        conn = Conn(rowcount=count)
        patch = {"stage": "upload", "images": {"urls": ["https://image.test/1.jpg"]}}
        assert asyncio.run(fn(conn, job_id="job", patch=patch, **filters)) is bool(count)
        sql, params = conn.cur.calls[0]
        assert f"{column} = coalesce({column}, '{{}}'::jsonb) || %s::jsonb" in sql
        assert "where id = %s" in sql
        assert all(clause in sql.split(" where ")[1] for clause in clauses)
        if "lease_token" not in filters:
            assert "locked_by" not in sql
        assert params[0].obj == patch
        assert tuple(params[1:]) == ("job", *bindings)


def test_progress_fenced_requires_lease():
    for count in (0, 1):
        conn = Conn(rowcount=count)
        assert asyncio.run(repo.set_job_progress_fenced(
            conn, job_id="job", progress=42, lease_token="lease")) is bool(count)
        sql, params = conn.cur.calls[0]
        assert "progress = greatest(progress, %s)" in sql
        assert "where id = %s" in sql and "locked_by = %s" in sql and "status = 'running'" in sql
        assert params == (42, "job", "lease")
    with pytest.raises(TypeError):
        asyncio.run(repo.set_job_progress_fenced(Conn(), job_id="job", progress=42))


@pytest.mark.parametrize("status", ["done", "error"])
@pytest.mark.parametrize("message", [None, "등록할 수 없어요."])
def test_finalize_uncharged_job_writes_error_message_only_for_error(status, message):
    conn = Conn(row={"id": "job", "kind": "mall_register", "payload": {}})
    result = {"outcome": "failed" if status == "error" else "created"}
    assert asyncio.run(repo.finalize_uncharged_job(
        conn, job_id="job", lease_token="lease", status=status,
        result=result, error_message=message)) is True
    select, params = conn.cur.calls[0]
    assert "locked_by = %s and status = 'running' for update" in select
    assert params == ("job", "lease")
    update, params = conn.cur.calls[1]
    assert ("error_message = %s" in update) is (status == "error")
    if status == "error":
        assert message in params
    assert conn.cur.calls[2][1][1] == status
    assert conn.cur.calls[2][1][2].obj == result
    lost = Conn(row=None)
    assert asyncio.run(repo.finalize_uncharged_job(
        lost, job_id="job", lease_token="stale", status=status,
        result=result, error_message=message)) is False
    assert len(lost.cur.calls) == 1


def test_job_request_identity_is_owner_scoped_without_widening_job_projection():
    row = {"idempotency_key": "request", "project_id": "project", "payload": {"price": 100}}
    for stored in (row, None):
        conn = Conn(row=stored)
        assert asyncio.run(repo.get_job_request_identity(conn, user_id="owner", job_id="job")) == stored
        sql, params = conn.cur.calls[0]
        assert sql == "select idempotency_key, project_id::text, payload from jobs where id = %s and user_id = %s"
        assert params == ("job", "owner")
    assert "idempotency_key" not in repo._JOB_COLS


def test_list_project_jobs_by_kind_is_owner_scoped_recent_and_limited():
    conn = Conn(rows=[{"id": "job"}])
    assert asyncio.run(repo.list_project_jobs_by_kind(
        conn, user_id="owner", project_id="project")) == [{"id": "job"}]
    sql, params = conn.cur.calls[0]
    assert "where user_id = %s and project_id = %s and kind = %s" in sql
    assert "order by created_at desc" in sql and "limit %s" in sql
    assert params == ("owner", "project", "mall_register", 5)
    assert "idempotency_key" not in sql
    asyncio.run(repo.list_project_jobs_by_kind(
        conn, user_id="owner", project_id="project", kind="export", limit=2))
    assert conn.cur.calls[-1][1] == ("owner", "project", "export", 2)


def test_mall_connection_projection_includes_token_lifecycle_without_ciphertext():
    columns = {value.strip() for value in repo._MALL_CONNECTION_COLS.split(",")}
    assert {"token_expires_at", "refresh_expires_at", "scopes"} <= columns
    assert not {"access_key_enc", "secret_key_enc"} & columns
