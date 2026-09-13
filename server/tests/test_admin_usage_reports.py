"""관리자 신고 목록과 상태 변경의 권한, 페이지 이동, 감사 기록 계약."""
import base64
import contextlib
import re
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_admin
from app.main import create_app
from conftest import assert_query_binds, make_settings


REPORT_ID = "00000000-0000-0000-0000-000000000002"
OLDER_ID = "00000000-0000-0000-0000-000000000001"
CREATED_AT = datetime(2026, 9, 11, 16, 30, tzinfo=timezone.utc)
BASE = "/v1/facemarket/admin/usage-reports"


@pytest.fixture()
def client(keypair):
    _, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


@pytest.fixture()
def enforce_client(keypair):
    _, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, admin_device_gate="enforce"))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.row = conn, None

    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        sql = " ".join(sql.split())
        self.conn.executed.append((sql, params))
        if sql.startswith("insert into admin_audit_log") and self.conn.fail_audit:
            raise RuntimeError("audit unavailable")
        self.row = self.conn.rows.pop(0) if self.conn.rows else None

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.row or []


class FakeConn:
    def __init__(self, rows=(), *, fail_audit=False):
        self.rows, self.executed = list(rows), []
        self.commits, self.rollbacks, self.fail_audit = 0, 0, fail_audit

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield FakeCursor(self)

    async def commit(self):
        self.commits += 1


def patch_db(monkeypatch, conn, *, admin=True):
    @contextlib.asynccontextmanager
    async def connection(_request):
        try:
            yield conn
        except Exception:
            conn.rollbacks += 1
            raise

    async def is_admin(actual_conn, user_id):
        assert actual_conn is conn
        return admin

    monkeypatch.setattr(facemarket_admin, "get_conn", connection)
    monkeypatch.setattr(facemarket_admin.admin_guard.repo, "is_admin", is_admin)


def report_row(report_id=REPORT_ID, **overrides):
    return {
        "id": report_id, "settlement_id": "settlement-1", "payment_id": "payment:1",
        "model_id": "model-1", "model_name": "모델 이름", "reason": "허용 품목 확인",
        "status": "open", "created_at": CREATED_AT, **overrides,
    }


