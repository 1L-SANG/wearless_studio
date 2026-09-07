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
    # 관리자 전원 + 대상 행을 한 쿼리로 조회 → admin-2 혼자뿐이다.
    conn = FakeConn([[{"user_id": "admin-2", "role": "admin"}]])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="admin-2", actor="admin-1", role="user",
        ))
    assert exc.value.detail["code"] == "last_admin"


def test_role_change_locks_admins_and_target_in_one_ordered_pass():
    """따로따로(대상 먼저, 관리자 집합 나중) 잠그면 두 관리자가 서로를 동시에 내릴 때
    서로의 대상 행을 쥔 채 서로의 관리자-집합 잠금을 기다려 데드락이 난다. 한 쿼리로,
    정렬해서, 한 번에 잠가야 동시 트랜잭션이 죽지 않고 줄을 선다.
    """
    conn = FakeConn([
        [{"user_id": "admin-1", "role": "admin"}, {"user_id": "admin-2", "role": "admin"}],
    ])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    selects = [sql for sql, _ in conn.executed if sql.startswith("select")]
    assert len(selects) == 1, "관리자 집합과 대상 행을 여전히 따로 잠그고 있다"
    assert "for update" in selects[0]
    assert "order by user_id" in selects[0], "정렬 없이 잠그면 트랜잭션마다 잠금 순서가 갈려 데드락 위험이 남는다"


def test_cannot_promote_a_user_without_a_profile():
    conn = FakeConn([None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="ghost", actor="admin-1", role="admin",
        ))
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "user_not_found"


def test_promotion_updates_role_and_writes_audit():
    # 승격 대상은 관리자가 아니므로 role='admin' 조건으로는 안 잡히고, or user_id = %s 로 잡힌다.
    conn = FakeConn([[{"user_id": "u2", "role": "user"}]])
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
    conn = FakeConn([
        [
            {"user_id": "admin-1", "role": "admin"},
            {"user_id": "admin-2", "role": "admin"},
            {"user_id": "admin-3", "role": "admin"},
        ],
    ])
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


def test_staff_search_is_case_insensitive_but_still_exact():
    """저장된 이메일 대소문자가 검색어와 다르면 실존 계정이 "가입 안 한 사람"처럼 보인다.

    list_staff 는 검색어를 lower() 해서 넘긴다 — 컬럼도 lower() 로 비교해야 한다. 여전히
    정확일치다: lower(u.email) = %(email)s 이지 ilike 가 아니다.
    """
    conn = FakeConn([[], []])
    asyncio.run(facemarket_admin.list_staff(conn, q="Foo@Example.com"))
    search = [(sql, p) for sql, p in conn.executed if "u.email" in sql]
    assert search, "이메일 검색 쿼리를 못 찾았다"
    sql, params = search[-1]
    assert "lower(u.email)" in sql
    assert "ilike" not in sql
    assert params["email"] == "foo@example.com"


def test_audit_listing_is_newest_first_and_capped():
    conn = FakeConn([[]])
    asyncio.run(facemarket_admin.list_audit(conn, limit=9999, target_type=None, target_id=None))
    sql, params = conn.executed[0]
    assert "order by l.created_at desc" in sql
    assert params["limit"] <= facemarket_admin.MAX_LIST_LIMIT
