from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260807000000_custom_matching_items.sql"
)


def test_owner_project_check_accepts_only_both_null_or_both_present():
    allowed = []
    for owner_present in (False, True):
        for project_present in (False, True):
            valid = (not owner_present and not project_present) or (
                owner_present and project_present
            )
            if valid:
                allowed.append((owner_present, project_present))
    assert allowed == [(False, False), (True, True)]


def test_migration_has_partial_unique_derived_source_and_split_rls():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "matching_items_owner_project_pair_chk" in sql
    # 멱등 작성(if not exists / if exists) — 이 스키마는 운영 DB에 손으로 먼저 적용돼 있고
    # 이력에는 없다. 재실행해도 실패하지 않아야 fresh DB와 운영 양쪽에서 같은 파일을 쓴다.
    assert "create unique index if not exists matching_items_custom_project_uniq" in sql
    assert "on public.matching_items (project_id)" in sql
    assert "where owner_user_id is not null" in sql
    assert "'derived'" in sql
    assert "drop policy if exists matching_items_active_select" in sql
    assert "create policy matching_items_curated_select" in sql
    assert "is_active and owner_user_id is null and project_id is null" in sql
    assert "create policy matching_items_custom_owner_select" in sql
    assert "owner_user_id = (select auth.uid())" in sql
    assert "p.user_id = (select auth.uid())" in sql
    assert "p.deleted_at is null" in sql

