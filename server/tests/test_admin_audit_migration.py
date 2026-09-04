"""admin_audit_log 마이그레이션 구조 검증 — SQL 텍스트 레벨.

원장은 행위자보다 오래 살아야 한다(actor on delete set null). 관리자 계정을 지웠다고
그 관리자가 무엇을 했는지가 사라지면 원장이 아니다.
"""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260904100000_admin_audit_log.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_creates_audit_table():
    assert "create table if not exists public.admin_audit_log" in _sql()


def test_actor_survives_account_deletion():
    sql = _sql()
    actor = sql.split("actor_user_id", 1)[1].split(",", 1)[0]
    assert "references auth.users(id) on delete set null" in actor


def test_records_before_and_after_as_jsonb():
    sql = _sql()
    for column in ("before jsonb", "after jsonb"):
        assert column in sql, column


def test_target_id_is_text_not_uuid():
    """대상이 uuid 가 아닌 액션도 있다(환불 요청 id 는 uuid 지만, 앞으로 늘어난다)."""
    assert "target_id text" in _sql()


def test_listing_and_target_indexes_exist():
    sql = _sql()
    assert "admin_audit_log_created_idx" in sql
    assert "admin_audit_log_target_idx" in sql


def test_overview_aggregation_indexes_exist():
    """대시보드 집계가 순차 스캔으로 떨어지지 않게 — 5.3 절."""
    sql = _sql()
    for index in (
        "fm_model_applications (status)",
        "fm_model_applications (created_at)",
        "fm_licenses (created_at)",
        "fm_settlements (chain_status, created_at)",
        "payment_history (status, created_at)",
    ):
        assert index in sql, index
