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
  - 정상 경로의 상태 전이(id_capture_pending → photos_pending, Task6 순서 뒤집기 이후)와
    저장 컬럼
  - 마스킹 기하 검증(FM_ID_MASK_VERIFY) — enforce 는 미검출을 422 로 거부하고 아무것도
    저장하지 않는다, shadow(기본)는 같은 업로드를 통과시키되 판정을 mean/stddev 와
    함께 반드시 로그로 남긴다, off 는 검사도 로그도 없다. 순수 함수 테스트만으로는
    이 검사가 라우트에 실제로 배선됐는지, 로그가 실제로 나가는지 알 수 없다.
"""

import logging
from contextlib import contextmanager

import cv2
import numpy as np
import pytest

from app import facemarket_enrollment, facemarket_id_document
from app.agents.face_qc import QcFailed
from app.facemarket_id_mask_verify import rrn_rect_in_frame

JPEG = b"\xff\xd8\xff" + b"id-card-bytes"


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):  # noqa: D102
        self.records.append(record)


@contextmanager
def _capture_enrollment_logs():
    """`app.facemarket_enrollment` 로거에 직접 붙는다.

    test_http_error_log.py 와 같은 이유(create_app 이 _configure_logging 으로 root
    핸들러를 통째로 교체하므로 pytest caplog(root 에 붙는다)는 client 픽스처 생성
    뒤에 지워진다) — 이름 있는 로거에 직접 붙이면 그 교체와 무관하다."""
    handler = _LogCapture()
    logger = logging.getLogger("app.facemarket_enrollment")
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)


def _id_card_bytes(masked: bool) -> bytes:
    """가이드를 채운 카드 한 장 — test_facemarket_id_mask_verify.py 의 `_card()` 와 동일한
    구성. masked=True 면 rrn_rect_in_frame() 이 계산한 자리를 단색으로 덮는다.

    RRN_REGION 비율을 프레임 전체에 바로 적용하면(fix round 1 이전의 버그) 서버가
    보는 자리와 다른 곳을 칠하게 된다 — 가이드 박스 "안에서" 적용하는 rrn_rect_in_frame
    을 그대로 써야 한다."""
    img = np.full((540, 856, 3), 200, np.uint8)
    img[::7, :] = 120
    if masked:
        r = rrn_rect_in_frame(856, 540)
        img[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]] = 17
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


@pytest.fixture()
def id_capture(enrollment_client_factory, monkeypatch):
    """id_capture_pending 상태의 simple_auth 등록 + 얼굴이 잡히는 crop 스텁.

    Task6(순서 뒤집기) 이후 생성 직후 상태는 identity_pending 이다(간편인증이 촬영보다
    먼저다) — 본인인증(POST /identity)을 통과해야 id_capture_pending 에 닿는다. 이 파일은
    /identity 라우트 자체가 관심사가 아니므로(신분증 업로드 라우트만 본다), 그 전이를
    직접 시뮬레이션하지 않고 도달한 상태만 밀어 넣는다.

    리뷰 finding(critical): id_capture_pending 에 왔다는 건 새 순서에서는 항상 본인확인이
    끝났다는 뜻이라, identity_ci_hash 도 함께 채워 둔다 — 실제 verify_enrollment_identity
    가 그렇게 하기 때문이다. 이걸 안 채우면 이 파일의 모든 "정상 경로" 테스트가
    test_legacy_id_capture_pending_without_identity_is_rejected 가 잡는 새 불변조건
    가드에 걸려 조용히 오탐(false negative)이 된다.
    """

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
            row = next(item for item in store.enrollments if item["id"] == enrollment_id)
            assert row["status"] == "identity_pending"
            row["status"] = "id_capture_pending"
            row["identity_ci_hash"] = "test-ci-hash-" + enrollment_id
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


def test_upload_transitions_to_photos_pending_and_records_the_key(id_capture):
    client, store, _settings, enrollment_id = id_capture()

    response = _upload(client, enrollment_id)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "photos_pending"
    row = _row(store, enrollment_id)
    assert row["status"] == "photos_pending"
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
    # 이미 다음 단계로 넘어간 등록 — 신분증을 받을 자리가 아니다. Task6 순서 뒤집기
    # 이후 id_capture_pending 다음 단계는 photos_pending 이다(identity_pending 은
    # 오히려 이보다 앞선 단계다).
    _row(store, enrollment_id)["status"] = "photos_pending"

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


# ── 본인확인 불변조건(리뷰 finding: critical) ────────────────────────────────────────
#
# Task6(순서 뒤집기) 이후 id_capture_pending 은 "본인확인이 이미 끝났다"는 뜻으로
# 의미가 뒤집혔다. 순서 뒤집기 배포 이전에 이 상태에서 멈춰 있던 simple_auth 행은
# (구버전 순서에서) 아직 본인확인을 거치지 않았으므로 identity_ci_hash 가 NULL 이다.
# 같은 상태값이 정반대 의미로 재사용되는 이 틈을 막지 않으면, 그런 행이 이 라우트를
# 그대로 통과해 본인확인(CI 교차계정 충돌 검사 포함)을 건너뛰고 photos_pending 으로
# 넘어간다 — 조용한 신원 검증 우회다.


def test_legacy_id_capture_pending_without_identity_is_rejected(id_capture):
    """구버전 순서로 남은 행(본인확인 미완료, identity_ci_hash NULL)은 거부되어야 한다.

    통과시키면 이후 바인딩 시점에 identity_ci_hash 가 NULL 인 채로
    fm_identity_verifications 삽입이 NOT NULL 제약 위반으로 죽는다(처리되지 않는 예외) —
    그전에 여기서, 진단 가능한 별도 에러 코드로 막는다.
    """
    client, store, _settings, enrollment_id = id_capture()
    # id_capture() 픽스처는 정상 흐름(본인확인 완료)을 시뮬레이션하며 identity_ci_hash 를
    # 채운다 — 여기서만 구버전 상태(본인확인 미완료)를 흉내내려 되돌린다.
    _row(store, enrollment_id)["identity_ci_hash"] = None

    response = _upload(client, enrollment_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "identity_not_verified"
    row = _row(store, enrollment_id)
    assert row["status"] == "id_capture_pending", "본인확인 없이 photos_pending 으로 새면 안 된다"
    assert row.get("id_document_r2_key") is None
    put_key = client.app.state.r2_face.puts[-1][0]
    assert put_key in client.app.state.r2_face.deletes, "올린 객체를 안 지웠다(고아 신분증)"


def test_upload_succeeds_when_identity_already_verified(id_capture):
    """가드의 반대쪽 확인 — identity_ci_hash 가 채워진 정상적인 새 순서 경로는 그대로
    통과해야 한다(가드가 무조건 거부만 하는 게 아님을 못박는다)."""
    client, store, _settings, enrollment_id = id_capture()
    assert _row(store, enrollment_id)["identity_ci_hash"] is not None

    response = _upload(client, enrollment_id)

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "photos_pending"


# ── 마스킹 기하 검증(FM_ID_MASK_VERIFY) ──────────────────────────────────────────────
#
# 순수 함수(mask_is_applied)는 test_facemarket_id_mask_verify.py 가 이미 잡는다. 여기서
# 확인할 건 그 함수가 이 라우트에 실제로, 얼굴 크롭 게이트 뒤·R2 put_bytes 앞에
# 배선됐는가다 — 잘못된 파일에 배선하거나 아예 안 배선해도 순수 함수 테스트는 계속
# 초록불이다.


def test_enforce_rejects_unmasked_upload_and_stores_nothing(id_capture):
    """enforce 에서 주민번호 자리가 안 덮인 사진은 422 로 거부되고 아무것도 저장하지
    않는다 — R2 put_bytes 보다 먼저 걸려야 고아 신분증 객체가 안 남는다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="enforce")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "id_mask_not_applied"
    assert client.app.state.r2_face.puts == [], "거부했는데 바이트가 저장됐다"
    assert _row(store, enrollment_id)["status"] == "id_capture_pending"


