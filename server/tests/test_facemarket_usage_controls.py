"""FaceMarket C1 사용 신고, 조건 변경, 활동 일시 중단 계약."""

import contextlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import facemarket
from app.facemarket_admin_models import ProfileLicenseView
from app.main import create_app
from conftest import make_settings


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
MODEL_ID = "33333333-3333-3333-3333-333333333333"
LICENSE_ID = "44444444-4444-4444-4444-444444444444"
SETTLEMENT_ID = "55555555-5555-5555-5555-555555555555"
PAYMENT_ID = "product:project-1:20260911"


def _license():
    created_at = NOW - timedelta(days=30)
    return {
        "id": LICENSE_ID,
        "model_id": MODEL_ID,
        "face_image_uri": f"/v1/facemarket/licenses/{LICENSE_ID}/face",
        "face_image_digest": "sha256-test",
        "allowed_use": ["일반 의류"],
        "forbidden_use": [],
        "unit_price": 10000,
        "license_valid_until": created_at + timedelta(days=365),
        "status": "active",
        "vc_id": "vc-1",
        "created_at": created_at,
        "updated_at": created_at,
        "cover_image_url": None,
    }


def _settlement():
    return {
        "id": SETTLEMENT_ID,
        "payment_id": PAYMENT_ID,
        "license_id": LICENSE_ID,
        "job_id": "job-1",
        "model_ref": "0x" + "ab" * 32,
        "total_amount": 10000,
        "model_amount": 7000,
        "platform_amount": 2000,
        "ops_amount": 1000,
        "chain_status": "confirmed",
        "tx_hash": "0x" + "cd" * 32,
        "chain_id": "1337",
        "recorded_block": 10,
        "created_at": NOW,
        "product_name": "골지 니트",
        "seller_name": "라임 스토어",
        "reported": False,
    }


class Cursor:
    def __init__(self, store):
        self.store = store
        self.one = None
        self.many = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.store["sql"].append((query, params))
        self.one, self.many = None, []

        if "ready_for_identity_delete" in query:
            self.one = {"closed": False}
        elif query.startswith("select") and "from fm_licenses l" in query and "for update" in query:
            license_id, user_id = params[:2]
            row = self.store["license"]
            if row["id"] == license_id and self.store["model"]["user_id"] == user_id:
                self.one = dict(row)
        elif query.startswith("update fm_licenses set allowed_use"):
            allowed, valid_until, license_id = params[:3]
            row = self.store["license"]
            if row["id"] == license_id and row["status"] == "active":
                row.update(
                    allowed_use=list(allowed),
                    license_valid_until=valid_until,
                    updated_at=NOW + timedelta(minutes=1),
                )
                self.one = dict(row)
        elif query.startswith("insert into fm_license_term_changes"):
            self.store["term_changes"].append(params)
        elif query.startswith("select") and "from fm_licenses l" in query and "order by l.created_at" in query:
            include_revoked = "l.status <> 'revoked'" not in query
            row = self.store["license"]
            if self.store["model"]["user_id"] == params[0] and (include_revoked or row["status"] != "revoked"):
                self.many = [dict(row)]
        elif query.startswith("select") and "from fm_models" in query and "where id = %s and user_id = %s" in query:
            model_id, user_id = params[:2]
            if self.store["model"]["id"] == model_id and self.store["model"]["user_id"] == user_id:
                self.one = dict(self.store["model"])
        elif query.startswith("update fm_models set status = 'suspended'"):
            source, model_id, user_id = params[:3]
            model = self.store["model"]
            if model["id"] == model_id and model["user_id"] == user_id and model["status"] == "verified":
                model.update(status="suspended", suspension_source=source, suspended_at=NOW)
                self.one = dict(model)
        elif query.startswith("update fm_models set status = 'verified'"):
            model_id, user_id = params[:2]
            model = self.store["model"]
            if (
                model["id"] == model_id
                and model["user_id"] == user_id
                and model["status"] == "suspended"
                and model.get("suspension_source") == "owner"
            ):
                model.update(status="verified", suspension_source=None, suspended_at=None)
                self.one = dict(model)
        elif query.startswith("select") and "from fm_settlements st" in query and "where st.payment_id = %s" in query:
            payment_id, user_id = params[:2]
            if payment_id == PAYMENT_ID and self.store["model"]["user_id"] == user_id:
                self.one = {
                    "settlement_id": SETTLEMENT_ID,
                    "model_id": MODEL_ID,
                }
        elif query.startswith("insert into fm_usage_reports"):
            settlement_id, model_id, reason = params[:3]
            if self.store["report"] is None:
                self.store["report"] = {
                    "id": "66666666-6666-6666-6666-666666666666",
                    "settlement_id": settlement_id,
                    "model_id": model_id,
                    "reason": reason,
                    "status": "open",
                    "created_at": NOW,
                }
                self.one = dict(self.store["report"])
        elif query.startswith("insert into admin_audit_log"):
            self.store["audits"].append(params)
        elif query.startswith("select") and "from fm_settlements st" in query:
            row = dict(self.store["settlement"])
            row["reported"] = self.store["report"] is not None
            self.many = [row] if self.store["model"]["user_id"] == params[0] else []
        elif query.startswith("select") and "from fm_models" in query and "where user_id = %s" in query:
            model = dict(self.store["model"])
            self.many = [model] if model["user_id"] == params[0] else []
        else:
            raise AssertionError(f"unexpected SQL: {query}")

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class Conn:
    def __init__(self, store):
        self.store = store
        self.commits = 0

    def cursor(self):
        return Cursor(self.store)

    async def commit(self):
        self.commits += 1


