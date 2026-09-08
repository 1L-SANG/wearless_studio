"""콘솔 사용자 목록 — 셀러 가입자와 FaceMarket 가입자를 구분해 보여주는 화면의 서버 쪽.

되돌아가면: 관리자가 두 서비스의 가입자를 구분 없이 보게 되고(원래 문제), 목록을 훑은
기록이 아무 데도 남지 않는다. 이 화면은 콘솔에서 유일하게 가입자 이메일을 전수로 보여준다.
"""
import asyncio
import contextlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_admin
from app.main import create_app
from conftest import auth_headers, make_settings


@pytest.fixture()
def client(keypair):
    """콘솔 라우터는 FaceMarket 플래그 아래 산다 — 기본 설정으로는 라우트 자체가 없다."""
    _, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


def _row(user_id, *, email="a@b.c", origin="seller", created=None, role="user"):
    return {
        "user_id": user_id,
        "email": email,
        "display_name": "이름",
        "role": role,
        "plan": "basic",
        "app_origin": origin,
        "created_at": created or datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        "last_sign_in_at": None,
    }


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
    def __init__(self, rows=()):
        self.executed, self.rows = [], list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _list(conn, **kwargs):
    params = {"q": None, "origin": None, "limit": 50, "cursor": None, **kwargs}
    return asyncio.run(facemarket_admin.list_users(conn, **params))


# ---------- 출처 필터 ----------

def test_origin_filter_passes_through_and_unknown_means_null():
    conn = FakeConn([[], []])
    _list(conn, origin="unknown")
    assert conn.executed[0][1]["origin"] == "unknown"
    # SQL 이 'unknown' 을 app_origin is null 로 해석해야 한다 — 그렇지 않으면 미상 계정이
    # 어느 칩에서도 안 보인다.
    assert "(%(origin)s = 'unknown' and p.app_origin is null)" in conn.executed[0][0]


@pytest.mark.parametrize("origin", ["seller", "facemarket", "both", "unknown"])
def test_every_chip_value_is_accepted(origin):
    assert facemarket_admin.validate_user_origin(origin) == origin


def test_all_and_empty_mean_no_filter():
    assert facemarket_admin.validate_user_origin("all") is None
    assert facemarket_admin.validate_user_origin("") is None
    assert facemarket_admin.validate_user_origin(None) is None


def test_unknown_origin_value_is_rejected():
    with pytest.raises(Exception) as exc:
        facemarket_admin.validate_user_origin("wearless")
    assert exc.value.detail["code"] == "invalid_origin"


# ---------- 검색 ----------

def test_search_matches_email_or_name_case_insensitively():
    conn = FakeConn([[], []])
    _list(conn, q="  KIM@Example.COM ")
    params = conn.executed[0][1]
    assert params["q"] == "kim@example.com"
    assert params["like"] == "%kim@example.com%"
    sql = conn.executed[0][0]
    assert "lower(coalesce(u.email, '')) like %(like)s" in sql
    assert "lower(coalesce(p.display_name, '')) like %(like)s" in sql


def test_blank_search_is_no_search_not_a_match_on_empty_string():
    conn = FakeConn([[], []])
    _list(conn, q="   ")
    assert conn.executed[0][1]["q"] is None


# ---------- 페이징 ----------

def test_next_cursor_appears_only_when_there_is_another_page():
    # limit+1 을 요청하므로, 3개를 돌려주면 limit 2 에서는 다음이 있다는 뜻이다.
    conn = FakeConn([[_row("u1"), _row("u2"), _row("u3")], []])
    result = _list(conn, limit=2)
    assert [i["userId"] for i in result["items"]] == ["u1", "u2"]
    assert result["nextCursor"]

    conn = FakeConn([[_row("u1"), _row("u2")], []])
    assert _list(conn, limit=2)["nextCursor"] is None


