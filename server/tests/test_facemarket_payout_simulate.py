"""데모용 지급 시뮬레이션 — 실제 이체 없이 기존 상태머신을 걷는다.

스텁은 `FM_PAYOUT_PROVIDER=stub` 일 때만 켜진다. 프로덕션 기본은 manual 이고, 그때
시뮬레이션 라우트는 **아무 일도 하지 않는다**(409). 모델에게 가는 알림은 이 경로에서
만들지 않는다 — 돈이 안 갔는데 "입금됐다"고 말하는 순간 목이 거짓말이 된다.
"""

import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from app import facemarket_payout
from app.main import create_app

CONFIRMATION_ID = "66666666-6666-4666-8666-666666666666"
MODEL_ID = "77777777-7777-4777-8777-777777777777"
BASE = f"/v1/facemarket/admin/payout-confirmations/{CONFIRMATION_ID}/simulate"
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _confirmation(**overrides):
    row = {
        "id": CONFIRMATION_ID, "model_id": MODEL_ID, "period_month": "2026-09-01",
        "responsible_admin": "user-1", "amount": 52150, "count": 5,
        "bank_code": "shinhan", "holder_name": "홍길동", "account_last4": "5359",
        "account_version": "88888888-8888-4888-8888-888888888888", "status": "prepared",
        "provider": "stub", "provider_ref": None, "failure_reason": None,
        "created_at": NOW, "started_at": None, "paid_at": None, "cancelled_at": None,
    }
    row.update(overrides)
    return row


class Cursor:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def execute(self, sql, params=None):
        self.conn.executed.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.conn.rows.pop(0) if self.conn.rows else None

    async def fetchall(self):
        return self.conn.rows.pop(0) if self.conn.rows else []


class Conn:
    def __init__(self, rows):
        self.rows, self.executed, self.commits = list(rows), [], 0

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield Cursor(self)

    async def commit(self):
        self.commits += 1


