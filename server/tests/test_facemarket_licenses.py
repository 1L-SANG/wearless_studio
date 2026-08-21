"""얼굴 라이선스 라우트 테스트 (생성 멀티파트 · 목록 · 얼굴 게이트).

DB·R2를 페이크로 대체해 순수 로직만 검증:
  · 얼굴 바이트는 비공개 R2에만 저장, 응답에 face_image_key/원본 바이트 미노출
  · face_image_uri = 게이트 URL(공개 R2 URL 아님), digest = 'sha256-...'
  · 소유 스코프(다른 사용자 접근 404) · revoked/expired 접근 차단(404)
  · verified 모델 선행 필수(없으면 400)
"""

import contextlib
import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket, holder_client
from app import facemarket_enrollment
from app.main import create_app
from conftest import make_settings

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"
OTHER_ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
MODEL_ID = "11111111-1111-1111-1111-111111111111"
FRONT_ASSET_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/assets/face_front.png"
GRID_ASSET_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/assets/grid_sedcard.png"
APPROVED_FRONT_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/approved/front.png"
APPROVED_FRONT_DIGEST = "sha256-approved-front"
EVIDENCE_VERSION = "dev-gold-v1"
_LICENSE_KEYS = (
    "id", "model_id", "face_image_uri", "face_image_digest", "allowed_use",
    "forbidden_use", "unit_price", "license_valid_until", "status", "vc_id", "created_at",
)


