"""아이덴티티-소스 상태머신 + 실존 자산 resolve 검증(codex [P1])."""

import asyncio
import pytest

from app.agents.identity_source import resolve_real_model_assets, select_source


_FM_UUID = "11111111-1111-1111-1111-111111111111"
_OTHER_UUID = "22222222-2222-2222-2222-222222222222"
_ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
_POLICY_VERSION = "policy-v1"


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
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cur(self._rows)


def _run(rows, **kwargs):
    return asyncio.run(resolve_real_model_assets(
        _Conn(rows), _FM_UUID,
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
):
    return [
        {"model_status": model_status, "assets_status": assets_status,
         "current_enrollment_id": current_enrollment_id,
         "enrollment_status": enrollment_status,
         "match_policy_version": match_policy_version, "view": "face_front",
         "r2_key": face_key, "mime": mime, "bucket": bucket,
         "source_enrollment_id": asset_source_enrollment_id, "evidence_version": evidence_version},
        {"model_status": model_status, "assets_status": assets_status,
         "current_enrollment_id": current_enrollment_id,
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
