"""계약 모델 단위테스트 — patch 화이트리스트 + camelCase alias (계약 §1·§6).

DB 없이 검증 가능한 순수 로직만 (DB 통합은 배포 DB에 테스트유저로 수동 검증).
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import repo
from app.models import Account, Project, ProjectPatch


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


def test_project_patch_allows_null_mannequin_and_omitted_fields():
    # selectedMannequinId는 null 허용, 나머지는 생략 가능
    patch = ProjectPatch(**{"selectedMannequinId": None})
    assert patch.model_dump(exclude_unset=True) == {"selected_mannequin_id": None}


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
