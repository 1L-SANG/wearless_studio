"""관리자 모델 조회 — 검색·필터·상세의 SQL 계약."""
import asyncio
import contextlib
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else []

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows = [], list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()


MODEL_ROW = {
    "id": "m1", "display_name": "모델 A", "status": "verified", "email": "a@example.com",
    "license_count": 2, "last_settlement_at": None, "created_at": None,
}


def test_status_filter_rejects_unknown_value():
    with pytest.raises(Exception) as exc:
        facemarket_admin.validate_model_status("deleted")
    assert exc.value.detail["code"] == "invalid_status"


def test_status_filter_accepts_schema_values():
    for status in ("pending", "awaiting_confirm", "verified", "suspended"):
        assert facemarket_admin.validate_model_status(status) == status


def test_status_filter_accepts_reverification_required():
    """fm_models_status_check(20260821010100 마이그레이션)는 pending·verified·suspended
    말고 reverification_required 도 허용한다(생체 재검증 대기). 필터가 이 값을 400 으로
    걷어차면 실재하는 상태를 콘솔에서 볼 방법이 없어진다."""
    assert facemarket_admin.validate_model_status("reverification_required") == "reverification_required"


def test_list_matches_name_partially_and_email_exactly():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q="모델", status=None, limit=50))
    sql, params = conn.executed[0]
    assert "ilike" in sql, "이름 부분일치가 없다"
    assert "u.email = " in sql, "이메일 정확일치가 없다"
    assert any("%모델%" == p for p in params.values()), "부분일치 패턴이 안 붙었다"


def test_list_joins_auth_users_for_email():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q=None, status=None, limit=50))
    sql, _ = conn.executed[0]
    assert "auth.users" in sql
    assert "left join" in sql, "계정 없는 모델(플랫폼 대행 온보딩)이 목록에서 사라지면 안 된다"


def test_search_also_matches_applicant_contact_email_exactly():
    """auth.users.email 은 없을 수 있다 — 카카오 로그인은 이메일 동의가 선택이고,
    fm_model_applications.contact_email 컬럼 주석이 직접 "auth 엔 이메일 없음"이라 말한다.
    운영자가 아는 건 지원서에 적힌 contact_email 일 때가 많은데, 검색이 auth.users.email
    만 보면 그 검색은 항상 허탕이다. fm_models.user_id 와 fm_model_applications.user_id 는
    같은 auth id 다 — 신원인증·enrollment 완료가 그 세션의 user_id 로 fm_models 행을
    채우므로(facemarket.py, facemarket_enrollment.py) 이 조인은 추측이 아니라 실재하는
    관계다.
    """
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q="a@example.com", status=None, limit=50))
    sql, params = conn.executed[0]
    assert "fm_model_applications" in sql, "지원서 contact_email 을 보는 조인이 없다"
    assert "ap.contact_email = " in sql, "정확일치가 아니다 — 이메일에 부분일치를 걸면 안 된다"
    assert "ap.contact_email ilike" not in sql, "이메일에 부분일치를 걸면 안 된다"


def test_model_row_exposes_application_contact_email_as_fallback():
    """auth 이메일이 없는 모델(계정) 행에서도 지원서 이메일을 화면에 내려줘야, 목록 화면이
    그 값을 계정 칸의 폴백으로 보여줄 수 있다."""
    row = dict(MODEL_ROW)
    row["application_contact_email"] = "applicant@example.com"
    result = facemarket_admin._model_row(row)
    assert result["applicationContactEmail"] == "applicant@example.com"


def test_model_row_exposes_suspension_source_for_admin_actions():
    row = dict(MODEL_ROW, status="suspended", suspension_source="owner")
    result = facemarket_admin._model_row(row)
    assert result["suspensionSource"] == "owner"


def test_list_caps_limit():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q=None, status=None, limit=9999))
    _sql, params = conn.executed[0]
    assert params["limit"] <= facemarket_admin.MAX_LIST_LIMIT


def test_detail_returns_licenses_settlements_and_enrollment():
    conn = FakeConn([
        MODEL_ROW,
        [{"id": "l1", "status": "active", "unit_price": 14900, "license_valid_until": None, "vc_id": None}],
        [{"id": "s1", "total_amount": 10000, "chain_status": "confirmed", "created_at": None, "tx_hash": None}],
        {"id": "e1", "status": "passed", "completed_at": None},
    ])
    payload = asyncio.run(facemarket_admin.model_detail(conn, model_id="m1"))
    assert payload["model"]["displayName"] == "모델 A"
    assert payload["licenses"][0]["unitPrice"] == 14900
    assert payload["settlements"][0]["chainStatus"] == "confirmed"
    assert payload["enrollment"]["status"] == "passed"