@pytest.mark.parametrize(("method", "path", "kwargs"), [
    ("get", BASE, {}),
    ("patch", f"{BASE}/{REPORT_ID}", {"json": {"status": "closed"}}),
])
def test_usage_report_routes_require_registered_device_in_enforce_mode(
    method, path, kwargs, enforce_client, make_token, monkeypatch,
):
    conn = FakeConn()
    patch_db(monkeypatch, conn)

    response = getattr(enforce_client, method)(
        path, headers={"Authorization": f"Bearer {make_token()}"}, **kwargs,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "device_missing"
    assert conn.executed == []


def test_list_serializes_report_fields_and_advances_a_stable_cursor(client, make_token, monkeypatch):
    conn = FakeConn([[report_row(), report_row(OLDER_ID)]])
    patch_db(monkeypatch, conn)
    headers = {"Authorization": f"Bearer {make_token()}"}
    response = client.get(BASE, params={"limit": 1}, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["items"] == [{
        "id": REPORT_ID, "settlementId": "settlement-1", "paymentId": "payment:1",
        "modelId": "model-1", "modelName": "모델 이름", "reason": "허용 품목 확인",
        "status": "open", "createdAt": "2026-09-11T16:30:00+00:00",
    }]
    assert page["nextCursor"]
    sql, params = conn.executed[0]
    assert "order by r.created_at desc, r.id desc" in sql
    assert "left join fm_models" in sql
    assert "join fm_settlements" in sql
    assert params["limit"] == 2

    conn.rows = [[report_row(OLDER_ID, model_name=None, reason=None)]]
    response = client.get(BASE, params={"limit": 1, "cursor": page["nextCursor"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == OLDER_ID
    assert response.json()["items"][0]["modelName"] is None
    assert response.json()["items"][0]["reason"] is None
    assert response.json()["nextCursor"] is None
    sql, params = conn.executed[-1]
    assert "(r.created_at, r.id) <" in sql
    assert params["cursor_id"] == REPORT_ID
    assert params["cursor_created"] == CREATED_AT


def test_empty_list_has_no_next_page(client, make_token, monkeypatch):
    conn = FakeConn([[]])
    patch_db(monkeypatch, conn)
    response = client.get(BASE, headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None}


@pytest.mark.parametrize("status", ["open", "closed"])
def test_status_filter_is_passed_to_the_query(status, client, make_token, monkeypatch):
    conn = FakeConn([[]])
    patch_db(monkeypatch, conn)
    response = client.get(BASE, params={"status": status},
                          headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert conn.executed[0][1]["status"] == status


def test_unknown_status_filter_is_rejected(client, make_token, monkeypatch):
    conn = FakeConn()
    patch_db(monkeypatch, conn)
    response = client.get(BASE, params={"status": "pending"},
                          headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 400
    assert conn.executed == []


def test_list_query_filters_and_keeps_reports_after_model_removal():
    # PostgreSQL의 바인딩과 캐스트만 바꿔 실제 목록 SQL의 조인과 정렬을 로컬에서 실행해요.
    sql = re.sub(r"::(?:text|timestamptz|uuid)", "", facemarket_admin.LIST_USAGE_REPORTS_SQL)
    sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            create table fm_usage_reports (
                id text, settlement_id text, model_id text, reason text, status text, created_at text
            );
            create table fm_settlements (id text, payment_id text);
            create table fm_models (id text, display_name text);
            insert into fm_models values ('m1', '모델 이름');
            insert into fm_settlements values ('s1', 'p1'), ('s2', 'p2'), ('s3', 'p3');
            insert into fm_usage_reports values
              ('r3', 's3', 'm1', null, 'closed', '2026-09-11'),
              ('r2', 's2', 'removed', '확인 부탁해요', 'open', '2026-09-11'),
              ('r1', 's1', 'm1', null, 'open', '2026-09-11');
        """)
        params = {"status": "open", "cursor_created": None, "cursor_id": None, "limit": 100}
        rows = conn.execute(sql, params).fetchall()
        assert [row["id"] for row in rows] == ["r2", "r1"]
        assert rows[0]["model_name"] is None
        params.update(cursor_created="2026-09-11", cursor_id="r2")
        assert [row["id"] for row in conn.execute(sql, params)] == ["r1"]
        params.update(status="closed", cursor_created=None, cursor_id=None)
        assert [row["id"] for row in conn.execute(sql, params)] == ["r3"]
    finally:
        conn.close()


@pytest.mark.parametrize("params", [
    {"cursor": "broken"},
    {"cursor": base64.urlsafe_b64encode(b"2026-09-11T16:30:00+00:00|bad-id").decode()},
    {"cursor": base64.urlsafe_b64encode(f"2026-09-11T16:30:00|{REPORT_ID}".encode()).decode()},
    {"limit": 0}, {"limit": 201},
])
def test_invalid_pagination_is_rejected_before_report_queries(params, client, make_token, monkeypatch):
    conn = FakeConn()
    patch_db(monkeypatch, conn)
    response = client.get(BASE, params=params, headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code in (400, 422)
    assert conn.executed == []


@pytest.mark.parametrize("method", ["get", "patch"])
def test_routes_require_login_and_admin(method, client, make_token, monkeypatch):
    conn = FakeConn()
    patch_db(monkeypatch, conn, admin=False)
    path = BASE if method == "get" else f"{BASE}/{REPORT_ID}"
    kwargs = {} if method == "get" else {"json": {"status": "closed"}}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    response = getattr(client, method)(path, **kwargs, headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert not conn.executed
    assert conn.commits == 0


@pytest.mark.parametrize(("before", "after"), [("open", "closed"), ("closed", "open")])
def test_patch_locks_and_audits_changes_before_committing(before, after, client, make_token, monkeypatch):
    conn = FakeConn([{"status": before}])
    patch_db(monkeypatch, conn)
    response = client.patch(f"{BASE}/{REPORT_ID}", json={"status": after},
                            headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {"id": REPORT_ID, "status": after}
    assert "for update" in conn.executed[0][0]
    update = next(params for sql, params in conn.executed if sql.startswith("update fm_usage_reports"))
    assert update == (after, REPORT_ID)
    audit = next(params for sql, params in conn.executed if sql.startswith("insert into admin_audit_log"))
    assert audit[:4] == ("user-1", "usage_report.status.update", "usage_report", REPORT_ID)
    assert audit[4].obj == {"status": before}
    assert audit[5].obj == {"status": after}
    assert conn.commits == 1


@pytest.mark.parametrize("status", ["open", "closed"])
def test_repeating_status_succeeds_without_duplicate_audit(status, client, make_token, monkeypatch):
    conn = FakeConn([{"status": status}])
    patch_db(monkeypatch, conn)
    response = client.patch(f"{BASE}/{REPORT_ID}", json={"status": status},
                            headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert response.json() == {"id": REPORT_ID, "status": status}
    assert len(conn.executed) == 1
    assert conn.commits == 1


@pytest.mark.parametrize(("report_id", "body", "expected"), [
    ("bad-id", {"status": "closed"}, 400),
    (REPORT_ID, {"status": "pending"}, 422),
    (REPORT_ID, {"status": None}, 422),
    (REPORT_ID, {}, 422),
])
def test_invalid_patch_does_not_write(report_id, body, expected, client, make_token, monkeypatch):
    conn = FakeConn()
    patch_db(monkeypatch, conn)
    response = client.patch(f"{BASE}/{report_id}", json=body,
                            headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == expected
    assert not conn.executed
    assert conn.commits == 0


def test_missing_report_is_404(client, make_token, monkeypatch):
    conn = FakeConn([None])
    patch_db(monkeypatch, conn)
    response = client.patch(f"{BASE}/{REPORT_ID}", json={"status": "closed"},
                            headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert conn.commits == 0


def test_audit_failure_does_not_commit_status(client, make_token, monkeypatch):
    conn = FakeConn([{"status": "open"}], fail_audit=True)
    patch_db(monkeypatch, conn)
    response = client.patch(f"{BASE}/{REPORT_ID}", json={"status": "closed"},
                            headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 500
    assert conn.commits == 0
    assert conn.rollbacks == 1