def test_shadow_lets_the_same_unmasked_upload_through(id_capture):
    """shadow(기본값)는 판정을 로그로만 남기고 절대 거부하지 않는다 — 임계가 캘리브
    전이라 아직 실사용자를 막을 근거가 없다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="shadow")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "photos_pending"
    row = _row(store, enrollment_id)
    assert row["status"] == "photos_pending"
    assert client.app.state.r2_face.puts, "정상 경로인데 저장이 안 됐다"


def test_shadow_logs_the_verdict_with_metrics(id_capture):
    """Correction 4 의 핵심: shadow 는 거부하지 않는 대신 판정을 반드시 기록해야
    임계 캘리브 근거가 된다(리뷰 finding 2). 분기만 맞고 로그 호출이 빠지는 리팩터를
    이 테스트가 잡는다 — 기존 테스트들은 거부 여부만 보고 로그 유무는 안 봤다.

    pytest caplog(root 로거) 대신 `app.facemarket_enrollment` 로거에 직접 핸들러를
    붙인다 — client 생성이 create_app()→_configure_logging() 을 거치며 root 핸들러를
    통째로 교체해 caplog 를 지운다(test_http_error_log.py 의 같은 함정)."""
    client, _store, _settings, enrollment_id = id_capture(fm_id_mask_verify="shadow")

    with _capture_enrollment_logs() as records:
        response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 201, response.text
    verdicts = [r for r in records if r.getMessage() == "facemarket_id_mask_verify_verdict"]
    assert len(verdicts) == 1, "판정 로그가 정확히 한 번 남아야 한다"
    record = verdicts[0]
    assert record.enrollment_id == enrollment_id
    assert record.fm_id_mask_verify == "shadow"
    assert record.mask_applied is False, "덮이지 않은 사진이니 판정은 False 여야 한다"
    assert hasattr(record, "mean") and hasattr(record, "stddev"), \
        "캘리브 근거인 mean/stddev 가 로그에 없다"


def test_off_skips_the_check_entirely(id_capture, monkeypatch):
    """off 면 mask_is_applied 자체를 호출하지 않는다 — 검사 대상 사진이 뭐든(마스킹
    함수가 터지더라도) 업로드는 영향받지 않는다. 검사를 안 하니 판정 로그도 없어야
    한다(리뷰 finding 2 — off 에서 로그가 남으면 검사 자체는 스킵했다는 주장과 모순)."""
    client, _store, _settings, enrollment_id = id_capture(fm_id_mask_verify="off")

    def boom(_data):
        raise AssertionError("off 인데 mask_is_applied 가 호출됐다")

    monkeypatch.setattr(facemarket_enrollment.facemarket_id_mask_verify, "mask_is_applied", boom)

    with _capture_enrollment_logs() as records:
        response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 201, response.text
    assert not any(
        r.getMessage() == "facemarket_id_mask_verify_verdict" for r in records
    ), "off 인데 판정 로그가 남았다"


# ── Task9: mask_mode 영속화 ──────────────────────────────────────────────────────────
#
# 검증 자체(mask_is_applied)의 판정을 그대로 옮겨 적는 것뿐이지만, 옮겨 적는 과정에서
# off 를 'auto'/'manual' 어느 쪽으로도 잘못 채우거나, enforce 의 거부 경로에서 판정 없이
# 저장해버리는 실수가 나기 쉽다 — 그 세 갈래(auto/manual/None)를 각각 관찰 가능한
# row 상태로 고정한다.


def test_shadow_pass_records_auto(id_capture):
    """기하 검증을 통과하면(마스킹된 카드) 'auto' 를 남긴다 — 사람이 아니라 서버 검증이
    통과시켰다는 뜻이라 심사자가 더 볼 필요가 없다는 신호다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="shadow")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=True))

    assert response.status_code == 201, response.text
    assert _row(store, enrollment_id)["mask_mode"] == "auto"


