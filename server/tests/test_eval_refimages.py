import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture()
def eval_refimages(monkeypatch):
    from scripts import _env

    monkeypatch.setattr(_env, "load_env", lambda: None)
    sys.modules.pop("scripts.eval_refimages", None)
    module = importlib.import_module("scripts.eval_refimages")
    yield module
    sys.modules.pop("scripts.eval_refimages", None)


def test_gpt_backend_accepts_openai_only_settings(eval_refimages):
    settings = SimpleNamespace(gemini_api_key=None, openai_api_key="sk-test")

    assert eval_refimages._backend_key_error(settings, "gpt") is None


def test_backend_key_error_checks_selected_provider(eval_refimages):
    settings = SimpleNamespace(gemini_api_key=None, openai_api_key=None)

    assert "OPENAI_API_KEY" in eval_refimages._backend_key_error(settings, "gpt")
    assert "GEMINI_API_KEY" in eval_refimages._backend_key_error(settings, "gemini")


def test_active_model_uses_gpt_model_for_gpt_backend(eval_refimages):
    settings = SimpleNamespace(mannequin_tier="high")
    args = SimpleNamespace(backend="gpt", gpt_model="gpt-image-2")

    assert eval_refimages._active_generation_model(settings, args) == "gpt-image-2"
