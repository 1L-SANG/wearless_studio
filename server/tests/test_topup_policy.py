from datetime import datetime, timezone

from app import topup_policy


def test_naive_admin_date_uses_kst_and_cutoff_is_exact():
    before = topup_policy.normalize_purchase_at("2026-11-22T23:59:59")
    effective = topup_policy.normalize_purchase_at("2026-11-23")
    assert before == datetime(2026, 11, 22, 14, 59, 59, tzinfo=timezone.utc)
    assert effective == datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
    assert topup_policy.validity_applies(before) is False
    assert topup_policy.validity_applies(effective) is True


def test_toss_z_timestamp_is_normalized_without_changing_the_instant():
    instant = topup_policy.normalize_purchase_at("2026-11-24T00:00:00Z")
    assert instant == datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
