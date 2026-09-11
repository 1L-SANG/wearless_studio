"""계좌 원문을 파일에 남기지 않는 지급 API 테스트 도우미."""
import contextlib
import secrets
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import assert_query_binds, make_settings

MODEL_ID = "33333333-3333-3333-3333-333333333333"
PUBLICATION_ID = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


class Cursor:
    def __init__(self, conn):
        self.conn, self.row = conn, None

    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        sql = " ".join(sql.split())
        self.conn.executed.append((sql, params))
        if sql.startswith("insert into admin_audit_log"):
            if self.conn.fail_audit:
                raise RuntimeError("audit unavailable")
            self.conn.events.append("audit")
            self.row = None
        else:
            self.row = self.conn.rows.pop(0) if self.conn.rows else None

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.row or []


class Conn:
    def __init__(self, rows=(), fail_audit=False, fail_commit=False):
        self.rows, self.executed = list(rows), []
        self.fail_audit, self.fail_commit = fail_audit, fail_commit
        self.events, self.rollbacks = [], 0

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield Cursor(self)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit unavailable")
        self.events.append("commit")


def client_for(keypair, **settings):
    _, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, **settings))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app, raise_server_exceptions=False)


def patch_db(monkeypatch, module, conn, admin=True):
    @contextlib.asynccontextmanager
    async def connection(_request):
        try:
            yield conn
        except Exception:
            conn.rollbacks += 1
            raise

    async def is_admin(_conn, _user):
        return admin

    monkeypatch.setattr(module, "get_conn", connection)
    monkeypatch.setattr(module.admin_guard.repo, "is_admin", is_admin)


def account_fixture():
    raw = "".join(secrets.choice("0123456789") for _ in range(12))
    key = Fernet.generate_key()
    row = {
        "bank_code": "shinhan", "holder_name": "테스트 예금주",
        "account_last4": raw[-4:], "updated_at": NOW,
        "account_number_enc": Fernet(key).encrypt(raw.encode()).decode(),
    }
    return key.decode(), raw, row
