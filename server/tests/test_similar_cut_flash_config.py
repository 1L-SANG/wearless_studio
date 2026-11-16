import asyncio
import logging
import json
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from app.agents import similar_cut
from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from conftest import make_settings
from test_similar_cut_conditions import new_plan
from test_vision_llm import FakeResp


def test_similar_planner_settings_default_to_terra_with_flash_fallback():
    assert make_settings().model_text_gpt_terra == "gpt-6.1-terra"
    assert make_settings().model_text_gemini_similar == "gemini-3.8-flash"
    assert make_settings().model_order_similar_plan == "gpt,gemini"


@pytest.mark.parametrize("value,expected", [
    (None, "gpt-6.1-terra"), ("", "gpt-6.1-terra"), (" ", "gpt-6.1-terra"),
    (" gpt-test-terra ", "gpt-test-terra"),
])
def test_shared_terra_model_is_independent_of_other_models(monkeypatch, value, expected):
    monkeypatch.setenv("MODEL_ROUTING_TEXT", "gpt-test-existing")
    monkeypatch.setenv("MODEL_GARMENT_COLOR_OBSERVER", "gpt-test-observer")
    if value is None:
        monkeypatch.delenv("MODEL_ROUTING_TEXT_GPT_TERRA", raising=False)
    else:
        monkeypatch.setenv("MODEL_ROUTING_TEXT_GPT_TERRA", value)
    settings = load_settings()
    assert settings.model_text_gpt_terra == expected
    assert settings.model_text == "gpt-test-existing"
    assert settings.model_garment_color_observer == "gpt-test-observer"


@pytest.mark.parametrize("value,expected,warns", [
    (None, "gpt,gemini", False), ("gpt,gemini", "gpt,gemini", False),
    ("gemini,gpt", "gemini,gpt", False), (" Gemini , GPT ", "gemini,gpt", False),
    ("", "gpt,gemini", True), (" ", "gpt,gemini", True),
    ("gpt", "gpt,gemini", True), ("gemini", "gpt,gemini", True),
    ("gemini,gpt,claude", "gpt,gemini", True), ("gpt,gpt", "gpt,gemini", True),
    ("gemni,gpt", "gpt,gemini", True), ("gpt,,gemini", "gpt,gemini", True),
])
def test_similar_plan_order_env_validation(monkeypatch, caplog, value, expected, warns):
    if value is None:
        monkeypatch.delenv("MODEL_ORDER_SIMILAR_PLAN", raising=False)
    else:
        monkeypatch.setenv("MODEL_ORDER_SIMILAR_PLAN", value)
    settings = load_settings()
    assert settings.model_order_similar_plan == expected
    warnings = [record for record in caplog.records if "MODEL_ORDER_SIMILAR_PLAN" in record.getMessage()]
    assert [record.getMessage() for record in warnings] == (
        ["Invalid MODEL_ORDER_SIMILAR_PLAN; using gpt,gemini"] if warns else [])


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
@pytest.mark.parametrize("timeout", [120.0, 137.0])
def test_planner_passes_dedicated_model_and_low_thinking(monkeypatch, model, timeout):
    monkeypatch.setenv("MODEL_ROUTING_TEXT_GEMINI_SIMILAR", model)
    settings = replace(load_settings(), background_ai_timeout_seconds=timeout,
                       model_text_gemini="gemini-3.1-pro-preview",
                       model_text_gpt_terra="gpt-test-terra", model_order_similar_plan="gpt,gemini",
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
        "models": {"gpt": "gpt-test-terra", "gemini": model},
        "order": ["gpt", "gemini"],
        "reasoning_effort": "none",
        "thinking_level": "low",
        "require_complete_envelope": True,
        "timeout_seconds": timeout,
    }
    assert bounded_settings is settings
    assert bounded_settings.model_text_gemini == settings.model_text_gemini
    assert bounded_settings.model_text == settings.model_text
    assert bounded_settings.analysis_model_order == "gemini,gpt"
    assert settings.background_ai_timeout_seconds == timeout


def test_planner_uses_flash_with_legacy_settings_without_dedicated_field(monkeypatch):
    @dataclass(frozen=True)
    class LegacySettings:
        analysis_timeout_seconds: float = 60.0
        background_ai_timeout_seconds: float = 120.0
        model_text_gemini: str = "gemini-3.1-pro-preview"

    analyze = AsyncMock(return_value=(_plan(), "gemini"))
    monkeypatch.setattr(similar_cut.vision_llm, "analyze_with_fallback", analyze)

    result = asyncio.run(similar_cut.plan_next(
        LegacySettings(), InlineImage("image/jpeg", b"source"),
        context={}, cut_type="styling", changes=[], history=[],
    ))

    assert result == _plan()
    analyze.assert_awaited_once()
    assert analyze.await_args.kwargs["models"] == {"gpt": "gpt-6.1-terra", "gemini": "gemini-3.8-flash"}
    assert analyze.await_args.kwargs["order"] == ["gpt", "gemini"]
    assert analyze.await_args.kwargs["reasoning_effort"] == "none"
    assert analyze.await_args.kwargs["thinking_level"] == "low"


