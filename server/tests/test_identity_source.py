"""아이덴티티-소스 상태머신 + 실존 자산 resolve 검증(codex [P1])."""

import asyncio
import logging

import pytest

from app.agents.identity_source import (
    compute_assets_source_hash,
    resolve_real_model_assets,
    select_source,
)


_FM_UUID = "11111111-1111-1111-1111-111111111111"
_OTHER_UUID = "22222222-2222-2222-2222-222222222222"
_ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
_POLICY_VERSION = "policy-v1"

# fm_biometric_enrollment_photos 의 현재 소스 사진 지문(front/angle45/side 순).
_DEFAULT_PHOTOS = [
    {"angle": "front", "image_digest": "sha256-front", "r2_key": "enroll/front.png"},
    {"angle": "angle45", "image_digest": "sha256-angle45", "r2_key": "enroll/angle45.png"},
    {"angle": "side", "image_digest": "sha256-side", "r2_key": "enroll/side.png"},
]
_DEFAULT_SOURCE_HASH = compute_assets_source_hash(_DEFAULT_PHOTOS)


def test_real_requires_active_license():
    # 실자산 있으나 라이선스 없음 → REJECTED(조용한 폴백 금지)
    assert select_source(selected_model_id=_FM_UUID, license_row=None,
                         has_real_assets=True, has_license_face=False) == "REJECTED"


def test_real_requires_matching_model():
    # 라이선스는 있으나 다른 모델 → REJECTED
    assert select_source(selected_model_id=_FM_UUID,
                         license_row={"model_id": _OTHER_UUID, "status": "active",
                                      "model_status": "verified"},
                         has_real_assets=True, has_license_face=False) == "REJECTED"


def test_real_with_active_matching_license():
    assert select_source(selected_model_id=_FM_UUID,
                         license_row={"model_id": _FM_UUID, "status": "active",
                                      "model_status": "verified"},
                         has_real_assets=True, has_license_face=True) == "REAL"


def test_uuid_without_exact_refs_is_rejected_not_virtual():
    assert select_source(
        selected_model_id=_FM_UUID,
        license_row={"model_id": _FM_UUID, "status": "active",
                     "model_status": "verified"},
        has_real_assets=False,
        has_license_face=True,
    ) == "REJECTED"


def test_virtual_no_license_needed():
    assert select_source(selected_model_id="mA", license_row=None,
                         has_real_assets=False, has_license_face=False) == "VIRTUAL"


def test_legacy_face_only():
    assert select_source(selected_model_id=None, license_row=None,
                         has_real_assets=False, has_license_face=True) == "LEGACY"


def test_none_when_nothing():
    assert select_source(selected_model_id=None, license_row=None,
                         has_real_assets=False, has_license_face=False) == "NONE"


# ── resolve_real_model_assets ──
class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.sql = None

    async def execute(self, sql, params=None):
        self.sql = sql
        return None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows, photo_rows=None):
        self._rows = rows
        self._photo_rows = _DEFAULT_PHOTOS if photo_rows is None else photo_rows
        self._call = 0

    def cursor(self):
        # 1번째 호출 = fm_models/fm_model_assets 조인, 2번째 호출 = 소스 사진 지문 재계산.
        self._call += 1
        return _Cur(self._rows if self._call == 1 else self._photo_rows)


def _run(rows, **kwargs):
    photo_rows = kwargs.pop("photo_rows", None)
    return asyncio.run(resolve_real_model_assets(
        _Conn(rows, photo_rows=photo_rows), _FM_UUID,
        enrollment_id=kwargs.pop("enrollment_id", _ENROLLMENT_ID),
        evidence_version=kwargs.pop("evidence_version", _POLICY_VERSION),
        **kwargs,
    ))


class _ExplodingConn:
    """가상모델 id 가 DB 에 닿으면 실패하는 감시 스텁."""

    def cursor(self):
        raise AssertionError("non-UUID model id must not reach the fm_models query")


def test_resolve_virtual_model_id_skips_db_and_returns_none():
    # 가상모델 id(mA 등)는 UUID 가 아니다 — fm_models.id(uuid) 쿼리에 그대로 바인딩하면
    # psycopg InvalidTextRepresentation 으로 상세페이지 잡 전체가 죽는다(2026-07-29 prod 재현:
    # facemarket_enabled=true + 가상모델 선택 조합에서 progress 5 즉사).
    for vid in ("mA", "mB", "mC", "mD", "mE", "model-1", ""):
        assert asyncio.run(resolve_real_model_assets(
            _ExplodingConn(), vid,
            enrollment_id=_ENROLLMENT_ID,
            evidence_version=_POLICY_VERSION,
        )) is None


