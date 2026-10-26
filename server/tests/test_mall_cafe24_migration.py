"""Cafe24 extends existing constraints using one append-only migration."""

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261027100000_mall_cafe24.sql"


def sql():
    assert MIGRATION.exists(), "Cafe24 migration is missing"
    return " ".join(re.sub(r"--[^\n]*", " ", MIGRATION.read_text()).lower().split())


def test_mall_check_constraints_include_cafe24_and_keep_existing():
    statement = sql()
    for table in ("mall_connections", "mall_defaults"):
        drop = f"alter table public.{table} drop constraint if exists {table}_mall_check"
        add = f"alter table public.{table} add constraint {table}_mall_check"
        assert statement.index(drop) < statement.index(add)
        check = statement.split(add)[1].split(";")[0]
        assert re.findall(r"'([^']+)'", check) == ["zigzag", "ably", "smartstore", "cafe24"]


def test_mall_connections_token_columns():
    statement = sql()
    for column in ("token_expires_at", "refresh_expires_at"):
        assert f"add column if not exists {column} timestamptz" in statement
    assert "add column if not exists scopes text[] not null default '{}'" in statement


def test_install_attempts_table_server_only_with_lifecycle_columns():
    statement = sql()
    table = statement.split("create table if not exists public.mall_install_attempts (")[1].split(";")[0]
    for column in ("started_at", "consumed_at", "saved_at", "voided_at"):
        assert f"{column} timestamptz" in table
    for definition in ("id uuid primary key default gen_random_uuid()",
                       "user_id uuid not null references auth.users(id) on delete cascade",
                       "mall text not null check (mall in ('cafe24'))", "mall_id text not null",
                       "expected_generation text not null", "return_to text",
                       "cookie_hash bytea not null", "expires_at timestamptz not null",
                       "created_at timestamptz not null default now()"):
        assert definition in table
    assert "on public.mall_install_attempts (user_id, mall, created_at desc)" in statement
    assert "alter table public.mall_install_attempts enable row level security" in statement
    assert "revoke all on public.mall_install_attempts from anon, authenticated" in statement
    assert "grant all on public.mall_install_attempts to service_role" in statement
    assert "create policy" not in statement
    assert not re.search(r"grant .*? to (?:anon|authenticated)", statement)
    assert "access_token" not in table and "refresh_token" not in table


def test_jobs_kind_check_has_fourteen_kinds_and_keeps_active_index():
    statement = sql()
    previous = (MIGRATION.parent / "20260813010000_matching_cutout_job_kind.sql").read_text()
    prior_check = re.search(r"check \(kind in \((.*?)\)\)", previous, re.S).group(1)
    check = re.search(r"check \(kind in \((.*?)\)\)", statement).group(1)
    expected = re.findall(r"'([^']+)'", prior_check) + ["mall_register"]
    assert len(expected) == 14
    assert re.findall(r"'([^']+)'", check) == expected
    assert statement.index("drop constraint if exists jobs_kind_check") < statement.index("add constraint jobs_kind_check")
    assert "jobs_active_unique_idx" not in statement
    assert "project_id" not in statement


def test_migration_touches_no_existing_files():
    assert MIGRATION.exists()
    assert list(MIGRATION.parent.glob("20261027100000*.sql")) == [MIGRATION]
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=MDR", "HEAD", "--", "supabase/migrations"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert not changed.stdout.strip(), changed.stdout