def test_cursor_round_trips_created_at_and_user_id():
    user_id = "0e2b1c9a-1111-4222-8333-444444444444"
    created = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    cursor = facemarket_admin.encode_user_cursor({"created_at": created, "user_id": user_id})
    assert facemarket_admin.decode_user_cursor(cursor) == (created, user_id)


def test_a_tampered_cursor_is_a_400_not_a_500():
    """uuid 가 아닌 값이 SQL 캐스트까지 가면 500 이 된다 — 주소창을 만진 사람에게도 400 이다."""
    for bad in ("not-base64!!", facemarket_admin.encode_user_cursor(
        {"created_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "user_id": "drop-table"}
    )):
        with pytest.raises(Exception) as exc:
            facemarket_admin.decode_user_cursor(bad)
        assert exc.value.detail["code"] == "invalid_cursor"


def test_paging_uses_a_keyset_not_an_offset():
    """가입이 하나 생기면 offset 은 이미 본 행을 다시 보여준다."""
    conn = FakeConn([[], []])
    _list(conn)
    sql = conn.executed[0][0]
    assert "(p.created_at, p.user_id) < (%(cursor_created)s::timestamptz" in sql
    assert "offset" not in sql
    assert "order by p.created_at desc, p.user_id desc" in sql


def test_limit_is_capped():
    conn = FakeConn([[], []])
    _list(conn, limit=100000)
    assert conn.executed[0][1]["limit"] == facemarket_admin.MAX_LIST_LIMIT + 1


# ---------- 칩 숫자 ----------

def test_counts_are_computed_on_the_first_page_only():
    first = FakeConn([[], [{"origin": "seller", "count": 3}, {"origin": "unknown", "count": 9}]])
    assert _list(first)["counts"] == {"seller": 3, "unknown": 9}
    assert len(first.executed) == 2

    later = FakeConn([[]])
    cursor = facemarket_admin.encode_user_cursor({
        "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "user_id": "0e2b1c9a-1111-4222-8333-444444444444",
    })
    assert _list(later, cursor=cursor)["counts"] is None
    assert len(later.executed) == 1


# ---------- 행 모양 ----------

def test_unknown_origin_stays_null_instead_of_being_guessed():
    """'미상' 과 '셀러' 가 화면에서 같아 보이면, 틀린 라벨을 사실로 읽게 된다."""
    conn = FakeConn([[_row("u1", origin=None)], []])
    assert _list(conn)["items"][0]["appOrigin"] is None


def test_row_carries_what_the_console_shows():
    conn = FakeConn([[_row("u1", email="k@w.kr", origin="both", role="admin")], []])
    item = _list(conn)["items"][0]
    assert item["email"] == "k@w.kr"
    assert item["appOrigin"] == "both"
    assert item["role"] == "admin"
    assert item["createdAt"].startswith("2026-09-07")


# ---------- 라우트 가드·감사 ----------

def _patch_conn(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn

    monkeypatch.setattr(facemarket_admin, "get_conn", fake_conn)


def test_non_admin_gets_403(client, make_token, monkeypatch):
    _patch_conn(monkeypatch, FakeConn([{"role": "user"}]))
    res = client.get("/v1/facemarket/admin/users", headers=auth_headers(make_token))
    assert res.status_code == 403


def test_listing_requires_a_session(client):
    assert client.get("/v1/facemarket/admin/users").status_code == 401


def test_admin_listing_is_written_to_the_audit_log(client, make_token, monkeypatch):
    conn = FakeConn([{"role": "admin"}, [_row("u1")], [], None])
    _patch_conn(monkeypatch, conn)
    res = client.get(
        "/v1/facemarket/admin/users?origin=facemarket&q=kim",
        headers=auth_headers(make_token),
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["userId"] == "u1"

    audit = [e for e in conn.executed if e[0].startswith("insert into admin_audit_log")]
    assert len(audit) == 1
    # 무엇으로 훑었는지가 남아야 사후에 의미가 있다.
    assert "users.list.view" in audit[0][1]
    after = audit[0][1][5].obj
    assert after["q"] == "kim" and after["origin"] == "facemarket"
