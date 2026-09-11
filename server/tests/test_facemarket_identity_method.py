import pytest

from app.config import load_settings


def _settings(monkeypatch, **env):
    for key in (
        "FM_IDENTITY_METHODS", "FM_ENROLLMENT_REVIEW", "FM_OACX_SIMPLE_AUTH_CONTRACT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def test_identity_methods_default_is_mid_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_identity_methods == ("mid",)


def test_identity_methods_parses_csv(monkeypatch):
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,simple_auth")
    assert settings.fm_identity_methods == ("mid", "simple_auth")


def test_identity_methods_rejects_unknown_value(monkeypatch):
    # 오타가 조용히 통과해 인증 수단이 통째로 사라지는 일을 막는다.
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,bogus")
    assert settings.fm_identity_methods == ("mid",)


def test_identity_methods_fallback_to_mid_when_all_unknown(monkeypatch):
    # 오타가 조용히 통과해 인증 수단이 통째로 사라지는 일을 막는다.
    # FM_IDENTITY_METHODS="bogus" 일 때 결과는 ("mid",) 이어야 한다.
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="bogus")
    assert settings.fm_identity_methods == ("mid",)


def test_review_default_is_simple_auth_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_enrollment_review == "simple_auth_only"


def test_simple_auth_contract_default_is_disabled(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_oacx_simple_auth_contract == "disabled"
