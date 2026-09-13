"""`POST /v1/facemarket/enrollments/{id}/id-document` 라우트 계약.

최종리뷰 I8: 신분증 카드를 실제로 받는 이 라우트에 백엔드 테스트가 **0건**이었다.
`test_facemarket_id_document.py` 는 순수 함수·키·스윕만 본다. 특히 위험한 건
`masked_confirmed` 게이트 — 이 브랜치의 **유일한 서버측 프라이버시 통제**인데, 공식
클라이언트는 그 값을 true 로 하드코딩하므로(IdDocumentStep.jsx) 이 게이트가 존재하는
이유는 오직 "공식 클라이언트가 아닌 호출자" 뿐이고, 그 경우를 아무 테스트도 안 다뤘다.

여기서 고정하는 것:
  - masked_confirmed 게이트(false 면 400 + **아무것도 저장하지 않음**)
  - identity_method_unavailable 409(경로 전체의 킬 스위치)
  - 문서 종류·MIME 화이트리스트, 12MB 상한, 빈 파일
  - 얼굴 미검출 → 저장 안 함
  - rowcount == 0 → 방금 올린 객체를 되돌리는 보상 삭제 + 409
  - put_bytes 실패 → 503
  - 정상 경로의 상태 전이(id_capture_pending → identity_pending)와 저장 컬럼
"""

import pytest

from app import facemarket_enrollment, facemarket_id_document
from app.agents.face_qc import QcFailed

JPEG = b"\xff\xd8\xff" + b"id-card-bytes"


@pytest.fixture()
def id_capture(enrollment_client_factory, monkeypatch):
    """id_capture_pending 상태의 simple_auth 등록 + 얼굴이 잡히는 crop 스텁."""

    def _make(*, face_detected=True, **settings_overrides):
        import test_facemarket_biometric_enrollment as biometric_tests

        overrides = dict(fm_identity_methods=("mid", "simple_auth"))
        overrides.update(settings_overrides)
        client, store, settings = enrollment_client_factory(**overrides)
        # conftest 의 기본 FakeR2 는 호출을 기록하지 않는다 — "거절했는데 저장했는가",
        # "보상 삭제가 자기 객체를 지웠는가" 가 이 파일의 핵심이라 기록형으로 바꾼다.
        client.app.state.r2_face = biometric_tests.FakeR2()

        def fake_crop(_data, *, settings):
            if not face_detected:
                raise facemarket_id_document.IdDocumentError("id_face_not_detected")
            return bytearray(b"face-crop")

        monkeypatch.setattr(
            facemarket_enrollment.facemarket_id_document, "crop_id_face", fake_crop
        )
        enrollment_id = None
        if "simple_auth" in settings.fm_identity_methods:
            response = client.post(
                "/v1/facemarket/enrollments",
                json={
                    "deviceId": "d" * 40,
                    "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
                    "identityMethod": "simple_auth",
                },
            )
            assert response.status_code == 201, response.text
            enrollment_id = response.json()["id"]
        return client, store, settings, enrollment_id

    return _make


def _upload(client, enrollment_id, *, masked="true", document_type="rrc",
            data=JPEG, mime="image/jpeg"):
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/id-document",
        data={"documentType": document_type, "maskedConfirmed": masked},
        files={"file": ("id.jpg", data, mime)},
    )


def _row(store, enrollment_id):
    return next(row for row in store.enrollments if row["id"] == enrollment_id)


# ── 정상 경로 ─────────────────────────────────────────────────────────────────────────


def test_upload_transitions_to_identity_pending_and_records_the_key(id_capture):
    client, store, _settings, enrollment_id = id_capture()

    response = _upload(client, enrollment_id)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "identity_pending"
    row = _row(store, enrollment_id)
    assert row["status"] == "identity_pending"
    assert row["id_document_type"] == "rrc"
    # 시도별 버전 키(/iddoc/) — 7일 배치 스윕이 prefix 로 훑는 바로 그 경로여야 한다.
    assert "/iddoc/" in row["id_document_r2_key"]
    assert client.app.state.r2_face.puts[-1][0] == row["id_document_r2_key"]


# ── 서버측 프라이버시 통제: masked_confirmed ────────────────────────────────────────


def test_masked_not_confirmed_is_rejected_and_stores_nothing(id_capture):
    """이 브랜치의 **유일한** 서버측 프라이버시 통제다.

    공식 클라이언트는 true 를 하드코딩하므로 이 게이트는 다른 호출자만을 위해 존재한다 —
    그래서 여기서 직접 못박는다. 거절이면 R2 에도 DB 에도 아무것도 남지 않아야 한다.
    """
    client, store, _settings, enrollment_id = id_capture()

    response = _upload(client, enrollment_id, masked="false")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "masking_required"
    assert client.app.state.r2_face.puts == [], "거절했는데 바이트가 저장됐다"
    row = _row(store, enrollment_id)
    assert row["status"] == "id_capture_pending"
    assert row.get("id_document_r2_key") is None


# ── 경로 킬 스위치 ────────────────────────────────────────────────────────────────────


