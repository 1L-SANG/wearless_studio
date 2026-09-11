import pytest
from cryptography.fernet import Fernet

from payout_helpers import MODEL_ID, Conn, account_fixture, client_for, patch_db

BASE = "/v1/facemarket/payout-account"
ADMIN = f"/v1/facemarket/admin/models/{MODEL_ID}/payout-account"


def module():
    from app import facemarket_payout
    return facemarket_payout


def auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_account_routes_exist(keypair, make_token):
    client = client_for(keypair)
    assert client.get(BASE, headers=auth(make_token)).status_code == 503


@pytest.mark.parametrize("status", ["pending", "verified", "suspended"])
def test_save_encrypts_then_get_returns_only_last_four(status, keypair, make_token, monkeypatch, caplog):
    key, raw, row = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn([{"id": MODEL_ID, "status": status}, row])
    patch_db(monkeypatch, module(), conn)
    response = client.put(BASE, headers=auth(make_token), json={
        "bankCode": "shinhan", "accountNumber": f" {raw[:4]}- {raw[4:]} ",
        "holderName": " 테스트 예금주 ",
    })
    assert response.status_code == 200
    assert response.json()["accountMasked"] == f"***-****-{raw[-4:]}"
    assert set(response.json()) == {"bankCode", "bankName", "holderName", "accountMasked", "updatedAt"}
    inserted = next(params for sql, params in conn.executed if sql.startswith("insert into fm_payout_accounts"))
    encrypted = inserted[2]
    assert raw not in encrypted
    assert raw not in repr(conn.executed)
    assert Fernet(key.encode()).decrypt(encrypted.encode()).decode() == raw
    assert not any("admin_audit_log" in sql for sql, _ in conn.executed)
    assert conn.events == ["commit"]
    conn.rows = [{"id": MODEL_ID}, row]
    response = client.get(BASE, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json()["accountMasked"] == f"***-****-{raw[-4:]}"
    assert raw not in response.text and raw not in caplog.text
    assert "account_number_enc" not in conn.executed[-1][0]


@pytest.mark.parametrize("key", [None, "invalid-key"])
@pytest.mark.parametrize("method", ["get", "put"])
def test_missing_or_invalid_key_fails_closed(key, method, keypair, make_token):
    client = client_for(keypair, fm_payout_account_key=key)
    kwargs = {"json": {"bankCode": "shinhan", "accountNumber": "", "holderName": "테스트"}} if method == "put" else {}
    response = getattr(client, method)(BASE, headers=auth(make_token), **kwargs)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "payout_account_unconfigured"


@pytest.mark.parametrize("patch", [
    {"accountNumber": "short"}, {"accountNumber": "9" * 17},
    {"accountNumber": "９" * 10}, {"accountNumber": 123},
    {"bankCode": "unknown"}, {"holderName": " "}, {"holderName": "가" * 41},
])
def test_invalid_accounts_are_400_without_echo(patch, keypair, make_token, monkeypatch):
    key, raw, _ = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn()
    patch_db(monkeypatch, module(), conn)
    payload = {"bankCode": "shinhan", "accountNumber": raw, "holderName": "테스트", **patch}
    response = client.put(BASE, json=payload, headers=auth(make_token))
    assert response.status_code == 400
    assert raw not in response.text
    assert "details" not in response.json()["error"]
    assert not conn.executed


@pytest.mark.parametrize("shape", ["missing", "list", "scalar", "broken_json"])
def test_validation_never_echoes_sensitive_body(shape, keypair, make_token):
    key, raw, _ = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    values = {"missing": {"accountNumber": raw}, "list": [raw], "scalar": raw}
    if shape == "broken_json":
        response = client.put(BASE, content='{"accountNumber":"' + raw, headers={**auth(make_token), "Content-Type": "application/json"})
    else:
        response = client.put(BASE, json=values[shape], headers=auth(make_token))
    assert response.status_code == 400
    assert raw not in response.text


@pytest.mark.parametrize("method", ["get", "put"])
def test_model_absence_and_account_absence_are_404(method, keypair, make_token, monkeypatch):
    key, raw, _ = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn([None])
    patch_db(monkeypatch, module(), conn)
    kwargs = {"json": {"bankCode": "shinhan", "accountNumber": raw, "holderName": "테스트"}} if method == "put" else {}
    response = getattr(client, method)(BASE, headers=auth(make_token), **kwargs)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert "user_id = %s" in conn.executed[0][0]
    assert conn.executed[0][1] == ("user-1",)
    conn.rows = [{"id": MODEL_ID}, None]
    response = client.get(BASE, headers=auth(make_token))
    assert response.json()["error"]["code"] == "payout_account_not_found"


def test_admin_reveal_is_audited_before_response(keypair, make_token, monkeypatch, caplog):
    key, raw, row = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn([row])
    patch_db(monkeypatch, module(), conn)
    response = client.get(ADMIN, headers=auth(make_token))
    assert response.status_code == 200
    assert response.json()["accountNumber"] == raw
    assert response.headers["cache-control"] == "no-store"
    assert conn.events == ["audit", "commit"]
    audit = next(params for sql, params in conn.executed if sql.startswith("insert into admin_audit_log"))
    assert audit[:4] == ("user-1", "payout_account.reveal", "model", MODEL_ID)
    assert raw not in repr(audit) and raw not in caplog.text


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_failed_audit_cannot_reveal(failure, keypair, make_token, monkeypatch, caplog):
    key, raw, row = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn([row], fail_audit=failure == "audit", fail_commit=failure == "commit")
    patch_db(monkeypatch, module(), conn)
    response = client.get(ADMIN, headers=auth(make_token))
    assert response.status_code == 500
    assert raw not in response.text and raw not in caplog.text
    assert conn.rollbacks == 1


def test_non_admin_and_anonymous_cannot_reveal(keypair, make_token, monkeypatch):
    key, _, _ = account_fixture()
    client = client_for(keypair, fm_payout_account_key=key)
    conn = Conn()
    patch_db(monkeypatch, module(), conn, admin=False)
    assert client.get(ADMIN).status_code == 401
    assert client.get(ADMIN, headers=auth(make_token)).status_code == 403
    assert conn.executed == []


def test_config_exposes_only_public_bank_names(keypair):
    from app.facemarket_enrollment import router
    client = client_for(keypair)
    client.app.include_router(router)
    response = client.get("/v1/facemarket/config")
    assert response.status_code == 200
    assert response.json()["payoutBanks"][0] == {"code": "shinhan", "name": "신한은행"}
    assert len(response.json()["payoutBanks"]) == 8