class FakeR2Face:
    """app.state.r2_face 대역 — 바이트를 dict에 보관(put/get/delete)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (data, mime)

    def get_bytes(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None
        self._many = None
        self.rowcount = -1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        models = self.store["models"]
        licenses = self.store["licenses"]
        self._result = None
        self._many = None
        self.rowcount = -1
        self.store.setdefault("sql", []).append(s)

        if s.startswith("select e.id::text as enrollment_id"):
            if len(params) == 3:
                license_id, enrollment_id, user_id = params[:3]
                owned = {m["id"] for m in models if m["user_id"] == user_id}
                lic = next(
                    (r for r in licenses
                     if r["id"] == license_id
                     and r.get("enrollment_id") == enrollment_id
                     and r["model_id"] in owned),
                    None,
                )
                if lic is None:
                    self._result = None
                    return
            else:
                enrollment_id, user_id = params[:2]
            e = next(
                (r for r in self.store["enrollments"]
                 if r["id"] == enrollment_id and r["user_id"] == user_id),
                None,
            )
            if e is None:
                self._result = None
                return
            m = next((r for r in models if r["id"] == e["model_id"] and r["user_id"] == user_id), None)
            front_photo = next(
                (p for p in self.store["enrollment_photos"]
                 if p["enrollment_id"] == e["id"] and p["angle"] == "front"),
                None,
            )
            assets = {
                a["view"]: a for a in self.store["assets"]
                if a["model_id"] == e["model_id"] and a["view"] in {"face_front", "grid_sedcard"}
            }
            face = assets.get("face_front") or {}
            grid = assets.get("grid_sedcard") or {}
            self._result = {
                "enrollment_id": e["id"],
                "enrollment_status": e["status"],
                "model_id": (m or {}).get("id"),
                "model_status": (m or {}).get("status"),
                "model_did": (m or {}).get("did"),
                "assets_status": (m or {}).get("assets_status"),
                "current_enrollment_id": (m or {}).get("current_enrollment_id"),
                "front_key": (front_photo or {}).get("r2_key"),
                "front_digest": (front_photo or {}).get("image_digest"),
                "front_storage_state": (front_photo or {}).get("storage_state"),
                "match_policy_version": e.get("match_policy_version"),
                "face_asset_key": face.get("r2_key"),
                "face_asset_source_enrollment_id": face.get("source_enrollment_id"),
                "face_asset_evidence_version": face.get("evidence_version"),
                "grid_asset_key": grid.get("r2_key"),
                "grid_asset_source_enrollment_id": grid.get("source_enrollment_id"),
                "grid_asset_evidence_version": grid.get("evidence_version"),
            }
        elif s.startswith("select id from fm_models where user_id"):
            # verified 모델 조회
            m = next(
                (r for r in models if r["user_id"] == params[0] and r["status"] == "verified"),
                None,
            )
            self._result = {"id": m["id"]} if m else None
        elif (
            s.startswith("select l.id::text as id")
            and "from fm_licenses l" in s
            and "where l.id = %s" in s
        ):
            license_id, user_id = params[:2]
            owned = {m["id"] for m in models if m["user_id"] == user_id}
            row = next((r for r in licenses if r["id"] == license_id and r["model_id"] in owned), None)
            if row:
                self._result = {k: row[k] for k in _LICENSE_KEYS}
                self._result["enrollment_id"] = row.get("enrollment_id")
                self._result["face_image_key"] = row.get("face_image_key")
            else:
                self._result = None
        elif s.startswith("select") and "from fm_licenses l" in s and "l.enrollment_id" in s:
            enrollment_id, user_id = params[:2]
            if self.store.pop("hide_existing_license_once", False):
                self._result = None
                return
            owned = {m["id"] for m in models if m["user_id"] == user_id}
            row = next(
                (r for r in licenses if r.get("enrollment_id") == enrollment_id and r["model_id"] in owned),
                None,
            )
            self._result = {k: row[k] for k in _LICENSE_KEYS} if row else None
        elif s.startswith("insert into fm_licenses") and "enrollment_id" in s:
            (lid, model_id, enrollment_id, gate_uri, key, digest,
             allowed, forbidden, unit_price, valid_until) = params
            row = next((r for r in licenses if r.get("enrollment_id") == enrollment_id), None)
            if row is not None:
                self._result = None
                self.rowcount = 0
                return
            row = {
                "id": lid, "model_id": model_id, "enrollment_id": enrollment_id,
                "face_image_uri": gate_uri, "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "pending", "vc_id": None, "created_at": NOW,
                "profile_id": None,
            }
            licenses.append(row)
            self._result = {k: row[k] for k in _LICENSE_KEYS}
            self.rowcount = 1
        elif s.startswith("insert into fm_licenses"):
            (lid, model_id, gate_uri, key, digest,
             allowed, forbidden, unit_price, valid_until, profile_id) = params
            row = {
                "id": lid, "model_id": model_id, "face_image_uri": gate_uri,
                "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "active", "vc_id": None, "created_at": NOW,
                "profile_id": profile_id,  # 개인화 프로필 참조(레거시 face 경로는 None)
            }
            licenses.append(row)
            self._result = {k: row[k] for k in _LICENSE_KEYS}
            self.rowcount = 1
        elif s.startswith("update fm_biometric_enrollments set status = 'vc_pending'"):
            enrollment_id = params[0]
            e = next((r for r in self.store["enrollments"] if r["id"] == enrollment_id), None)
            if e and e["status"] in {"license_pending", "vc_pending"}:
                e["status"] = "vc_pending"
                self._result = {"id": e["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_licenses set status = 'active'"):
            vc_id, license_id = params[:2]
            if self.store.get("final_license_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            row = next((r for r in licenses if r["id"] == license_id and r["status"] == "pending"), None)
            if row:
                row["status"] = "active"
                row["vc_id"] = vc_id
                self._result = {k: row[k] for k in _LICENSE_KEYS}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_models set status = 'verified'"):
            user_did, model_id, enrollment_id = params[:3]
            if self.store.get("final_model_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            m = next(
                (r for r in models
                 if r["id"] == model_id
                 and r.get("current_enrollment_id") == enrollment_id
                 and r["status"] in {"pending", "reverification_required"}),
                None,
            )
            if m:
                m["status"] = "verified"
                if user_did and not m.get("did"):
                    m["did"] = user_did
                self._result = {"id": m["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_biometric_enrollments set status = 'passed'"):
            vc_id, enrollment_id = params[:2]
            if self.store.get("final_enrollment_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            e = next(
                (r for r in self.store["enrollments"]
                 if r["id"] == enrollment_id and r["status"] == "vc_pending"),
                None,
            )
            if e:
                e["status"] = "passed"
                e["decision"] = "passed"
                e["vc_id"] = vc_id
                e["completed_at"] = NOW
                self._result = {"id": e["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("insert into fm_vc_revocation_jobs"):
            license_id, model_id, vc_id = params[:3]
            self.store.setdefault("revocations", {}).setdefault(
                vc_id,
                {
                    "license_id": license_id,
                    "model_id": model_id,
                    "vc_id": vc_id,
                    "status": "pending",
                },
            )
            self.rowcount = 1
        elif s.startswith("select l.id::text as id, l.model_id::text as model_id"):
            # 목록: 소유 모델 경유
            owned = {m["id"] for m in models if m["user_id"] == params[0]}
            rows = [r for r in licenses if r["model_id"] in owned]
            self._many = [{k: r[k] for k in _LICENSE_KEYS} for r in rows]
        elif s.startswith("select l.face_image_key, l.status"):
            # 게이트: license id + 소유자 조인
            lid, uid = params
            owned = {m["id"] for m in models if m["user_id"] == uid}
            r = next((x for x in licenses if x["id"] == lid and x["model_id"] in owned), None)
            self._result = (
                {"face_image_key": r["face_image_key"], "status": r["status"],
                 "license_valid_until": r["license_valid_until"]}
                if r else None
            )
        elif s.startswith("select id::text as id, status from personalization_profiles"):
            # 개인화 프로필 — 소유자 스코프 + purged 제외를 SQL 이 하므로 페이크도 동일하게
            pid, uid = params
            p = next(
                (x for x in self.store["profiles"]
                 if x["id"] == pid and x["user_id"] == uid and x["status"] != "purged"),
                None,
            )
            self._result = {"id": p["id"], "status": p["status"]} if p else None
        elif s.startswith("select r2_key, image_digest from personalization_face_photos"):
            pid = params[0]
            ph = next(
                (x for x in self.store["face_photos"]
                 if x["profile_id"] == pid and x["angle"] == "front"),
                None,
            )
            self._result = (
                {"r2_key": ph["r2_key"], "image_digest": ph["image_digest"]} if ph else None
            )
        elif s.startswith("select l.status, l.allowed_use"):
            # 공개 검증(QR) — 라이선스 + 모델 표시명 + 최신 본인확인의 birthYear
            lid = params[0]
            lic = next((x for x in licenses if x["id"] == lid), None)
            if lic is None:
                self._result = None
            else:
                m = next((x for x in models if x["id"] == lic["model_id"]), None)
                ident = next(
                    (i for i in self.store["identities"] if i["model_id"] == lic["model_id"]), None
                )
                self._result = {
                    "status": lic["status"], "allowed_use": lic["allowed_use"],
                    "forbidden_use": lic["forbidden_use"], "unit_price": lic["unit_price"],
                    "license_valid_until": lic["license_valid_until"], "vc_id": lic["vc_id"],
                    "display_name": (m or {}).get("display_name") or "",
                    "birth_year": (ident or {}).get("birth_year"),
                }
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._many or []


class FakeConn:
    def __init__(self, store):
        self.store = store
        self._snapshot = copy.deepcopy(store)

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        self._snapshot = copy.deepcopy(self.store)
        return None

    async def rollback(self):
        self.store.clear()
        self.store.update(copy.deepcopy(self._snapshot))
        winner_vc = self.store.pop("winner_after_rollback", None)
        if winner_vc:
            self.store["licenses"][0].update(status="active", vc_id=winner_vc)
            self.store["models"][0]["status"] = "verified"
            self.store["enrollments"][0].update(status="passed", vc_id=winner_vc)


@pytest.fixture()
def fm(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    fake_r2 = FakeR2Face()
    app.state.r2_face = fake_r2

    # user-1 소유의 verified 모델 1개 시드
    store = {
        "models": [
            {"id": "model-1", "user_id": "user-1", "status": "verified", "display_name": "홍*동"}
        ],
        "licenses": [],
        "enrollments": [],
        "enrollment_photos": [],
        "assets": [],
        "profiles": [],     # 개인화 프로필 {id, user_id, status}
        "face_photos": [],  # 개인화 얼굴 슬롯 {profile_id, angle, r2_key, image_digest}
        "identities": [],   # fm_identity_verifications {model_id, birth_year}
        "revocations": {},
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store, fake_r2


@pytest.fixture()
def biometric_fm(keypair, monkeypatch):
    _priv, public_key = keypair
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )
    app = create_app(make_settings(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version=EVIDENCE_VERSION,
        fm_ci_pepper="pep",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
        opendid_holder_hmac_secret="shared-secret",
    ))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.r2_face = FakeR2Face()
    store = {
        "models": [
            {
                "id": MODEL_ID, "user_id": "user-1", "status": "pending",
                "display_name": "홍*동", "assets_status": "ready",
                "current_enrollment_id": ENROLLMENT_ID, "did": None,
            }
        ],
        "licenses": [],
        "enrollments": [],
        "enrollment_photos": [],
        "assets": [],
        "profiles": [],
        "face_photos": [],
        "identities": [],
        "revocations": {},
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store, app.state.r2_face


class HolderStub:
    def __init__(self):
        self.calls = []
        self.fail_with_status = None
        self.fail_path = None
        self.malformed_issue = False
        self.wallet_status = 201
        self.register_body = {"flowAComplete": True, "userDid": "did:dev:user-1"}
        self.issue_body = {"vcId": "vc:dev:1", "userDid": "did:dev:user-1"}
        self.after_issue = None


class _HolderResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.text = "SECRET_HOLDER_BODY_WITH_CLAIMS"

    def json(self):
        return self._body


@pytest.fixture()
def holder_stub(monkeypatch):
    stub = HolderStub()

    async def fake_post(_client, **kwargs):
        stub.calls.append(kwargs)
        path = kwargs["path"]
        if stub.fail_with_status and (
            stub.fail_path is None or path.endswith(stub.fail_path)
        ):
            return _HolderResponse(stub.fail_with_status, {"error": "SECRET_CLAIM"})
        if path.endswith("/wallet"):
            return _HolderResponse(stub.wallet_status, {"walletId": "wallet-1"})
        if path.endswith("/register-did"):
            return _HolderResponse(200, stub.register_body)
        if stub.malformed_issue:
            return _HolderResponse(200, {"userDid": "did:dev:user-1", "claims": "SECRET_CLAIM"})
        if stub.after_issue:
            stub.after_issue()
        return _HolderResponse(200, stub.issue_body)

    monkeypatch.setattr(holder_client, "post", fake_post)
    return stub


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _png():
    return ("face.png", b"\x89PNG\r\n\x1a\nFAKEBYTES", "image/png")


def valid_license_body(enrollment_id=ENROLLMENT_ID):
    return {
        "enrollmentId": enrollment_id,
        "allowedUse": ["일반 여성 의류"],
        "forbiddenUse": ["성인용품"],
        "unitPrice": 10000,
        "validDays": 365,
    }


def test_license_use_categories_are_the_exact_approved_sets():
    assert facemarket.ALLOWED_BRAND_USE_CATEGORIES == (
        "일반 여성 의류",
        "남성 의류",
        "캐주얼·스트릿",
        "스포츠·애슬레저",
        "뷰티·화장품",
        "액세서리·잡화",
    )
    assert facemarket.FORBIDDEN_BRAND_USE_CATEGORIES == (
        "속옷·란제리",
        "수영복·비키니",
        "성인용품",
        "주류·담배",
        "의료·성형",
        "정치·종교",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowedUse", "광고"),
        ("forbiddenUse", "성인"),
        ("allowedUse", "정치·종교"),
        ("forbiddenUse", "일반 여성 의류"),
    ],
    ids=[
        "unknown-allowed",
        "unknown-forbidden",
        "forbidden-preset-in-allowed",
        "allowed-preset-in-forbidden",
    ],
)
def test_create_license_rejects_invalid_use_category_before_db_and_holder(
    biometric_fm, make_token, holder_stub, field, value
):
    client, store, _ = biometric_fm
    body = valid_license_body()
    body[field] = [value]

    response = client.post(
        "/v1/facemarket/licenses",
        json=body,
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


def _assert_biometric_creation_gate(response):
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "biometric_enrollment_required"


def _seed_license_pending_enrollment(store, *, enrollment_id=ENROLLMENT_ID, user_id="user-1"):
    model = store["models"][0]
    model.update(
        {
            "id": MODEL_ID,
            "user_id": user_id,
            "status": "pending",
            "assets_status": "ready",
            "current_enrollment_id": enrollment_id,
            "did": None,
        }
    )
    store["enrollments"].append(
        {
            "id": enrollment_id,
            "user_id": user_id,
            "model_id": MODEL_ID,
            "status": "license_pending",
            "decision": "passed",
            "match_policy_version": EVIDENCE_VERSION,
            "vc_id": None,
            "completed_at": None,
        }
    )
    store["enrollment_photos"].append(
        {
            "enrollment_id": enrollment_id,
            "angle": "front",
            "r2_key": APPROVED_FRONT_KEY,
            "image_digest": APPROVED_FRONT_DIGEST,
            "storage_state": "approved",
        }
    )
    store["assets"].extend(
        [
            {
                "model_id": MODEL_ID,
                "view": "face_front",
                "r2_key": FRONT_ASSET_KEY,
                "source_enrollment_id": enrollment_id,
                "evidence_version": EVIDENCE_VERSION,
            },
            {
                "model_id": MODEL_ID,
                "view": "grid_sedcard",
                "r2_key": GRID_ASSET_KEY,
                "source_enrollment_id": enrollment_id,
                "evidence_version": EVIDENCE_VERSION,
            },
        ]
    )
    return enrollment_id


def _seed_active_license(
    store,
    r2,
    *,
    license_id="44444444-4444-4444-4444-444444444444",
    model_id="model-1",
    key="private/facemarket/front.png",
    digest="sha256-seeded-face",
    allowed_use=None,
    forbidden_use=None,
    unit_price=5000,
    data=b"\x89PNG\r\n\x1a\nFAKEBYTES",
):
    row = {
        "id": license_id,
        "model_id": model_id,
        "face_image_uri": f"/v1/facemarket/licenses/{license_id}/face",
        "face_image_key": key,
        "face_image_digest": digest,
        "allowed_use": [allowed_use] if isinstance(allowed_use, str) else (allowed_use or []),
        "forbidden_use": [forbidden_use] if isinstance(forbidden_use, str) else (forbidden_use or []),
        "unit_price": unit_price,
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=365),
        "status": "active",
        "vc_id": "vc:seeded",
        "created_at": NOW,
        "profile_id": None,
    }
    store["licenses"].append(row)
    r2.objects[key] = (data, "image/png")
    return {"id": license_id, "faceImageDigest": digest}


def _seed_pending_license(
    store,
    *,
    license_id="55555555-5555-5555-5555-555555555555",
    enrollment_id=ENROLLMENT_ID,
    allowed_use=None,
    forbidden_use=None,
    unit_price=7000,
    valid_until=None,
    digest="sha256-persisted-front",
):
    row = {
        "id": license_id,
        "model_id": MODEL_ID,
        "enrollment_id": enrollment_id,
        "face_image_uri": f"/v1/facemarket/licenses/{license_id}/face",
        "face_image_key": APPROVED_FRONT_KEY,
        "face_image_digest": digest,
        "allowed_use": allowed_use or ["persisted allowed"],
        "forbidden_use": forbidden_use or ["persisted forbidden"],
        "unit_price": unit_price,
        "license_valid_until": valid_until or datetime(2027, 1, 1, tzinfo=timezone.utc),
        "status": "pending",
        "vc_id": None,
        "created_at": NOW,
        "profile_id": None,
    }
    store["licenses"].append(row)
    return row


def test_license_starts_pending_and_activates_only_after_vc(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 201, response.text
    card = response.json()
    assert card["status"] == "active"
    assert card["vcId"] == "vc:dev:1"
    assert card["faceImageUri"] == f"/v1/facemarket/licenses/{card['id']}/face"
    assert APPROVED_FRONT_KEY not in response.text
    assert store["licenses"][0]["status"] == "active"
    assert store["licenses"][0]["face_image_key"] == APPROVED_FRONT_KEY
    assert store["models"][0]["status"] == "verified"
    assert store["enrollments"][0]["status"] == "passed"
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["idempotencyKey"] == f"fm-license:{card['id']}"
    assert all(c["secret"] == "shared-secret" for c in holder_stub.calls)


def test_license_terms_are_normalized_once_for_storage_and_holder_claims(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json={
            **valid_license_body(enrollment_id),
            "allowedUse": [
                "  일반 여성 의류  ",
                "",
                "남성 의류",
                "일반 여성 의류",
            ],
            "forbiddenUse": [
                "  정치·종교  ",
                "\t",
                "의료·성형",
                "정치·종교",
            ],
        },
        headers=_auth(make_token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["allowedUse"] == ["일반 여성 의류", "남성 의류"]
    assert response.json()["forbiddenUse"] == ["정치·종교", "의료·성형"]
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["claims"]["allowedUse"] == "일반 여성 의류, 남성 의류"
    assert issue_call["payload"]["claims"]["forbiddenUse"] == "정치·종교, 의료·성형"


def test_holder_failure_leaves_everything_non_active(
    biometric_fm, make_token, holder_stub, caplog
):
    client, store, _ = biometric_fm
    holder_stub.fail_with_status = 503
    holder_stub.fail_path = "/issue-vc"
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_repeated_pending_post_reuses_license_and_holder_idempotency(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    holder_stub.fail_with_status = 503
    holder_stub.fail_path = "/issue-vc"
    enrollment_id = _seed_license_pending_enrollment(store)
    first = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    second = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert first.status_code == second.status_code == 503
    assert len(store["licenses"]) == 1
    license_id = store["licenses"][0]["id"]
    assert [
        c["payload"]["idempotencyKey"]
        for c in holder_stub.calls
        if c["path"].endswith("/issue-vc")
    ] == [f"fm-license:{license_id}", f"fm-license:{license_id}"]


@pytest.mark.parametrize(
    ("allowed_use", "forbidden_use"),
    [
        (["legacy allowed"], ["정치·종교"]),
        (["일반 여성 의류"], ["legacy forbidden"]),
    ],
    ids=["invalid-stored-allowed", "invalid-stored-forbidden"],
)
def test_pending_retry_rejects_invalid_persisted_terms_before_enrollment_or_holder(
    biometric_fm, make_token, holder_stub, allowed_use, forbidden_use
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=allowed_use,
        forbidden_use=forbidden_use,
    )

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store["enrollments"][0]["status"] == "license_pending"
    assert holder_stub.calls == []


def test_active_retry_returns_existing_card_without_reissue(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    first = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    before = len(holder_stub.calls)
    second = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(holder_stub.calls) == before


def test_enrollment_contract_rejects_stale_foreign_and_incomplete_assets(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    _seed_license_pending_enrollment(store)
    foreign = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(OTHER_ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert foreign.status_code == 404
    store["models"][0]["current_enrollment_id"] = OTHER_ENROLLMENT_ID
    stale = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert stale.status_code == 409
    store["models"][0]["current_enrollment_id"] = ENROLLMENT_ID
    store["assets"] = [a for a in store["assets"] if a["view"] != "grid_sedcard"]
    incomplete = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert incomplete.status_code == 409
    assert holder_stub.calls == []
    assert store["licenses"] == []


def test_multipart_license_request_never_creates_row(biometric_fm, make_token, holder_stub):
    client, store, _ = biometric_fm
    _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token),
    )
    assert response.status_code in {400, 415, 422}
    assert store["licenses"] == []
    assert holder_stub.calls == []


def test_malformed_holder_issue_response_stays_pending(
    biometric_fm, make_token, holder_stub, caplog
):
    client, store, _ = biometric_fm
    holder_stub.malformed_issue = True
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert "SECRET_CLAIM" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_final_activation_rejects_suspended_model_and_rolls_back(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["models"][0]["status"] = "suspended"

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] == "suspended"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert set(store["revocations"]) == {"vc:dev:1"}


@pytest.mark.parametrize(
    "mutate_evidence",
    [
        lambda store: store["enrollment_photos"][0].update(
            image_digest="sha256-replaced-front"
        ),
        lambda store: store["models"][0].update(
            current_enrollment_id=OTHER_ENROLLMENT_ID
        ),
        lambda store: store["assets"][0].update(evidence_version="replaced-policy"),
        lambda store: store["enrollments"][0].update(status="failed"),
    ],
    ids=["front-digest", "current-enrollment", "asset-version", "enrollment-status"],
)
def test_final_activation_rechecks_current_evidence_and_queues_issued_vc(
    biometric_fm, make_token, holder_stub, mutate_evidence
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    holder_stub.after_issue = lambda: mutate_evidence(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] != "verified"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_final_activation_concurrent_winner_returns_active_card(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    def concurrent_winner():
        lic = store["licenses"][0]
        lic["status"] = "active"
        lic["vc_id"] = "vc:dev:1"
        store["models"][0]["status"] = "verified"
        store["enrollments"][0]["status"] = "passed"
        store["enrollments"][0]["vc_id"] = "vc:dev:1"

    holder_stub.after_issue = concurrent_winner
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["id"] == store["licenses"][0]["id"]
    assert response.json()["status"] == "active"
    assert response.json()["vcId"] == "vc:dev:1"
    assert store["revocations"] == {}


def test_final_activation_different_concurrent_winner_queues_loser_vc(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    def concurrent_winner():
        license_row = store["licenses"][0]
        license_row["status"] = "active"
        license_row["vc_id"] = "vc:winner"
        store["models"][0]["status"] = "verified"
        store["enrollments"][0]["status"] = "passed"
        store["enrollments"][0]["vc_id"] = "vc:winner"

    holder_stub.after_issue = concurrent_winner
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201
    assert response.json()["vcId"] == "vc:winner"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_final_activation_queues_vc_after_license_and_model_are_deleted(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    deleted = {}

    def delete_activation_rows():
        deleted["license_id"] = store["licenses"][0]["id"]
        deleted["model_id"] = store["models"][0]["id"]
        store["licenses"].clear()
        store["models"].clear()

    holder_stub.after_issue = delete_activation_rows
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"] == []
    assert store["models"] == []
    assert store["revocations"]["vc:dev:1"] == {
        "license_id": deleted["license_id"],
        "model_id": deleted["model_id"],
        "vc_id": "vc:dev:1",
        "status": "pending",
    }


@pytest.mark.parametrize(
    "winner_vc,expected_revocations",
    [("vc:dev:1", set()), ("vc:other", {"vc:dev:1"})],
)
def test_final_activation_cas_race_returns_winner_and_revokes_only_loser(
    biometric_fm, make_token, holder_stub, winner_vc, expected_revocations
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["final_license_update_misses"] = True
    store["winner_after_rollback"] = winner_vc

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201
    assert response.json()["vcId"] == winner_vc
    assert set(store["revocations"]) == expected_revocations


@pytest.mark.parametrize(
    "miss_flag",
    ["final_model_update_misses", "final_enrollment_update_misses"],
)
def test_final_activation_cas_failure_rolls_back_all_updates_and_queues_vc(
    biometric_fm, make_token, holder_stub, miss_flag
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store[miss_flag] = True

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] == "pending"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert store["enrollments"][0]["vc_id"] is None
    assert set(store["revocations"]) == {"vc:dev:1"}


@pytest.mark.parametrize("register_body", [[], "not-object", None])
def test_malformed_holder_register_body_is_closed_502(
    biometric_fm, make_token, holder_stub, register_body
):
    client, store, _ = biometric_fm
    holder_stub.register_body = register_body
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"


@pytest.mark.parametrize("issue_body", [[], "not-object", None, {"vcId": ""}, {"vcId": 123}])
def test_malformed_holder_issue_body_is_closed_502(
    biometric_fm, make_token, holder_stub, issue_body, caplog
):
    client, store, _ = biometric_fm
    holder_stub.issue_body = issue_body
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_license_creation_flag_off_rejects_json_and_multipart_before_db(
    fm, make_token, holder_stub
):
    client, store, _ = fm

    json_response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(),
        headers=_auth(make_token),
    )
    multipart_response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token),
    )

    assert json_response.status_code == 409
    assert json_response.json()["error"]["code"] == "biometric_enrollment_required"
    assert multipart_response.status_code == 409
    assert multipart_response.json()["error"]["code"] == "biometric_enrollment_required"
    assert store["licenses"] == []
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


def test_malformed_enrollment_uuid_rejected_before_sql(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body("not-a-uuid"),
        headers=_auth(make_token),
    )

    assert response.status_code in {400, 404}
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


@pytest.mark.parametrize(
    ("allowed_use", "forbidden_use"),
    [
        (["legacy allowed"], ["정치·종교"]),
        (["일반 여성 의류"], ["legacy forbidden"]),
    ],
    ids=["invalid-stored-allowed", "invalid-stored-forbidden"],
)
def test_conflict_reload_rejects_invalid_persisted_terms_before_enrollment_or_holder(
    biometric_fm, make_token, holder_stub, allowed_use, forbidden_use
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=allowed_use,
        forbidden_use=forbidden_use,
    )
    store["hide_existing_license_once"] = True

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store["enrollments"][0]["status"] == "license_pending"
    assert holder_stub.calls == []


def test_conflict_reload_uses_persisted_terms_for_holder_claims(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    persisted = _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=["남성 의류"],
        forbidden_use=["주류·담배"],
        unit_price=4321,
        valid_until=datetime(2027, 2, 3, tzinfo=timezone.utc),
        digest="sha256-persisted-digest",
    )
    store["hide_existing_license_once"] = True
    holder_stub.issue_body = {}

    response = client.post(
        "/v1/facemarket/licenses",
        json={
            "enrollmentId": enrollment_id,
            "allowedUse": ["일반 여성 의류"],
            "forbiddenUse": ["정치·종교"],
            "unitPrice": 9999,
            "validDays": 30,
        },
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["idempotencyKey"] == f"fm-license:{persisted['id']}"
    assert issue_call["payload"]["claims"] == {
        "allowedUse": "남성 의류",
        "forbiddenUse": "주류·담배",
        "unitPrice": 4321,
        "licenseValidUntil": "2027-02-03",
        "faceImageDigest": "sha256-persisted-digest",
    }


def test_final_stale_transition_does_not_report_active(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    store["final_license_update_misses"] = True
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_biometric_startup_requires_holder_url(monkeypatch):
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )
    with pytest.raises(RuntimeError, match="OPENDID_HOLDER_URL"):
        create_app(make_settings(
            app_env="dev",
            facemarket_enabled=True,
            fm_biometric_enrollment_enabled=True,
            fm_oacx_contract_mode="dev-mock-v1",
            fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
            fm_liveness_confidence_threshold=90.0,
            fm_id_live_threshold=0.45,
            fm_retouched_live_threshold=0.40,
            fm_match_policy_version=EVIDENCE_VERSION,
            fm_ci_pepper="pep",
            fm_face_qc_enabled=True,
            opendid_holder_url=None,
        ))


def test_direct_face_license_request_is_rejected_before_storage(fm, make_token):
    client, store, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"allowed_use": ["광고", "상세페이지"], "forbidden_use": ["성인"],
              "unit_price": "5000", "valid_days": "30"},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0
    assert store["licenses"] == []


def test_create_license_requires_verified_model(fm, make_token):
    client, _, _ = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token, sub="user-2"),
    )
    _assert_biometric_creation_gate(r)


def test_create_license_rejects_non_image(fm, make_token):
    client, _, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0  # 저장 안 됨


def test_create_license_requires_auth(fm):
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()})
    assert r.status_code == 401


def test_list_licenses_scoped_to_owner(fm, make_token):
    client, store, r2 = fm
    _seed_active_license(store, r2)
    mine = client.get("/v1/facemarket/licenses", headers=_auth(make_token))
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    # 다른 사용자는 못 본다
    other = client.get("/v1/facemarket/licenses", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200 and other.json() == []


def test_face_gate_owner_gets_bytes_others_404(fm, make_token):
    client, store, r2 = fm
    created = _seed_active_license(store, r2)
    lid = created["id"]
    # 소유자 = 바이트 200
    ok = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/")
    assert ok.headers["cache-control"] == "no-store, private"
    assert ok.content == b"\x89PNG\r\n\x1a\nFAKEBYTES"
    # 타인 = 404(존재 노출 방지)
    other = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 404


def test_face_gate_blocks_revoked_and_expired(fm, make_token):
    client, store, r2 = fm
    created = _seed_active_license(store, r2)
    lid = created["id"]
    lic = store["licenses"][0]
    # revoked → 404
    lic["status"] = "revoked"
    assert client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token)).status_code == 404
    # active 로 되돌리고 만료시키면 → 404
    lic["status"] = "active"
    lic["license_valid_until"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token)).status_code == 404


def test_face_gate_missing_license_404(fm, make_token):
    client, _, _ = fm
    r = client.get("/v1/facemarket/licenses/00000000-0000-0000-0000-000000000000/face",
                   headers=_auth(make_token))
    assert r.status_code == 404


# ── step02: 개인화 프로필 기반 발급 ────────────────────────────
PROFILE_ID = "11111111-1111-1111-1111-111111111111"
FRONT_KEY = f"personalization/profiles/{PROFILE_ID}/faces/front.png"
FRONT_BYTES = b"\x89PNG\r\n\x1a\nPROFILEFRONT"
FRONT_DIGEST = "sha256-frontdigestvalue"


def _seed_profile(store, r2, *, status="ready", user_id="user-1", with_front=True):
    store["profiles"].append({"id": PROFILE_ID, "user_id": user_id, "status": status})
    if with_front:
        store["face_photos"].append(
            {"profile_id": PROFILE_ID, "angle": "front",
             "r2_key": FRONT_KEY, "image_digest": FRONT_DIGEST}
        )
        r2.objects[FRONT_KEY] = (FRONT_BYTES, "image/png")


def test_create_license_from_profile_references_front_slot(fm, make_token):
    """profile_id 직접 발급은 더 이상 라이선스를 만들 수 없다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": PROFILE_ID, "allowed_use": ["광고"], "unit_price": "7000"},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []
    assert len(r2.objects) == 1                     # 프로필 시드뿐 — 새 업로드 0


