from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_valid_token_returns_user_id(client, make_token):
    res = client.get(
        "/v1/me/ping", headers={"Authorization": f"Bearer {make_token(sub='user-42')}"}
    )
    assert res.status_code == 200
    assert res.json() == {"userId": "user-42"}


def test_missing_header_is_401_envelope(client):
    res = client.get("/v1/me/ping")
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"] == "로그인이 필요합니다."


def test_expired_token_is_401(client, make_token):
    token = make_token(exp_offset=-60)
    res = client.get("/v1/me/ping", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_wrong_audience_is_401(client, make_token):
    token = make_token(aud="other-audience")
    res = client.get("/v1/me/ping", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_garbage_token_is_401(client):
    res = client.get("/v1/me/ping", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_dev_flag_bypasses_auth_in_dev_only(keypair):
    _, public_key = keypair

    dev_app = create_app(make_settings(app_env="dev", dev_user_id="dev-user"))
    dev_app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(dev_app).get("/v1/me/ping")
    assert res.status_code == 200
    assert res.json() == {"userId": "dev-user"}


def test_dev_flag_ignored_outside_dev_env(monkeypatch):
    # load_settings가 APP_ENV != dev에서 AUTH_DEV_USER_ID를 무시하는지
    from app.config import load_settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_DEV_USER_ID", "dev-user")
    assert load_settings().dev_user_id is None
