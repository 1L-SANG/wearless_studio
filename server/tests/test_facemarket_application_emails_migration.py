"""지원서 메일 원장 마이그레이션(T4/E4) 구조 검증 — SQL 텍스트 레벨."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260902130000_facemarket_application_emails.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_creates_email_ledger_table():
    assert "create table if not exists public.fm_model_application_emails" in _sql()


def test_email_type_and_status_checks():
    sql = _sql()
    for v in ("approved", "rejected", "pending", "sent", "failed"):
        assert v in sql, v


def test_references_application_cascade():
    sql = _sql()
    assert "references public.fm_model_applications" in sql
    assert "on delete cascade" in sql


def test_provider_message_id_for_idempotency():
    assert "provider_message_id" in _sql()
