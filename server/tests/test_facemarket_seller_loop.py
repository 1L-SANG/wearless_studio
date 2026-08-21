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
MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
CATEGORY = "일반 여성 의류"


def _valid_gate_row(**overrides):
    row = {
        "id": LICENSE_ID,
        "model_id": MODEL_ID,
        "model_name": "홍*동",
        "status": "active",
        "license_valid_until": FUTURE,
        "unit_price": 100,
        "vc_id": "vc-1",
        "allowed_use": [CATEGORY],
        "forbidden_use": [],
        "model_status": "verified",
        "assets_status": "ready",
        "current_enrollment_id": ENROLLMENT_ID,
        "license_enrollment_id": ENROLLMENT_ID,
        "enrollment_status": "passed",
        "match_policy_version": "policy-v1",
        "has_face_front": True,
        "has_grid_sedcard": True,
        "assets_current_evidence": True,
    }
    row.update(overrides)
    return row


def _verify(app, row=None, *, model_id=MODEL_ID, category=CATEGORY):
    return facemarket.verify_license(
        app,
        _valid_gate_row() if row is None else row,
        model_id=model_id,
        brand_use_category=category,
    )


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
        asyncio.run(_verify(_app(), _valid_gate_row(status="revoked")))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "license_revoked"
    assert "해지" in ei.value.detail["message"]


def test_verify_inactive_raises_409_license_inactive():
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app(), _valid_gate_row(status="suspended")))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_inactive"


def test_verify_expired_raises_409_license_expired():
    row = _valid_gate_row(license_valid_until=PAST)
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app(), row))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_expired"


def test_verify_active_valid_passes_without_holder():
    # 홀더 미설정 → 라이브 arm skip. active+미만료 → 통과(예외 없음).
    assert asyncio.run(_verify(_app(None))) is None


def test_verify_naive_datetime_treated_as_utc():
    # tz-naive valid_until(미래) 도 통과해야 한다(비교 시 utc 부여).
    naive_future = datetime.now() + timedelta(days=5)
    row = _valid_gate_row(license_valid_until=naive_future)
    assert asyncio.run(_verify(_app(None), row)) is None


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
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app("http://holder", required=True)))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "license_unverified"


def test_verify_holder_valid_passes(monkeypatch):
    calls = _patch_holder(
        monkeypatch,
        _FakeResp(200, {"verified": True, "status": "valid", "onChain": True}),
    )
    assert asyncio.run(
        _verify(_app("http://holder", required=True))
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
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app("http://holder", required=True)))
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "holder_unavailable"


def test_required_verify_without_vc_is_409(monkeypatch):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))
    row = _valid_gate_row(vc_id=None)
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app("http://holder", required=True), row))
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
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(app))
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
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app("http://holder", required=True)))
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
    with pytest.raises(facemarket.HTTPException) as ei:
        asyncio.run(_verify(_app("http://holder", required=True)))
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "license_unverified"


def test_optional_dev_mode_preserves_local_only_behavior():
    assert asyncio.run(_verify(_app(required=False), _valid_gate_row(vc_id=None))) is None


@pytest.mark.parametrize(
    ("overrides", "model_id", "category", "code"),
    [
        ({}, None, None, None),
        ({}, "mA", None, None),
        ({}, "44444444-4444-4444-4444-444444444444", CATEGORY, "model_unavailable"),
        ({"model_status": "pending"}, MODEL_ID, CATEGORY, "model_unavailable"),
        ({"id": None, "status": None}, MODEL_ID, CATEGORY, "license_inactive"),
        ({"status": "reverification_required"}, MODEL_ID, CATEGORY, "license_inactive"),
        ({}, MODEL_ID, " ", "brand_use_category_required"),
        ({}, MODEL_ID, "not-fixed", "brand_use_category_required"),
        ({"forbidden_use": [CATEGORY]}, MODEL_ID, CATEGORY, "license_use_forbidden"),
        ({"allowed_use": None}, MODEL_ID, CATEGORY, "license_use_not_allowed"),
        ({"allowed_use": "not-a-list"}, MODEL_ID, CATEGORY, "license_use_not_allowed"),
        ({"allowed_use": ["남성 의류"]}, MODEL_ID, CATEGORY, "license_use_not_allowed"),
        ({"current_enrollment_id": None}, MODEL_ID, CATEGORY, "model_enrollment_unavailable"),
        ({"enrollment_status": "pending"}, MODEL_ID, CATEGORY, "model_enrollment_unavailable"),
        ({"match_policy_version": " "}, MODEL_ID, CATEGORY, "model_enrollment_unavailable"),
        ({"license_enrollment_id": "other"}, MODEL_ID, CATEGORY, "model_enrollment_unavailable"),
        ({"assets_status": "building"}, MODEL_ID, CATEGORY, "model_assets_unavailable"),
        ({"has_face_front": False}, MODEL_ID, CATEGORY, "model_assets_unavailable"),
        ({"has_grid_sedcard": False}, MODEL_ID, CATEGORY, "model_assets_unavailable"),
        ({"assets_current_evidence": False}, MODEL_ID, CATEGORY, "model_assets_unavailable"),
    ],
)
def test_verify_current_runtime_gate_fails_closed_without_holder(
    monkeypatch, overrides, model_id, category, code
):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))
    row = _valid_gate_row(**overrides)
    if code is None:
        assert asyncio.run(_verify(_app("http://holder", required=True), row,
                                   model_id=model_id, category=category)) is None
    else:
        with pytest.raises(facemarket.HTTPException) as exc:
            asyncio.run(_verify(_app("http://holder", required=True), row,
                                model_id=model_id, category=category))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == code
    assert calls == []


