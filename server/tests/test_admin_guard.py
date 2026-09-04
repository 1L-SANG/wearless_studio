"""관리자 게이트·감사 기록 헬퍼 단위 테스트."""
import asyncio
import contextlib

import pytest
from fastapi import HTTPException

from app import admin_guard


class FakeCursor:
    def __init__(self, store):
        self.store = store

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return None


class FakeConn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed)

        return _cm()


def test_require_admin_raises_403_for_non_admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return False

    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1"))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "forbidden"
    assert exc.value.detail["message"] == "관리자만 가능해요."


def test_require_admin_passes_for_admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return True

    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1"))  # 예외 없음


def test_write_audit_inserts_one_row_with_all_fields():
    conn = FakeConn()
    asyncio.run(admin_guard.write_audit(
        conn,
        actor_user_id="admin-1",
        action="application.reject",
        target_type="application",
        target_id="app-1",
        before={"status": "under_review"},
        after={"status": "rejected"},
        note="사진 불충분",
    ))
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert sql.startswith("insert into admin_audit_log")
    assert params[0] == "admin-1"
    assert params[1] == "application.reject"
    assert params[2] == "application"
    assert params[3] == "app-1"
    assert params[6] == "사진 불충분"


def test_write_audit_defaults_before_and_after_to_empty_objects():
    conn = FakeConn()
    asyncio.run(admin_guard.write_audit(
        conn, actor_user_id="admin-1", action="staff.role.grant",
        target_type="user", target_id="u2",
    ))
    _sql, params = conn.executed[0]
    # psycopg Json 래퍼 — 원본 dict 를 들고 있다.
    assert params[4].obj == {}
    assert params[5].obj == {}