def _asset_rows(
    *,
    model_status="verified",
    assets_status="ready",
    current_enrollment_id=_ENROLLMENT_ID,
    enrollment_status="passed",
    match_policy_version=_POLICY_VERSION,
    asset_source_enrollment_id=_ENROLLMENT_ID,
    evidence_version=_POLICY_VERSION,
    face_key="facemarket/models/m1/face_front.png",
    grid_key="facemarket/models/m1/grid_sedcard.png",
    bucket="face",
    mime="image/png",
    assets_source_hash=_DEFAULT_SOURCE_HASH,
):
    return [
        {"model_status": model_status, "assets_status": assets_status,
         "current_enrollment_id": current_enrollment_id,
         "assets_source_hash": assets_source_hash,
         "enrollment_status": enrollment_status,
         "match_policy_version": match_policy_version, "view": "face_front",
         "r2_key": face_key, "mime": mime, "bucket": bucket,
         "source_enrollment_id": asset_source_enrollment_id, "evidence_version": evidence_version},
        {"model_status": model_status, "assets_status": assets_status,
         "current_enrollment_id": current_enrollment_id,
         "assets_source_hash": assets_source_hash,
         "enrollment_status": enrollment_status,
         "match_policy_version": match_policy_version, "view": "grid_sedcard",
         "r2_key": grid_key, "mime": mime, "bucket": bucket,
         "source_enrollment_id": asset_source_enrollment_id, "evidence_version": evidence_version},
    ]


def test_resolve_ready_returns_two_refs_face_bucket():
    refs = _run(_asset_rows())
    assert refs is not None and len(refs) == 2
    assert refs[0]["key"].endswith("face_front.png") and refs[0]["bucket"] == "face"
    assert refs[1]["key"].endswith("grid_sedcard.png")


def test_resolve_not_ready_returns_none():
    rows = [{"model_status": "verified", "assets_status": "building",
             "current_enrollment_id": _ENROLLMENT_ID, "enrollment_status": "passed",
             "match_policy_version": _POLICY_VERSION, "view": None, "r2_key": None,
             "mime": None, "bucket": None, "source_enrollment_id": None,
             "evidence_version": None}]
    assert _run(rows) is None


def test_resolve_missing_view_returns_none():
    rows = [
        {"model_status": "verified", "assets_status": "ready",
         "current_enrollment_id": _ENROLLMENT_ID, "enrollment_status": "passed",
         "match_policy_version": _POLICY_VERSION, "view": "face_front",
         "r2_key": "k", "mime": "image/png", "bucket": "face",
         "source_enrollment_id": _ENROLLMENT_ID, "evidence_version": _POLICY_VERSION},
    ]  # grid_sedcard 없음
    assert _run(rows) is None


def test_resolver_requires_verified_model_status():
    assert _run(_asset_rows(model_status="pending")) is None


@pytest.mark.parametrize(
    ("row_overrides", "pin_overrides"),
    [
        ({"model_status": "pending"}, {}),
        ({"assets_status": "building"}, {}),
        ({"enrollment_status": "pending"}, {}),
        ({"current_enrollment_id": _OTHER_UUID}, {}),
        ({}, {"enrollment_id": _OTHER_UUID}),
        ({"match_policy_version": ""}, {}),
        ({}, {"evidence_version": "policy-v2"}),
        ({"face_key": ""}, {}),
        ({"grid_key": " "}, {}),
        ({"bucket": "public"}, {}),
        ({"bucket": None}, {}),
        ({"mime": "application/octet-stream"}, {}),
        ({"asset_source_enrollment_id": _OTHER_UUID}, {}),
        ({"evidence_version": ""}, {}),
        ({"evidence_version": "policy-v2"}, {}),
    ],
)
def test_resolver_requires_exact_current_private_evidence(row_overrides, pin_overrides):
    assert _run(_asset_rows(**row_overrides), **pin_overrides) is None


def test_resolver_unconditionally_rejects_legacy_personalization_assets():
    rows = _asset_rows(
        current_enrollment_id=None,
        enrollment_status=None,
        match_policy_version=None,
        asset_source_enrollment_id=None,
        evidence_version="legacy-personalization-v1",
    )
    assert _run(rows) is None


# ── assets_source_hash 무결성 강제(Task 5) ──
# fm_model_asset_job._source_hash 가 자산 빌드 시점에 새긴 지문. 조회 시점에
# fm_biometric_enrollment_photos(현재 소스)에서 동일 알고리즘으로 재계산해 비교한다.
# 비교 결과가 다르면(=자산이 지금의 소스 사진과 더 이상 일치하지 않으면) fail-closed.


def test_stale_assets_source_hash_rejects_even_when_everything_else_matches():
    # 나머지 상태(모델/등록/정책버전/뷰)는 전부 정상이지만 assets_source_hash 만
    # 현재 소스 사진 지문과 어긋난다 — DB 직접 변조나 원본 사진 교체를 가정한
    # 시나리오. 조용히 낡은 얼굴을 신뢰하면 안 되므로 None(REJECTED) 이어야 한다.
    rows = _asset_rows(assets_source_hash="deadbeef" * 8)
    assert _run(rows) is None


