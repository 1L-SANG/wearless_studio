"""Cafe24 configuration and the shared registration gate."""

from dataclasses import replace

from app import config
from conftest import make_settings


def test_cafe24_ready_and_registration_enabled_require_all_flags(monkeypatch):
    ready = make_settings(multimall_register_enabled=True, mall_connect_enabled=True, detail_publish_enabled=True,
                          cafe24_enabled=True, cafe24_client_id="test-client",
                          cafe24_client_secret="test-secret", mall_keys_kek="test-kek")
    assert ready.cafe24_ready is True
    assert config.registration_enabled(ready) is True
    for field, missing in (("cafe24_enabled", False), ("cafe24_client_id", None),
                           ("cafe24_client_secret", None), ("mall_keys_kek", None),
                           ("cafe24_client_id", ""), ("cafe24_client_secret", ""), ("mall_keys_kek", "")):
        settings = replace(ready, **{field: missing})
        assert settings.cafe24_ready is False
        assert config.registration_enabled(settings) is False
    for field in ("multimall_register_enabled", "mall_connect_enabled", "detail_publish_enabled"):
        settings = replace(ready, **{field: False})
        assert settings.cafe24_ready is True
        assert config.registration_enabled(settings) is False

    for name in ("MULTIMALL_REGISTER_ENABLED", "CAFE24_ENABLED", "CAFE24_CLIENT_ID", "CAFE24_CLIENT_SECRET", "CAFE24_REDIRECT_URI",
                 "CAFE24_SCOPES", "CAFE24_API_VERSION", "CAFE24_ALLOWED_MALL_IDS"):
        monkeypatch.delenv(name, raising=False)
    defaults = config.load_settings()
    assert defaults.cafe24_ready is False
    assert defaults.multimall_register_enabled is False
    assert defaults.cafe24_redirect_uri == "https://api.wearless.kr/v1/malls/cafe24/callback"
    assert defaults.cafe24_api_origin == "https://api.wearless.kr"
    assert defaults.cafe24_scopes == "mall.read_product mall.write_product mall.read_store mall.read_category"
    assert defaults.cafe24_api_version == "2026-09-01"
    for name, value in {"CAFE24_ENABLED": "true", "CAFE24_CLIENT_ID": "test-client",
                        "CAFE24_CLIENT_SECRET": "test-secret", "MALL_KEYS_KEK": "test-kek",
                        "MALL_CONNECT_ENABLED": "true", "MULTIMALL_REGISTER_ENABLED": "true", "DETAIL_PUBLISH_ENABLED": "true",
                        "CAFE24_REDIRECT_URI": "http://localhost:9000/v1/malls/cafe24/callback?x=1",
                        "CAFE24_SCOPES": "mall.read_store", "CAFE24_API_VERSION": "2026-08-01"}.items():
        monkeypatch.setenv(name, value)
    settings = config.load_settings()
    assert settings.cafe24_ready is True
    assert config.registration_enabled(settings) is True
    assert settings.cafe24_api_origin == "http://localhost:9000"
    assert settings.cafe24_scopes == "mall.read_store"
    assert settings.cafe24_api_version == "2026-08-01"


def test_cafe24_allowed_mall_ids_parses_csv_and_empty_means_open(monkeypatch):
    monkeypatch.setenv("CAFE24_ALLOWED_MALL_IDS", " first, second ,,first, ")
    settings = config.load_settings()
    assert settings.cafe24_allowed_mall_ids == frozenset({"first", "second"})
    assert config.is_allowed_mall(settings, "first") is True
    assert config.is_allowed_mall(settings, "other") is False
    for value in ("", " , , ", None):
        if value is None:
            monkeypatch.delenv("CAFE24_ALLOWED_MALL_IDS", raising=False)
        else:
            monkeypatch.setenv("CAFE24_ALLOWED_MALL_IDS", value)
        settings = config.load_settings()
        assert settings.cafe24_allowed_mall_ids == frozenset()
        assert config.is_allowed_mall(settings, "any-shop") is True
