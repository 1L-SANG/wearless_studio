"""관리자 기기 게이트 — repo·순수 함수·라우트.

설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md
"""
import asyncio
import contextlib
from datetime import datetime, timezone

from app import repo


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
        self.executed, self.rows, self.commits = [], list(rows), 0

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


# ---------- repo ----------

def test_find_device_by_hash_selects_text_ids_and_status():
    conn = FakeConn([{"id": "d1", "user_id": "u1", "status": "approved", "label": "Mac", "last_seen_at": None}])
    row = asyncio.run(repo.find_admin_device_by_hash(conn, "abc"))
    assert row["id"] == "d1" and row["status"] == "approved"
    sql, params = conn.executed[0]
    assert sql.startswith("select id::text as id, user_id::text as user_id")
    assert "where token_hash = %s" in sql
    assert params == ("abc",)


def test_find_device_by_hash_returns_none_when_missing():
    assert asyncio.run(repo.find_admin_device_by_hash(FakeConn([]), "nope")) is None


def test_touch_device_updates_last_seen_only():
    conn = FakeConn()
    asyncio.run(repo.touch_admin_device(conn, "d1"))
    sql, params = conn.executed[0]
    assert sql == "update admin_devices set last_seen_at = now() where id = %s"
    assert params == ("d1",)


import pytest
from fastapi import HTTPException

from app import facemarket_admin_devices as devices


# ---------- 라벨 ----------

@pytest.mark.parametrize("ua,label", [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36", "macOS · Chrome"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15", "macOS · Safari"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0", "Windows · Edge"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "iPhone · Safari"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36", "Android · Chrome"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0", "Linux · Firefox"),
    ("", "알 수 없는 기기"),
    (None, "알 수 없는 기기"),
])
def test_label_from_user_agent(ua, label):
    assert devices.label_from_user_agent(ua) == label


# ---------- register ----------

def test_register_creates_a_pending_row_with_a_hash_and_returns_the_token_once():
    conn = FakeConn([{"count": 0}, {"id": "d1"}])
    out = asyncio.run(devices.register_device(
        conn, user_id="u1", label="Mac", user_agent="UA", max_pending=5,
    ))
    assert out["deviceId"] == "d1" and out["status"] == "pending" and out["label"] == "Mac"
    assert len(out["token"]) >= 32
    insert = [(sql, p) for sql, p in conn.executed if sql.startswith("insert into admin_devices")]
    assert len(insert) == 1
    sql, params = insert[0]
    assert params[0] == "u1" and params[2] == "Mac" and params[3] == "UA"
    # 원문 토큰은 DB 로 가지 않는다 — 해시만.
    assert params[1] != out["token"]
    assert params[1] == __import__("hashlib").sha256(out["token"].encode()).hexdigest()