def test_detail_404_for_unknown_model():
    conn = FakeConn([None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.model_detail(conn, model_id="nope"))
    assert exc.value.status_code == 404


def test_detail_includes_submission_profile_terms_and_consent_without_private_fields():
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    application = {
        "id": "application-private", "user_id": "user-private", "status": "approved",
        "applicant_name": "김모델", "contact_email": "model@example.com",
        "birthdate": date(2000, 1, 2), "region": "서울", "gender": "female",
        "height_cm": 170, "weight_kg": 52, "phone": "010-1234-5678",
        "experience_level": "professional", "agency_contracted": False,
        "categories": ["패션"], "portfolio_url": "https://example.com/portfolio",
        "sns_url": "https://example.com/sns", "bio": "첫 줄\n마지막 줄",
        "photo_keys": {"profile": "private/photo.webp"}, "profile_image_r2_key": "private/photo.webp",
        "created_at": now, "reviewed_at": now, "reject_reason": None,
        "identity_mismatch_count": 0, "privacy_consent_version": "privacy-v1",
        "privacy_consented_at": now, "attestations": {"photosAreMine": True, "digest": "private"},
    }
    profile = {"height_cm": Decimal("170.5"), "weight_kg": Decimal("52.5"),
               "bust_cm": Decimal("85"), "waist_cm": Decimal("60"), "hip_cm": Decimal("90"),
               "body_type": "slim", "body_type_custom": "직접 적은 체형", "gender": "female",
               "age_range": "20s", "skin_tone": "밝음", "hair": "긴 머리", "clothing_size": "S",
               "hair_color": "brown", "hair_length": "long", "eye_color": "brown",
               "id": "profile-private", "image_digest": "digest-private"}
    consent = {"biometric_version": "bio-v1", "terms_version": "terms-v1",
               "overseas_version": "notice-v1", "accepted_at": now, "user_id": "private"}
    conn = FakeConn([
        dict(MODEL_ROW, gender="female", height_bucket="f_170_175", body_type="regular"),
        [{"id": "l1", "status": "active", "unit_price": 7000, "license_valid_until": None,
          "vc_id": "vc-visible", "created_at": now, "allowed_use": ["일반 패션"],
          "opt_location_cuts": True, "opt_lookbook_person_replace": False,
          "opt_consent_version": "opt-v1", "opt_consented_at": now,
          "face_image_key": "private-face", "face_image_digest": "private-digest"}],
        [], {"id": "e1", "status": "passed", "completed_at": now, "photo_count": 18,
             "body_type": "slim", "height_bucket": "f_170_175", "device_digest": "private-device"},
        application, profile, [consent],
    ])
    payload = asyncio.run(facemarket_admin.model_detail(conn, model_id="m1"))
    assert payload["application"]["bio"] == "첫 줄\n마지막 줄"
    assert payload["application"]["photoUris"] == {
        "profile": "/v1/facemarket/admin/applications/application-private/profile-image?kind=profile"
    }
    assert payload["application"]["privacyConsentVersion"] == "privacy-v1"
    assert payload["application"]["attestations"] == {"photosAreMine": True}
    assert "id" not in payload["application"] and "userId" not in payload["application"]
    assert set(payload["application"]) == {
        "status", "rejectReason", "identityMismatchCount", "hasProfileImage", "createdAt", "reviewedAt",
        "contactEmail", "applicantName", "birthdate", "region", "gender", "heightCm", "weightKg", "phone",
        "experienceLevel", "agencyContracted", "categories", "portfolioUrl", "snsUrl", "bio", "photoKinds",
        "photoUris", "attestations", "privacyConsentVersion", "privacyConsentedAt",
    }
    assert payload["profile"] == {
        "heightCm": 170.5, "weightKg": 52.5, "bustCm": 85.0, "waistCm": 60.0, "hipCm": 90.0,
        "bodyType": "slim", "bodyTypeCustom": "직접 적은 체형", "gender": "female", "ageRange": "20s",
        "skinTone": "밝음", "hair": "긴 머리", "clothingSize": "S",
        "hairColor": "brown", "hairLength": "long", "eyeColor": "brown",
    }
    assert payload["model"]["gender"] == "female"
    assert payload["enrollment"]["photoCount"] == 18
    assert payload["enrollment"]["bodyType"] == "slim"
    assert payload["licenses"][0]["allowedUse"] == ["일반 패션"]
    assert payload["licenses"][0]["optLocationCuts"] is True
    assert payload["licenses"][0]["optConsentVersion"] == "opt-v1"
    assert payload["consentEvents"] == [{"biometricVersion": "bio-v1", "termsVersion": "terms-v1",
                                          "overseasVersion": "notice-v1", "acceptedAt": now.isoformat()}]
    import json
    encoded = json.dumps(payload)
    for secret in ["private/photo.webp", "private-face", "private-digest", "private-device", "profile-private", "digest-private"]:
        assert secret not in encoded
    assert all(not sql.lower().startswith(("insert", "update", "delete")) for sql, _ in conn.executed)
    assert "application_id" in conn.executed[4][0]
    assert "current_enrollment_id" in conn.executed[4][0]
    assert "fm_enrollment_consent_events" in conn.executed[6][0]


def test_detail_handles_missing_submission_profile_and_consent():
    conn = FakeConn([MODEL_ROW, [], [], None, None, None, []])
    payload = asyncio.run(facemarket_admin.model_detail(conn, model_id="m1"))
    assert payload["application"] is None
    assert payload["profile"] is None
    assert payload["consentEvents"] == []


def test_application_list_card_exposes_consent_without_storage_evidence():
    from app.facemarket_applications import _admin_card

    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    payload = _admin_card({
        "id": "a1", "user_id": "u1", "status": "approved", "applicant_name": "모델",
        "birthdate": date(2000, 1, 2), "contact_email": "model@example.com", "created_at": now,
        "privacy_consent_version": "privacy-v1", "privacy_consented_at": now,
        "profile_image_r2_key": "private-photo", "device_digest": "private-device",
    }).model_dump(mode="json", by_alias=True)
    assert payload["privacyConsentVersion"] == "privacy-v1"
    assert payload["privacyConsentedAt"] == "2026-09-12T00:00:00Z"
    assert "private-photo" not in str(payload)
    assert "private-device" not in str(payload)