def test_profile_license_face_gate_streams_profile_bytes(fm, make_token):
    """얼굴 게이트는 기존 저장된 비공개 키를 소유자에게만 스트림한다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    card = _seed_active_license(store, r2, key=FRONT_KEY, digest=FRONT_DIGEST, data=FRONT_BYTES)
    ok = client.get(f"/v1/facemarket/licenses/{card['id']}/face", headers=_auth(make_token))
    assert ok.status_code == 200
    assert ok.content == FRONT_BYTES
    assert ok.headers["cache-control"] == "no-store, private"
    # 타인은 여전히 404
    other = client.get(f"/v1/facemarket/licenses/{card['id']}/face",
                       headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 404


def test_create_license_rejects_not_ready_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="draft")  # 온보딩 미완(3각도·동의·신체 중 결손)
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


def test_create_license_rejects_foreign_profile(fm, make_token):
    """타인 프로필은 '없는 프로필'과 같은 코드 — 존재 여부가 새지 않는다."""
    client, store, r2 = fm
    _seed_profile(store, r2, user_id="user-2")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    missing = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": "22222222-2222-2222-2222-222222222222"},
        headers=_auth(make_token),
    )
    assert missing.json()["error"]["code"] == "biometric_enrollment_required"  # 동일 코드
    assert store["licenses"] == []


def test_create_license_rejects_purged_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="purged")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)


def test_create_license_rejects_malformed_profile_id(fm, make_token):
    """비-uuid 는 500 아닌 400."""
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", data={"profile_id": "not-a-uuid"},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)


def test_create_license_requires_face_or_profile(fm, make_token):
    client, _, r2 = fm
    r = client.post("/v1/facemarket/licenses", data={"unit_price": "1000"},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0


def test_create_license_rejects_face_and_profile_together(fm, make_token):
    """둘 다 오면 명시적 거절 — 어느 얼굴을 라이선스했는지 모호해지면 안 된다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"profile_id": PROFILE_ID}, headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []
    assert len(r2.objects) == 1  # 프로필 얼굴만 — 업로드분 저장 0


