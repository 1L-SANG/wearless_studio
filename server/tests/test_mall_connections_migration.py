"""Mall secrets have server-only writes and owner-scoped reads."""

import re
from pathlib import Path

from app.config import load_settings


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261024120000_mall_connections.sql"


def sql():
    assert MIGRATION.exists(), "mall connections migration is missing"
    return " ".join(re.sub(r"--[^\n]*", " ", MIGRATION.read_text()).lower().split())


def test_mall_connections_migration_creates_tables_and_partial_unique_index():
    statement = sql()
    for table in ("mall_connections", "mall_defaults"):
        assert f"create table if not exists public.{table}" in statement
    assert "(user_id, mall) where status = 'connected'" in statement
    assert "access_key_enc bytea" in statement and "secret_key_enc bytea" in statement
    assert "primary key (user_id, mall)" in statement
    assert "shop_id text" in statement.split("create table if not exists public.mall_defaults")[1]
    assert "references auth.users(id) on delete cascade" in statement


def test_mall_connections_migration_has_only_owner_select_policies():
    statement = sql()
    assert statement.count("enable row level security") == 2
    assert statement.count("for select to authenticated") == 2
    assert statement.count("auth.uid()") == 2
    for action in ("insert", "update", "delete", "all"):
        assert f"for {action} to authenticated" not in statement
    assert "revoke all on public.mall_connections from anon, authenticated" in statement


def test_mall_connections_migration_timestamp_is_unique():
    assert MIGRATION.exists()
    assert list(MIGRATION.parent.glob("20261024120000*.sql")) == [MIGRATION]


def test_mall_connect_settings_default_off_and_use_separate_kek(monkeypatch):
    for name in ("MALL_CONNECT_ENABLED", "MALL_KEYS_KEK", "ZIGZAG_CONNECT_ENABLED", "ZIGZAG_OPENAPI_URL", "ZIGZAG_HMAC_ALGORITHM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOSS_BILLING_KEK", "billing-only")
    settings = load_settings()
    assert settings.mall_connect_enabled is False
    assert settings.zigzag_connect_enabled is False
    assert settings.mall_keys_kek is None
    assert settings.zigzag_hmac_algorithm == "sha1"
    assert settings.zigzag_openapi_url == "https://openapi.zigzag.kr/1/graphql"
    monkeypatch.setenv("MALL_CONNECT_ENABLED", "true")
    monkeypatch.setenv("ZIGZAG_CONNECT_ENABLED", "TRUE")
    monkeypatch.setenv("MALL_KEYS_KEK", "mall-only")
    monkeypatch.setenv("ZIGZAG_HMAC_ALGORITHM", "sha256")
    monkeypatch.setenv("ZIGZAG_OPENAPI_URL", "https://alpha.invalid/graphql")
    settings = load_settings()
    assert settings.mall_connect_enabled is True
    assert settings.zigzag_connect_enabled is True
    assert settings.mall_keys_kek == "mall-only"
    assert settings.zigzag_hmac_algorithm == "sha256"
    assert settings.zigzag_openapi_url == "https://alpha.invalid/graphql"
