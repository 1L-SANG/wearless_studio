"""repo.get_project — malformed uuid 방어 (프론트 stale mock 'prj_xxx' → 404, 500 아님)."""

import asyncio
import uuid

from app import repo


class _FakeCursorCtx:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params):
        self.store["params"] = params

    async def fetchone(self):
        pid, uid = self.store["params"]
        return {"id": pid, "user_id": uid}


class _FakeConn:
    """cursor() 있는 정상 conn — valid uuid 경로 검증용."""

    def __init__(self):
        self.store = {}

    def cursor(self):
        return _FakeCursorCtx(self.store)


class _NoCursorConn:
    """cursor() 없음 — malformed 경로가 DB 건드리면 AttributeError로 실패."""


def test_get_project_malformed_uuid_returns_none_without_db():
    # 비-uuid id → uuid 캐스트 쿼리(500) 안 하고 None. conn.cursor 접근조차 안 함.
    result = asyncio.run(repo.get_project(_NoCursorConn(), "user-1", "prj_ysq005"))
    assert result is None


def test_get_project_valid_uuid_runs_query():
    # 정상 uuid는 그대로 owner 조건 쿼리 실행 (해피패스 회귀 방지).
    pid = str(uuid.uuid4())
    conn = _FakeConn()
    row = asyncio.run(repo.get_project(conn, "user-1", pid))
    assert conn.store["params"] == (pid, "user-1")
    assert row["id"] == pid
