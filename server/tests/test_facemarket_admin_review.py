"""Task7: 간편인증(simple_auth) 경로의 advisory 매칭 + review_pending 전이.

이 파일은 `enrollment_client_factory` 만 쓴다(컨트롤러 룰링 — `admin_client` 는 Task8
소관, 여기서 만들지 않는다). 등록 행 조립은 test_facemarket_biometric_enrollment.py 의
EnrollmentStore/FakeCursor 를 그대로 재사용하되, 그 파일 자체는 **손대지 않는다**(mid
경로 회귀 방어망이라는 그 파일의 관례를 따른다). FakeCursor.execute 가 아직 모르는
SQL(신규 identity_method/id_document_r2_key 컬럼을 얹은 완료-체크 SELECT, review_pending
전이 UPDATE, simple_auth 전용 match_scores UPDATE) 세 가지만 이 파일 안에서 얇게 감싸
처리한다 — mid 전용 코드 경로는 이 새 SQL 문 자체를 절대 실행하지 않으므로(리뷰 필요
없을 때 mid 는 새 UPDATE 를 안 탄다), 기존 4600+ 테스트 회귀 스위트는 이 래핑과 무관하게
그대로 초록이다.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import facemarket_enrollment, facemarket_id_document
from app.agents.face_qc import QcFailed

ANGLES = facemarket_enrollment.ANGLES
PORTRAIT_HEX = (b"\xff\xd8\xff" + b"portrait-bytes").hex()


class ScriptedFaceQc:
    """각도 순서(ANGLES)대로 정확히 한 번씩만 불린다는 전제로 점수를 내준다.

    라이브니스 off + id_live 페어 없음(이 파일의 모든 테스트가 fm_liveness_enabled=False
    로 돈다) 이라 process_enrollment_completion 의 photo_items 루프가 유일한 호출자이고,
    그 루프는 _initial_completion_checks 가 반환한 photos 순서(front/angle45/side, SQL의
    ORDER BY 로 고정)를 그대로 따른다.
    """

    def __init__(self, scores: dict[str, float] | None = None, skip: tuple[str, ...] = ()):
        self._scores = dict(scores or {})
        self._skip = set(skip)
        self._pending = list(ANGLES)

    def one_to_one_similarity(self, _reference, _candidate):
        angle = self._pending.pop(0)
        if angle in self._skip:
            raise QcFailed("no_face_detected")
        return self._scores[angle]


def _json_value(value):
    return getattr(value, "obj", value)


def _wrap_fake_cursor(monkeypatch, biometric_tests):
    """factory 가 조립한 FakeCursor 에 이 태스크가 새로 필요로 하는 SQL 세 가지를 얹는다."""
    FakeCursor = biometric_tests.FakeCursor
    original_execute = FakeCursor.execute

    async def patched_execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()

        if query.startswith(
            "update fm_biometric_enrollments set status = 'review_pending'"
        ):
            match_scores, enrollment_id = params
            row = next(
                item for item in self.store.enrollments if item["id"] == enrollment_id
            )
            row.update(
                status="review_pending",
                review_status="pending",
                match_scores=_json_value(match_scores),
            )
            self.result = None
            self.many = []
            return

        if query == "update fm_biometric_enrollments set match_scores = %s where id = %s":
            match_scores, enrollment_id = params
            row = next(
                item for item in self.store.enrollments if item["id"] == enrollment_id
            )
            row["match_scores"] = _json_value(match_scores)
            self.result = None
            self.many = []
            return

        # 완료-체크 SELECT 의 두 신규 컬럼(identity_method·id_document_r2_key)을 여기서
        # 손으로 채워 넣던 블록은 삭제했다 — 그 주입이 있는 한 프로덕션 SELECT 가 그
        # 컬럼들을 실제로 뽑는지 아무도 확인하지 못하고, 실제로 안 뽑고 있었다(최종리뷰 C1:
        # 간편인증 완료 경로가 프로덕션에서만 죽어 있었다). 이제 FakeCursor 가
        # `completion_check_columns()`(프로덕션 SQL 파싱)로 투영하므로 여기서 채울 게 없다.
        await original_execute(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", patched_execute)


def _seed_ready_enrollment(
    store,
    *,
    identity_method="simple_auth",
    id_document_r2_key=None,
    user_id="user-1",
):
    """`liveness_pending`(사진 3장 통과, 완료 대기) 상태의 등록 행 + 통과한 사진 3장을 만든다.

    실제 흐름은 신분증 촬영/본인확인/사진업로드 여러 라우트를 거치지만, 이 테스트는
    /complete 하나만 검증하므로 그 앞단은 건너뛰고 store 를 직접 채운다 —
    test_facemarket_biometric_enrollment.py 의 `_fast_forward_identity`/
    `create_ready_enrollment` 와 같은 결의 지름길이다.
    """
    enrollment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    store.enrollments.append(
        {
            "id": enrollment_id,
            "user_id": user_id,
            "model_id": None,
            "device_digest": "device-digest-" + enrollment_id[:8],
            "consent_version": "2026-08-v2",
            "status": "liveness_pending",
            "identity_method": identity_method,
            "review_status": None,
            "decision": None,
            "reason": None,
            "provider_versions": {},
            "cooldown_until": None,
            "expires_at": now + timedelta(hours=1),
            "completed_at": None,
            "raw_deletion_evidence": {},
            "identity_ci_hash": "identity-ci-hash-1",
            "identity_name_masked": "홍*동",
            "identity_birth_year": "1990",
            "identity_tx_digest": "cxsha256:tx-1",
            "identity_contract_version": (
                "simple-auth-v1" if identity_method == "simple_auth" else "dev-mock-v1"
            ),
            "application_id": None,
            "id_document_r2_key": id_document_r2_key,
            "liveness_session_digest": None,
            "profile_image_r2_key": None,
            "height_bucket": None,
            "body_type": None,
        }
    )
    for angle in ANGLES:
        store.photos.append(
            {
                "enrollment_id": enrollment_id,
                "angle": angle,
                "r2_key": f"private/{enrollment_id}/{angle}.jpg",
                "image_digest": f"sha256-{angle}",
                "mime_type": "image/jpeg",
                "byte_size": 10,
                "qc_status": "passed",
                "storage_state": "quarantine",
                "uploaded_at": now,
            }
        )
    return enrollment_id


def _latest_row(store, enrollment_id):
    return next(item for item in store.enrollments if item["id"] == enrollment_id)


def _setup(
    enrollment_client_factory,
    monkeypatch,
    *,
    scores=None,
    skip=(),
    **settings_overrides,
):
    import test_facemarket_biometric_enrollment as biometric_tests

    overrides = dict(
        fm_liveness_enabled=False,
        fm_retouched_live_threshold=0.15,
        fm_side_live_threshold=0.10,
    )
    overrides.update(settings_overrides)
    client, store, settings = enrollment_client_factory(**overrides)
    _wrap_fake_cursor(monkeypatch, biometric_tests)
    monkeypatch.setattr(
        facemarket_enrollment.facemarket_id_document,
        "crop_id_face",
        lambda _data, *, settings: bytearray(b"id-crop-bytes"),
    )
    qc = ScriptedFaceQc(scores=scores, skip=skip)
    monkeypatch.setattr(
        facemarket_enrollment, "load_face_qc", lambda _settings, *, required=False: qc
    )
    return client, store, settings


# ── review_pending 전이 ──────────────────────────────────────────────────────────────

def test_simple_auth_stops_at_review_pending(enrollment_client_factory, monkeypatch):
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.9, "angle45": 0.85, "side": 0.8},
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is False
    assert body["status"] == "review_pending"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "review_pending"
    assert row["review_status"] == "pending"
    # 자산 빌드가 시작되면 안 된다 — 심사 안 된 얼굴이 생성 파이프라인에 들어가면 안 된다.
    assert store.jobs == []
    assert row["model_id"] is None


def test_simple_auth_records_advisory_scores_without_blocking(
    enrollment_client_factory, monkeypatch
):
    """임계 미달이어도 face_match_failed 로 끝나지 않고 심사로 넘어간다.

    advisory 가 enforce 로 되돌아가면(=이 테스트가 잡아야 할 회귀) 첫 각도(front, 0.04 <
    0.15)에서 _assert_match 가 즉시 face_match_failed 를 던져 status 가 'failed' 로 끝난다
    — 아래 status/review_status/scores 단언이 전부 깨진다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.04, "angle45": 0.05, "side": 0.02},
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.status_code == 200, response.text
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "review_pending"
    assert row["review_status"] == "pending"
    scores = row["match_scores"]
    assert scores["anchor"] == "id_document_crop"
    assert scores["scores"]["front"] == pytest.approx(0.04)
    assert scores["scores"]["angle45"] == pytest.approx(0.05)
    assert scores["scores"]["side"] == pytest.approx(0.02)
    assert set(scores["belowThreshold"]) == {"front", "angle45", "side"}
    assert scores["skipped"] == []
    # raw 코사인 그대로 — 백분율(4.0 등)로 저장되면 안 된다.
    assert all(0.0 <= value <= 1.0 for value in scores["scores"].values())
    # 임계 스냅샷도 저장돼야 한다 — 나중에 임계가 재캘리브돼도 이 리뷰 카드는 "그때
    # 기준"을 보여줘야 한다. _setup() 의 fm_retouched_live_threshold=0.15 /
    # fm_side_live_threshold=0.10 을 그대로 반영해야 한다.
    assert scores["thresholds"] == {"front": 0.15, "angle45": 0.15, "side": 0.10}


