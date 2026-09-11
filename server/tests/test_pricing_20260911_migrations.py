"""2026-09-11 가격 및 크레딧 환율 전진 마이그레이션 계약."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LICENSE_MIGRATION = ROOT / "supabase/migrations/20260911120000_fm_licenses_unit_price_default_14900.sql"
CREDIT_MIGRATION = ROOT / "supabase/migrations/20260911120100_credit_fx_100won_2cr.sql"


def _sql(path):
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_license_default_migration_moves_forward_to_platform_price():
    sql = _sql(LICENSE_MIGRATION)

    assert "alter table public.fm_licenses alter column unit_price set default 14900;" in sql
    assert "update public.fm_licenses" not in sql


def test_credit_fx_migration_updates_only_active_catalog_rows_at_expected_old_values():
    sql = _sql(CREDIT_MIGRATION)
    changes = (
        ("starter", "subscription", 6000, 600),
        ("seller", "subscription", 18000, 1800),
        ("pro", "subscription", 38000, 3800),
        ("topup_finish", "topup", 1800, 180),
        ("topup_start", "topup", 4700, 470),
        ("topup_repeat", "topup", 13800, 1380),
        ("topup_season", "topup", 30500, 3050),
        ("topup_bulk", "topup", 64000, 6400),
    )

    assert sql.startswith("--")
    assert "begin;" in sql and sql.endswith("commit;")
    for code, kind, old, new in changes:
        statement = (
            f"update public.pricing_plans set credits = {new} "
            f"where code = '{code}' and kind = '{kind}' and credits = {old} and is_active = true;"
        )
        assert statement in sql


def test_credit_fx_migration_cancels_unsafe_pending_finish_orders_only():
    sql = _sql(CREDIT_MIGRATION)

    assert (
        "update public.toss_payment_orders set status = 'canceled' "
        "where status = 'pending' and plan_code = 'topup_finish' and credits = 1800;"
    ) in sql
    for table in ("credit_accounts", "credit_sources", "credit_ledger", "subscription_invoices"):
        assert f"update public.{table}" not in sql