# ── persisted use-policy shape 가드 ─────────────────────────────

@pytest.mark.parametrize(
    "malformed",
    [
        None,
        CATEGORY,
        (CATEGORY,),
        {CATEGORY: True},
        [CATEGORY, "unknown-category"],
        [CATEGORY, facemarket.FORBIDDEN_BRAND_USE_CATEGORIES[0]],
        [
            CATEGORY,
            facemarket.ALLOWED_BRAND_USE_CATEGORIES[1],
            "unknown-category",
        ],
        [],
    ],
    ids=(
        "none", "string", "tuple", "dict", "unknown", "cross-list", "mixed", "empty"
    ),
)
def test_verify_rejects_malformed_allowed_policy_before_holder(monkeypatch, malformed):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))

    with pytest.raises(facemarket.HTTPException) as exc:
        asyncio.run(_verify(
            _app("http://holder", required=True),
            _valid_gate_row(allowed_use=malformed),
        ))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "license_use_not_allowed"
    assert calls == []


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        facemarket.FORBIDDEN_BRAND_USE_CATEGORIES[0],
        (facemarket.FORBIDDEN_BRAND_USE_CATEGORIES[0],),
        {facemarket.FORBIDDEN_BRAND_USE_CATEGORIES[0]: True},
        ["unknown-category"],
        [facemarket.ALLOWED_BRAND_USE_CATEGORIES[1]],
        [facemarket.FORBIDDEN_BRAND_USE_CATEGORIES[0], "unknown-category"],
    ],
    ids=("none", "string", "tuple", "dict", "unknown", "cross-list", "mixed"),
)
def test_verify_rejects_malformed_forbidden_policy_before_holder(monkeypatch, malformed):
    calls = _patch_holder(monkeypatch, error=AssertionError("must not call Holder"))

    with pytest.raises(facemarket.HTTPException) as exc:
        asyncio.run(_verify(
            _app("http://holder", required=True),
            _valid_gate_row(forbidden_use=malformed),
        ))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "license_use_forbidden"
    assert calls == []


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
        self.store.setdefault("queries", []).append((s, p))
        if "l.id = %s" in s:
            self._one = self.store["by_pair"].get((p[0], p[1]))
        elif "m.id = %s" in s:
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
    out = _resolve({}, {"selectedModelId": "mA"}, {"by_pair": {}, "by_model": {}})
    assert out is None


def test_resolve_no_selection_is_noop():
    assert _resolve({}, {}, {"by_pair": {}, "by_model": {}}) is None


def test_resolve_uuid_without_current_license_returns_model_row():
    row = _valid_gate_row(id=None, status=None, license_enrollment_id=None)
    out = _resolve({}, {"selectedModelId": MODEL_ID},
                   {"by_pair": {}, "by_model": {MODEL_ID: row}})
    assert out["model_id"] == MODEL_ID and out["id"] is None


def test_resolve_picks_only_current_enrollment_license_and_masks_name():
    row = _valid_gate_row(model_name="홍길동")
    store = {"by_pair": {}, "by_model": {MODEL_ID: row}}
    out = _resolve({}, {"selectedModelId": MODEL_ID}, store)
    assert out["id"] == LICENSE_ID and out["model_name"] == "홍*동"
    sql = store["queries"][0][0]
    for fragment in (
        "e.id = m.current_enrollment_id", "e.model_id = m.id",
        "l.enrollment_id = m.current_enrollment_id", "match_policy_version",
        "face_front", "grid_sedcard", "bucket = 'face'", "like 'image/%%'",
        "nullif(btrim", "source_enrollment_id", "evidence_version",
    ):
        assert fragment in sql


def test_resolve_accepts_snake_case_selected_model_alias():
    out = _resolve(
        {},
        {"selected_model_id": MODEL_ID},
        {"by_pair": {}, "by_model": {MODEL_ID: _valid_gate_row()}},
    )
    assert out["id"] == LICENSE_ID


def test_resolve_ignores_stale_project_lock_for_current_selection():
    store = {"by_pair": {}, "by_model": {MODEL_ID: _valid_gate_row()}}
    out = _resolve(
        {"facemarket_license_id": "stale-license"},
        {"selectedModelId": MODEL_ID},
        store,
    )
    assert out["id"] == LICENSE_ID
    assert len(store["queries"]) == 1


def test_resolve_pinned_requires_exact_model_license_pair():
    row = _valid_gate_row()
    store = {"by_pair": {(MODEL_ID, LICENSE_ID): row}, "by_model": {}}
    out = asyncio.run(facemarket.resolve_model_license(
        _Conn(store), MODEL_ID, license_id=LICENSE_ID
    ))
    missing = asyncio.run(facemarket.resolve_model_license(
        _Conn(store), MODEL_ID,
        license_id="44444444-4444-4444-4444-444444444444",
    ))
    assert out["id"] == LICENSE_ID
    assert missing is None
    pinned_sql = store["queries"][0][0]
    assert "left join fm_licenses l on l.model_id = m.id" in pinned_sql
    assert "l.enrollment_id = m.current_enrollment_id" not in pinned_sql


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