def test_register_refuses_when_too_many_pending():
    conn = FakeConn([{"count": 5}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.register_device(
            conn, user_id="u1", label="Mac", user_agent=None, max_pending=5,
        ))
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "too_many_pending"
    assert not any(sql.startswith("insert") for sql, _ in conn.executed)


def test_register_trims_and_caps_the_label():
    conn = FakeConn([{"count": 0}, {"id": "d1"}])
    out = asyncio.run(devices.register_device(
        conn, user_id="u1", label="  " + "x" * 100 + "  ", user_agent=None, max_pending=5,
    ))
    assert out["label"] == "x" * devices.LABEL_MAX


# ---------- me ----------

def test_device_status_without_token_is_unknown():
    out = asyncio.run(devices.device_status(FakeConn([]), user_id="u1", token=None))
    assert out == {"status": "unknown"}


def test_device_status_reports_own_device_and_hides_others():
    row = {"id": "d1", "user_id": "u1", "status": "pending", "label": "Mac", "last_seen_at": None}
    out = asyncio.run(devices.device_status(FakeConn([row]), user_id="u1", token="tok"))
    assert out == {"status": "pending", "deviceId": "d1", "label": "Mac"}
    out = asyncio.run(devices.device_status(FakeConn([dict(row, user_id="u2")]), user_id="u1", token="tok"))
    assert out == {"status": "unknown"}


# ---------- list ----------

def test_list_marks_the_current_device_and_never_leaks_hashes():
    rows = [
        {"id": "d1", "user_id": "u1", "user_email": "a@x", "label": "Mac", "status": "approved",
         "created_at": None, "last_seen_at": None, "approved_at": None, "revoked_at": None,
         "approved_by_email": "b@x", "token_hash": "h1"},
        {"id": "d2", "user_id": "u2", "user_email": "b@x", "label": "Win", "status": "pending",
         "created_at": None, "last_seen_at": None, "approved_at": None, "revoked_at": None,
         "approved_by_email": None, "token_hash": "h2"},
    ]
    out = asyncio.run(devices.list_devices(FakeConn([rows]), current_token_hash="h1"))
    assert [d["id"] for d in out["items"]] == ["d1", "d2"]
    assert out["items"][0]["isCurrent"] is True and out["items"][1]["isCurrent"] is False
    assert all("token_hash" not in d and "tokenHash" not in d for d in out["items"])
    assert out["items"][0]["approvedByEmail"] == "b@x"


def test_list_sql_puts_pending_first():
    conn = FakeConn([[]])
    asyncio.run(devices.list_devices(conn, current_token_hash=None))
    sql, _ = conn.executed[0]
    assert "order by (d.status = 'pending') desc" in sql


# ---------- approve / revoke ----------

def test_approve_moves_pending_to_approved_and_audits():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "pending", "token_hash": "h2"}])
    out = asyncio.run(devices.approve_device(conn, device_id="d1", actor="u1"))
    assert out == {"deviceId": "d1", "status": "approved"}
    sqls = [sql for sql, _ in conn.executed]
    assert any(s.startswith("select") and "for update" in s for s in sqls)
    assert any(s.startswith("update admin_devices set status = 'approved'") for s in sqls)
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][0] == "u1" and audit[0][1] == "device.approve" and audit[0][2] == "admin_device"


def test_approve_refuses_non_pending():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "approved", "token_hash": "h2"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.approve_device(conn, device_id="d1", actor="u1"))
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "device_not_pending"


def test_approve_unknown_id_is_404():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.approve_device(FakeConn([]), device_id="nope", actor="u1"))
    assert exc.value.status_code == 404


def test_revoke_refuses_the_current_device():
    conn = FakeConn([{"id": "d1", "user_id": "u1", "label": "Mac", "status": "approved", "token_hash": "h1"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash="h1"))
    assert exc.value.status_code == 400 and exc.value.detail["code"] == "cannot_revoke_current"
    assert not any(sql.startswith("update") for sql, _ in conn.executed)


def test_revoke_works_for_pending_and_approved_and_audits_the_previous_status():
    for previous in ("pending", "approved"):
        conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": previous, "token_hash": "h2"}])
        out = asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash="h1"))
        assert out == {"deviceId": "d1", "status": "revoked"}
        audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
        assert audit[0][1] == "device.revoke"
        assert audit[0][4].obj == {"status": previous}   # before (psycopg Json 래퍼)


def test_revoke_twice_is_409():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "revoked", "token_hash": "h2"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash=None))
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "device_already_revoked"


def test_revoke_devices_for_user_returns_the_count():
    conn = FakeConn([[{"id": "d1"}, {"id": "d2"}]])
    n = asyncio.run(devices.revoke_devices_for_user(conn, user_id="u2", actor="u1"))
    assert n == 2
    sql, params = conn.executed[0]
    assert sql.startswith("update admin_devices set status = 'revoked'")
    assert "status in ('pending', 'approved')" in sql and "returning id" in sql
    assert params == ("u1", "u2")


# ---------- 라우트 (TestClient) ----------

from fastapi.testclient import TestClient
from app.main import create_app
from conftest import auth_headers, make_settings


