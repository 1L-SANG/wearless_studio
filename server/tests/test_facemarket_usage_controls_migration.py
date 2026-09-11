"""C1 전진 마이그레이션의 최소 스키마 계약."""

from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260911120000_facemarket_usage_controls.sql"
)


def _sql():
    return " ".join(MIGRATION.read_text(encoding="utf-8").split()).lower()


def test_usage_controls_migration_exists():
    assert MIGRATION.exists()


def test_usage_report_is_unique_per_settlement_and_owner_scoped():
    sql = _sql()
    assert "create table public.fm_usage_reports" in sql
    assert "settlement_id uuid not null unique" in sql
    assert "alter table public.fm_usage_reports enable row level security" in sql
    assert "fm_usage_reports_owner_select" in sql


def test_terms_history_and_permanent_validity_are_supported():
    sql = _sql()
    assert "create table public.fm_license_term_changes" in sql
    assert "before jsonb not null" in sql
    assert "after jsonb not null" in sql
    assert "actor uuid not null" in sql
    assert "alter column license_valid_until drop not null" in sql


def test_model_suspension_source_is_constrained():
    sql = _sql()
    assert "add column suspension_source text" in sql
    assert "add column suspended_at timestamptz" in sql
    assert "suspension_source in ('owner', 'admin')" in sql