def _client(monkeypatch, locked, popped, *, provider="stub"):
    """locked = _locked_confirmation 이 돌려줄 행(라우트가 소비하지 않는다).
    popped = 라우트의 UPDATE ... returning 이 차례로 받아갈 행들."""
    conn = Conn(popped)

    @contextlib.asynccontextmanager
    async def connection(_request):
        yield conn

    async def is_admin(_conn, _user):
        return True

    async def locked_confirmation(_conn, _id, _user):
        return locked

    monkeypatch.setattr(facemarket_payout, "get_conn", connection)
    monkeypatch.setattr(facemarket_payout.admin_guard.repo, "is_admin", is_admin)
    monkeypatch.setattr(facemarket_payout, "_locked_confirmation", locked_confirmation)
    sent = []

    async def notify(_settings, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(facemarket_payout.facemarket_notify, "notify_slack_payout_simulated", notify)
    app = create_app(make_settings(facemarket_enabled=True, fm_payout_provider=provider))
    app.state.jwt_key_resolver = lambda _token: None
    return TestClient(app, raise_server_exceptions=False), conn, sent


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _client_with_key(keypair, monkeypatch, locked, popped=(), *, provider="stub"):
    client, conn, sent = _client(monkeypatch, locked, list(popped), provider=provider)
    client.app.state.jwt_key_resolver = lambda _token: keypair[1]
    return client, conn, sent


def test_manual_provider_refuses_to_simulate(keypair, make_token, monkeypatch):
    client, conn, sent = _client_with_key(
        keypair, monkeypatch, _confirmation(provider="manual"), provider="manual")
    response = client.post(BASE, json={"outcome": "paid"}, headers=_auth(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payout_provider_manual"
    assert not any("update" in sql.lower() for sql, _ in conn.executed)
    assert sent == []


def test_stub_success_walks_started_then_paid_and_records_reference(keypair, make_token, monkeypatch):
    client, conn, _sent = _client_with_key(keypair, monkeypatch, _confirmation(), [
        _confirmation(status="transfer_started", started_at=NOW),
        _confirmation(status="paid", started_at=NOW, paid_at=NOW, provider_ref="stub-abc")])
    response = client.post(BASE, json={"outcome": "paid"}, headers=_auth(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "paid"
    assert body["simulated"] is True
    updates = [sql for sql, _ in conn.executed if sql.lower().startswith("update")]
    assert any("status = 'transfer_started'" in sql or "transfer_started" in str(params)
               for sql, params in conn.executed)
    assert any("provider_ref" in sql for sql in updates)


def test_stub_failure_cancels_and_releases_the_entries(keypair, make_token, monkeypatch):
    client, conn, _sent = _client_with_key(keypair, monkeypatch, _confirmation(), [
        _confirmation(status="cancelled", cancelled_at=NOW, failure_reason="account_error")])
    response = client.post(BASE, json={"outcome": "account_error"}, headers=_auth(make_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["failureReason"] == "account_error"
    # 실패한 건의 정산 항목은 풀려야 다음 달에 다시 지급할 수 있다.
    assert any("released_at = now()" in sql for sql, _ in conn.executed)


def test_already_finished_confirmation_is_not_touched(keypair, make_token, monkeypatch):
    client, conn, _sent = _client_with_key(
        keypair, monkeypatch, _confirmation(status="paid", started_at=NOW, paid_at=NOW))
    response = client.post(BASE, json={"outcome": "paid"}, headers=_auth(make_token))
    assert response.status_code == 409
    assert not any(sql.lower().startswith("update") for sql, _ in conn.executed)


def test_unknown_outcome_is_rejected(keypair, make_token, monkeypatch):
    client, conn, _sent = _client_with_key(keypair, monkeypatch, _confirmation(), [_confirmation()])
    response = client.post(BASE, json={"outcome": "exploded"}, headers=_auth(make_token))
    assert response.status_code in (400, 422)
    assert not any(sql.lower().startswith("update") for sql, _ in conn.executed)


def test_anonymous_cannot_simulate(keypair, monkeypatch):
    client, conn, _sent = _client_with_key(keypair, monkeypatch, _confirmation(), [_confirmation()])
    assert client.post(BASE, json={"outcome": "paid"}).status_code == 401
    assert conn.executed == []


@pytest.mark.parametrize("outcome", ["paid", "account_error"])
def test_every_simulated_run_tells_slack_it_was_a_simulation(outcome, keypair, make_token, monkeypatch):
    client, _conn, sent = _client_with_key(keypair, monkeypatch, _confirmation(), [
        _confirmation(status="paid", started_at=NOW, paid_at=NOW),
        _confirmation(status="paid", started_at=NOW, paid_at=NOW)])
    client.post(BASE, json={"outcome": outcome}, headers=_auth(make_token))
    assert len(sent) == 1
    assert sent[0]["paid"] is (outcome == "paid")
    assert sent[0]["amount"] == 52150


def test_confirmation_is_born_with_the_configured_provider(keypair, make_token, monkeypatch):
    """확인서를 만들 때 provider 를 박아야 한다.

    DB 기본값은 manual 이다. 여기서 안 넣으면 서버를 stub 으로 띄워도 확인서가 manual 로
    남아 시뮬레이션 버튼이 영영 안 뜬다(화면은 confirmation.simulated 를 본다). 단위
    테스트가 provider 를 손으로 넣어 준 탓에 안 잡혔고, 실제 DB 로 한 번 걸어 보고서야
    드러났다."""
    account = {"bank_code": "shinhan", "holder_name": "데모", "account_number_enc": "enc",
               "account_last4": "6789", "account_version": "version-1"}
    entries = [{"id": "settlement-1", "model_amount": 10430}]
    # 라우트가 차례로 받아가는 행들: 모델 잠금 → 기존 확인서(둘) → 정산서 → 계좌 → 항목 → INSERT
    rows = [{"id": MODEL_ID}, None, None, None, account, entries,
            _confirmation(status="prepared")]
    client, conn, _sent = _client_with_key(keypair, monkeypatch, _confirmation(), rows)
    monkeypatch.setattr(facemarket_payout, "_cipher", lambda _r: SimpleNamespace(decrypt=lambda _v: b"110"))

    response = client.post(
        f"/v1/facemarket/admin/payout-statements/{MODEL_ID}/2026-08/confirm",
        json={"confirmationId": CONFIRMATION_ID}, headers=_auth(make_token))

    assert response.status_code == 200, response.text
    insert = next((sql, params) for sql, params in conn.executed
                  if sql.lower().startswith("insert into fm_payout_confirmations"))
    columns = insert[0].split("(", 1)[1].split(")", 1)[0]
    assert "provider" in columns, columns  # RETURNING 이 아니라 INSERT 컬럼 목록이어야 한다
    assert "stub" in insert[1], insert[1]
