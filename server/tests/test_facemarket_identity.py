"""FM-11 본인확인 + FM-13 모델 카탈로그 라우트 테스트.

CX `trans` 호출·DB를 페이크로 대체해 순수 로직(HMAC dedup·리플레이 차단·마스킹·게이트·
화이트리스트 노출)만 검증.
"""

import contextlib
import hashlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation

from app import facemarket, facemarket_enrollment
from app.main import create_app
from conftest import make_settings

FIXED_DT = datetime(2026, 7, 9, 12, 0, 0)

# FM-03 실측 기반 대표 trans 응답(ENT_MID mdriverlic). 원문 PII 형태 모사.
SAMPLE_TRANS = {
    "vcTypeCodeList": "[mdriverlic]",
    "engnm": "NOH JEONGWOON",
    "nm": "노정운",
    "ci": "GWGPw9ZKtEBu5NW+3Jbdq74U32ogxvXRCArgdZnOvUtNdDZBA5K+Mie4w==",
    "birth": "20040722",
}

_CARD_KEYS = ("id", "display_name", "status", "cover_image_url", "created_at")
# 카탈로그(enriched) 추가 라이선스 필드 — store 에 라이선스 없으면 None/False.
_LICENSE_ENRICH = {
    "license_id": None, "unit_price": None, "vc_id": None, "has_active_license": False,
}


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None
        self._many = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        models = self.store["models"]
        if s.startswith("select id, status from fm_models where ci_hash"):
            m = next((r for r in models if r["ci_hash"] == params[0]), None)
            self._result = {"id": m["id"], "status": m["status"]} if m else None
        elif s.startswith("update fm_models set status"):
            self._result = None
        elif s.startswith("insert into fm_models"):
            user_id, name, ci_hash = params
            mid = f"model-{len(models) + 1}"
            models.append({
                "id": mid, "user_id": user_id, "display_name": name,
                "status": "verified", "ci_hash": ci_hash,
                "cover_image_url": None, "created_at": FIXED_DT,
            })
            self._result = {"id": mid}
        elif s.startswith("insert into fm_identity_verifications"):
            self.store["identity_insert_sql"] = s
            _model_id, cx_tx_id, *rest = params
            if rest and rest[0] == "sha256-v1":
                self.store["identity_insert_format"] = rest[0]
            if cx_tx_id in self.store["tx"]:
                raise UniqueViolation("duplicate cx_tx_id")
            self.store["tx"].add(cx_tx_id)
            self._result = None
        elif s.startswith("select id::text as id, display_name, status, cover_image_url, created_at"):
            # /models/me — 본인 소유(모든 상태). 기본 카드 컬럼(+assets_ready 파생).
            rows = [r for r in models if r["user_id"] == params[0]]
            self._many = [{k: r[k] for k in _CARD_KEYS} for r in rows]
        elif s.startswith("select m.id::text as id"):
            # 카탈로그(enriched) = verified 만 + 최근 active 라이선스 LEFT JOIN LATERAL.
            rows = [r for r in models if r["status"] == "verified"]
            self._many = [
                {**{k: r[k] for k in _CARD_KEYS}, **_LICENSE_ENRICH} for r in rows
            ]
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._many or []


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

    store = {"models": [], "tx": set(), "identity_insert_sql": "", "identity_insert_format": None}

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
    assert len(store["models"]) == 1 and len(store["tx"]) == 1
    assert store["tx"] == {f"cxsha256:{hashlib.sha256(b'tok-1').hexdigest()}"}
    assert "(model_id, cx_tx_id, cx_tx_id_format, fields)" in store["identity_insert_sql"]
    assert store["identity_insert_format"] == "sha256-v1"


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
    assert len(store["models"]) == 1
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


def test_identity_only_cannot_activate_model_when_biometrics_are_enabled(
    keypair, make_token, monkeypatch
):
    _priv, public_key = keypair
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )
    app = create_app(
        make_settings(
            app_env="dev",
            facemarket_enabled=True,
            fm_biometric_enrollment_enabled=True,
            fm_oacx_contract_mode="dev-mock-v1",
            fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
            fm_liveness_confidence_threshold=90.0,
            fm_id_live_threshold=0.45,
            fm_retouched_live_threshold=0.40,
            fm_match_policy_version="dev-gold-v1",
            fm_ci_pepper="pep",
            fm_face_qc_enabled=True,
            opendid_holder_url="http://holder.test",
        )
    )
    app.state.jwt_key_resolver = lambda _token: public_key
    store = {"models": []}

    async def fail_fetch(*_args):
        pytest.fail("identity-only route called OACX while biometrics enabled")

    monkeypatch.setattr(facemarket, "_fetch_trans", fail_fetch)

    response = TestClient(app).post(
        "/v1/facemarket/identity/verify",
        json={"token": "tok-1"},
        headers=_headers(make_token),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "biometric_enrollment_required"
    assert store["models"] == []


# ---- FM-13 카탈로그 ----------------------------------------------------------


def test_catalog_lists_verified_without_pii(fm, make_token):
    client, _, _ = fm
    client.post("/v1/facemarket/identity/verify", json={"token": "tok-c"}, headers=_headers(make_token))
    r = client.get("/v1/facemarket/models", headers=_headers(make_token))
    assert r.status_code == 200, r.text
    cards = r.json()
    assert len(cards) == 1
    card = cards[0]
    # T2 enriched — 기본 카드 + 라이선스 필드(store 무라이선스 → None/False).
    assert set(card) == {
        "id", "displayName", "status", "coverImageUrl", "createdAt",
        "licenseId", "unitPrice", "hasActiveLicense", "vcId", "assetsReady", "faceThumbUri",
    }
    assert card["status"] == "verified"
    assert card["assetsReady"] is False  # 자산 미빌드 → 셀러 선택 불가 표식
    assert card["hasActiveLicense"] is False
    assert card["licenseId"] is None and card["unitPrice"] is None and card["vcId"] is None
    # PII/식별자 미노출
    assert "ciHash" not in card and "userId" not in card and "ci_hash" not in r.text


def test_my_models_scoped_to_owner(fm, make_token):
    client, _, _ = fm
    client.post("/v1/facemarket/identity/verify", json={"token": "tok-m"}, headers=_headers(make_token))
    mine = client.get("/v1/facemarket/models/me", headers=_headers(make_token))
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    # 다른 사용자는 이 모델을 못 본다.
    other = client.get("/v1/facemarket/models/me", headers={"Authorization": f"Bearer {make_token(sub='other')}"})
    assert other.status_code == 200
    assert other.json() == []


def test_catalog_requires_auth_401(fm):
    client, _, _ = fm
    assert client.get("/v1/facemarket/models").status_code == 401
