"""Phase 4 — Product Truth Package 계약.

테스트 seam:
  · app.services.product_truth 순수 함수
  · 후속 migration SQL 정적 계약

라우트/repo/worker 배선은 부모 에이전트가 공용 파일에서 연결한다. 여기서는 승인 revision 의
불변 계약과 생성 입력으로 쓰기 전 stale 검출을 먼저 고정한다.
"""

from __future__ import annotations

from pathlib import Path

from datetime import UTC, datetime

import pytest

from app.services import product_truth as pt

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260803000000_product_truth.sql"


def _product(*, detail=True, detail_role=None, name="핀 스트라이프 버튼 셔츠"):
    images = [
        {"id": "00000000-0000-0000-0000-000000000001", "slot": "Front", "label": "Front"},
        {"id": "00000000-0000-0000-0000-000000000002", "slot": "Back", "label": "Back"},
    ]
    if detail:
        im = {"id": "00000000-0000-0000-0000-000000000003", "slot": "Detail", "label": "원단 디테일"}
        if detail_role:
            im["truthRole"] = detail_role
        images.append(im)
    return {
        "id": "prod-1",
        "project_id": "prj-1",
        "name": name,
        "clothing_type": "top",
        "colors": [{"id": "base", "isBase": True, "name": "ivory", "swatchId": "ivory",
                    "images": images}],
        "measurements": [{"name": "총장", "value": 72}],
        "measurements_unknown": False,
    }


def _analysis(**overrides):
    base = {
        "subCategory": "shirt",
        "fit": "regular",
        "materials": ["cotton"],
        "styleTags": ["fine stripe", "button", "collar"],
        "buttonCount": 7,
        "pocketCount": 1,
        "patternConfidence": 0.87,
    }
    base.update(overrides)
    return base


ASSETS = {
    "00000000-0000-0000-0000-000000000001": {
        "id": "00000000-0000-0000-0000-000000000001",
        "checksum": "front-sha",
        "width": 1800,
        "height": 2400,
        "mime_type": "image/jpeg",
        "source": "upload",
    },
    "00000000-0000-0000-0000-000000000002": {
        "id": "00000000-0000-0000-0000-000000000002",
        "checksum": "back-sha",
        "width": 1800,
        "height": 2400,
        "mime_type": "image/jpeg",
        "source": "upload",
    },
    "00000000-0000-0000-0000-000000000003": {
        "id": "00000000-0000-0000-0000-000000000003",
        "checksum": "detail-sha",
        "width": 1200,
        "height": 1200,
        "mime_type": "image/jpeg",
        "source": "upload",
    },
}


def test_detail_slot_can_be_promoted_to_fabric_macro_role():
    product = _product(detail_role="fabric_macro")
    assets = pt.source_assets(product, ASSETS)
    assert [a["role"] for a in assets] == ["FRONT", "BACK", "FABRIC_MACRO"]
    assert assets[2]["checksum"] == "detail-sha"
    assert assets[2]["metadata"]["source"] == "upload"


def test_detail_role_uses_label_hints_when_no_explicit_truth_role():
    assert pt.source_assets(_product(), ASSETS)[2]["role"] == "FABRIC_MACRO"
    logo_product = _product(detail_role=None)
    logo_product["colors"][0]["images"][2]["label"] = "브랜드 로고 확대"
    assert pt.source_assets(logo_product, ASSETS)[2]["role"] == "LOGO"


def test_build_truth_draft_structures_garment_color_pattern_and_protected_details():
    truth = pt.build_truth_draft(_product(), _analysis(), ASSETS, version=3)
    assert truth["status"] == "draft"
    assert truth["schemaVersion"] == pt.SCHEMA_VERSION
    assert truth["version"] == 3
    assert truth["garmentSpec"]["category"] == "TOP"
    assert truth["garmentSpec"]["subcategory"] == "SHIRT"
    assert truth["garmentSpec"]["fit"] == "REGULAR"
    assert truth["garmentSpec"]["buttonCount"] == 7
    assert "BUTTONS" in truth["garmentSpec"]["structureFlags"]
    assert truth["garmentSpec"]["materialTraits"] == ["cotton"]
    assert truth["colorSpec"]["colorSpace"] == "Lab"
    assert truth["colorSpec"]["baseColors"][0]["name"] == "ivory"
    assert truth["patternSpec"]["type"] == "STRIPE"
    assert truth["patternSpec"]["finePattern"] is True


