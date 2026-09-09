"""구독 스키마 회귀 — 마이그레이션 파일의 계약을 문자열로 고정한다.

DB 없이 검증한다(CI 에 Postgres 가 없다). 여기서 지키는 것은 '돈이 새지 않는 구조':
빌링키가 평문 컬럼이 아니고, RLS 가 켜져 있고, 같은 주기를 두 번 청구할 수 없다.
"""

from pathlib import Path

import pytest

MIGRATION = (Path(__file__).resolve().parents[2]
             / "supabase/migrations/20260909100000_subscriptions.sql")


@pytest.fixture(scope="module")
def sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_billing_key_is_encrypted_not_plaintext(sql):
    assert "billing_key_enc bytea not null" in sql
    assert "billing_key text" not in sql


def test_rls_enabled_on_both_tables(sql):
    assert "alter table public.subscriptions enable row level security;" in sql
    assert "alter table public.subscription_invoices enable row level security;" in sql
    assert "create policy" not in sql          # 정책 0개 = service_role 전용


def test_one_subscription_per_user(sql):
    assert "user_id uuid not null unique references auth.users" in sql


def test_same_period_cannot_be_charged_twice(sql):
    assert "subscription_invoices_period_idx" in sql
    assert "(subscription_id, period_start)" in sql


def test_terminal_status_cannot_be_scheduled(sql):
    assert "status in ('canceled', 'ended') and next_billing_at is null" in sql