def test_matching_assets_source_hash_still_resolves_no_false_reject():
    # 하드 가드레일: 지금 소스와 정확히 일치하는 자산은 절대 false-reject 되면
    # 안 된다. 기본 photo_rows(_DEFAULT_PHOTOS)로 계산한 해시와 asset row 의
    # assets_source_hash(_DEFAULT_SOURCE_HASH)가 일치하는 정상 케이스.
    refs = _run(_asset_rows(assets_source_hash=_DEFAULT_SOURCE_HASH))
    assert refs is not None and len(refs) == 2


def test_matching_assets_source_hash_with_explicit_photo_rows_resolves():
    # photo_rows 를 명시적으로 다른(그러나 서로 일치하는) 지문 세트로 줘도
    # 정확히 재계산되어 통과해야 한다 — 순서(front/angle45/side)에 의존하지
    # 않고 각도 키로 매칭됨을 증명(입력 순서를 뒤섞음).
    photos = [
        {"angle": "side", "image_digest": "sha256-s2", "r2_key": "k-side"},
        {"angle": "front", "image_digest": "sha256-f2", "r2_key": "k-front"},
        {"angle": "angle45", "image_digest": "sha256-a2", "r2_key": "k-angle45"},
    ]
    expected_hash = compute_assets_source_hash(
        [{"angle": "front", "image_digest": "sha256-f2"},
         {"angle": "angle45", "image_digest": "sha256-a2"},
         {"angle": "side", "image_digest": "sha256-s2"}]
    )
    refs = _run(
        _asset_rows(assets_source_hash=expected_hash),
        photo_rows=photos,
    )
    assert refs is not None and len(refs) == 2


def test_incomplete_current_photo_set_rejects():
    # 소스 사진 3장 중 일부가 사라진 경우(예: 예상 밖 데이터 유실) — 지문을
    # 재계산할 수 없으므로 fail-closed. 자산 빌드가 성공했던 REAL 모델이라면
    # 정상 상태에서는 절대 도달하지 않는 분기(front/angle45/side 3장은 자산
    # 빌드 불변식이 보장).
    photos = [
        {"angle": "front", "image_digest": "sha256-front", "r2_key": "k"},
        {"angle": "angle45", "image_digest": "sha256-angle45", "r2_key": "k"},
    ]
    assert _run(_asset_rows(), photo_rows=photos) is None


def test_missing_assets_source_hash_rejects():
    # assets_status='ready' 인데 assets_source_hash 가 비어 있으면(이론상
    # 발생 불가해야 하는 상태) 검증 불가로 간주해 fail-closed.
    assert _run(_asset_rows(assets_source_hash=None)) is None


# ── final-review Minor M1: mismatch reject 관측성 ──
# Task 5 로 assets_source_hash 가 ENFORCED 됐지만, mismatch 때도 다른 REJECTED
# 사유와 똑같은 bare None 만 반환한다 — 실제 데이터 이상(변조·유실)이 터져도
# 운영에서 흔한 REJECTED 와 구분할 신호가 없다. mismatch 발생 지점에서만 distinct
# 사유를 로그로 남기고(PII 없음: reason·model_id·enrollment_id 만, 해시 값 자체는
# 남기지 않음), 다른 reject 경로에는 노이즈를 추가하지 않는다.


def test_hash_mismatch_logs_distinct_reason(caplog):
    with caplog.at_level(logging.WARNING, logger="wearless.identity_source"):
        result = _run(_asset_rows(assets_source_hash="deadbeef" * 8))
    assert result is None
    matches = [
        r for r in caplog.records if "assets_source_hash_mismatch" in r.getMessage()
    ]
    assert len(matches) == 1
    rec = matches[0]
    assert getattr(rec, "model_id", None) == _FM_UUID
    assert getattr(rec, "enrollment_id", None) == _ENROLLMENT_ID
    # PII/비밀 금지: 실제 해시 값이 로그 어디에도 나타나면 안 된다.
    assert "deadbeef" not in rec.getMessage()
    assert "deadbeef" not in repr(rec.__dict__)


def test_matching_hash_does_not_log_mismatch(caplog):
    with caplog.at_level(logging.WARNING, logger="wearless.identity_source"):
        refs = _run(_asset_rows(assets_source_hash=_DEFAULT_SOURCE_HASH))
    assert refs is not None and len(refs) == 2
    assert not any(
        "assets_source_hash_mismatch" in r.getMessage() for r in caplog.records
    )


def test_other_reject_reasons_do_not_log_hash_mismatch(caplog):
    # state check(모델 상태 불량)와 per-view check(자산 뷰 필드 결손)는 hash 비교
    # 지점과 별개 분기다 — mismatch 로그가 여기까지 새어 나오면 안 된다.
    with caplog.at_level(logging.WARNING, logger="wearless.identity_source"):
        state_reject = _run(_asset_rows(model_status="pending"))
        view_reject = _run(_asset_rows(face_key=""))
    assert state_reject is None and view_reject is None
    assert not any(
        "assets_source_hash_mismatch" in r.getMessage() for r in caplog.records
    )