def test_shadow_fail_records_manual(id_capture):
    """기하 검증을 통과하지 못하면(마스킹 안 된 카드) 'manual' 을 남긴다 — shadow 라
    업로드 자체는 막지 않지만, 이 값이 있어야 심사자가 이 건을 더 꼼꼼히 봐야 한다는
    걸 안다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="shadow")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 201, response.text
    assert _row(store, enrollment_id)["mask_mode"] == "manual"


def test_enforce_pass_records_auto(id_capture):
    """enforce 에서도(거부 경로가 아니라 통과 경로) 판정은 그대로 'auto' 로 남는다 —
    enforce 여부는 업로드를 막을지 말지를 정할 뿐, 기록하는 값 자체를 바꾸지 않는다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="enforce")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=True))

    assert response.status_code == 201, response.text
    assert _row(store, enrollment_id)["mask_mode"] == "auto"


def test_off_leaves_mask_mode_null(id_capture):
    """off 는 검사 자체를 안 하므로 판정이 없다 — 'auto'(검사 안 한 걸 통과로) 도
    'manual'(통과 못 한 걸로) 도 거짓 기록이라 NULL 로 남겨야 한다."""
    client, store, _settings, enrollment_id = id_capture(fm_id_mask_verify="off")

    response = _upload(client, enrollment_id, data=_id_card_bytes(masked=False))

    assert response.status_code == 201, response.text
    assert _row(store, enrollment_id)["mask_mode"] is None


# ── enforce 가 아직 안전하지 않다는 경고(최종리뷰 I1) ────────────────────────────────
#
# 이 검사는 client 가 camera/file/manual 어느 모드로 찍었는지 안 보고 업로드마다 돈다
# — file·manual 사진은 가이드로 찍히지 않아 구조적으로 이 기하 검사를 통과할 수 없다.
# 그래서 enforce 로 올리면 카메라를 못 쓰는 사용자는 등록 자체가 영영 불가능해진다.
# 서버 쪽에 진짜 수동 경로 허용(예: 연속 실패 N회는 통과)이 생기기 전까지는 아무도
# 이 값을 enforce 로 올리면 안 된다 — 그 경고가 플래그 정의 옆(env 하나로 바꿀 수 있는
# 바로 그 자리)에서 사라지지 않게 문구 존재를 고정한다. 값 자체(shadow)는
# test_deploy_manifest_qc_flags.py 류가 이미 다루므로 여기서는 경고 문구만 본다.


def test_enforce_warning_survives_in_manifest_and_config():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    manifest = (root / "copilot/api/manifest.yml").read_text()
    config = (root / "server/app/config.py").read_text()

    warning = "수동 경로 허용을 먼저 만들 것"
    assert warning in manifest, (
        "copilot/api/manifest.yml 의 FM_ID_MASK_VERIFY 주석에서 enforce 경고가 사라졌다 — "
        "이게 없으면 누군가 env 값만 바꿔 enforce 를 켜고, 카메라를 못 쓰는 사용자는 "
        "등록이 통째로 막힌다."
    )
    assert warning in config, (
        "server/app/config.py 의 fm_id_mask_verify 주석에서 enforce 경고가 사라졌다."
    )