def test_simple_auth_all_angles_undetected_blocks_instead_of_review(
    enrollment_client_factory, monkeypatch
):
    """세 각도 전부 얼굴 미검출이면 심사할 근거가 없다 — 그때만 차단한다."""
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        skip=("front", "angle45", "side"),
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.json()["reason"] == "face_match_failed"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "failed"


def test_simple_auth_partial_detection_still_reviews(enrollment_client_factory, monkeypatch):
    """한 각도만 얼굴 미검출이어도(다른 각도는 점수가 났으면) 심사로 넘어간다.

    가드가 `if advisory and not scores`(전부 미검출일 때만 차단) 대신 `if advisory and
    skipped`(하나라도 스킵되면 차단)로 퇴행하면, side 만 스킵되고 front/angle45 는 점수가
    났는데도 review_pending 이 아니라 실패로 끝나야 하므로 이 테스트가 잡는다. 관리자가
    "이 각도는 안 보였다"(skipped)와 "이 각도는 점수가 나빴다"(belowThreshold)를 구분해서
    볼 수 있어야 하므로 그 두 목록의 배타성도 함께 못박는다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.9, "angle45": 0.05},
        skip=("side",),
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.status_code == 200, response.text
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "review_pending"
    assert row["review_status"] == "pending"
    assert store.jobs == []
    scores = row["match_scores"]
    assert scores["skipped"] == ["side"]
    assert "side" not in scores["scores"]
    assert scores["scores"]["front"] == pytest.approx(0.9)
    assert scores["scores"]["angle45"] == pytest.approx(0.05)
    # "안 보였다"(skipped) 와 "점수가 나빴다"(belowThreshold) 는 서로 배타적이어야 한다 —
    # 관리자 심사 카드가 이 둘을 구분해서 보여줄 수 있는 근거.
    assert "angle45" in scores["belowThreshold"]
    assert "front" not in scores["belowThreshold"]
    assert "side" not in scores["belowThreshold"]


def test_simple_auth_id_document_buffer_wiped_on_success(
    enrollment_client_factory, monkeypatch
):
    """ID 크롭 앵커 원본(신분증 촬영본 전체)이 성공 경로에서 확실히 지워진다.

    `finally` 의 wipe 줄을 지우거나 엉뚱한 변수를 지우면(예: portrait 만 지우고
    id_document_buffer 는 빠뜨리면) 이 테스트가 실패한다 — id_document_buffer 로
    crop_id_face 에 넘어간 바로 그 객체(id() 동일성)가 wipe_bytearray 호출 인자에
    나타나야 통과한다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.9, "angle45": 0.85, "side": 0.8},
    )
    captured = {}

    def fake_crop(data, *, settings):
        captured["id_document_buffer_id"] = id(data)
        return bytearray(b"id-crop-bytes")

    monkeypatch.setattr(
        facemarket_enrollment.facemarket_id_document, "crop_id_face", fake_crop
    )
    wiped_ids = []
    original_wipe = facemarket_enrollment.cx_identity.wipe_bytearray

    def spy_wipe(value):
        if value is not None:
            wiped_ids.append(id(value))
        original_wipe(value)

    monkeypatch.setattr(facemarket_enrollment.cx_identity, "wipe_bytearray", spy_wipe)

    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.status_code == 200, response.text
    assert "id_document_buffer_id" in captured
    assert captured["id_document_buffer_id"] in wiped_ids