def test_complex_material_words_are_persisted_for_pipeline_policy():
    truth = pt.build_truth_draft(_product(), _analysis(materials=["cotton", "lace"]), ASSETS)
    assert truth["garmentSpec"]["materialTraits"] == ["cotton", "lace"]
    assert truth["protectedDetails"]["pattern"] is True
    assert truth["protectedDetails"]["buttonCount"] is True
    assert truth["sourceEvidence"]["roles"] == ["BACK", "FABRIC_MACRO", "FRONT"]


def test_visible_counts_become_protected_facts_and_invalid_counts_block_approval():
    truth = pt.build_truth_draft(
        _product(name="기본 셔츠"),
        _analysis(buttonCount=7, pocketCount=1, styleTags=[]),
        ASSETS,
    )
    assert truth["protectedDetails"]["buttonCount"] is True
    assert truth["protectedDetails"]["pocketCount"] is True

    truth["garmentSpec"]["buttonCount"] = -1
    errors = {i.code for i in pt.validation_issues(truth) if i.severity == "error"}
    assert "invalid_buttonCount" in errors
    with pytest.raises(pt.ProductTruthError):
        pt.approve_snapshot(truth)


def test_source_fingerprint_changes_when_source_checksum_or_analysis_changes():
    p = _product()
    a = _analysis()
    fp1 = pt.source_fingerprint(p, a, ASSETS)
    changed_asset = {**ASSETS, "00000000-0000-0000-0000-000000000003": {
        **ASSETS["00000000-0000-0000-0000-000000000003"],
        "checksum": "new-detail-sha",
    }}
    fp2 = pt.source_fingerprint(p, a, changed_asset)
    fp3 = pt.source_fingerprint(p, {**a, "buttonCount": 6}, ASSETS)
    assert fp1 != fp2
    assert fp1 != fp3
    assert pt.source_fingerprint(p, a, ASSETS) == fp1


def test_approval_can_refresh_fingerprint_only_while_source_assets_are_unchanged():
    """분석 직후 프론트가 상품 메타를 동기화해도 같은 사진이면 승인을 막지 않는다.

    반대로 사진 id/checksum/역할이 바뀐 draft를 새 fingerprint로 재봉인하면 오래된 사실을
    새 원본에 승인하는 꼴이므로 반드시 stale로 거부한다.
    """
    product = _product()
    draft = pt.build_truth_draft(product, _analysis(), ASSETS)

    # 상품명·실측 같은 비자산 메타가 분석 완료 직후 동기화된 경우는 같은 source authority다.
    synced = _product(name="사용자가 확정한 상품명")
    synced["measurements"] = [{"name": "총장", "value": 70}]
    pt.assert_source_assets_current(draft, synced, ASSETS)

    changed = _product()
    changed["colors"][0]["images"][0]["id"] = (
        "00000000-0000-0000-0000-000000000099"
    )
    with pytest.raises(pt.ProductTruthError) as stale:
        pt.assert_source_assets_current(draft, changed, ASSETS)
    assert stale.value.code == "truth_stale"

    changed_checksum = {
        **ASSETS,
        "00000000-0000-0000-0000-000000000003": {
            **ASSETS["00000000-0000-0000-0000-000000000003"],
            "checksum": "replaced-detail-sha",
        },
    }
    with pytest.raises(pt.ProductTruthError) as stale_checksum:
        pt.assert_source_assets_current(draft, product, changed_checksum)
    assert stale_checksum.value.code == "truth_stale"


def test_validation_requires_front_and_pattern_evidence():
    missing_front = _product()
    missing_front["colors"][0]["images"] = [im for im in missing_front["colors"][0]["images"]
                                             if im["slot"] != "Front"]
    truth = pt.build_truth_draft(missing_front, _analysis(), ASSETS)
    errors = {i.code for i in pt.validation_issues(truth) if i.severity == "error"}
    assert "missing_front_asset" in errors

    no_detail = pt.build_truth_draft(_product(detail=False), _analysis(), ASSETS)
    errors = {i.code for i in pt.validation_issues(no_detail) if i.severity == "error"}
    assert errors == {"missing_pattern_evidence"}
    assert pt.can_approve(no_detail) is False


