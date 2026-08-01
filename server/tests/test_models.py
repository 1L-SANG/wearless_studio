"""계약 모델 단위테스트 — patch 화이트리스트 + camelCase alias (계약 §1·§6).

DB 없이 검증 가능한 순수 로직만 (DB 통합은 배포 DB에 테스트유저로 수동 검증).
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import repo
from app.models import Account, JobView, Product, ProductPatch, Project, ProjectPatch


def test_project_patch_ignores_server_only_fields():
    # adjustCount·status는 모델에 없어 무시돼야 한다 (계약 §6)
    patch = ProjectPatch(**{"composeMode": "extended", "adjustCount": 99, "status": "done"})
    dumped = patch.model_dump(exclude_unset=True)
    assert dumped == {"compose_mode": "extended"}
    assert "adjust_count" not in dumped
    assert "status" not in dumped


def test_project_patch_exclude_unset_only_sent_fields():
    patch = ProjectPatch(copywriting=False)
    assert patch.model_dump(exclude_unset=True) == {"copywriting": False}


def test_project_patch_rejects_explicit_null_on_non_nullable():
    # {"composeMode": null} / {"copywriting": null} → 422 (NOT NULL 컬럼 500 방지)
    with pytest.raises(ValidationError):
        ProjectPatch(**{"composeMode": None})
    with pytest.raises(ValidationError):
        ProjectPatch(**{"copywriting": None})


def test_project_patch_rejects_retired_simple_mode():
    with pytest.raises(ValidationError):
        ProjectPatch(**{"composeMode": "simple"})


def test_project_patch_allows_null_mannequin_and_omitted_fields():
    # selectedMannequinId는 null 허용, 나머지는 생략 가능
    patch = ProjectPatch(**{"selectedMannequinId": None})
    assert patch.model_dump(exclude_unset=True) == {"selected_mannequin_id": None}


def test_product_patch_rejects_null_on_not_null_columns():
    for field in ("name", "colors", "measurements", "measurementsUnknown", "uploadComplete"):
        with pytest.raises(ValidationError):
            ProductPatch(**{field: None})


def test_product_patch_allows_null_clothing_type_and_partial():
    patch = ProductPatch(**{"clothingType": None, "name": "린넨 셔츠"})
    dumped = patch.model_dump(exclude_unset=True)
    assert dumped == {"clothing_type": None, "name": "린넨 셔츠"}


def test_product_serializes_camel_and_jsonb_passthrough():
    p = Product(id="pr1", projectId="p1", name="", colors=[{"id": "c1", "isBase": True}],
                measurements=[{"key": "totalLength", "value": None, "unit": "cm"}])
    out = p.model_dump(by_alias=True)
    assert out["projectId"] == "p1"
    assert out["measurementsUnknown"] is False
    # JSONB는 패스스루 — 중첩 키 그대로
    assert out["colors"] == [{"id": "c1", "isBase": True}]


def test_patchable_columns_match_model():
    # 모델(1차 화이트리스트)과 repo SQL 가드(2차)가 어긋나지 않게 고정
    assert set(ProjectPatch.model_fields) == set(repo.PATCHABLE_COLUMNS)


def test_account_serializes_to_camel():
    acct = Account(name="한지수", avatar="", credits=24, plan="basic")
    out = acct.model_dump(by_alias=True)
    assert out == {"name": "한지수", "avatar": "", "credits": 24, "plan": "basic"}


def test_project_serializes_to_camel():
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    proj = Project(
        id="p1",
        status="draft",
        title="",
        compose_mode="basic",
        copywriting=True,
        selected_mannequin_id=None,
        adjust_count=0,
        created_at=now,
        updated_at=now,
    )
    out = proj.model_dump(by_alias=True)
    assert "composeMode" in out
    assert "selectedMannequinId" in out
    assert "adjustCount" in out
    assert "createdAt" in out and "updatedAt" in out
    # snake_case 키는 노출되지 않아야
    assert "compose_mode" not in out


def test_job_view_serializes_typed_failure_contract_without_raw_metadata():
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    job = JobView(
        id="j1",
        project_id="p1",
        kind="mannequin",
        status="error",
        progress=100,
        error_message="마네킹컷 생성에 실패했어요.",
        error_code="hybrid_composite_failed_closed",
        error_details={
            "error": "hybrid_composite_failed_closed",
            "failureReason": "geometry_carrier_mismatch",
            "detail": "carrier geometry does not match source",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
            },
        },
        metadata={"providerPrompt": "internal"},
        created_at=now,
        updated_at=now,
    )

    out = job.model_dump(by_alias=True)

    assert out["errorMessage"] == "마네킹컷 생성에 실패했어요."
    assert out["errorCode"] == "hybrid_composite_failed_closed"
    assert out["errorDetails"]["error"] == "hybrid_composite_failed_closed"
    assert out["errorDetails"]["hybridComposite"]["failureReason"] == (
        "geometry_carrier_mismatch"
    )
    assert "metadata" not in out


def test_repo_public_failure_whitelists_hybrid_closed_metadata():
    public = repo._public_failure_from_metadata(
        "generation_failed",
        {
            "error": "hybrid_composite_failed_closed",
            "failureReason": "geometry_carrier_mismatch",
            "detail": "carrier geometry does not match source",
            "providerPrompt": "internal",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
                "failureDetail": "geometry carrier mismatch",
                "metrics": {"internal": True},
            },
        },
    )

    assert public == {
        "code": "hybrid_composite_failed_closed",
        "details": {
            "error": "hybrid_composite_failed_closed",
            "failureReason": "geometry_carrier_mismatch",
            "detail": "carrier geometry does not match source",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
                "failureDetail": "geometry carrier mismatch",
            },
        },
    }


def test_repo_public_failure_rejects_arbitrary_exception_error_strings():
    public = repo._public_failure_from_metadata(
        "generation_failed",
        {
            "error": "ValueError('carrier path leaked /tmp/internal.png')",
            "failureReason": "geometry_carrier_mismatch",
            "detail": "internal detail should not become public",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
            },
        },
    )

    assert public is None


class _CaptureCursor:
    def __init__(self):
        self.calls = []
        self._fetches = [{"id": "j1"}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))

    async def fetchone(self):
        return self._fetches.pop(0)


class _CaptureConn:
    def __init__(self):
        self.cursor_obj = _CaptureCursor()

    def cursor(self):
        return self.cursor_obj


def _json_obj(value):
    return getattr(value, "obj", value)


@pytest.mark.anyio
async def test_finalize_failure_persists_and_emits_public_error_contract():
    conn = _CaptureConn()

    ok = await repo._finalize_job_failure(
        conn,
        job_id="j1",
        lease_token="lease",
        message="마네킹컷 생성에 실패했어요.",
        metadata={
            "error": "hybrid_composite_failed_closed",
            "failureReason": "geometry_carrier_mismatch",
            "providerPrompt": "internal",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
                "metrics": {"internal": True},
            },
        },
        code="generation_failed",
    )

    assert ok is True
    update_sql = conn.cursor_obj.calls[1][0]
    assert "metadata = (metadata - 'publicFailure') || %s::jsonb" in update_sql
    update_params = conn.cursor_obj.calls[1][1]
    persisted = _json_obj(update_params[1])
    assert persisted["publicFailure"]["code"] == "hybrid_composite_failed_closed"
    assert persisted["publicFailure"]["details"]["failureReason"] == (
        "geometry_carrier_mismatch"
    )
    assert "providerPrompt" in persisted
    assert "metrics" not in persisted["publicFailure"]["details"]["hybridComposite"]

    event_params = conn.cursor_obj.calls[2][1]
    event_payload = _json_obj(event_params[1])
    assert event_payload["code"] == "generation_failed"
    assert event_payload["message"] == "마네킹컷 생성에 실패했어요."
    assert event_payload["errorCode"] == "hybrid_composite_failed_closed"
    assert event_payload["errorDetails"]["failureReason"] == (
        "geometry_carrier_mismatch"
    )
    assert "providerPrompt" not in event_payload["errorDetails"]


@pytest.mark.anyio
async def test_finalize_failure_does_not_emit_public_error_for_raw_exception_metadata():
    conn = _CaptureConn()

    ok = await repo._finalize_job_failure(
        conn,
        job_id="j1",
        lease_token="lease",
        message="마네킹컷 생성에 실패했어요.",
        metadata={
            "error": "RuntimeError('provider traceback /tmp/secret.png')",
            "failureReason": "geometry_carrier_mismatch",
            "detail": "internal detail should not become public",
            "hybridComposite": {
                "applied": False,
                "needsReview": True,
                "failureReason": "geometry_carrier_mismatch",
            },
        },
        code="generation_failed",
    )

    assert ok is True
    update_params = conn.cursor_obj.calls[1][1]
    persisted = _json_obj(update_params[1])
    assert persisted["error"] == "RuntimeError('provider traceback /tmp/secret.png')"
    assert "publicFailure" not in persisted

    event_params = conn.cursor_obj.calls[2][1]
    event_payload = _json_obj(event_params[1])
    assert event_payload == {
        "code": "generation_failed",
        "message": "마네킹컷 생성에 실패했어요.",
    }


def test_project_rejects_retired_simple_mode():
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        Project(
            id="p1",
            status="draft",
            title="",
            compose_mode="simple",
            copywriting=True,
            selected_mannequin_id=None,
            adjust_count=0,
            created_at=now,
            updated_at=now,
        )