@contextlib.asynccontextmanager
async def _connection(store):
    yield Conn(store)


def _headers(make_token, sub=OWNER):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _fixture(monkeypatch, keypair):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(facemarket, "datetime", Clock)
    _private, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda _token: public_key
    store = {
        "model": {
            "id": MODEL_ID,
            "user_id": OWNER,
            "display_name": "테스트 모델",
            "status": "verified",
            "cover_image_url": None,
            "created_at": NOW,
            "assets_ready": True,
            "redo_count": 0,
            "suspension_source": None,
            "suspended_at": None,
            "enrollment_completed_at": NOW - timedelta(days=3),
            "review_completed_at": NOW - timedelta(days=2),
            "confirmed_at": NOW - timedelta(days=1),
        },
        "license": _license(),
        "settlement": _settlement(),
        "report": None,
        "term_changes": [],
        "audits": [],
        "sql": [],
    }
    monkeypatch.setattr(facemarket, "get_conn", lambda _request: _connection(store))
    return app, TestClient(app), store


def test_owner_reports_usage_and_dispatches_one_operational_email(monkeypatch, keypair, make_token):
    app, client, store = _fixture(monkeypatch, keypair)
    calls = []

    async def fake_email(_settings, *, to, payment_id, reason):
        calls.append((to, payment_id, reason))
        return True, "message-1", None

    monkeypatch.setattr(facemarket, "send_usage_report_email", fake_email, raising=False)
    object.__setattr__(app.state.settings, "fm_usage_report_to_email", "ops@example.com")

    response = client.post(
        f"/v1/facemarket/settlements/{PAYMENT_ID}/report",
        json={"reason": "사용 범위를 확인해 주세요."},
        headers=_headers(make_token),
    )

    assert response.status_code == 201, response.text
    assert response.json() == {
        "id": "66666666-6666-6666-6666-666666666666",
        "settlementId": SETTLEMENT_ID,
        "modelId": MODEL_ID,
        "reason": "사용 범위를 확인해 주세요.",
        "status": "open",
        "createdAt": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert len(store["audits"]) == 1
    assert calls == [("ops@example.com", PAYMENT_ID, "사용 범위를 확인해 주세요.")]


def test_report_usage_hides_foreign_or_unknown_settlement(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)
    for payment_id, sub in ((PAYMENT_ID, OTHER), ("missing", OWNER)):
        response = client.post(
            f"/v1/facemarket/settlements/{payment_id}/report",
            json={},
            headers=_headers(make_token, sub),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert store["report"] is None


def test_report_usage_rejects_duplicate_settlement(monkeypatch, keypair, make_token):
    _app, client, _store = _fixture(monkeypatch, keypair)
    first = client.post(
        f"/v1/facemarket/settlements/{PAYMENT_ID}/report",
        json={},
        headers=_headers(make_token),
    )
    second = client.post(
        f"/v1/facemarket/settlements/{PAYMENT_ID}/report",
        json={},
        headers=_headers(make_token),
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "usage_already_reported"


def test_settlement_list_exposes_safe_labels_and_report_state(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)
    store["report"] = {"settlement_id": SETTLEMENT_ID}

    response = client.get("/v1/facemarket/settlements", headers=_headers(make_token))

    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row["productName"] == "골지 니트"
    assert row["sellerName"] == "라임 스토어"
    assert row["reported"] is True
    assert "userId" not in row and "email" not in row


def test_terms_patch_changes_allowed_use_without_rewriting_historical_validity(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)

    response = client.patch(
        f"/v1/facemarket/licenses/{LICENSE_ID}/terms",
        json={"allowedUse": ["액티브웨어"], "validDays": 730},
        headers=_headers(make_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowedUse"] == ["액티브웨어"]
    assert body["licenseValidUntil"] == (NOW - timedelta(days=30) + timedelta(days=365)).isoformat().replace("+00:00", "Z")
    assert body["unitPrice"] == 10000
    assert body["updatedAt"] == (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    assert len(store["term_changes"]) == 1
    change = store["term_changes"][0]
    assert change[0] == LICENSE_ID and change[3] == OWNER
    assert change[1].obj == {
        "allowedUse": ["일반 의류"],
        "licenseValidUntil": (NOW - timedelta(days=30) + timedelta(days=365)).isoformat(),
    }
    assert change[2].obj == {
        "allowedUse": ["액티브웨어"],
        "licenseValidUntil": (NOW - timedelta(days=30) + timedelta(days=365)).isoformat(),
    }


def test_terms_patch_does_not_offer_duration_changes(monkeypatch, keypair, make_token):
    _app, client, _store = _fixture(monkeypatch, keypair)
    response = client.patch(
        f"/v1/facemarket/licenses/{LICENSE_ID}/terms",
        json={"validDays": None},
        headers=_headers(make_token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "terms_required"


def test_terms_patch_cannot_add_an_expiry_to_a_permanent_license(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)
    store["license"]["license_valid_until"] = None
    response = client.patch(
        f"/v1/facemarket/licenses/{LICENSE_ID}/terms",
        json={"allowedUse": ["액티브웨어"], "validDays": 365},
        headers=_headers(make_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["allowedUse"] == ["액티브웨어"]
    assert response.json()["licenseValidUntil"] is None
    assert store["license"]["license_valid_until"] is None


def test_permanent_profile_license_accepts_null_validity_fields():
    profile = ProfileLicenseView.model_validate({
        "allowedUse": ["일반 의류"],
        "forbiddenUse": [],
        "unitPrice": 10000,
        "validUntil": None,
        "validDays": None,
    })
    assert profile.valid_until is None
    assert profile.valid_days is None


def test_terms_patch_rejects_empty_invalid_and_foreign_requests(monkeypatch, keypair, make_token):
    _app, client, _store = _fixture(monkeypatch, keypair)
    cases = [
        ({}, OWNER, 400),
        ({"validDays": 90}, OWNER, 400),
        ({"allowedUse": ["성인물"]}, OWNER, 400),
        ({"allowedUse": None}, OWNER, 400),
        ({"allowedUse": []}, OWNER, 400),
        ({"allowedUse": ["   "]}, OWNER, 400),
        ({"allowedUse": ["일반 의류"]}, OTHER, 404),
    ]
    for body, sub, status in cases:
        response = client.patch(
            f"/v1/facemarket/licenses/{LICENSE_ID}/terms",
            json=body,
            headers=_headers(make_token, sub),
        )
        assert response.status_code == status, response.text


def test_revoked_licenses_are_opt_in(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)
    store["license"]["status"] = "revoked"

    default = client.get("/v1/facemarket/licenses", headers=_headers(make_token))
    included = client.get(
        "/v1/facemarket/licenses?includeRevoked=true",
        headers=_headers(make_token),
    )

    assert default.json() == []
    assert [row["status"] for row in included.json()] == ["revoked"]


def test_owner_pause_and_resume_preserve_admin_boundary(monkeypatch, keypair, make_token):
    _app, client, store = _fixture(monkeypatch, keypair)
    paused = client.post(
        f"/v1/facemarket/models/{MODEL_ID}/pause", headers=_headers(make_token)
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["suspensionSource"] == "owner"
    assert paused.json()["suspendedAt"] is not None

    resumed = client.post(
        f"/v1/facemarket/models/{MODEL_ID}/resume", headers=_headers(make_token)
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json() == {
        "id": MODEL_ID,
        "status": "verified",
        "suspensionSource": None,
        "suspendedAt": None,
    }

    store["model"].update(
        status="suspended", suspension_source="admin", suspended_at=NOW
    )
    blocked = client.post(
        f"/v1/facemarket/models/{MODEL_ID}/resume", headers=_headers(make_token)
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "admin_suspended"


def test_my_models_returns_existing_progress_timestamps(monkeypatch, keypair, make_token):
    _app, client, _store = _fixture(monkeypatch, keypair)

    response = client.get("/v1/facemarket/models/me", headers=_headers(make_token))

    assert response.status_code == 200, response.text
    body = response.json()[0]
    assert body["enrollmentCompletedAt"] == (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    assert body["reviewCompletedAt"] == (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    assert body["confirmedAt"] == (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