def test_back_missing_is_warning_not_approval_blocker_for_plain_product():
    product = _product(detail=False, name="무지 라운드 티")
    product["colors"][0]["images"] = [im for im in product["colors"][0]["images"]
                                      if im["slot"] != "Back"]
    truth = pt.build_truth_draft(product, _analysis(styleTags=["plain"]), ASSETS)
    issues = pt.validation_issues(truth)
    assert [i.code for i in issues] == ["missing_back_asset"]
    assert pt.can_approve(truth) is True


def test_approve_snapshot_is_immutable_copy_and_adds_garment_profile():
    draft = pt.build_truth_draft(_product(), _analysis(), ASSETS)
    approved = pt.approve_snapshot(draft, actor_id="user-1")
    assert draft["status"] == "draft"
    assert approved["status"] == "approved"
    assert approved["approvedBy"] == "user-1"
    assert approved["garmentProfile"]["patternType"] == "STRIPE"
    assert "FINE_PATTERN" in approved["garmentProfile"]["riskFlags"]
    assert approved["garmentProfile"]["materialRisk"] == "HIGH"


def test_approve_snapshot_accepts_database_datetime_fields():
    """라우트가 DB row를 domain으로 바꿀 때 datetime을 유지해도 승인이 500이면 안 된다."""
    draft = pt.build_truth_draft(_product(), _analysis(), ASSETS)
    draft["createdAt"] = datetime(2026, 8, 4, 3, 2, 57, tzinfo=UTC)

    approved = pt.approve_snapshot(draft, actor_id="user-1")

    assert approved["createdAt"] == "2026-08-04T03:02:57+00:00"
    assert approved["status"] == "approved"


def test_generation_gate_requires_approved_and_current_truth():
    draft = pt.build_truth_draft(_product(), _analysis(), ASSETS)
    with pytest.raises(pt.ProductTruthError) as e:
        pt.assert_approved_for_generation(draft)
    assert e.value.code == "approved_truth_required"

    approved = pt.approve_snapshot(draft)
    pt.assert_approved_for_generation(approved, current_fingerprint=approved["sourceFingerprint"])
    with pytest.raises(pt.ProductTruthError) as stale:
        pt.assert_approved_for_generation(approved, current_fingerprint="different")
    assert stale.value.code == "truth_stale"


def test_pattern_and_logo_are_independent_protected_facts():
    product = _product(name="무지 로고 자수 스웨트")
    truth = pt.build_truth_draft(product, _analysis(styleTags=["logo embroidery"], logo=True), ASSETS)
    protected = truth["protectedDetails"]
    assert protected["logo"] is True
    assert protected["embroidery"] is True
    assert protected["pattern"] is False


def test_migration_creates_truth_tables_and_role_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "create table if not exists public.product_truth_packages" in sql
    assert "create table if not exists public.product_truth_assets" in sql
    assert "create table if not exists public.product_truth_review_events" in sql
    for role in pt.TRUTH_ASSET_ROLES:
        assert f"'{role}'" in sql
    assert "product_truth_packages_status_check" in sql
    assert "status in ('draft', 'approved', 'superseded', 'rejected')" in sql


def test_migration_pins_one_draft_and_one_approved_revision_per_project():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "product_truth_packages_one_draft_per_project" in sql
    assert "where status = 'draft'" in sql
    assert "product_truth_packages_one_approved_per_project" in sql
    assert "where status = 'approved'" in sql
    assert "product_truth_packages_project_version_idx" in sql


def test_migration_links_baseline_and_generation_run_to_truth_package():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "approved_baselines_truth_package_id_fkey" in sql
    assert "foreign key (truth_package_id) references public.product_truth_packages" in sql
    assert "on delete restrict" in sql
    assert "add column if not exists truth_package_id uuid" in sql
    assert "generation_runs_truth_package_idx" in sql


def test_migration_scopes_rls_by_project_ownership():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "alter table public.product_truth_packages enable row level security;" in sql
    assert "alter table public.product_truth_assets enable row level security;" in sql
    assert "alter table public.product_truth_review_events enable row level security;" in sql
    assert "p.user_id = (select auth.uid())" in sql


def test_migration_is_replayable_and_append_only():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table if not exists" in sql
    assert "add column if not exists" in sql
    assert "drop table" not in sql
    assert "delete from" not in sql
    assert "truncate " not in sql
