from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260822000000_facemarket_purge_receipts.sql"


def test_purge_receipts_migration_is_service_only_and_pii_free():
    sql = MIGRATION.read_text().lower()

    assert "create table if not exists public.fm_biometric_purge_receipts" in sql
    assert "source_job_id uuid unique references public.jobs(id) on delete set null" in sql
    assert "reason text not null default 'account_delete'" in sql
    assert "outcome text not null default 'ready_for_identity_delete'" in sql
    assert "target_count integer not null" in sql
    assert "confirmed_absent_count integer not null" in sql
    assert "check (target_count = confirmed_absent_count)" in sql
    assert "alter table public.fm_biometric_purge_receipts enable row level security" in sql
    assert "revoke all on public.fm_biometric_purge_receipts from anon, authenticated" in sql
    forbidden = (
        "user_id",
        "model_id",
        "profile_id",
        "enrollment_id",
        "raw",
        "r2_key",
        "raw_key",
        "target_key",
        "digest",
        "ci_hash",
        "did",
        "vc_id",
        "face",
        "exception",
        "error_text",
    )
    assert not any(token in sql for token in forbidden)
