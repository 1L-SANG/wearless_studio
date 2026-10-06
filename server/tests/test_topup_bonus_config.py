"""충전 보너스는 모든 지급 서버를 배포한 뒤 설정으로만 켠다."""

import pytest

from app.config import load_settings
from conftest import make_settings


def test_topup_bonus_is_off_when_settings_are_constructed_without_opt_in():
    assert getattr(make_settings(), "topup_bonus_enabled", None) is False


@pytest.mark.parametrize("raw,enabled", [
    (None, False), ("false", False), ("", False), ("yes", False),
    ("true", True), ("TRUE", True),
])
def test_topup_bonus_env_requires_explicit_true(monkeypatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("TOPUP_BONUS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TOPUP_BONUS_ENABLED", raw)

    assert getattr(load_settings(), "topup_bonus_enabled", None) is enabled
