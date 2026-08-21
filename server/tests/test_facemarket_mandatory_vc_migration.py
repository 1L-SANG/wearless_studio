from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821010000_facemarket_mandatory_vc.sql"
)
PREDECESSOR = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260821000000_facemarket_biometric_runtime.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text(encoding="utf-8").split()).lower()


def test_predecessor_owns_the_expanded_license_status_check():
    sql = " ".join(PREDECESSOR.read_text(encoding="utf-8").split()).lower()
    assert "fm_licenses_status_check" in sql
    assert "'pending'" in sql and "'reverification_required'" in sql


def test_license_status_defaults_pending_without_rewriting_existing_rows():
    sql = _sql()
    assert "alter column status set default 'pending'" in sql
    assert "fm_licenses_status_check" not in sql
    assert "update public.fm_licenses" not in sql


def test_revocation_queue_is_durable_idempotent_and_service_private():
    sql = _sql()
    assert "create table if not exists public.fm_vc_revocation_jobs" in sql
    assert "vc_id text not null unique" in sql
    assert "status in ('pending', 'processing', 'retry', 'revoked')" in sql
    assert "next_attempt_at" in sql and "lease_expires_at" in sql
    assert "enable row level security" in sql
