from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260822020000_facemarket_purge_manifests.sql"


def test_purge_manifest_is_private_durable_retry_state():
    sql = MIGRATION.read_text().lower()

    assert "create table if not exists public.fm_biometric_purge_manifests" in sql
    assert "scope_key text primary key" in sql
    assert "target_manifest jsonb not null" in sql
    assert "alter table public.fm_biometric_purge_manifests enable row level security" in sql
    assert "revoke all on public.fm_biometric_purge_manifests from anon, authenticated" in sql
