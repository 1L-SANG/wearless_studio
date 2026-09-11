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

        await original_execute(self, sql, params)

        # _initial_completion_checks 의 완료-체크 select 는 identity_method/
        # id_document_r2_key 가 생기기 전에 쓰인 코드라 그 두 컬럼을 모른다 — 여기서 채운다.
        if (
            query.startswith("select e.id::text as id, e.user_id::text as user_id")
            and self.result is not None
        ):
            enrollment_id, user_id = params
            row = next(
                (
                    item
                    for item in self.store.enrollments
                    if item["id"] == enrollment_id and item["user_id"] == user_id
                ),
                None,
            )
            if row is not None:
                self.result["identity_method"] = row.get("identity_method") or "mid"
                self.result["id_document_r2_key"] = row.get("id_document_r2_key")

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
