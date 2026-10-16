import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from app.agents import similar_cut
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings
from test_similar_cut_conditions import new_plan


def test_similar_planner_settings_default_to_flash():
    assert make_settings().model_text_gemini_similar == "gemini-3.8-flash"


@pytest.mark.parametrize("value, expected", [
    (None, "gemini-3.8-flash"),
    ("", "gemini-3.8-flash"),
    ("gemini-test-similar", "gemini-test-similar"),
])
def test_similar_planner_env_is_independent_of_shared_text_model(monkeypatch, value, expected):
    monkeypatch.setenv("MODEL_ROUTING_TEXT_GEMINI", "gemini-3.1-pro-preview")
    if value is None:
        monkeypatch.delenv("MODEL_ROUTING_TEXT_GEMINI_SIMILAR", raising=False)
    else:
        monkeypatch.setenv("MODEL_ROUTING_TEXT_GEMINI_SIMILAR", value)

    settings = load_settings()

    assert settings.model_text_gemini_similar == expected
    assert settings.model_text_gemini == "gemini-3.1-pro-preview"


def _plan():
    return new_plan()


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-test-similar"])
@pytest.mark.parametrize("timeout", [10.0, 60.0])
def test_planner_passes_dedicated_model_and_low_thinking(monkeypatch, model, timeout):
    monkeypatch.setenv("MODEL_ROUTING_TEXT_GEMINI_SIMILAR", model)
    settings = replace(load_settings(), analysis_timeout_seconds=timeout,
                       model_text_gemini="gemini-3.1-pro-preview",
                       model_text="gpt-test-fallback", analysis_model_order="gemini,gpt")
    source = InlineImage("image/jpeg", b"source")
    analyze = AsyncMock(return_value=(_plan(), "gemini"))
    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)

    result = asyncio.run(similar_cut.plan_next(
        settings, source, context={}, cut_type="styling", changes=[], history=[],
    ))

    assert result == _plan()
    analyze.assert_awaited_once()
    bounded_settings, _prompt, images, _schema = analyze.await_args.args
    assert images == [source]
    assert analyze.await_args.kwargs == {
        "models": {"gemini": model},
        "thinking_level": "low",
        "require_complete_envelope": True,
    }
    assert bounded_settings.analysis_timeout_seconds == min(timeout, 25)
    assert bounded_settings.model_text_gemini == settings.model_text_gemini
    assert bounded_settings.model_text == settings.model_text
    assert bounded_settings.analysis_model_order == "gemini,gpt"
    assert settings.analysis_timeout_seconds == timeout


def test_planner_uses_flash_with_legacy_settings_without_dedicated_field(monkeypatch):
    @dataclass(frozen=True)
    class LegacySettings:
        analysis_timeout_seconds: float = 60.0
        model_text_gemini: str = "gemini-3.1-pro-preview"

    analyze = AsyncMock(return_value=(_plan(), "gemini"))
    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)

    result = asyncio.run(similar_cut.plan_next(
        LegacySettings(), InlineImage("image/jpeg", b"source"),
        context={}, cut_type="styling", changes=[], history=[],
    ))

    assert result == _plan()
    analyze.assert_awaited_once()
    assert analyze.await_args.kwargs["models"] == {"gemini": "gemini-3.8-flash"}
    assert analyze.await_args.kwargs["thinking_level"] == "low"


@pytest.mark.parametrize("service", ["api", "detail-worker"])
def test_production_similar_planner_uses_flash_in_both_services(service):
    manifest_path = Path(__file__).resolve().parents[2] / f"copilot/{service}/manifest.yml"
    variables = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["variables"]

    assert variables["MODEL_ROUTING_TEXT_GEMINI_SIMILAR"] == "gemini-3.8-flash"
    assert variables["MODEL_ROUTING_TEXT_GEMINI"] == "gemini-3.1-pro-preview"
