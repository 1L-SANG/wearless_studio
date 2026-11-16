"""Per-call routing and the unchanged legacy request/failure contract; no network."""

import asyncio
import logging

import pytest

from app.agents import vision_llm
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from test_vision_llm import FakeResp, RAW


@pytest.mark.parametrize("first", ["gpt", "gemini"])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("system", [None, "fixed policy"])
def test_legacy_requests_order_logs_and_breaker_are_unchanged(monkeypatch, caplog, first, fails, complete, system):
    """Fixed expectations also pass on the implementation before the routing change."""
    second = "gemini" if first == "gpt" else "gpt"
    settings = make_settings(openai_api_key="test", gemini_api_key="test",
                             analysis_model_order=f"{first},{second}", ai_breaker_failures=1)
    monkeypatch.setattr(vision_llm, "_breaker", vision_llm._CircuitBreaker(clock=lambda: 1000))
    monkeypatch.setattr(vision_llm.time, "perf_counter", lambda: 10)
    seen = []
    expected = {
        "gpt": {
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "product_analysis", "strict": True, "schema": {"type": "object"},
            }},
        },
        "gemini": {
            "contents": [{"role": "user", "parts": [
                {"text": "inspect"}, {"inline_data": {"mime_type": "image/png", "data": "aW1hZ2U="}},
            ]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": {"type": "OBJECT", "properties": {}},
                                 "thinkingConfig": {"thinkingLevel": "low"}},
        },
    }
    if system is not None:
        expected["gpt"]["messages"].insert(0, {"role": "developer", "content": system})
        expected["gemini"]["systemInstruction"] = {"parts": [{"text": system}]}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": settings.analysis_timeout_seconds}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, headers):
            name = "gpt" if "openai.com" in url else "gemini"
            seen.append((name, json))
            if fails and name == first:
                return FakeResp(500, text="unavailable")
            if name == "gpt":
                return FakeResp(payload={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
            return FakeResp(payload={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}]})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    caplog.set_level(logging.INFO, logger="wearless.vision_llm")
    for _ in range(2):
        result = asyncio.run(vision_llm.analyze_with_fallback(
            settings, "inspect", [InlineImage("image/png", b"image")], {"type": "object"},
            require_complete_envelope=complete, system_prompt=system))
        assert result == ({"ok": True}, second if fails else first)
    assert [name for name, _ in seen] == ([first, second, second] if fails else [first, first])
    assert all(body == expected[name] for name, body in seen)
    records = [record for record in caplog.records if record.name == "wearless.vision_llm"]
    if not fails:
        assert records == []
        assert vision_llm._breaker._states == {}
        return
    model = expected["gpt"]["model"] if first == "gpt" else settings.model_text_gemini
    label = "OpenAI" if first == "gpt" else "Gemini"
    assert [record.getMessage() for record in records] == [
        f"vision_llm breaker opened: provider={first} model={model} reason=http_500",
        f"vision_llm provider failed: {first} model={model} VisionError({label} 500: unavailable) after 0.0s images=1",
        "vision_llm fallback used",
        f"vision_llm breaker skipped: provider={first} model={model} reason=http_500",
        "vision_llm fallback used",
    ]
    assert [(record.provider, record.prior) for record in records if hasattr(record, "prior")] == [
        (second, [f"{first}:err"]), (second, [f"{first}:breaker_open"]),
    ]
    state = vision_llm._breaker._states[(first, model)]
    assert (state.failures, state.open_until, state.reason) == ([1000], 1300, "http_500")


@pytest.mark.parametrize("fail_gpt", [False, True])
def test_per_call_order_and_effort_only_reach_gpt(monkeypatch, fail_gpt):
    seen = []

    async def gpt(settings, model, prompt, images, schema, timeout, *, thinking_level=None,
                  reasoning_effort=None):
        seen.append(("gpt", model, reasoning_effort, thinking_level))
        if fail_gpt:
            raise TimeoutError("test timeout")
        return RAW

    # No **kwargs: accidentally passing reasoning_effort here must fail the test.
    async def gemini(settings, model, prompt, images, schema, timeout, *, thinking_level=None):
        seen.append(("gemini", model, thinking_level))
        return RAW

    for name, call in (("gpt", gpt), ("gemini", gemini)):
        _, selector, key = vision_llm._PROVIDERS[name]
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (call, selector, key))
    settings = make_settings(openai_api_key="test", gemini_api_key="test", analysis_model_order="gemini,gpt")
    order = ["gpt", "gemini", "gpt"]
    result = asyncio.run(vision_llm.analyze_with_fallback(
        settings, "inspect", [], {}, models={"gpt": "gpt-6.1-terra"},
        order=order, reasoning_effort="none", thinking_level="low"))
    assert result == (RAW, "gemini" if fail_gpt else "gpt")
    assert seen == ([('gpt', 'gpt-6.1-terra', 'none', 'low'),
                     ('gemini', settings.model_text_gemini, 'low')] if fail_gpt else
                    [('gpt', 'gpt-6.1-terra', 'none', 'low')])
    assert order == ["gpt", "gemini", "gpt"]
    assert settings.analysis_model_order == "gemini,gpt"


