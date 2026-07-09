"""FM-11 본인확인 라우트 테스트.

CX `trans` 호출·DB를 페이크로 대체해 순수 로직(HMAC dedup·리플레이 차단·마스킹·게이트)만 검증.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation

from app import facemarket
from app.main import create_app
from conftest import AUDIENCE, make_settings

# FM-03 실측 기반 대표 trans 응답(ENT_MID mdriverlic). 원문 PII 형태 모사.
SAMPLE_TRANS = {
    "vcTypeCodeList": "[mdriverlic]",
    "engnm": "NOH JEONGWOON",
    "nm": "노정운",
    "ci": "GWGPw9ZKtEBu5NW+3Jbdq74U32ogxvXRCArgdZnOvUtNdDZBA5K+Mie4w==",
    "birth": "20040722",
}


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        if s.startswith("select id, status from fm_models where ci_hash"):
            m = self.store["by_ci"].get(params[0])
            self._result = dict(m) if m else None
        elif s.startswith("update fm_models set status"):
            self._result = None
        elif s.startswith("insert into fm_models"):
            user_id, name, ci_hash = params
            mid = f"model-{len(self.store['by_ci']) + 1}"
            self.store["by_ci"][ci_hash] = {"id": mid, "status": "verified"}
            self._result = {"id": mid}
        elif s.startswith("insert into fm_identity_verifications"):
            _model_id, cx_tx_id, _fields = params
            if cx_tx_id in self.store["tx"]:
                raise UniqueViolation("duplicate cx_tx_id")
            self.store["tx"].add(cx_tx_id)
            self._result = None
        else:  # pragma: no cover - guard for unexpected SQL
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        return None


@pytest.fixture()
def fm(keypair, monkeypatch):
    """facemarket 활성 클라이언트 + 공유 store + trans 응답 setter."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key

    store = {"by_ci": {}, "tx": set()}

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)

    trans_box = {"value": dict(SAMPLE_TRANS)}

    async def fake_fetch(_base, _token):
        return trans_box["value"]

    monkeypatch.setattr(facemarket, "_fetch_trans", fake_fetch)

    def set_trans(value):
        trans_box["value"] = value

    return TestClient(app), store, set_trans


def _headers(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_verify_success_creates_verified_model(fm, make_token):
    client, store, _ = fm
    r = client.post("/v1/facemarket/identity/verify", json={"token": "tok-1"}, headers=_headers(make_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True
    assert body["status"] == "verified"
    assert body["nameMasked"] == "노*운"  # 원문 이름 미노출
    assert body["modelId"]
    # 원문 CI가 응답 어디에도 없어야 한다.
    assert "ci" not in r.text and SAMPLE_TRANS["ci"] not in r.text
    assert len(store["by_ci"]) == 1 and len(store["tx"]) == 1


def test_replay_same_token_409(fm, make_token):
    client, _, _ = fm
    first = client.post("/v1/facemarket/identity/verify", json={"token": "dup"}, headers=_headers(make_token))
    assert first.status_code == 200
    again = client.post("/v1/facemarket/identity/verify", json={"token": "dup"}, headers=_headers(make_token))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "identity_replay"


def test_same_person_new_token_reuses_model(fm, make_token):
    client, store, _ = fm
    client.post("/v1/facemarket/identity/verify", json={"token": "t-a"}, headers=_headers(make_token))
    client.post("/v1/facemarket/identity/verify", json={"token": "t-b"}, headers=_headers(make_token))
    # 같은 ci → 모델 1개, 인증 로그 2개(서로 다른 token).
    assert len(store["by_ci"]) == 1
    assert len(store["tx"]) == 2


def test_missing_ci_400(fm, make_token):
    client, _, set_trans = fm
    set_trans({"engnm": "NO CI", "birth": "19990101"})
    r = client.post("/v1/facemarket/identity/verify", json={"token": "tok"}, headers=_headers(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ci_missing"


def test_requires_auth_401(fm):
    client, _, _ = fm
    r = client.post("/v1/facemarket/identity/verify", json={"token": "tok"})
    assert r.status_code == 401


def test_disabled_when_flag_off(keypair):
    _priv, public_key = keypair
    app = create_app(make_settings())  # facemarket_enabled 기본 False
    app.state.jwt_key_resolver = lambda token: public_key
    client = TestClient(app)
    r = client.post("/v1/facemarket/identity/verify", json={"token": "tok"})
    assert r.status_code == 404  # 라우트 미등록
