import pytest

from app import cx_identity
from app.config import load_settings
from conftest import make_settings


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


def test_simple_auth_contract_blocked_when_disabled():
    settings = make_settings(fm_oacx_simple_auth_contract="disabled")
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")


def test_simple_auth_contract_returned_when_enabled():
    settings = make_settings(fm_oacx_simple_auth_contract="simple-auth-v1")
    contract = cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")
    assert contract.version == "simple-auth-v1"
    # 간편인증은 신분증 초상을 주지 않는다 — 초상 상한이 0이어야 릴레이 시도가 막힌다.
    assert contract.max_portrait_bytes == 0


def test_mid_contract_unchanged_by_new_flag():
    settings = make_settings(
        fm_oacx_contract_mode="prod-dlphoto-v1",
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    contract = cx_identity.get_oacx_biometric_contract(settings, method="mid")
    assert contract.version == "prod-dlphoto-v1"
    assert contract.max_portrait_bytes == 5 * 1024 * 1024


def test_method_defaults_to_mid():
    settings = make_settings(fm_oacx_contract_mode="prod-dlphoto-v1")
    assert cx_identity.get_oacx_biometric_contract(settings).version == "prod-dlphoto-v1"


def test_simple_auth_evidence_parses_ci_name_birth():
    contract = cx_identity.SIMPLE_AUTH_CONTRACT
    evidence = cx_identity.parse_simple_auth_evidence(
        {"ci": "CI-VALUE", "name": "홍길동", "birth": "19900101", "txId": "tx-1"},
        contract=contract,
    )
    assert bytes(evidence.ci) == b"CI-VALUE"
    assert evidence.birth == "19900101"
    assert evidence.name_masked == "홍*동"
    assert evidence.contract_version == "simple-auth-v1"


def test_simple_auth_evidence_requires_ci():
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.parse_simple_auth_evidence(
            {"name": "홍길동", "birth": "19900101"},
            contract=cx_identity.SIMPLE_AUTH_CONTRACT,
        )
