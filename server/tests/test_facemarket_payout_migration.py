"""지급 테이블의 격리와 기존 마이그레이션 불변 계약."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "supabase/migrations"


def test_existing_migrations_remain_unchanged():
    files = sorted(p for p in ROOT.glob("*.sql") if p.name[:14] < "20260911140000")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    # main의 전진 마이그레이션 13개를 포함한 병합 시점 스냅샷이에요.
    assert len(files) == 86
    assert digest.hexdigest() == "7f1c8d70582ceca797d825cc8a431b0736bb4d55b2a4bf9b4db34458ec3e10e4"


def test_accounts_are_encrypted_and_service_role_only():
    sql = " ".join((ROOT / "20260911140000_fm_payout_accounts.sql").read_text().lower().split())
    assert "account_number_enc text not null" in sql
    assert "account_number text" not in sql
    assert "account_last4 ~ '^[0-9]{4}$'" in sql
    assert "alter table public.fm_payout_accounts enable row level security" in sql
    assert "create policy" not in sql
    assert "execute function public.set_updated_at()" in sql


def test_statements_have_one_row_per_model_month_and_valid_amounts():
    sql = " ".join((ROOT / "20260911140100_fm_payout_statements.sql").read_text().lower().split())
    assert "unique (model_id, period_month)" in sql
    assert "check (amount >= 0)" in sql and "check (count >= 0)" in sql
    assert "status in ('scheduled','paid','held')" in sql
    assert "alter table public.fm_payout_statements enable row level security" in sql
    assert "create policy" not in sql
    assert "execute function public.set_updated_at()" in sql
