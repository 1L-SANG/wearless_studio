"""Append-only schema and ownership boundaries for immutable detail publications."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261023120000_detail_publications.sql"


def executable_sql():
    sql = MIGRATION.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def test_detail_publications_migration_creates_immutable_versions():
    assert MIGRATION.exists()
    sql = executable_sql()
    assert "create table if not exists public.detail_publications" in sql
    assert "check (status in ('pending','ready','deleted'))" in sql
    assert "unique (project_id, version)" in sql
    assert "references public.projects(id) on delete set null" in sql


def test_detail_publications_migration_has_owner_rls_without_delete_policy():
    assert MIGRATION.exists()
    sql = executable_sql()
    assert "enable row level security" in sql
    for action in ("select", "insert", "update"):
        assert f"for {action} to authenticated" in sql
    assert "auth.uid() = user_id" in sql
    assert "for delete" not in sql


def test_detail_publications_migration_has_retention_indexes():
    assert MIGRATION.exists()
    sql = executable_sql()
    assert "(user_id, created_at desc)" in sql
    assert "(expires_at) where status = 'ready'" in sql


def test_detail_publications_migration_timestamp_is_unique():
    assert list((ROOT / "supabase/migrations").glob("20261023120000*.sql")) == [MIGRATION]