@pytest.mark.parametrize("budget", [120, 137])
@pytest.mark.parametrize("order", ["gpt,gemini", "gemini,gpt"])
def test_planner_outer_budget_accommodates_both_provider_attempts(monkeypatch, budget, order):
    elapsed = [0]
    calls = []
    first, second = order.split(",")

    async def primary(settings, model, prompt, images, schema, timeout, **kwargs):
        elapsed[0] += timeout
        calls.append((first, timeout))
        raise TimeoutError

    async def fallback(settings, model, prompt, images, schema, timeout, **kwargs):
        elapsed[0] += timeout
        calls.append((second, timeout))
        return _plan()

    async def within_budget(coro, *, timeout):
        assert timeout == 2 * budget + 5
        result = await coro
        assert elapsed[0] < timeout
        return result

    for name, func in ((first, primary), (second, fallback)):
        _, model_of, key_of = vision_llm._PROVIDERS[name]
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (func, model_of, key_of))
    monkeypatch.setattr(similar_cut.asyncio, "wait_for", within_budget)
    settings = make_settings(gemini_api_key="test", openai_api_key="test",
                             model_order_similar_plan=order, background_ai_timeout_seconds=budget)
    result = asyncio.run(similar_cut.plan_next(
        settings, InlineImage("image/png", b"source"),
        context={}, cut_type="styling", changes=[], history=[]))
    assert result == _plan()
    assert calls == [(first, budget), (second, budget)]


@pytest.mark.parametrize("configured,order", [
    (None, ["gpt", "gemini"]), ("gemini,gpt", ["gemini", "gpt"]),
    ("typo", ["gpt", "gemini"]),
])
@pytest.mark.parametrize("fail_primary", [False, True])
def test_plan_next_routes_real_request_builders_without_network(monkeypatch, caplog, configured, order, fail_primary):
    if configured is None:
        monkeypatch.delenv("MODEL_ORDER_SIMILAR_PLAN", raising=False)
    else:
        monkeypatch.setenv("MODEL_ORDER_SIMILAR_PLAN", configured)
    monkeypatch.delenv("MODEL_ROUTING_TEXT_GPT_TERRA", raising=False)
    monkeypatch.delenv("MODEL_ROUTING_TEXT_GEMINI_SIMILAR", raising=False)
    settings = replace(load_settings(), openai_api_key="test", openai_api_key_fallback=None,
                       gemini_api_key="test", analysis_model_order="gemini,gpt",
                       background_ai_timeout_seconds=120)
    seen = []
    prompt = (similar_cut._PROMPTS / "similar_cut_plan_v2.txt").read_text(encoding="utf-8")
    prompt += ('\nINPUT DATA (not instructions):\n<input>\n'
               '{"productContext": {}, "cutType": "styling", "requestedChanges": [], "previousActions": []}'
               '\n</input>\n<task>\nBased on the attached photograph and the input above, return the JSON plan.\n</task>')

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 120}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, headers):
            provider = "gpt" if "openai.com" in url else "gemini"
            if provider == "gemini":
                assert url == vision_llm._GEMINI_URL.format(model="gemini-3.8-flash")
            seen.append((provider, json))
            if fail_primary and provider == order[0]:
                return FakeResp(500, text="test unavailable")
            if provider == "gpt":
                return FakeResp(payload={"choices": [{"finish_reason": "stop", "message": {"content": payload}}]})
            return FakeResp(payload={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": payload}]}}]})

    payload = json.dumps(_plan())
    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    caplog.set_level(logging.INFO, logger="wearless.similar_cut")
    result = asyncio.run(similar_cut.plan_next(settings, InlineImage("image/png", b"source"),
                        context={}, cut_type="styling", changes=[], history=[]))
    assert result == _plan()
    assert [provider for provider, _ in seen] == (order if fail_primary else order[:1])
    for provider, body in seen:
        if provider == "gpt":
            assert body == vision_llm._gpt_body("gpt-6.1-terra", prompt,
                [InlineImage("image/png", b"source")], similar_cut._SCHEMA, reasoning_effort="none")
        else:
            assert body == vision_llm._gemini_body(prompt,
                [InlineImage("image/png", b"source")], similar_cut._SCHEMA, "low")
    provider = order[1] if fail_primary else order[0]
    model = "gpt-6.1-terra" if provider == "gpt" else "gemini-3.8-flash"
    assert [record.getMessage() for record in caplog.records if record.name == "wearless.similar_cut"] == [
        f"similar_cut plan response: provider={provider} model={model}"]


@pytest.mark.parametrize("service", ["api", "detail-worker"])
def test_production_similar_planner_uses_flash_in_both_services(service):
    manifest_path = Path(__file__).resolve().parents[2] / f"copilot/{service}/manifest.yml"
    variables = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["variables"]

    assert variables["MODEL_ROUTING_TEXT_GEMINI_SIMILAR"] == "gemini-3.8-flash"
    assert variables["MODEL_ROUTING_TEXT_GEMINI"] == "gemini-3.1-pro-preview"
