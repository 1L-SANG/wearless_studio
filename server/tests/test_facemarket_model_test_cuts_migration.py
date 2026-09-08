"""모델 테스트컷 확인 게이트 마이그레이션 계약."""

from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260907120000_fm_model_test_cuts.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_model_confirmation_state_and_audit_columns_exist():
    sql = _sql()
    assert "awaiting_confirm" in sql
    for column in (
        "confirm_requested_at",
        "confirmed_at",
        "confirm_consent_version",
        "redo_count",
    ):
        assert column in sql, column
    assert "redo_count >= 0" in sql


def test_private_test_cut_ledger_has_owner_fk_and_approval_state():
    sql = _sql()
    assert "create table public.fm_model_test_cuts" in sql
    assert "references public.fm_models(id) on delete cascade" in sql
    for column in ("r2_key", "mime", "sort", "approved", "created_at"):
        assert column in sql, column
    assert "approved boolean" in sql


def test_test_cut_ledger_is_rls_protected_without_direct_select_policy():
    sql = _sql()
    assert "alter table public.fm_model_test_cuts enable row level security" in sql
    assert "create policy" not in sql


def test_model_cut_order_has_a_stable_unique_index():
    sql = _sql()
    assert "fm_model_test_cuts_model_sort_unique" in sql
    assert "unique" in sql
