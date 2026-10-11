from dataclasses import replace

import pytest

from app.config import load_settings
from conftest import make_settings


@pytest.mark.parametrize("core,specialist", [
    ("claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("claude-sonnet-5-5", "gpt-5.4-2026-03-05"),
    ("gpt-5.4-2026-03-05", "claude-sonnet-5-5"),
])
@pytest.mark.parametrize("key", [None, "", " \t\n"])
def test_claude_qc_model_requires_nonblank_key(core, specialist, key):
    with pytest.raises(ValueError) as exc:
        make_settings(model_detail_core=core, model_detail_specialist=specialist,
                      anthropic_api_key=key)
    assert str(exc.value) == "Claude 검수 모델을 쓰려면 ANTHROPIC_API_KEY가 필요해요."


@pytest.mark.parametrize("core,specialist", [
    ("claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("claude-sonnet-5-5", "gpt-5.4-2026-03-05"),
    ("gpt-5.4-2026-03-05", "claude-sonnet-5-5"),
])
def test_claude_qc_model_accepts_key(core, specialist):
    settings = make_settings(model_detail_core=core, model_detail_specialist=specialist,
                             anthropic_api_key="test-claude-key")
    assert settings.anthropic_api_key == "test-claude-key"


@pytest.mark.parametrize("key", [None, "", " \t\n"])
def test_gpt_qc_models_do_not_require_claude_key(key):
    settings = make_settings(model_detail_core="gpt-5.4-2026-03-05",
                             model_detail_specialist="gpt-5.4-2026-03-05",
                             anthropic_api_key=key)
    assert settings.anthropic_api_key == key


@pytest.mark.parametrize("model", [None, 42])
def test_nonstring_qc_models_do_not_trigger_claude_key_guard(model):
    settings = make_settings(model_detail_core=model, model_detail_specialist=model,
                             anthropic_api_key="")
    assert settings.anthropic_api_key == ""


@pytest.mark.parametrize("key", [None, "", " \t\n"])
def test_replace_cannot_remove_claude_qc_key(key):
    settings = make_settings(model_detail_core="claude-sonnet-5-5",
                             anthropic_api_key="test-claude-key")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        replace(settings, anthropic_api_key=key)


@pytest.mark.parametrize("field", ["MODEL_DETAIL_CORE", "MODEL_DETAIL_SPECIALIST"])
def test_environment_claude_qc_model_requires_key(monkeypatch, field):
    monkeypatch.setenv("MODEL_DETAIL_CORE", "gpt-5.4-2026-03-05")
    monkeypatch.setenv("MODEL_DETAIL_SPECIALIST", "gpt-5.4-2026-03-05")
    monkeypatch.setenv(field, "claude-sonnet-5-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        load_settings()
