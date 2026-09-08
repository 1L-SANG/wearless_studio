"""가입 출처 스탬프 — 셀러와 FaceMarket 이 auth 를 공유하는 동안 둘을 구분하는 유일한 근거.

되돌아가면: 콘솔의 사용자 목록이 두 서비스의 가입자를 구분 없이 섞어 보여준다. 그리고
이 판정이 본문(클라이언트 값)으로 넘어가면, 어느 탭에서든 자기 출처를 원하는 대로 적을 수
있어 라벨이 통째로 무의미해진다.
"""
import asyncio
import contextlib

import pytest

from app import app_origin, repo, routes
from conftest import auth_headers


# ---------- Origin 헤더 판정 (순수 함수) ----------

@pytest.mark.parametrize("origin,expected", [
    ("https://facemarket.wearless.kr", "facemarket"),
    ("https://ai.wearless.kr", "seller"),
    ("https://wearless.kr", "seller"),
    # 관리자 콘솔은 출처가 아니다 — 관리자가 콘솔을 연 것으로 그 계정의 진짜 출처를 덮으면
    # 안 된다.
    ("https://admin.wearless.kr", None),
    # 로컬 dev 서버는 세 문서를 한 오리진에서 배급한다(vite.config.js) — 호스트로 구분 불가.
    ("http://localhost:5173", None),
    ("http://127.0.0.1:5173", None),
    ("http://[::1]:5173", None),
    # 브라우저가 아닌 호출.
    (None, None),
    ("", None),
])
def test_origin_header_decides_the_app(origin, expected):
    assert app_origin.app_from_origin(origin) == expected


def test_body_cannot_override_a_usable_origin_header():
    """본문은 클라이언트가 쓰는 값이라 위조된다 — 헤더가 말해 주면 본문은 안 본다."""
    assert app_origin.resolve_app("https://facemarket.wearless.kr", "seller") == "facemarket"
    assert app_origin.resolve_app("https://ai.wearless.kr", "facemarket") == "seller"


def test_body_is_the_fallback_only_when_the_header_cannot_decide():
    assert app_origin.resolve_app("http://localhost:5173", "facemarket") == "facemarket"
    assert app_origin.resolve_app(None, "seller") == "seller"


def test_body_cannot_claim_both_or_junk():
    """'both' 는 서버가 승격시켜 만드는 값이지 클라이언트가 주장할 값이 아니다."""
    assert app_origin.resolve_app(None, "both") is None
    assert app_origin.resolve_app(None, "admin") is None
    assert app_origin.resolve_app(None, "") is None


# ---------- merge 규칙 ----------

def test_first_value_is_preserved_and_cross_use_promotes_to_both():
    assert app_origin.merge(None, "seller") == "seller"
    assert app_origin.merge("seller", "seller") == "seller"
    assert app_origin.merge("seller", "facemarket") == "both"
    assert app_origin.merge("facemarket", "seller") == "both"
    assert app_origin.merge("both", "seller") == "both"


# ---------- repo.touch_app_origin ----------

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


def test_touch_returns_the_new_value_when_the_update_changed_a_row():
    conn = FakeConn([{"app_origin": "both"}])
    result = asyncio.run(repo.touch_app_origin(conn, "u1", "facemarket"))
    assert result == "both"
    # 바뀐 게 있으면 확인용 select 를 또 던지지 않는다.
    assert len(conn.executed) == 1


def test_touch_reads_current_value_when_nothing_changed():
    """이미 맞게 적혀 있으면 UPDATE 가 0행이다 — 그때만 현재 값을 읽는다."""
    conn = FakeConn([None, {"app_origin": "seller"}])
    result = asyncio.run(repo.touch_app_origin(conn, "u1", "seller"))
    assert result == "seller"
    assert len(conn.executed) == 2


def test_touch_survives_a_missing_profile_row():
    conn = FakeConn([None, None])
    assert asyncio.run(repo.touch_app_origin(conn, "ghost", "seller")) is None


def test_touch_merges_in_one_statement_not_read_then_write():
    """두 탭이 동시에 스탬프를 보내도 늦은 쪽이 'both' 를 한쪽 값으로 되돌리지 못하게."""
    conn = FakeConn([{"app_origin": "both"}])
    asyncio.run(repo.touch_app_origin(conn, "u1", "facemarket"))
    sql, params = conn.executed[0]
    assert sql.startswith("update profiles")
    assert "case when app_origin is null" in sql
    assert "returning app_origin" in sql
    # 이미 맞는 행은 아예 안 건드린다 — updated_at 을 헛되이 흔들지 않는다.
    assert "not in (%(app)s, 'both')" in sql
    assert params == {"user_id": "u1", "app": "facemarket"}


# ---------- POST /v1/me/app-origin ----------

def _patch_conn(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn

    monkeypatch.setattr(routes, "get_conn", fake_conn)


def test_stamp_records_the_origin_host(client, make_token, monkeypatch):
    conn = FakeConn([{"app_origin": "facemarket"}])
    _patch_conn(monkeypatch, conn)
    res = client.post(
        "/v1/me/app-origin",
        json={"app": "seller"},  # 위조 시도 — 헤더가 이긴다
        headers={**auth_headers(make_token), "Origin": "https://facemarket.wearless.kr"},
    )
    assert res.status_code == 200
    assert res.json() == {"appOrigin": "facemarket"}
    assert conn.executed[0][1]["app"] == "facemarket"


def test_stamp_accepts_an_empty_body(client, make_token, monkeypatch):
    """프런트는 fire-and-forget 이라 본문 없이 부를 수도 있다 — 헤더만으로 충분하다."""
    conn = FakeConn([{"app_origin": "seller"}])
    _patch_conn(monkeypatch, conn)
    res = client.post(
        "/v1/me/app-origin",
        headers={**auth_headers(make_token), "Origin": "https://ai.wearless.kr"},
    )
    assert res.status_code == 200
    assert res.json() == {"appOrigin": "seller"}


def test_stamp_writes_nothing_when_the_app_cannot_be_decided(client, make_token, monkeypatch):
    """관리자 콘솔에서 불려도 200 이다 — 로그인마다 의미 없는 에러를 띄우지 않는다."""
    conn = FakeConn([{"app_origin": "both"}])
    _patch_conn(monkeypatch, conn)
    res = client.post(
        "/v1/me/app-origin",
        headers={**auth_headers(make_token), "Origin": "https://admin.wearless.kr"},
    )
    assert res.status_code == 200
    assert res.json() == {"appOrigin": "both"}
    assert len(conn.executed) == 1
    assert conn.executed[0][0].startswith("select app_origin")


def test_stamp_rejects_an_unknown_app_value(client, make_token, monkeypatch):
    """계약 밖 값은 422 — 조용히 무시하면 프런트 오타가 영영 안 드러난다."""
    _patch_conn(monkeypatch, FakeConn())
    res = client.post(
        "/v1/me/app-origin",
        json={"app": "both"},
        headers={**auth_headers(make_token), "Origin": "http://localhost:5173"},
    )
    assert res.status_code == 422


def test_stamp_requires_a_session(client):
    assert client.post("/v1/me/app-origin", json={"app": "seller"}).status_code == 401
