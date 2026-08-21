"""FM-30 셀러 루프 백엔드: verify-before-use 게이트 · 라이선스 해지 · 잡 정산 영수증.

DB/홀더를 페이크로 대체해 순수 로직만 검증:
verify_license 4-arm 409 계약, resolve_project_license no-op 가드(비-UUID·무라이선스·잠금),
revoke 소유 스코프·멱등·내구성 있는 큐 적재, 영수증 shape·소유 스코프.
"""

import asyncio
import contextlib
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import facemarket, holder_client
from app.main import create_app
from conftest import make_settings

NOW = datetime.now(timezone.utc)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=1)


def _app(opendid_holder_url=None, *, required=False, secret="shared-secret"):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            settings=types.SimpleNamespace(
                fm_vc_required=required,
                opendid_holder_url=opendid_holder_url,
                opendid_holder_hmac_secret=secret,
            )
        )
    )


# ── verify_license (순수 4-arm 계약) ──────────────────────────────

def test_verify_revoked_raises_409_license_revoked():
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app(), {"status": "revoked"}))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "license_revoked"
    assert "해지" in ei.value.detail["message"]


def test_verify_inactive_raises_409_license_inactive():
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app(), {"status": "suspended"}))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_inactive"


def test_verify_expired_raises_409_license_expired():
    row = {"status": "active", "license_valid_until": PAST}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app(), row))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_expired"


def test_verify_active_valid_passes_without_holder():
    # 홀더 미설정 → 라이브 arm skip. active+미만료 → 통과(예외 없음).
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    assert asyncio.run(facemarket.verify_license(_app(None), row)) is None


def test_verify_naive_datetime_treated_as_utc():
    # tz-naive valid_until(미래) 도 통과해야 한다(비교 시 utc 부여).
    naive_future = datetime.now() + timedelta(days=5)
    row = {"status": "active", "license_valid_until": naive_future}
    assert asyncio.run(facemarket.verify_license(_app(None), row)) is None


class _FakeResp:
    def __init__(self, status_code, payload, *, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def _patch_holder(monkeypatch, resp=None, error=None):
    calls = []

    async def fake_post(_client, **kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return resp

    monkeypatch.setattr(holder_client, "post", fake_post)
    return calls


def test_verify_holder_revoked_raises_license_unverified(monkeypatch):
    _patch_holder(monkeypatch, _FakeResp(200, {"verified": True, "status": "revoked"}))
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app("http://holder", required=True), row))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_unverified"


def test_verify_holder_valid_passes(monkeypatch):
    calls = _patch_holder(
        monkeypatch,
        _FakeResp(200, {"verified": True, "status": "valid", "onChain": True}),
    )
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    assert asyncio.run(
        facemarket.verify_license(_app("http://holder", required=True), row)
    ) is None
    assert calls == [{
        "base_url": "http://holder",
        "secret": "shared-secret",
        "path": "/holder/vc/verify",
        "payload": {"vcId": "vc-1"},
    }]


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("holder down"), httpx.TimeoutException("holder timeout")],
)
def test_required_verify_holder_transport_failure_is_503(monkeypatch, error):
    _patch_holder(monkeypatch, error=error)
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app("http://holder", required=True), row))
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "holder_unavailable"


def test_required_verify_without_vc_is_409(monkeypatch):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": None}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app("http://holder", required=True), row))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "license_unverified"
    assert calls == []


@pytest.mark.parametrize(
    "app",
    [
        _app(None, required=True),
        _app(" ", required=True),
        _app("http://holder", required=True, secret=None),
        _app("http://holder", required=True, secret=" "),
    ],
)
def test_required_verify_missing_runtime_config_is_503(monkeypatch, app):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(app, row))
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "holder_unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        _FakeResp(500, {}),
        _FakeResp(401, {}),
        _FakeResp(200, []),
        _FakeResp(200, None),
        _FakeResp(200, {}, json_error=ValueError("bad json")),
    ],
)
def test_required_verify_non_200_or_malformed_is_503(monkeypatch, response):
    _patch_holder(monkeypatch, response)
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app("http://holder", required=True), row))
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "holder_unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {"verified": False, "status": "valid"},
        {"verified": True, "status": "revoked"},
        {"verified": True, "status": "unknown"},
        {"verified": "true", "status": "valid"},
        {"verified": True, "status": "VALID"},
        {},
    ],
)
def test_required_verify_non_valid_credential_is_409(monkeypatch, payload):
    _patch_holder(monkeypatch, _FakeResp(200, payload))
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": "vc-1"}
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(facemarket.verify_license(_app("http://holder", required=True), row))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "license_unverified"


def test_optional_dev_mode_preserves_local_only_behavior():
    row = {"status": "active", "license_valid_until": FUTURE, "vc_id": None}
    assert asyncio.run(facemarket.verify_license(_app(required=False), row)) is None


