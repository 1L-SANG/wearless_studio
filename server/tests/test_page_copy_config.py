"""Page-copy routing is independent and rejects malformed provider chains."""

import pytest

from app.config import load_settings


GROUPS = ("intro", "points", "fabric", "voice", "misc")


@pytest.mark.parametrize("raw,expected,warns", [
    (None, "gemini,gpt", False),
    ("gemini,gpt", "gemini,gpt", False),
    (" GPT , Claude , Gemini ", "gpt,claude,gemini", False),
    ("gpt", "gpt", False),
    ("gemini", "gemini", False),
    ("claude", "claude", False),
    ("claude,gpt", "claude,gpt", False),
    ("", "gemini,gpt", True),
    ("  ", "gemini,gpt", True),
    ("gemni,gpt", "gemini,gpt", True),
    ("gpt,gpt", "gemini,gpt", True),
    ("gpt,,gemini", "gemini,gpt", True),
    ("gpt,", "gemini,gpt", True),
    ("other", "gemini,gpt", True),
])
def test_page_copy_provider_order(monkeypatch, caplog, raw, expected, warns):
    if raw is None:
        monkeypatch.delenv("MODEL_ORDER_PAGE_COPY", raising=False)
    else:
        monkeypatch.setenv("MODEL_ORDER_PAGE_COPY", raw)
    monkeypatch.setenv("MODEL_ORDER_FEATURES", "gpt,gemini")
    monkeypatch.setenv("MODEL_ORDER_POINTS_MERGE", "gemini")
    settings = load_settings()
    assert settings.model_order_page_copy == expected
    assert settings.model_order_features == "gpt,gemini"
    assert settings.model_order_points_merge == "gemini"
    warnings = [r.getMessage() for r in caplog.records if "MODEL_ORDER_PAGE_COPY;" in r.getMessage()]
    assert warnings == (["Invalid MODEL_ORDER_PAGE_COPY; using gemini,gpt"] if warns else [])


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("raw,expected,warns", [
    (None, "", False), ("", "", False), (" \t", "", False),
    (" Claude , GPT ", "claude,gpt", False), ("gemini", "gemini", False),
    ("gpt,gpt", "", True), ("gemni", "", True), ("gpt,", "", True),
])
def test_page_copy_group_order_preserves_inheritance(monkeypatch, caplog, group, raw, expected, warns):
    env = f"MODEL_ORDER_PAGE_COPY_{group.upper()}"
    monkeypatch.setenv("MODEL_ORDER_PAGE_COPY", "gpt,claude")
    if raw is None:
        monkeypatch.delenv(env, raising=False)
    else:
        monkeypatch.setenv(env, raw)
    settings = load_settings()
    assert getattr(settings, f"model_order_page_copy_{group}") == expected
    assert settings.model_order_page_copy == "gpt,claude"
    warnings = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Invalid "+env+";")]
    assert warnings == ([f"Invalid {env}; using inherited MODEL_ORDER_PAGE_COPY"] if warns else [])


@pytest.mark.parametrize("env,field", [
    ("MODEL_ROUTING_TEXT_GEMINI_PAGE_COPY", "model_routing_text_gemini_page_copy"),
    ("MODEL_TEXT_GPT_PAGE_COPY", "model_text_gpt_page_copy"),
    ("MODEL_TEXT_CLAUDE_PAGE_COPY", "model_text_claude_page_copy"),
])
@pytest.mark.parametrize("raw,expected", [(None, ""), ("", ""), ("  ", ""), (" custom-model ", "custom-model")])
def test_page_copy_model_overrides_are_optional_and_independent(monkeypatch, env, field, raw, expected):
    if raw is None:
        monkeypatch.delenv(env, raising=False)
    else:
        monkeypatch.setenv(env, raw)
    monkeypatch.setenv("MODEL_ROUTING_TEXT", "shared-gpt")
    monkeypatch.setenv("MODEL_ROUTING_TEXT_GEMINI", "shared-gemini")
    monkeypatch.setenv("MODEL_ROUTING_TEXT_GPT_TERRA", "feature-terra")
    settings = load_settings()
    assert getattr(settings, field) == expected
    assert settings.model_text == "shared-gpt"
    assert settings.model_text_gemini == "shared-gemini"
    assert settings.model_text_gpt_terra == "feature-terra"




@pytest.mark.parametrize("raw,expected", [
    (None, "v2"), ("", "v2"), ("v1", "v1"), (" V2 ", "v2"),
    ("v3", "v2"), ("feature_v2", "v2"),
])
def test_page_copy_prompt_version_defaults_v2(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("PAGE_COPY_PROMPT_VERSION", raising=False)
    else:
        monkeypatch.setenv("PAGE_COPY_PROMPT_VERSION", raw)
    assert load_settings().page_copy_prompt_version == expected


def test_direct_settings_rejects_unsupported_page_copy_prompt_version():
    from conftest import make_settings

    with pytest.raises(ValueError, match="PAGE_COPY_PROMPT_VERSION"):
        make_settings(page_copy_prompt_version="v3")


def test_legacy_provider_order_retains_shared_model_defaults(monkeypatch, caplog):
    monkeypatch.delenv('PAGE_COPY_ROUTES', raising=False)
    monkeypatch.delenv('MODEL_ROUTING_TEXT', raising=False)
    monkeypatch.delenv('MODEL_TEXT_GPT_PAGE_COPY', raising=False)
    monkeypatch.setenv('MODEL_ORDER_PAGE_COPY', 'gpt')
    settings = load_settings()
    assert settings.page_copy_routes == f'gpt:{settings.model_text}'
    assert 'deprecated' in caplog.text


@pytest.mark.parametrize('raw,expected', [('17.5', 17.5), ('0', 90), ('-1', 90), ('nan', 90), ('inf', 90), ('oops', 90)])
def test_page_copy_timeout_is_finite_and_positive(monkeypatch, raw, expected):
    monkeypatch.setenv('PAGE_COPY_TIMEOUT_SECONDS', raw)
    assert load_settings().page_copy_timeout_seconds == expected