def test_upload_rejected_when_simple_auth_is_disabled(id_capture):
    """FM_IDENTITY_METHODS 에서 simple_auth 를 빼면 이 라우트도 닫힌다(409).

    라우터 자체는 fm_biometric_enrollment_enabled 로 마운트되므로, 경로를 끄는 유일한
    수단이 이 가드다.
    """
    client, store, _settings, _ = id_capture(fm_identity_methods=("mid",))
    # 등록은 다른(mid) 경로로 만들어졌으므로 id 만 있으면 된다 — 가드가 그보다 먼저 답한다.
    response = _upload(client, "123e4567-e89b-12d3-a456-426614174000")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "identity_method_unavailable"
    assert client.app.state.r2_face.puts == []


# ── 입력 화이트리스트 ─────────────────────────────────────────────────────────────────


def test_document_type_whitelist(id_capture):
    """v1 은 주민등록증만 받는다 — 마스크가 사각형 하나뿐이라 다른 신분증의 고유식별번호는
    가려지지 않는다(최종리뷰 I12)."""
    client, _store, _settings, enrollment_id = id_capture()
    for bad in ("dl", "passport", "arc", "../../etc/passwd", ""):
        response = _upload(client, enrollment_id, document_type=bad)
        assert response.status_code in (400, 422), bad
        if response.status_code == 400:
            assert response.json()["error"]["code"] == "invalid_document_type", bad
    assert client.app.state.r2_face.puts == []


def test_mime_whitelist(id_capture):
    client, _store, _settings, enrollment_id = id_capture()
    response = _upload(client, enrollment_id, mime="application/pdf")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_type"
    assert client.app.state.r2_face.puts == []


def test_empty_upload_is_rejected(id_capture):
    client, _store, _settings, enrollment_id = id_capture()
    response = _upload(client, enrollment_id, data=b"")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_upload"


def test_size_cap(id_capture):
    client, _store, _settings, enrollment_id = id_capture()
    oversized = b"\xff\xd8\xff" + b"x" * facemarket_id_document.MAX_ID_BYTES
    response = _upload(client, enrollment_id, data=oversized)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert client.app.state.r2_face.puts == []


# ── 얼굴 게이트 ───────────────────────────────────────────────────────────────────────


def test_face_not_detected_stores_nothing(id_capture):
    """얼굴이 안 잡히면 심사할 대상이 없다 — 저장하지 말고 재촬영을 요구한다."""
    client, store, _settings, enrollment_id = id_capture(face_detected=False)

    response = _upload(client, enrollment_id)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "id_face_not_detected"
    assert client.app.state.r2_face.puts == [], "검출 실패인데 바이트가 저장됐다"
    assert _row(store, enrollment_id)["status"] == "id_capture_pending"


def test_qc_infrastructure_failure_maps_to_503(id_capture, monkeypatch):
    """가중치 부재 같은 인프라 장애는 재촬영 안내(4xx)가 아니라 503 이어야 한다."""
    client, _store, _settings, enrollment_id = id_capture()

    def boom(_data, *, settings):
        raise QcFailed("model_unavailable")

    monkeypatch.setattr(
        facemarket_enrollment.facemarket_id_document, "crop_id_face", boom
    )
    response = _upload(client, enrollment_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "qc_unavailable"
    assert client.app.state.r2_face.puts == []


# ── 저장 실패·상태 경쟁 ───────────────────────────────────────────────────────────────


def test_storage_failure_is_503_not_raw_500(id_capture, monkeypatch):
    client, store, _settings, enrollment_id = id_capture()

    def boom(*_args, **_kwargs):
        raise RuntimeError("r2 down")

    monkeypatch.setattr(client.app.state.r2_face, "put_bytes", boom)
    response = _upload(client, enrollment_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert _row(store, enrollment_id)["status"] == "id_capture_pending"


def test_wrong_state_deletes_the_object_it_just_wrote(id_capture):
    """상태 가드 UPDATE 가 0-row 면 방금 올린 객체를 되돌린다.

    이게 없으면 R2 에 참조 없는 신분증 이미지(마스킹본이지만 여전히 신분증)가 7일 스윕
    전까지 남는다. 버전 키를 쓰므로 이 삭제는 **자기 객체만** 지운다.
    """
    client, store, _settings, enrollment_id = id_capture()
    # 이미 다음 단계로 넘어간 등록 — 신분증을 받을 자리가 아니다.
    _row(store, enrollment_id)["status"] = "identity_pending"

    response = _upload(client, enrollment_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_enrollment_state"
    put_key = client.app.state.r2_face.puts[-1][0]
    assert put_key in client.app.state.r2_face.deletes, "올린 객체를 안 지웠다(고아 신분증)"


def test_other_users_enrollment_is_not_writable(id_capture):
    """남의 등록에는 쓸 수 없다 — 같은 상태 가드가 user_id 까지 본다."""
    client, store, _settings, enrollment_id = id_capture()
    _row(store, enrollment_id)["user_id"] = "someone-else"

    response = _upload(client, enrollment_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_enrollment_state"
    assert _row(store, enrollment_id).get("id_document_r2_key") is None