def test_legacy_face_license_records_null_profile(fm, make_token):
    """레거시 face 1장 경로는 제거되어 라이선스 행을 만들지 않는다."""
    client, store, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


# ── step02: 공개 검증(QR — 무인증) ─────────────────────────────
_PUBLIC_KEYS = {
    "valid", "status", "allowedUse", "forbiddenUse", "unitPrice", "validUntil", "vcId", "model",
}


def _make_license(store, r2, **data):
    return _seed_active_license(
        store,
        r2,
        allowed_use=data.get("allowed_use"),
        forbidden_use=data.get("forbidden_use"),
        unit_price=int(data.get("unit_price", 5000)),
    )


def test_public_verify_exposes_only_whitelist_no_pii(fm, make_token):
    """🔴 하드룰 — 무인증 라우트에 얼굴·신원·내부키가 한 톨도 실리면 안 된다(영구 유출)."""
    client, store, r2 = fm
    store["identities"].append({"model_id": "model-1", "birth_year": "1996"})
    card = _make_license(store, r2, allowed_use="광고", forbidden_use="성인")
    r = client.get(f"/v1/facemarket/verify/{card['id']}")  # Authorization 헤더 없음
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _PUBLIC_KEYS
    assert set(body["model"]) == {"nameMasked", "age"}
    assert body["valid"] is True and body["status"] == "active"
    assert body["allowedUse"] == ["광고"] and body["forbiddenUse"] == ["성인"]
    assert body["unitPrice"] == 5000
    assert body["model"]["nameMasked"] == "홍*동"
    assert body["model"]["age"] == datetime.now(timezone.utc).year - 1996 - 1  # 보수적 하한
    # 유출 금지 값이 응답 본문 어디에도 등장하지 않는지 원문으로 확인
    raw = r.text
    for leaked in (
        card["faceImageDigest"],          # 얼굴 digest(생체 파생 고정 식별자)
        "sha256-", "face_image", "faceImage", "faceImageKey",
        "facemarket/models",              # 내부 R2 키스페이스
        "model-1", "user-1",              # model_id · user_id
        "ci_hash", "birthYear", "1996",   # CI 해시 · 생년(원문)
    ):
        assert leaked not in raw, f"공개 검증 응답에 {leaked!r} 유출"
    assert r.headers["cache-control"] == "no-store"