def test_effort_is_not_forwarded_to_other_provider_names(monkeypatch):
    async def claude(settings, model, prompt, images, schema, timeout, *, thinking_level=None):
        return RAW

    monkeypatch.setitem(vision_llm._PROVIDERS, "claude", (claude, lambda s: "claude-test", lambda s: True))
    assert asyncio.run(vision_llm.analyze_with_fallback(
        make_settings(), "inspect", [], {}, order=["claude"], reasoning_effort="none")) == (RAW, "claude")


def test_per_call_order_skips_missing_key_without_forwarding_effort(monkeypatch, caplog):
    async def gemini(settings, model, prompt, images, schema, timeout, *, thinking_level=None):
        return RAW

    _, selector, key = vision_llm._PROVIDERS["gemini"]
    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (gemini, selector, key))
    caplog.set_level(logging.INFO, logger="wearless.vision_llm")
    assert asyncio.run(vision_llm.analyze_with_fallback(
        make_settings(gemini_api_key="test"), "inspect", [], {},
        order=["gpt", "gemini"], reasoning_effort="none")) == (RAW, "gemini")
    assert caplog.records[-1].prior == ["gpt:no_key"]


def test_per_call_breaker_and_failure_records_remain_model_specific(monkeypatch, caplog):
    calls = []
    clock = [1000]
    monkeypatch.setattr(vision_llm, "_breaker", vision_llm._CircuitBreaker(clock=lambda: clock[0]))

    async def gpt(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append(("gpt", model))
        assert kwargs == {"thinking_level": "low", "reasoning_effort": "none"}
        raise TimeoutError("test timeout")

    async def gemini(settings, model, prompt, images, schema, timeout, *, thinking_level=None):
        calls.append(("gemini", model))
        return RAW

    for name, call in (("gpt", gpt), ("gemini", gemini)):
        _, selector, key = vision_llm._PROVIDERS[name]
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (call, selector, key))
    settings = make_settings(openai_api_key="test", gemini_api_key="test", ai_breaker_failures=1)
    caplog.set_level(logging.INFO, logger="wearless.vision_llm")

    def call(model):
        return asyncio.run(vision_llm.analyze_with_fallback(settings, "inspect", [], {},
            order=["gpt", "gemini"], models={"gpt": model}, reasoning_effort="none", thinking_level="low"))

    for model in ("terra", "terra", "other-terra"):
        assert call(model) == (RAW, "gemini")
    assert calls == [("gpt", "terra"), ("gemini", settings.model_text_gemini),
                     ("gemini", settings.model_text_gemini),
                     ("gpt", "other-terra"), ("gemini", settings.model_text_gemini)]
    assert [record.prior for record in caplog.records if hasattr(record, "prior")] == [
        ["gpt:err"], ["gpt:breaker_open"], ["gpt:err"]]
    clock[0] += settings.ai_breaker_cooldown_seconds
    call("terra")
    assert calls[-2] == ("gpt", "terra")
    assert "cooldown_elapsed" in caplog.text


def test_all_per_call_providers_failing_keep_the_existing_error_and_logs(monkeypatch, caplog):
    settings = make_settings(openai_api_key="test", gemini_api_key="test")
    seen = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json, headers):
            seen.append(json)
            return FakeResp(500, text="test unavailable")

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    with pytest.raises(vision_llm.VisionError, match="상품 분석에 실패했어요"):
        asyncio.run(vision_llm.analyze_with_fallback(settings, "inspect", [], {},
                    order=["gpt", "gemini"], reasoning_effort="none"))
    assert seen[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in seen[1]
    assert [record.getMessage().split(" model=")[0] for record in caplog.records] == [
        "vision_llm provider failed: gpt", "vision_llm provider failed: gemini"]
