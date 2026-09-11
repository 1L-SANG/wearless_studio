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
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request()))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "forbidden"
    assert exc.value.detail["message"] == "관리자만 가능해요."


def test_require_admin_passes_for_admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return True

    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request()))  # 예외 없음


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


import types
from datetime import datetime, timedelta, timezone

from fastapi import Request


def fake_request(*, gate="off", device=None, path="/v1/facemarket/admin/overview") -> Request:
    headers = [(b"host", b"api.test")]
    if device is not None:
        headers.append((b"x-admin-device", device.encode()))
    scope = {
        "type": "http", "method": "GET", "path": path, "headers": headers,
        "app": types.SimpleNamespace(state=types.SimpleNamespace(
            settings=types.SimpleNamespace(admin_device_gate=gate))),
    }
    return Request(scope)


def _admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return True
    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)


def _device(monkeypatch, row, touched=None):
    async def find(_conn, _hash):
        return row
    async def touch(_conn, device_id):
        if touched is not None:
            touched.append(device_id)
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", find)
    monkeypatch.setattr(admin_guard.repo, "touch_admin_device", touch)


def test_hash_is_sha256_hex_of_the_token():
    assert admin_guard.hash_device_token("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_check_device_missing_and_unknown_and_other_users_token(monkeypatch):
    _device(monkeypatch, None)
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", None)).code == "device_missing"
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "  ")).code == "device_missing"
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_unknown"
    _device(monkeypatch, {"id": "d1", "user_id": "u2", "status": "approved", "last_seen_at": None})
    # 남의 토큰은 '모르는 기기' 로 답한다 — 남의 것이라는 사실을 드러내지 않는다.
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_unknown"


def test_check_device_pending_and_revoked(monkeypatch):
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "pending", "last_seen_at": None})
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_pending"
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "revoked", "last_seen_at": None})
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_revoked"


def test_check_device_approved_touches_only_when_stale(monkeypatch):
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    touched = []
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved", "last_seen_at": None}, touched)
    v = asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now))
    assert v.ok and touched == ["d1"]

    touched.clear()
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved",
                          "last_seen_at": now - timedelta(seconds=30)}, touched)
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now)).ok
    assert touched == []  # 60초 안이면 안 찍는다

    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved",
                          "last_seen_at": now - timedelta(seconds=61)}, touched)
    asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now))
    assert touched == ["d1"]


def test_require_admin_off_never_looks_at_devices(monkeypatch):
    _admin(monkeypatch)
    async def boom(_conn, _hash):
        raise AssertionError("off 인데 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="off")))


def test_require_admin_shadow_logs_and_passes(monkeypatch, caplog):
    _admin(monkeypatch)
    _device(monkeypatch, None)
    with caplog.at_level("WARNING"):
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="shadow")))
    assert "admin_device_gate shadow reject" in caplog.text
    assert "device_missing" in caplog.text


def test_require_admin_enforce_rejects_with_the_device_code(monkeypatch):
    _admin(monkeypatch)
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "pending", "last_seen_at": None})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "device_pending"
    assert exc.value.detail["message"] == admin_guard.DEVICE_MESSAGES["device_pending"]


def test_require_admin_enforce_passes_an_approved_device(monkeypatch):
    _admin(monkeypatch)
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved", "last_seen_at": None})
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))


def test_non_admin_is_rejected_before_any_device_lookup(monkeypatch):
    async def is_admin(_conn, _user_id):
        return False
    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    async def boom(_conn, _hash):
        raise AssertionError("비관리자인데 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))
    assert exc.value.detail["code"] == "forbidden"


def test_require_admin_identity_checks_role_only(monkeypatch):
    _admin(monkeypatch)
    async def boom(_conn, _hash):
        raise AssertionError("identity 가드가 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    asyncio.run(admin_guard.require_admin_identity(FakeConn(), "u1"))
