"""관리자 승격·회수의 안전장치 셋.

되돌아가면: 관리자가 자기 권한을 내려 콘솔에서 영영 잠기거나(복구는 DB 직접 UPDATE 뿐),
서로를 동시에 내려 관리자가 0명이 된다.
"""
import asyncio
import contextlib

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else None

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows = [], list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        return None


def test_role_value_must_be_admin_or_user():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            FakeConn([]), target_user_id="u2", actor="admin-1", role="superadmin",
        ))
    assert exc.value.detail["code"] == "invalid_role"


def test_cannot_demote_self():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            FakeConn([]), target_user_id="admin-1", actor="admin-1", role="user",
        ))
    assert exc.value.detail["code"] == "cannot_demote_self"


def test_cannot_demote_the_last_admin():
    # 1) 대상 조회 → 관리자, 2) 관리자 수 → 1
    conn = FakeConn([{"role": "admin"}, {"count": 1}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="admin-2", actor="admin-1", role="user",
        ))
    assert exc.value.detail["code"] == "last_admin"


def test_last_admin_check_locks_the_rows():
    """잠금 없이 세면 두 관리자가 서로를 동시에 내려 0명이 된다."""
    conn = FakeConn([{"role": "admin"}, {"count": 2}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    counting = [sql for sql, _ in conn.executed if "count(" in sql]
    assert counting and "for update" in counting[0]


def test_cannot_promote_a_user_without_a_profile():
    conn = FakeConn([None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="ghost", actor="admin-1", role="admin",
        ))
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "user_not_found"


def test_promotion_updates_role_and_writes_audit():
    conn = FakeConn([{"role": "user"}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="u2", actor="admin-1", role="admin",
    ))
    updates = [(sql, p) for sql, p in conn.executed if sql.startswith("update profiles")]
    assert updates and updates[0][1][0] == "admin"
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][1] == "staff.role.grant"
    assert audit[0][4].obj == {"role": "user"}
    assert audit[0][5].obj == {"role": "admin"}


def test_demotion_audit_action_is_revoke():
    conn = FakeConn([{"role": "admin"}, {"count": 3}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][1] == "staff.role.revoke"


def test_staff_listing_returns_admins_and_search_matches():
    conn = FakeConn([
        [{"user_id": "admin-1", "email": "a@x.com", "display_name": "A", "role": "admin"}],
        [{"user_id": "u2", "email": "b@x.com", "display_name": "B", "role": "user"}],
    ])
    payload = asyncio.run(facemarket_admin.list_staff(conn, q="b@x.com"))
    assert payload["admins"][0]["email"] == "a@x.com"
    assert payload["matches"][0]["userId"] == "u2"


def test_staff_search_is_exact_email_match():
    """부분일치로 열면 관리자 승격 대상을 훑는 이메일 스캐너가 된다."""
    conn = FakeConn([[], []])
    asyncio.run(facemarket_admin.list_staff(conn, q="b@"))
    search = [sql for sql, _ in conn.executed if "u.email" in sql]
    assert search and "ilike" not in search[-1]


def test_audit_listing_is_newest_first_and_capped():
    conn = FakeConn([[]])
    asyncio.run(facemarket_admin.list_audit(conn, limit=9999, target_type=None, target_id=None))
    sql, params = conn.executed[0]
    assert "order by l.created_at desc" in sql
    assert params["limit"] <= facemarket_admin.MAX_LIST_LIMIT