# ── resolve_project_license (no-op 가드) ─────────────────────────

class _Cur:
    def __init__(self, store):
        self.store = store
        self._one = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        p = params or ()
        if "from fm_licenses where id" in s:
            self._one = self.store["by_id"].get(p[0])
        elif "from fm_licenses where model_id" in s:
            self._one = self.store["by_model"].get(p[0])
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._one


class _Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _Cur(self.store)

    async def commit(self):
        return None


def _resolve(project, analysis, store):
    return asyncio.run(
        facemarket.resolve_project_license(_Conn(store), project, analysis)
    )


def test_resolve_non_uuid_selected_model_is_noop():
    # 구 정적 mock id → None(500/409 아님).
    out = _resolve({}, {"selectedModelId": "mA"}, {"by_id": {}, "by_model": {}})
    assert out is None


def test_resolve_no_selection_is_noop():
    assert _resolve({}, {}, {"by_id": {}, "by_model": {}}) is None


def test_resolve_model_with_no_license_is_noop():
    mid = "11111111-1111-1111-1111-111111111111"
    out = _resolve({}, {"selectedModelId": mid}, {"by_id": {}, "by_model": {}})
    assert out is None


def test_resolve_picks_model_latest_license():
    mid = "11111111-1111-1111-1111-111111111111"
    lic = {"id": "lic-1", "model_id": mid, "status": "active"}
    out = _resolve({}, {"selectedModelId": mid}, {"by_id": {}, "by_model": {mid: lic}})
    assert out["id"] == "lic-1"


def test_resolve_prefers_locked_project_license():
    # 프로젝트가 이미 라이선스에 잠겨 있으면(재생성) 그 라이선스를 우선 로드(상태 무관).
    mid = "11111111-1111-1111-1111-111111111111"
    locked = {"id": "lic-locked", "model_id": mid, "status": "revoked"}
    fresh = {"id": "lic-fresh", "model_id": mid, "status": "active"}
    store = {"by_id": {"lic-locked": locked}, "by_model": {mid: fresh}}
    out = _resolve({"facemarket_license_id": "lic-locked"}, {"selectedModelId": mid}, store)
    assert out["id"] == "lic-locked" and out["status"] == "revoked"


# ── revoke 라우트 + 영수증 라우트 (소유 스코프·멱등·shape) ─────────

_LIC_ROW = {
    "id": "lic-1", "model_id": "m-1", "face_image_uri": "/v1/facemarket/licenses/lic-1/face",
    "face_image_digest": "sha256-x", "allowed_use": [], "forbidden_use": [],
    "unit_price": 10000, "license_valid_until": FUTURE, "status": "revoked",
    "vc_id": "vc-1", "created_at": NOW,
}


class _RouteCur:
    def __init__(self, store):
        self.store = store
        self._one = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        params = params or ()
        if normalized.startswith("select") and "from fm_licenses l join fm_models m" in normalized and "l.vc_id" in normalized:
            lic = self.store["licenses"].get(params[0])
            # 스냅샷 반환(dict 복사) — 실제 DB fetchone 처럼 이후 UPDATE 변형과 격리.
            self._one = dict(lic) if (lic and lic["user_id"] == params[1]) else None
            self.store["select_for_update"] += int("for update" in normalized)
        elif normalized.startswith("update fm_licenses set status = 'revoked'"):
            lic = self.store["licenses"].get(params[0])
            if lic:
                lic["status"] = "revoked"
            self._one = dict(_LIC_ROW, status="revoked")
        elif normalized.startswith("insert into fm_vc_revocation_jobs"):
            if self.store.get("enqueue_error"):
                raise RuntimeError("queue unavailable")
            license_id, model_id, vc_id = params
            self.store["revocations"].setdefault(
                vc_id,
                {"license_id": license_id, "model_id": model_id, "status": "pending"},
            )
            self._one = None
        elif "from fm_settlements st" in normalized:
            row = self.store["settlements"].get(params[0])
            self._one = row if (row and row["user_id"] == params[1]) else None
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {normalized}")

    async def fetchone(self):
        return self._one


class _RouteConn:
    def __init__(self, store):
        self.store = store
        self._statuses = {
            key: value["status"] for key, value in store["licenses"].items()
        }

    def cursor(self):
        return _RouteCur(self.store)

    async def commit(self):
        self.store["commit_count"] += 1

    async def rollback(self):
        for key, status in self._statuses.items():
            self.store["licenses"][key]["status"] = status


