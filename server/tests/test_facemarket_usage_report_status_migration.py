"""C3 전진 마이그레이션이 신고 상태를 열림과 닫힘으로 제한하는지 확인한다."""

from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260911131500_facemarket_usage_report_status.sql"
)


def _sql():
    return " ".join(MIGRATION.read_text(encoding="utf-8").split()).lower()


def test_usage_report_status_migration_replaces_the_open_only_constraint():
    assert MIGRATION.exists()
    sql = _sql()
    assert "drop constraint if exists fm_usage_reports_status_check" in sql
    assert "add constraint fm_usage_reports_status_check" in sql
    assert "status in ('open', 'closed')" in sql
    assert "drop policy" not in sql
    assert "disable row level security" not in sql
