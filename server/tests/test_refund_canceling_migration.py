from pathlib import Path


def test_refund_canceling_migration_blocks_reject_after_external_cancel_start():
    path = Path(__file__).parents[2] / "supabase/migrations/20261106084254_refund_canceling_state.sql"
    sql = path.read_text()
    assert "'pending', 'canceling', 'approved', 'rejected'" in sql
    assert "status in ('pending', 'canceling')" in sql
    assert "refund_requests_open_unique_idx" in sql