@pytest.fixture()
def route(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    store = {
        "licenses": {}, "settlements": {}, "revocations": {},
        "commit_count": 0, "select_for_update": 0,
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        conn = _RouteConn(store)
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store


def _uid(make_token):
    import jwt as _jwt
    tok = make_token()
    return tok, _jwt.decode(tok, options={"verify_signature": False})["sub"]


def test_revoke_route_halts_license_and_enqueues_in_one_commit(route, make_token):
    client, store = route
    tok, uid = _uid(make_token)
    store["licenses"]["lic-1"] = {
        "id": "lic-1", "model_id": "m-1", "vc_id": "vc-1", "status": "active",
        "user_id": uid,
    }
    r = client.post("/v1/facemarket/licenses/lic-1/revoke",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked" and r.json()["id"] == "lic-1"
    assert store["licenses"]["lic-1"]["status"] == "revoked"
    assert store["revocations"]["vc-1"] == {
        "license_id": "lic-1", "model_id": "m-1", "status": "pending",
    }
    assert store["commit_count"] == 1
    assert store["select_for_update"] == 1


def test_revoke_nonowner_404(route, make_token):
    client, store = route
    tok, _uid_self = _uid(make_token)
    store["licenses"]["lic-x"] = {
        "id": "lic-x", "model_id": "m-2", "vc_id": None, "status": "active",
        "user_id": "someone-else",
    }
    r = client.post("/v1/facemarket/licenses/lic-x/revoke",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


def test_revoke_already_revoked_self_heals_missing_intent(route, make_token):
    client, store = route
    tok, uid = _uid(make_token)
    store["licenses"]["lic-1"] = {
        "id": "lic-1", "model_id": "m-1", "vc_id": "vc-1", "status": "revoked",
        "user_id": uid,
    }
    r = client.post("/v1/facemarket/licenses/lic-1/revoke",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert store["revocations"]["vc-1"]["status"] == "pending"
    assert store["commit_count"] == 1


def test_revoke_without_vc_still_halts_locally(route, make_token):
    client, store = route
    tok, uid = _uid(make_token)
    store["licenses"]["lic-1"] = {
        "id": "lic-1", "model_id": "m-1", "vc_id": None, "status": "active",
        "user_id": uid,
    }
    r = client.post("/v1/facemarket/licenses/lic-1/revoke",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert store["licenses"]["lic-1"]["status"] == "revoked"
    assert store["revocations"] == {}
    assert store["commit_count"] == 1


def test_revoke_enqueue_failure_rolls_back_local_halt(route, make_token):
    client, store = route
    tok, uid = _uid(make_token)
    store["licenses"]["lic-1"] = {
        "id": "lic-1", "model_id": "m-1", "vc_id": "vc-1", "status": "active",
        "user_id": uid,
    }
    store["enqueue_error"] = True

    response = client.post(
        "/v1/facemarket/licenses/lic-1/revoke",
        headers={"Authorization": f"Bearer {tok}"},
    )

    assert response.status_code == 500
    assert store["licenses"]["lic-1"]["status"] == "active"
    assert store["commit_count"] == 0


def test_job_settlement_receipt_shape(route, make_token):
    client, store = route
    tok, uid = _uid(make_token)
    store["settlements"]["job:jid-1"] = {
        "payment_id": "job:jid-1", "tx_hash": "0xabc", "chain_id": "1337",
        "total_amount": 10000, "model_amount": 7000, "platform_amount": 2000,
        "ops_amount": 1000, "chain_status": "confirmed", "vc_id": "vc-1",
        "user_id": uid,
    }
    r = client.get("/v1/facemarket/jobs/jid-1/settlement",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {
        "paymentId", "txHash", "chainId", "totalAmount", "modelAmount",
        "platformAmount", "opsAmount", "vcId", "chainStatus",
    }
    assert b["paymentId"] == "job:jid-1" and b["txHash"] == "0xabc"
    assert (b["modelAmount"], b["platformAmount"], b["opsAmount"]) == (7000, 2000, 1000)
    assert b["vcId"] == "vc-1" and b["chainStatus"] == "confirmed"


def test_job_settlement_404_when_unrecorded(route, make_token):
    client, _store = route
    tok, _sub = _uid(make_token)
    r = client.get("/v1/facemarket/jobs/unknown/settlement",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


# ── resolve_model_license (에디터 새 컷 게이트 해석) ──────────────────
def test_resolve_model_license_noop_guards_before_db():
    """미선택·비-UUID(구 'mA'/'mB' 가상모델)는 커서 생성 전에 None — DB 없이 검증."""
    conn = object()  # cursor() 호출되면 AttributeError로 즉시 드러난다
    assert asyncio.run(facemarket.resolve_model_license(conn, None)) is None
    assert asyncio.run(facemarket.resolve_model_license(conn, "")) is None
    assert asyncio.run(facemarket.resolve_model_license(conn, "mA")) is None