def test_public_verify_requires_no_auth_and_hides_nothing_else(fm, make_token):
    """무인증 도달 확인 — 인증 헤더 유무와 무관하게 같은 응답."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    anon = client.get(f"/v1/facemarket/verify/{card['id']}")
    authed = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token))
    assert anon.status_code == authed.status_code == 200
    assert anon.json() == authed.json()
    # 타인 토큰으로도 동일(공개 라우트)
    other = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200


def test_public_verify_revoked_is_invalid(fm, make_token):
    client, store, r2 = fm
    card = _make_license(store, r2)
    store["licenses"][0]["status"] = "revoked"
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "revoked"


def test_public_verify_expired_is_invalid(fm, make_token):
    """DB status='active' 라도 기간이 지났으면 status='expired' + valid=false(실시간 판정)."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    store["licenses"][0]["license_valid_until"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "expired"


def test_public_verify_age_null_when_birth_year_unusable(fm, make_token):
    """birthYear 없음/파싱 불가 → age null(성인 오통과 방지 — 안전측)."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["model"]["age"] is None  # identities 미시드
    store["identities"].append({"model_id": "model-1", "birth_year": "0101"})  # MMDD 오염
    body2 = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body2["model"]["age"] is None  # 연도 범위 밖 → null(1900+ 세 오표기 금지)


def test_public_verify_unknown_and_malformed_404(fm):
    client, _, _ = fm
    assert client.get(
        "/v1/facemarket/verify/00000000-0000-0000-0000-000000000000"
    ).status_code == 404
    assert client.get("/v1/facemarket/verify/not-a-uuid").status_code == 404


def test_storage_unavailable_503(keypair, monkeypatch, make_token):
    """multipart 생성은 저장소 확인 전에 거절되어 R2 폴백을 타지 않는다."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.r2_face = None  # 저장소 없음

    store = {
        "models": [{"id": "model-1", "user_id": "user-1", "status": "verified"}],
        "licenses": [], "enrollments": [], "enrollment_photos": [], "assets": [],
        "profiles": [], "face_photos": [], "identities": [],
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    client = TestClient(app)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers={"Authorization": f"Bearer {make_token(sub='user-1')}"})
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


def test_enabled_face_features_reject_main_bucket_fallback_in_dev():
    """개발 환경도 생체 얼굴을 공개 가능 메인 버킷에 저장하는 폴백을 허용하지 않는다."""
    settings = make_settings(
        app_env="dev",
        facemarket_enabled=True,
        r2_account_id="account",
        r2_access_key_id="access",
        r2_secret_access_key="secret",
        r2_bucket="main-bucket",
    )
    with pytest.raises(RuntimeError, match="R2_FACE_BUCKET"):
        create_app(settings)