def test_simple_auth_id_document_buffer_wiped_on_face_not_detected(
    enrollment_client_factory, monkeypatch
):
    """crop_id_face 가 실패해도(id_face_not_detected) 이미 읽어들인 신분증 원본은 지운다.

    성공 경로만 지우는 `finally` 는 이 테스트가 실제로 잡아야 할 버그다 — 완료 요청이
    실패로 끝나도 원시 생체 바이트가 메모리에 남아 있으면 안 된다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
    )
    captured = {}

    def failing_crop(data, *, settings):
        captured["id_document_buffer_id"] = id(data)
        raise facemarket_id_document.IdDocumentError("id_face_not_detected")

    monkeypatch.setattr(
        facemarket_enrollment.facemarket_id_document, "crop_id_face", failing_crop
    )
    wiped_ids = []
    original_wipe = facemarket_enrollment.cx_identity.wipe_bytearray

    def spy_wipe(value):
        if value is not None:
            wiped_ids.append(id(value))
        original_wipe(value)

    monkeypatch.setattr(facemarket_enrollment.cx_identity, "wipe_bytearray", spy_wipe)

    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.json()["reason"] == "id_face_not_detected"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "failed"
    assert "id_document_buffer_id" in captured
    assert captured["id_document_buffer_id"] in wiped_ids


def test_simple_auth_crop_infra_failure_maps_to_qc_unavailable(
    enrollment_client_factory, monkeypatch
):
    """crop_id_face 가 IdDocumentError 가 아니라 맨 QcFailed 를 던지면(가중치 부재 등
    인프라 문제) 사유가 qc_unavailable 이어야 한다 — 재촬영으로 못 고치는 원인을
    id_portrait_unavailable(재촬영 문제로 오인되는 사유)로 뭉개면 on-call 이 헛다리를
    짚는다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
    )

    def infra_down_crop(_data, *, settings):
        raise QcFailed("qc_unavailable")

    monkeypatch.setattr(
        facemarket_enrollment.facemarket_id_document, "crop_id_face", infra_down_crop
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.json()["reason"] == "qc_unavailable"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "failed"


def test_simple_auth_id_document_missing_blocks_before_matching(
    enrollment_client_factory, monkeypatch
):
    """id_document_r2_key 가 없으면(마이그레이션 이전 행 등) 매칭 자체를 시도하지 않는다."""
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
    )
    enrollment_id = _seed_ready_enrollment(
        store, identity_method="simple_auth", id_document_r2_key=None
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.json()["reason"] == "id_portrait_unavailable"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "failed"


def test_simple_auth_off_review_reaches_asset_building_with_scores(
    enrollment_client_factory, monkeypatch
):
    """fm_enrollment_review=off 면 simple_auth 도 심사 없이 바로 자산빌드로 간다.

    다만 advisory 로 계산된 점수는 감사 기록으로 그대로 남는다.
    """
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="off",
        scores={"front": 0.04, "angle45": 0.9, "side": 0.02},
    )
    enrollment_id = _seed_ready_enrollment(
        store,
        identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    response = client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete", json={})
    assert response.status_code == 202, response.text
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "asset_building"
    assert row["review_status"] is None
    assert len(store.jobs) == 1
    scores = row["match_scores"]
    assert scores["anchor"] == "id_document_crop"
    assert scores["scores"]["front"] == pytest.approx(0.04)


# ── 경로 M(mid) 불변 ─────────────────────────────────────────────────────────────────

def test_mid_path_still_enforces_match(enrollment_client_factory, monkeypatch):
    """경로 M 은 무변경 — 임계 미달이면 여전히 즉시 실패한다."""
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid",),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.04, "angle45": 0.05, "side": 0.02},
    )
    enrollment_id = _seed_ready_enrollment(store, identity_method="mid")
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json={"idPhotoHex": PORTRAIT_HEX},
    )
    assert response.json()["reason"] == "face_match_failed"
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "failed"
    # mid 는 심사로 새지 않는다 — review_status 는 애초에 세팅된 적이 없어야 한다.
    assert row["review_status"] is None
    assert store.jobs == []


def test_mid_path_succeeds_above_threshold_without_review(
    enrollment_client_factory, monkeypatch
):
    """경로 M 은 review_required 가 "all" 이 아닌 한 review_pending 을 타지 않는다."""
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid",),
        fm_enrollment_review="simple_auth_only",
        scores={"front": 0.9, "angle45": 0.85, "side": 0.8},
    )
    enrollment_id = _seed_ready_enrollment(store, identity_method="mid")
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json={"idPhotoHex": PORTRAIT_HEX},
    )
    assert response.status_code == 202, response.text
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "asset_building"
    assert row["review_status"] is None
    assert len(store.jobs) == 1


def test_review_all_mode_parks_mid_after_passing_match(
    enrollment_client_factory, monkeypatch
):
    """fm_enrollment_review="all" 이면 매칭을 통과한 mid 등록도 심사 대기로 멈춘다."""
    client, store, settings = _setup(
        enrollment_client_factory,
        monkeypatch,
        fm_identity_methods=("mid",),
        fm_enrollment_review="all",
        scores={"front": 0.9, "angle45": 0.85, "side": 0.8},
    )
    enrollment_id = _seed_ready_enrollment(store, identity_method="mid")
    response = client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json={"idPhotoHex": PORTRAIT_HEX},
    )
    assert response.status_code == 200, response.text
    row = _latest_row(store, enrollment_id)
    assert row["status"] == "review_pending"
    assert row["review_status"] == "pending"
    assert store.jobs == []


# ── review_required() 단위 테스트 ────────────────────────────────────────────────────

def test_review_required_off_never_requires_review():
    from conftest import make_settings

    settings = make_settings(fm_enrollment_review="off")
    assert facemarket_enrollment.review_required(settings, "simple_auth") is False
    assert facemarket_enrollment.review_required(settings, "mid") is False


def test_review_required_all_requires_review_for_every_method():
    from conftest import make_settings

    settings = make_settings(fm_enrollment_review="all")
    assert facemarket_enrollment.review_required(settings, "simple_auth") is True
    assert facemarket_enrollment.review_required(settings, "mid") is True


def test_review_required_simple_auth_only_targets_simple_auth_only():
    from conftest import make_settings

    settings = make_settings(fm_enrollment_review="simple_auth_only")
    assert facemarket_enrollment.review_required(settings, "simple_auth") is True
    assert facemarket_enrollment.review_required(settings, "mid") is False


# ── Task8: 관리자 육안 심사 API ──────────────────────────────────────────────────────
#
# `admin_client` 픽스처는 conftest.py 에 산다(컨트롤러 룰링). 여기 정의된 AdminStore/
# AdminFakePool/AdminFakeR2 는 그 픽스처가 지연 임포트해서 쓴다(enrollment_client_factory
# 가 이 파일의 EnrollmentStore/FakeCursor 를 지연 임포트하는 것과 같은 결).
#
# 이 페이크 DB 는 test_facemarket_biometric_enrollment.py 의 거대한 EnrollmentStore 와는
# 별개의, admin review 라우트 + 그 tail(모델 바인딩·잡 큐잉, Task7 정상완료 경로와 공유하는
# bind_model_and_enqueue_asset_build)에 필요한 SQL만 아는 lean 한 대역이다.

import contextlib
from datetime import timezone


def _json_value(value):
    return getattr(value, "obj", value)


