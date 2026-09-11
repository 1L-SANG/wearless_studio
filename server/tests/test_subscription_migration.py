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


def test_crypto_wrappers_pin_search_path(sql):
    """Supabase 는 pgcrypto 를 extensions 스키마에 깐다. 앱 커넥션은 search_path 를
    건드리지 않으므로, 함수에 search_path 를 박아 두지 않으면 로컬에서는 되고
    prod 에서만 `function pgp_sym_encrypt does not exist` 로 죽는다."""
    for fn in ("wl_billing_encrypt", "wl_billing_decrypt"):
        assert f"function public.{fn}" in sql
    assert sql.count("set search_path = public, extensions") == 2


def test_crypto_wrappers_are_not_callable_by_api_roles(sql):
    """복호화 함수를 anon/authenticated 가 부를 수 있으면 RLS 로 테이블을 막은 의미가 없다."""
    for fn in ("wl_billing_encrypt(text, text)", "wl_billing_decrypt(bytea, text)"):
        assert f"revoke all on function public.{fn} from public, anon, authenticated;" in sql


def test_app_code_never_calls_pgcrypto_directly():
    """호출부는 래퍼만 쓴다 — 한 곳이라도 직접 부르면 그 경로만 prod 에서 터진다."""
    app_dir = MIGRATION.parents[1] / "server/app"
    offenders = [
        path.name for path in app_dir.rglob("*.py")
        if "pgp_sym_" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