@pytest.fixture()
def enforce_client(keypair):
    private_key, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, admin_device_gate="enforce"))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


@pytest.fixture()
def off_client(keypair):
    private_key, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, admin_device_gate="off"))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


def _patch_conn(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn
    monkeypatch.setattr(devices, "get_conn", fake_conn)


def test_register_route_is_open_to_admins_without_a_device_and_pings_slack(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "admin"}, {"count": 0}, {"id": "d1"}, {"email": "a@x"}])
    _patch_conn(monkeypatch, conn)
    pings = []
    async def fake_slack(settings, *, email, label):
        pings.append((email, label))
    monkeypatch.setattr(devices.facemarket_notify, "notify_slack_admin_device_requested", fake_slack)

    res = enforce_client.post(
        "/v1/facemarket/admin/devices/register",
        json={"label": "내 맥북"}, headers=auth_headers(make_token),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending" and body["gate"] == "enforce" and body["token"]
    assert pings == [("a@x", "내 맥북")]
    assert conn.commits == 1


def test_register_route_rejects_non_admins_without_creating_a_row(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "user"}])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.post("/v1/facemarket/admin/devices/register", json={}, headers=auth_headers(make_token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "forbidden"
    assert not any(sql.startswith("insert") for sql, _ in conn.executed)


def test_me_route_reports_status_and_gate_without_403(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "admin"}, {"id": "d1", "user_id": "user-1", "status": "pending", "label": "Mac", "last_seen_at": None}])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.get(
        "/v1/facemarket/admin/devices/me",
        headers={**auth_headers(make_token), "X-Admin-Device": "tok"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "pending", "deviceId": "d1", "label": "Mac", "gate": "enforce"}


def test_me_route_skips_the_device_lookup_when_gate_is_off(off_client, make_token, monkeypatch):
    """off 는 조회를 하지 않는다 — admin_devices 테이블이 없어도(마이그레이션 미적용) 살아야
    프런트가 /me 에 의존할 수 있다."""
    conn = FakeConn([{"role": "admin"}])
    _patch_conn(monkeypatch, conn)
    res = off_client.get(
        "/v1/facemarket/admin/devices/me",
        headers=auth_headers(make_token),
    )
    assert res.status_code == 200
    assert res.json() == {"status": "unknown", "gate": "off"}
    assert len(conn.executed) == 1  # role 조회 하나뿐 — 기기 조회가 없다


def test_list_route_requires_an_approved_device(enforce_client, make_token, monkeypatch):
    # 관리자지만 기기 헤더 없음 → enforce 라 403 device_missing
    _patch_conn(monkeypatch, FakeConn([{"role": "admin"}]))
    res = enforce_client.get("/v1/facemarket/admin/devices", headers=auth_headers(make_token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "device_missing"


def test_approve_route_commits_after_audit(enforce_client, make_token, monkeypatch):
    # FakeCursor 는 execute 마다 큐를 하나 소비한다. 순서 = 라우트의 실제 SQL 순서:
    # role 조회 → 가드의 기기 조회 → for update 잠금 → approve update → 감사 insert
    # last_seen_at 을 방금(now)으로 둬서 가드가 touch 를 안 하게 한다 — touch 가 나면 가드가
    # 자기 커밋을 하나 더 해서 conn.commits == 1 이 "라우트가 한 번 커밋했다"를 안 뜻하게 된다.
    conn = FakeConn([
        {"role": "admin"},
        {"id": "cur", "user_id": "user-1", "status": "approved", "label": "Mac",
         "last_seen_at": datetime.now(timezone.utc)},
        {"id": "d2", "user_id": "u2", "label": "Win", "status": "pending", "token_hash": "h2"},         # lock
    ])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.post(
        "/v1/facemarket/admin/devices/d2/approve",
        headers={**auth_headers(make_token), "X-Admin-Device": "tok"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"deviceId": "d2", "status": "approved"}
    assert conn.commits == 1
