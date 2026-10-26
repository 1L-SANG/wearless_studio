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


def test_detail_publications_server_write_only_migration_revokes_client_writes():
    migrations = list((ROOT / "supabase/migrations").glob(
        "20261026*_detail_publications_server_write_only.sql"))
    assert len(migrations) == 1
    migration = migrations[0]
    assert list(migration.parent.glob(migration.name[:14] + "*.sql")) == [migration]
    sql = re.sub(r"--[^\n]*", " ", migration.read_text(encoding="utf-8"))
    statements = [statement.strip().lower() for statement in " ".join(sql.split()).split(";") if statement.strip()]
    assert statements == [
        "drop policy if exists detail_publications_owner_insert on public.detail_publications",
        "drop policy if exists detail_publications_owner_update on public.detail_publications",
        "revoke all on public.detail_publications from anon, authenticated",
        "grant select on public.detail_publications to authenticated",
        "grant all on public.detail_publications to service_role",
    ]


def test_detail_publication_cleanup_bounded_migration_allows_null_schedule():
    migrations = list((ROOT / "supabase/migrations").glob("20261025*_detail_publication_cleanup_bounded.sql"))
    assert len(migrations) == 1
    sql = " ".join(migrations[0].read_text(encoding="utf-8").split()).lower()
    assert "alter table public.detail_publications alter column cleanup_after drop not null;" in sql
    assert "comment on column public.detail_publications.cleanup_after" in sql
    assert "null이면 더 이상 재확인하지 않는다" in sql