class AdminStore:
    def __init__(self):
        self.enrollments: list[dict] = []
        self.applications: dict[str, dict] = {}
        self.photos: list[dict] = []
        self.models: list[dict] = []
        self.identity_verifications: list[dict] = []
        self.jobs: list[dict] = []
        self.audit: list[dict] = []
        self.deleted_r2_keys: list[str] = []
        self.admin_user_ids: set[str] = set()
        self.latest_id: str | None = None
        # fix round 1, IMPORTANT B: 재개-select 가 "레이스로 행을 못 찾음" 을 겪는 상황을
        # 시뮬레이트한다 — 여기 넣은 enrollment_id 는 승인 UPDATE 가 방금 확정한 상태여도
        # 재개-select 에서 못 찾은 것처럼 굴어(다른 프로세스가 그 사이 가로챈 것과 동치).
        self.race_lost_enrollment_ids: set[str] = set()
        # fix round 1, IMPORTANT E: _assert_account_open/_reject_cutover_closed 시뮬레이션용.
        self.account_closed_user_ids: set[str] = set()
        self.cutover_closed: bool = False

    def add_enrollment(self, **overrides) -> str:
        enrollment_id = overrides.pop("id", None) or str(uuid.uuid4())
        application = overrides.pop("application", None)
        row = {
            "id": enrollment_id,
            "user_id": "enrollee-1",
            "model_id": None,
            "identity_method": "mid",
            "review_status": None,
            "status": "review_pending",
            "match_scores": None,
            "application_id": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "review_reason": None,
            "reason": None,
            "decision": None,
            "created_at": datetime.now(timezone.utc),
            # 생성 + 24h. 사람 심사는 그보다 늦게 끝나는 게 정상이라 승인 시점엔 이미
            # 지나 있는 게 기본값이다(최종리뷰 I4 가 실제로 문제 삼은 상황).
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "completed_at": None,
            "oacx_tx_digest": None,
            "id_document_r2_key": None,
            "id_document_purged_at": None,
            "identity_ci_hash": f"ci-{enrollment_id}",
            "identity_tx_digest": f"tx-{enrollment_id}",
            "identity_name_masked": "홍*동",
            "identity_birth_year": "1990",
            "identity_contract_version": "simple-auth-v1",
            "profile_image_r2_key": None,
            "height_bucket": None,
            "body_type": None,
            "provider_versions": {},
            "match_policy_version": None,
        }
        row.update(overrides)
        if application is not None:
            app_id = str(uuid.uuid4())
            row["application_id"] = app_id
            self.applications[app_id] = application
        self.enrollments.append(row)
        self.latest_id = enrollment_id
        return enrollment_id

    def add_photo(self, enrollment_id: str, angle: str, r2_key: str, mime_type: str = "image/jpeg"):
        self.photos.append(
            {"enrollment_id": enrollment_id, "angle": angle, "r2_key": r2_key, "mime_type": mime_type}
        )

    @property
    def latest_enrollment(self) -> dict:
        return next(row for row in self.enrollments if row["id"] == self.latest_id)


class AdminFakeR2:
    """get_bytes 는 키를 그대로 되돌리는(고정) 바이트를 준다 — 내용 검증은 이 태스크의
    관심사가 아니다(스트리밍 여부·헤더가 관심사). delete 는 store 에 남아 파기를 검증한다."""

    def __init__(self, store: AdminStore):
        self.store = store

    def get_bytes(self, key: str) -> bytes:
        return b"\xff\xd8\xff" + key.encode("utf-8")

    def delete(self, key: str) -> None:
        self.store.deleted_r2_keys.append(key)

    def put_bytes(self, key, data, mime, cache=None):
        return None

    def public_url(self, key: str) -> str:
        return f"https://r2.test/{key}"

    def preview_url(self, key: str, expires: int = 3600) -> str:
        return f"https://r2.test/{key}"


class AdminFakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.store: AdminStore = conn.store
        self.result = None
        self._many: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchone(self):
        return self.result

    async def fetchall(self):
        return self._many

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.result = None
        self._many = []
        store = self.store

        # --- admin_guard.require_admin_identity → repo.is_admin ---
        if query.startswith("select role from profiles where user_id"):
            (user_id,) = params
            self.result = {"role": "admin"} if user_id in store.admin_user_ids else None
            return

        # --- 심사 큐 목록 ---
        if query.startswith(
            "select id::text as id, identity_method, review_status, status, created_at"
        ):
            (review_status,) = params
            rows = [r for r in store.enrollments if r.get("review_status") == review_status]
            # 대기 큐는 status='review_pending' 인 행만 센다 — 취소/만료된 행이 큐에 남으면
            # 승인 버튼이 409 를 내는 유령 항목이 된다(최종리뷰 I5).
            if "and status = 'review_pending'" in query:
                rows = [r for r in rows if r["status"] == "review_pending"]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            self._many = [
                {
                    "id": r["id"],
                    "identity_method": r.get("identity_method"),
                    "review_status": r.get("review_status"),
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return

        # --- 심사 카드 단건 (application_id 컬럼이 있는 쪽으로 재개-select 와 구분) ---
        if "application_id::text as application_id" in query:
            (enrollment_id,) = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            # 심사에 들어온 등록만 본다(최종리뷰 I6) — 프로덕션 SQL 의 술어를 그대로 흉내낸다.
            if row is not None and "review_status is not null" in query and not row.get("review_status"):
                row = None
            self.result = None if row is None else dict(row)
            return

        # --- 지원서 요약 ---
        if "fm_model_applications" in query:
            (application_id,) = params
            self.result = store.applications.get(application_id)
            return

        # --- 이미지: 신분증 키 조회 (purge_id_document 의 첫 SELECT 와 동일 SQL) ---
        if query.startswith("select id_document_r2_key from fm_biometric_enrollments"):
            (enrollment_id,) = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            # 이미지 라우트 쪽 SQL 에만 붙는 범위 술어(파기 경로의 같은 SELECT 엔 없다).
            if row is not None and "review_status is not null" in query and not row.get("review_status"):
                row = None
            self.result = {"id_document_r2_key": row.get("id_document_r2_key")} if row else None
            return

        # --- 이미지: 각도 사진 키 조회 ---
        if "fm_biometric_enrollment_photos" in query and query.startswith(
            "select p.r2_key, p.mime_type"
        ):
            enrollment_id, angle = params
            enrollment = next(
                (r for r in store.enrollments if r["id"] == enrollment_id), None
            )
            photo = next(
                (
                    p
                    for p in store.photos
                    if p["enrollment_id"] == enrollment_id and p["angle"] == angle
                ),
                None,
            )
            # join fm_biometric_enrollments … and e.review_status is not null (최종리뷰 I6)
            if enrollment is None or not enrollment.get("review_status"):
                photo = None
            self.result = photo
            return

        # --- 승인 UPDATE (상태 가드) ---
        if "set review_status = 'approved'" in query:
            reviewed_by, enrollment_id = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            if row and row["status"] == "review_pending" and row.get("review_status") == "pending":
                now = datetime.now(timezone.utc)
                row.update(
                    review_status="approved", reviewed_by=reviewed_by,
                    reviewed_at=now, status="processing",
                    # expires_at = greatest(expires_at, now() + interval '1 hour')
                    expires_at=max(row["expires_at"], now + timedelta(hours=1)),
                )
                self.result = {"id": enrollment_id}
            else:
                self.result = None
            return

        # --- 거절 UPDATE (상태 가드) ---
        if "set review_status = 'rejected'" in query:
            reviewed_by, review_reason, enrollment_id = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            if row and row["status"] == "review_pending" and row.get("review_status") == "pending":
                row.update(
                    review_status="rejected", reviewed_by=reviewed_by,
                    reviewed_at=datetime.now(timezone.utc), review_reason=review_reason,
                    status="failed", reason="review_rejected",
                    completed_at=datetime.now(timezone.utc),
                )
                self.result = {"id": enrollment_id}
            else:
                self.result = None
            return

        # --- purge_id_document 의 UPDATE ---
        if "id_document_purged_at = now()" in query:
            (enrollment_id,) = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            if row is not None:
                row["id_document_r2_key"] = None
                row["id_document_purged_at"] = datetime.now(timezone.utc)
            return

        # --- write_audit ---
        if "admin_audit_log" in query:
            actor_user_id, action, target_type, target_id, before, after, note = params
            store.audit.append(
                {
                    "actor_user_id": actor_user_id,
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "before": _json_value(before),
                    "after": _json_value(after),
                    "note": note,
                }
            )
            return

        # --- 재개-select (identity_ci_hash 포함, status='processing' 가드) ---
        if "identity_ci_hash, identity_tx_digest, identity_name_masked" in query:
            (enrollment_id,) = params
            if enrollment_id in store.race_lost_enrollment_ids:
                # 승인 UPDATE 가 방금 확정한 상태여도, 레이스로 이 select 가 그 행을
                # 다시 못 찾은 상황을 시뮬레이트한다(fix round 1, IMPORTANT B 테스트).
                self.result = None
                return
            row = next(
                (
                    r
                    for r in store.enrollments
                    if r["id"] == enrollment_id
                    and r["status"] == "processing"
                    and r.get("review_status") == "approved"
                ),
                None,
            )
            self.result = None if row is None else dict(row)
            return

        # --- _assert_account_open (계정 폐쇄 여부, fix round 1 IMPORTANT E) ---
        if "kind = 'personalization_purge'" in query:
            (user_id,) = params
            self.result = {"closed": user_id in store.account_closed_user_ids}
            return

        # --- _reject_cutover_closed 의 advisory lock(부수효과 없음) ---
        if query.startswith("select pg_advisory_xact_lock"):
            return

        # --- _reject_cutover_closed 의 컷오버 배치 조회 ---
        if "fm_cutover_batches" in query:
            self.result = {"closed": store.cutover_closed}
            return

        # --- bind_model_and_enqueue_asset_build 의 tail (Task5/6/7 정상경로와 동일 SQL) ---
        if "from fm_models where ci_hash" in query:
            (ci_hash,) = params
            model = next((m for m in store.models if m.get("ci_hash") == ci_hash), None)
            self.result = None if model is None else {"id": model["id"], "user_id": model["user_id"]}
            return

        if "set ci_hash = %s, display_name = %s, user_id = %s" in query:
            ci_hash, display_name, user_id, model_id = params
            model = next((m for m in store.models if m["id"] == model_id), None)
            if model is not None:
                model.update(ci_hash=ci_hash, display_name=display_name, user_id=user_id)
            return

        if query.startswith("insert into fm_models"):
            user_id, display_name, ci_hash = params
            model_id = str(uuid.uuid4())
            store.models.append(
                {
                    "id": model_id, "user_id": user_id, "display_name": display_name,
                    "status": "pending", "ci_hash": ci_hash, "assets_status": None,
                    "current_enrollment_id": None, "cover_image_url": None,
                    "height_bucket": None, "body_type": None, "gender": None,
                }
            )
            self.result = {"id": model_id}
            return

        if "fm_identity_verifications" in query:
            model_id, cx_tx_id, fields = params
            store.identity_verifications.append(
                {"model_id": model_id, "cx_tx_id": cx_tx_id, "fields": _json_value(fields)}
            )
            return

        if "assets_status = 'building'" in query:
            enrollment_id, model_id = params
            model = next((m for m in store.models if m["id"] == model_id), None)
            if model is not None:
                model.update(assets_status="building", current_enrollment_id=enrollment_id)
            return

        if "cover_image_url" in query:
            cover_image_r2_key, model_id = params
            model = next((m for m in store.models if m["id"] == model_id), None)
            if model is not None:
                model["cover_image_url"] = cover_image_r2_key
            return

        if "height_bucket = coalesce" in query:
            height_bucket, body_type, gender, model_id = params
            model = next((m for m in store.models if m["id"] == model_id), None)
            if model is not None:
                model["height_bucket"] = height_bucket or model.get("height_bucket")
                model["body_type"] = body_type or model.get("body_type")
                model["gender"] = model.get("gender") or gender
            return

        if "status = 'asset_building', decision = 'passed'" in query:
            model_id, oacx_tx_digest, match_policy_version, provider_versions, enrollment_id = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            if row is not None:
                merged = dict(row.get("provider_versions") or {})
                merged.update(_json_value(provider_versions))
                row.update(
                    model_id=model_id, status="asset_building", decision="passed", reason=None,
                    completed_at=datetime.now(timezone.utc), oacx_tx_digest=oacx_tx_digest,
                    match_policy_version=match_policy_version, provider_versions=merged,
                )
            return

        if "set match_scores = %s where id = %s" in query:
            match_scores, enrollment_id = params
            row = next((r for r in store.enrollments if r["id"] == enrollment_id), None)
            if row is not None:
                row["match_scores"] = _json_value(match_scores)
            return

        # --- 재조정 스윕 select(최종리뷰 I4) ---
        # reviewed_at 간격 조건은 흉내내지 않는다(가짜 시계가 없다) — 여기서 검증하려는
        # 건 "승인됐는데 잡이 안 걸린 행을 다시 집는가" 이지 지연 시간이 아니다.
        if "e.status = 'processing' and e.review_status = 'approved'" in query:
            (limit,) = params
            pending = [
                r
                for r in store.enrollments
                if r["status"] == "processing" and r.get("review_status") == "approved"
                and not any(
                    (j.get("payload") or {}).get("enrollmentId") == r["id"] for j in store.jobs
                )
            ]
            self._many = [{"id": r["id"]} for r in pending[:limit]]
            return

        if query.startswith("insert into jobs"):
            user_id, payload = params
            store.jobs.append(
                {"user_id": user_id, "kind": "fm_model_asset_build", "payload": _json_value(payload)}
            )
            return

        raise AssertionError(f"AdminFakeCursor 가 모르는 쿼리: {query}")


class AdminFakeConn:
    def __init__(self, store: AdminStore):
        self.store = store

    def cursor(self):
        return AdminFakeCursor(self)

    async def commit(self):
        return None

    async def rollback(self):
        return None


class AdminFakePool:
    def __init__(self, store: AdminStore):
        self.store = store

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield AdminFakeConn(self.store)

        return _cm()


# ── 테스트 ────────────────────────────────────────────────────────────────────────────


def test_review_queue_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    assert client.get("/v1/facemarket/admin/enrollments?review=pending").status_code == 403


def test_review_queue_requires_admin_before_validating_filter(admin_client):
    """fix round 1, minor: 관리자 판정이 요청 모양 검증보다 먼저 답한다 — review 값이
    화이트리스트 밖이어도 비관리자에겐 403 이어야 한다. 순서가 뒤집히면 비관리자가
    400/403 응답 차이로 필터 값을 스캔해볼 수 있다(오늘 노출되는 데이터는 없지만, 관리자
    판정이 언제나 첫 문장이어야 한다는 규율은 지킨다)."""
    client, store = admin_client(is_admin=False)
    response = client.get("/v1/facemarket/admin/enrollments?review=not-a-real-status")
    assert response.status_code == 403


def test_review_queue_lists_pending(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    rows = client.get("/v1/facemarket/admin/enrollments?review=pending").json()
    assert len(rows) == 1
    assert rows[0]["identityMethod"] == "simple_auth"


def test_review_card_includes_scores_and_application(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        match_scores={"scores": {"front": 0.31}, "belowThreshold": []},
        application={"applicant_name": "홍길동", "birthdate": "1990-01-01"},
    )
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["matchScores"]["scores"]["front"] == 0.31
    assert card["application"]["applicantName"] == "홍길동"
    assert set(card["images"]) == {"id_document", "front", "angle45", "side"}


def test_review_card_exposes_identity_mismatch_count_and_application_id(admin_client):
    """fix round 1: identity_mismatch_count 는 identity_method 로 안 갈린다(게이트는
    fm_application_required + application_id 뿐, facemarket_enrollment.py :1170 근처) —
    simple_auth 등록도 지원서 이름·생년월일이 이미 몇 번 어긋났는지 심사자가 봐야 한다.
    application_id 는 관리자 지원서 사진 라우트(GET /admin/applications/{id}/profile-image)
    를 프런트가 직접 부르는 데 필요하다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        application={
            "applicant_name": "홍길동", "birthdate": "1990-01-01",
            "identity_mismatch_count": 2, "profile_image_r2_key": "private/fm-application/app-1/profile.jpg",
        },
    )
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["application"]["identityMismatchCount"] == 2
    assert card["application"]["hasProfileImage"] is True
    assert card["applicationId"] == store.latest_enrollment["application_id"]


def test_review_card_has_profile_image_false_without_key(admin_client):
    """fix round 1, SPEC GAP 2: profile_image_r2_key 가 없으면 hasProfileImage=False 로
    낮춘다 — 프런트가 이 값으로 지원서 사진 fetch 를 걸지 결정한다(무턱대고 요청했다가
    404 를 받는 대신, AdminApplications.jsx 의 hasProfileImage 게이트와 같은 관례)."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        application={"applicant_name": "홍길동", "birthdate": "1990-01-01"},
    )
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["application"]["hasProfileImage"] is False


def test_review_card_defaults_identity_mismatch_count_when_absent(admin_client):
    """지원서 행에 identity_mismatch_count 가 없으면(구버전 행 등) 0 으로 낮춘다 — None 을
    그대로 내보내면 프런트가 '몇 번 실패했는지' 를 못 그린다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        application={"applicant_name": "홍길동", "birthdate": "1990-01-01"},
    )
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["application"]["identityMismatchCount"] == 0


def test_review_card_application_id_is_null_without_application(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending", identity_method="simple_auth")
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["applicationId"] is None
    assert card["application"] is None


def test_image_route_is_no_store(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         id_document_r2_key="k", identity_method="simple_auth")
    response = client.get(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/images/id_document"
    )
    assert response.headers["cache-control"] == "private, no-store"


def test_image_route_rejects_unknown_kind(admin_client):
    """kind 화이트리스트 — 클라이언트 문자열을 R2 키에 그대로 끼워 넣지 않는다는 계약.

    fix round 1, IMPORTANT C: 이전 버전은 `.../images/../../etc/passwd` 를 썼는데,
    httpx 가 `../../` 를 클라이언트 쪽에서 정규화해 실제로 나가는 URL 이 라우트에
    아예 안 맞아 Starlette 이 우리 핸들러(그리고 그 안의 화이트리스트 검사)에 도달하기도
    전에 제네릭 404 를 냈다 — 화이트리스트가 있든 없든 항상 통과하는 5번째 무효 테스트였다.
    같은 세그먼트 안에 있는 값(경로 구분자를 안 씀)을 써야 실제로 라우트·핸들러에
    도달한다. `400` 하나로 단언해야 "핸들러가 못 봄" 상태(404)와 구분된다.
    """
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    response = client.get(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/images/unknown_kind"
    )
    assert response.status_code == 400


def test_image_route_streams_angle_photo(admin_client):
    """fix round 1, IMPORTANT A: id_document 가 아닌 각도 사진 분기(다른 SQL·다른
    파라미터·다른 mime 출처)는 이전까지 완전 무점검이었다 — add_photo 헬퍼가 있었지만
    아무 테스트도 부르지 않는 죽은 스캐폴딩이었다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    key = "facemarket/enrollments/e1/quarantine/front.jpg"
    store.add_photo(store.latest_id, "front", key, mime_type="image/jpeg")
    response = client.get(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/images/front"
    )
    assert response.status_code == 200
    # 정확한 키로 fetch 했는지 — AdminFakeR2.get_bytes 는 키를 바이트에 그대로 반영한다.
    assert response.content == b"\xff\xd8\xff" + key.encode("utf-8")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"] == "image/jpeg"


def test_approve_transitions_and_purges_document(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", id_document_r2_key="k")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["assetBuildError"] is None
    row = store.latest_enrollment
    assert row["review_status"] == "approved"
    # 브리프 초안의 리터럴 테스트는 최종 status 를 'processing' 으로 기대했다. 하지만
    # 'processing'+review_status='approved' 조합을 다시 집어 asset_building 으로 밀어줄
    # 소비자가 이 코드베이스 어디에도 없다(디스패처는 jobs 테이블만 폴링하고,
    # fm_model_asset_build 워커는 e.status='asset_building' 을 전제로 SELECT 한다 —
    # server/app/workers/fm_model_asset_job.py:173,398,469,549). 그 상태로 두면 "승인은
    # 됐는데 영원히 안 만들어지는" 반쪽짜리 배선이 된다 — 브리프 자체가 이걸 "한 것보다
    # 못한 결과"로 명시적으로 금지한다. 그래서 승인 응답이 같은 요청 안에서
    # bind_model_and_enqueue_asset_build(Task7 정상완료 경로의 tail 을 그대로 재사용)를
    # 동기 호출해 재개까지 마친다 — 최종 status 는 'asset_building' 이다. 상세 근거는
    # task-8-report.md 참조.
    assert row["status"] == "asset_building"
    assert row["model_id"] is not None
    assert len(store.jobs) == 1
    assert store.jobs[0]["kind"] == "fm_model_asset_build"
    assert store.jobs[0]["payload"]["modelId"] == row["model_id"]
    # 심사가 끝나면 신분증은 더 쓸 데가 없다 — 즉시 파기.
    assert row["id_document_r2_key"] is None
    assert row["id_document_purged_at"] is not None
    assert "k" in store.deleted_r2_keys


def test_approve_resume_failure_is_visible_not_silent(admin_client):
    """fix round 1, IMPORTANT B (핵심): 재개가 identity_recovery_required 로 실패해도
    (예: 다른 유저가 심사 제출과 승인 사이에 같은 ci_hash 로 이미 모델을 만든 경우)
    승인 자체는 200 이고 결정(review_status·purge·audit)은 그대로 유효해야 한다 — 다만
    그 실패가 조용히 사라지면 안 된다: 응답 바디, 감사 로그(별도 행) 양쪽에서 보여야
    관리자가 "승인은 됐는데 왜 자산이 안 만들어지지"를 알 수 있다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        id_document_r2_key="k", identity_ci_hash="shared-ci-hash",
    )
    # 다른 유저가 이미 같은 ci_hash 로 모델을 갖고 있다 — bind_model_and_enqueue_asset_build
    # 가 identity_recovery_required 를 던지는 조건.
    store.models.append({
        "id": str(uuid.uuid4()), "user_id": "someone-else", "display_name": "다른사람",
        "status": "pending", "ci_hash": "shared-ci-hash", "assets_status": None,
        "current_enrollment_id": None, "cover_image_url": None,
        "height_bucket": None, "body_type": None, "gender": None,
    })
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assetBuildError"] == "identity_recovery_required"
    assert body["status"] == "processing"
    row = store.latest_enrollment
    # 결정 자체는 유효 — 승인·파기는 재개 실패와 무관하게 이미 확정됐다.
    assert row["review_status"] == "approved"
    assert row["status"] == "processing"
    assert row["model_id"] is None
    assert row["id_document_r2_key"] is None
    assert row["id_document_purged_at"] is not None
    assert "k" in store.deleted_r2_keys
    approve_audit = next(a for a in store.audit if a["action"] == "enrollment_review_approve")
    assert approve_audit is not None
    failure_audit = next(
        a for a in store.audit if a["action"] == "enrollment_review_resume_failed"
    )
    assert failure_audit["note"] == "identity_recovery_required"
    assert failure_audit["target_id"] == store.latest_id


def test_approve_resume_race_lost_is_visible(admin_client):
    """fix round 1, IMPORTANT B: 재개-select 가 방금 승인이 확정한 행을 다시 못 찾는
    (레이스) 경로도 identity_recovery_required 경로와 마찬가지로 조용히 사라지면 안 된다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    store.race_lost_enrollment_ids.add(store.latest_id)
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assetBuildError"] == "race_lost"
    assert body["status"] == "processing"
    row = store.latest_enrollment
    assert row["review_status"] == "approved"
    assert row["status"] == "processing"
    assert row["model_id"] is None
    approve_audit = next(a for a in store.audit if a["action"] == "enrollment_review_approve")
    assert approve_audit is not None
    failure_audit = next(
        a for a in store.audit if a["action"] == "enrollment_review_resume_failed"
    )
    assert failure_audit["note"] == "race_lost"


def test_approve_resume_blocked_by_closed_account_is_visible(admin_client):
    """fix round 1, IMPORTANT E: 정상 완료 경로(process_enrollment_completion)가 도는
    _assert_account_open 관문을 재개 경로도 돌아야 한다 — 승인과 재개 사이(짧은 창)에
    계정이 닫히면 자산빌드가 시작되면 안 된다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", user_id="closing-user")
    store.account_closed_user_ids.add("closing-user")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assetBuildError"] == "account_closed"
    row = store.latest_enrollment
    assert row["review_status"] == "approved"
    assert row["model_id"] is None
    failure_audit = next(
        a for a in store.audit if a["action"] == "enrollment_review_resume_failed"
    )
    assert failure_audit["note"] == "account_closed"


def test_approve_resume_blocked_by_cutover_is_visible(admin_client):
    """fix round 1, IMPORTANT E: 컷오버(실물 모델 보안 전환)가 진행 중이면 재개도
    멈춰야 한다 — 정상 완료 경로와 동일한 _reject_cutover_closed 관문."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    store.cutover_closed = True
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assetBuildError"] == "facemarket_cutover_in_progress"
    row = store.latest_enrollment
    assert row["review_status"] == "approved"
    assert row["model_id"] is None


def test_reject_requires_reason_and_purges(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", id_document_r2_key="k")
    assert client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject", json={"reason": ""}
    ).status_code == 400
    response = client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject",
        json={"reason": "신분증 얼굴과 등록 사진이 다른 사람"},
    )
    assert response.status_code == 200
    row = store.latest_enrollment
    assert row["review_status"] == "rejected"
    assert row["status"] == "failed"
    assert row["reason"] == "review_rejected"
    assert row["id_document_r2_key"] is None
    assert row["id_document_purged_at"] is not None
    assert "k" in store.deleted_r2_keys


def test_approve_writes_audit(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert store.audit[-1]["action"] == "enrollment_review_approve"
    assert store.audit[-1]["target_type"] == "enrollment"


def test_reject_writes_audit(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject",
        json={"reason": "위조 의심"},
    )
    assert store.audit[-1]["action"] == "enrollment_review_reject"
    assert store.audit[-1]["target_type"] == "enrollment"
    assert store.audit[-1]["note"] == "위조 의심"


def test_approve_rejects_non_review_state(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="photos_pending", identity_method="simple_auth")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 409


def test_reject_rejects_non_review_state(admin_client):
    """승인/거절 둘 다 같은 상태가드 규율을 지키는지 — approve 쪽만 검사하면 reject 의
    WHERE 절이 빠져도 아무도 못 잡는다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="asset_building", identity_method="simple_auth")
    response = client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject",
        json={"reason": "위조 의심"},
    )
    assert response.status_code == 409


def test_double_approve_second_call_loses_race(admin_client):
    """두 관리자가 동시에 승인 버튼을 눌러도 둘 다 '이겼다'고 믿으면 안 된다 —
    상태가드 UPDATE 가 두 번째 호출에서 0-row 여야 하고, 그게 409 여야지 500 이면 안 된다."""
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    first = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert first.status_code == 200
    second = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert second.status_code == 409


def test_approve_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 403
    # 403 이면 상태도 바뀌면 안 된다 — 가드가 라우트 진입 전에 막았다는 증거.
    assert store.latest_enrollment["review_status"] == "pending"


def test_reject_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    response = client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject",
        json={"reason": "위조 의심"},
    )
    assert response.status_code == 403
    assert store.latest_enrollment["review_status"] == "pending"


def test_card_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    response = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}")
    assert response.status_code == 403


def test_image_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    store.add_enrollment(status="review_pending", review_status="pending",
                         id_document_r2_key="k", identity_method="simple_auth")
    response = client.get(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/images/id_document"
    )
    assert response.status_code == 403


# ── 최종리뷰 I4 · I5 · I6 ─────────────────────────────────────────────────────────────


def test_approve_pushes_expires_at_out_of_the_sweep(admin_client):
    """승인은 expires_at 을 미래로 민다.

    expires_at 은 '생성 + 24h' 인데 사람 심사는 그보다 늦게 끝나는 게 정상이다. 그리고
    'processing' 은 만료 스윕의 대상 상태다 — 안 밀면 승인된 등록이 ≤60초 안에 expired 로
    뒤집히고 격리 사진까지 지워진다(최종리뷰 I4).
    """
    client, store = admin_client(is_admin=True)
    enrollment_id = store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
    )
    before = store.latest_enrollment["expires_at"]
    assert before < datetime.now(timezone.utc), "픽스처 전제: 승인 시점엔 이미 만료 시각이 지났다"
    assert client.post(f"/v1/facemarket/admin/enrollments/{enrollment_id}/approve").status_code == 200
    assert store.latest_enrollment["expires_at"] > datetime.now(timezone.utc), (
        "승인이 expires_at 을 안 밀면 방금 승인한 등록이 곧바로 만료 스윕 대상이다"
    )


async def _run_resume_sweep(client):
    from app.facemarket_admin_review import sweep_stalled_review_approvals

    return await sweep_stalled_review_approvals(client.app, limit=20)


def test_stalled_approval_is_reconciled_by_the_sweep(admin_client):
    """재개가 실패해 processing+approved 로 멈춘 행을 스윕이 다시 집는다(최종리뷰 I4).

    이 스윕이 없으면 아무도 다시 시도하지 않는다 — 사람이 ERROR 로그를 읽을 때쯤이면
    신분증은 이미 파기됐고 사진도 만료 스윕이 지운 뒤라 복구가 불가능하다.
    """
    import asyncio

    client, store = admin_client(is_admin=True)
    enrollment_id = store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
    )
    # 승인 시점의 재개는 레이스로 실패시킨다 → status='processing', 잡 0건.
    store.race_lost_enrollment_ids.add(enrollment_id)
    body = client.post(f"/v1/facemarket/admin/enrollments/{enrollment_id}/approve").json()
    assert body["assetBuildError"] == "race_lost"
    assert store.jobs == [], "재개가 실패했으므로 아직 잡이 없어야 한다"

    # 원인이 사라진 뒤 스윕이 돈다.
    store.race_lost_enrollment_ids.discard(enrollment_id)
    assert asyncio.run(_run_resume_sweep(client)) == 1
    assert len(store.jobs) == 1 and store.jobs[0]["kind"] == "fm_model_asset_build"
    assert store.latest_enrollment["status"] == "asset_building"

    # 두 번 돌아도 중복 큐잉하지 않는다(상태가 이미 asset_building 이라 대상이 아니다).
    assert asyncio.run(_run_resume_sweep(client)) == 0
    assert len(store.jobs) == 1


def test_review_queue_pending_excludes_rows_that_left_review(admin_client):
    """review_status 만 보면 취소·만료된 행이 대기 큐에 영원히 남는다(최종리뷰 I5).

    그 행에 승인을 누르면 상태 가드 UPDATE 가 0-row → 409 다. 심사자는 지울 수도, 처리할
    수도 없는 유령 항목을 계속 본다.
    """
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    live_id = store.latest_id
    # 사용자가 취소했지만(또는 만료됐지만) review_status 가 남아 있는 행.
    store.add_enrollment(status="cancelled", review_status="pending",
                         identity_method="simple_auth")
    rows = client.get("/v1/facemarket/admin/enrollments?review=pending").json()
    assert [row["id"] for row in rows] == [live_id]


def test_review_card_and_images_are_scoped_to_enrollments_under_review(admin_client):
    """심사에 들어오지 않은 등록(mid 포함)은 카드도 이미지도 볼 수 없다(최종리뷰 I6).

    이 라우터는 `fm_biometric_enrollment_enabled` 로 마운트돼 간편인증이 꺼진 프로덕션에서도
    살아 있다 — 범위 술어가 없으면 등록 id 하나만 알면 **모든 등록의 생체 사진 3장**을
    스트리밍할 수 있고, 이 브랜치 이전엔 그런 라우트가 아예 없었다.
    """
    client, store = admin_client(is_admin=True)
    enrollment_id = store.add_enrollment(
        status="liveness_pending", review_status=None, identity_method="mid",
        id_document_r2_key="facemarket/enrollments/x/iddoc/a.jpg",
    )
    store.add_photo(enrollment_id, "front", "private/x/front.jpg")

    assert client.get(f"/v1/facemarket/admin/enrollments/{enrollment_id}").status_code == 404
    assert client.get(
        f"/v1/facemarket/admin/enrollments/{enrollment_id}/images/front"
    ).status_code == 404
    assert client.get(
        f"/v1/facemarket/admin/enrollments/{enrollment_id}/images/id_document"
    ).status_code == 404


def test_review_image_view_writes_an_audit_row(admin_client):
    """이미지 열람도 감사 기록을 남긴다(최종리뷰 I6, 처리방침 §접속기록).

    승인·거절은 남기는데 열람만 안 남기면 "누가 무엇을 봤는가"가 아무 데도 없다.
    """
    client, store = admin_client(is_admin=True)
    enrollment_id = store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/x/iddoc/a.jpg",
    )
    assert client.get(
        f"/v1/facemarket/admin/enrollments/{enrollment_id}/images/id_document"
    ).status_code == 200
    views = [row for row in store.audit if row["action"] == "enrollment_review_image_view"]
    assert len(views) == 1
    assert views[0]["target_id"] == enrollment_id
    assert views[0]["note"] == "id_document"
    assert views[0]["actor_user_id"] == "admin-1"
