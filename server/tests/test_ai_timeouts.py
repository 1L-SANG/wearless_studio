import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
import pytest
import yaml

from app.agents import vision_llm
from app.config import load_settings
from conftest import make_settings


CONFIG = {
    "background_ai_timeout_seconds": 120,
    "page_copy_timeout_seconds": 300,
    "ai_breaker_failures": 3,
    "ai_breaker_window_seconds": 300,
    "ai_breaker_cooldown_seconds": 300,
}


def test_timeout_and_breaker_defaults_and_env(monkeypatch):
    for field, default in CONFIG.items():
        monkeypatch.delenv(field.upper(), raising=False)
        assert getattr(make_settings(), field) == default
    defaults = load_settings()
    for field, default in CONFIG.items():
        assert getattr(defaults, field) == default
        monkeypatch.setenv(field.upper(), str(default + 1))
    configured = load_settings()
    for field, default in CONFIG.items():
        assert getattr(configured, field) == default + 1


@pytest.mark.parametrize("service", ["api", "detail-worker"])
def test_production_timeout_and_breaker_settings(service):
    path = Path(__file__).resolve().parents[2] / "copilot" / service / "manifest.yml"
    variables = yaml.safe_load(path.read_text())["variables"]
    for field, default in CONFIG.items():
        assert float(variables[field.upper()]) == default


@pytest.fixture
def routing(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(vision_llm, "_breaker", vision_llm._CircuitBreaker(clock=lambda: clock[0]))
    calls = []
    failing = [True]

    async def primary(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append(("gemini", model, timeout))
        if failing[0]:
            raise httpx.ReadTimeout("test timeout")
        return {"ok": True}

    async def fallback(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append(("gpt", model, timeout))
        return {"ok": True}

    for name, func in (("gemini", primary), ("gpt", fallback)):
        _, model_of, key_of = vision_llm._PROVIDERS[name]
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (func, model_of, key_of))
    settings = make_settings(gemini_api_key="test", openai_api_key="test",
                             analysis_model_order="gemini,gpt")

    def call(s=settings, **kwargs):
        return asyncio.run(vision_llm.analyze_with_fallback(s, "prompt", [], {}, **kwargs))

    return settings, clock, calls, failing, call


@pytest.mark.parametrize("timeout,expected", [(None, 17), (120, 120), (7.5, 7.5), (0, 0)])
@pytest.mark.parametrize("text_only", [False, True])
def test_optional_timeout_reaches_each_provider(routing, timeout, expected, text_only):
    settings, _, calls, _, _ = routing
    from dataclasses import replace
    settings = replace(settings, analysis_timeout_seconds=17)
    if text_only:
        result = asyncio.run(vision_llm.complete_json(settings, "prompt", {}, timeout_seconds=timeout))
    else:
        result = asyncio.run(vision_llm.analyze_with_fallback(
            settings, "prompt", [], {}, timeout_seconds=timeout))
    assert result[1] == "gpt"
    assert [call[2] for call in calls] == [expected, expected]


def test_three_failures_skip_then_cooldown_releases(routing, caplog):
    _, clock, calls, failing, call = routing
    for _ in range(3):
        assert call()[1] == "gpt"
    calls.clear()
    assert call()[1] == "gpt"
    assert [item[0] for item in calls] == ["gpt"]
    assert "breaker opened" in caplog.text and "breaker skipped" in caplog.text
    failing[0] = False
    clock[0] += 300
    with caplog.at_level("INFO", logger="wearless.vision_llm"):
        assert call()[1] == "gemini"
    assert "cooldown_elapsed" in caplog.text


def test_success_resets_consecutive_failures(routing):
    _, _, calls, failing, call = routing
    call()
    call()
    failing[0] = False
    assert call()[1] == "gemini"
    failing[0] = True
    call()
    call()
    calls.clear()
    call()
    assert calls[0][0] == "gemini"
    calls.clear()
    call()
    assert [item[0] for item in calls] == ["gpt"]


def test_failure_window_excludes_old_signals(routing):
    _, clock, calls, _, call = routing
    call()
    clock[0] += 200
    call()
    clock[0] += 101
    call()
    calls.clear()
    call()
    assert calls[0][0] == "gemini"
    calls.clear()
    call()
    assert [item[0] for item in calls] == ["gpt"]


def test_breaker_threshold_window_and_cooldown_follow_settings():
    clock = [10.0]
    breaker = vision_llm._CircuitBreaker(clock=lambda: clock[0])
    settings = make_settings(ai_breaker_failures=2, ai_breaker_window_seconds=3,
                             ai_breaker_cooldown_seconds=7)
    breaker.failure(settings, "gemini", "model", "timeout")
    clock[0] += 4
    breaker.failure(settings, "gemini", "model", "timeout")
    assert not breaker.should_skip("gemini", "model", has_next=True)
    breaker.failure(settings, "gemini", "model", "timeout")
    clock[0] += 6.9
    assert breaker.should_skip("gemini", "model", has_next=True)
    clock[0] += 0.1
    assert not breaker.should_skip("gemini", "model", has_next=True)


def test_models_have_independent_breakers(routing):
    _, _, calls, _, call = routing
    for _ in range(3):
        call()
    calls.clear()
    call(models={"gemini": "other-model"})
    assert calls[0][:2] == ("gemini", "other-model")
    calls.clear()
    call()
    assert [item[0] for item in calls] == ["gpt"]


def test_provider_is_part_of_breaker_key():
    breaker = vision_llm._CircuitBreaker(clock=lambda: 0)
    settings = make_settings()
    for _ in range(3):
        breaker.failure(settings, "gemini", "shared-name", "timeout")
    assert breaker.should_skip("gemini", "shared-name", has_next=True)
    assert not breaker.should_skip("gpt", "shared-name", has_next=True)


@pytest.mark.parametrize("order", ["gemini,gpt", "gemini", "gemini,gemini", "gemini,unknown,gpt"])
def test_no_next_key_never_skips_the_only_provider(routing, order):
    from dataclasses import replace
    settings, _, calls, _, call = routing
    settings = replace(settings, openai_api_key=None, openai_api_key_fallback=None,
                       analysis_model_order=order)
    for _ in range(5):
        with pytest.raises(vision_llm.VisionError):
            call(settings)
    assert len(calls) == 5 and all(item[0] == "gemini" for item in calls)


def test_when_both_breakers_open_final_provider_is_still_tried(routing):
    settings, _, calls, _, call = routing
    for name, model in (("gemini", settings.model_text_gemini), ("gpt", settings.model_text)):
        for _ in range(3):
            vision_llm._breaker.failure(settings, name, model, "timeout")
    assert call()[1] == "gpt"
    assert [item[0] for item in calls] == ["gpt"]


@pytest.mark.parametrize("provider", ["gemini", "gpt"])
@pytest.mark.parametrize("status,counts", [
    (400, False), (401, False), (403, False), (404, False), (408, False),
    (409, False), (422, False), (429, True), (500, True), (503, True),
])
def test_http_status_classification_uses_status_without_counting_other_4xx(monkeypatch, provider, status, counts):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(status, json={"error": {"code": "test"}})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    settings = make_settings(openai_api_key="test", gemini_api_key="test")
    with pytest.raises(vision_llm.VisionError) as exc:
        asyncio.run(getattr(vision_llm, f"_call_{provider}")(settings, "model", "prompt", [], {}, 120))
    assert (vision_llm._breaker_failure_reason(exc.value) is not None) is counts


@pytest.mark.parametrize("error,counts", [
    (httpx.ConnectError("offline"), True), (httpx.ReadError("disconnected"), True),
    (httpx.ReadTimeout("slow"), True), (httpx.ConnectTimeout("slow"), True),
    (httpx.RemoteProtocolError("disconnected"), True), (TimeoutError(), True),
    (httpx.LocalProtocolError("bad header"), False), (httpx.DecodingError("invalid"), False),
    (vision_llm.VisionError("schema mismatch", unknown_cause="invalid_response"), False),
    (vision_llm.VisionError("blocked", unknown_cause="provider_rejection"), False),
    (vision_llm.VisionError("invalid input", unknown_cause="invalid_input"), False),
])
def test_only_outages_are_breaker_signals(error, counts):
    assert (vision_llm._breaker_failure_reason(error) is not None) is counts


@pytest.mark.parametrize("error", [
    vision_llm.VisionError("bad input", status_code=400),
    vision_llm.VisionError("bad schema", unknown_cause="invalid_response"),
    vision_llm.VisionError("blocked", unknown_cause="provider_rejection"),
])
def test_repeated_non_outage_errors_never_open_breaker(routing, monkeypatch, error):
    _, _, calls, _, call = routing

    async def invalid(*args, **kwargs):
        calls.append(("gemini", "model", 30))
        raise error

    _, model_of, key_of = vision_llm._PROVIDERS["gemini"]
    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (invalid, model_of, key_of))
    for _ in range(5):
        assert call()[1] == "gpt"
    assert len([item for item in calls if item[0] == "gemini"]) == 5


def test_breaker_bookkeeping_is_safe_across_threads(caplog):
    breaker = vision_llm._CircuitBreaker(clock=lambda: 1000)
    settings = make_settings(ai_breaker_failures=12)
    barrier = Barrier(12)

    def fail(_):
        barrier.wait()
        breaker.failure(settings, "gemini", "model", "timeout")

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(fail, range(12)))
    assert breaker.should_skip("gemini", "model", has_next=True)
    assert caplog.text.count("breaker opened") == 1
    with caplog.at_level("INFO", logger="wearless.vision_llm"):
        breaker.success("gemini", "model")
    assert not breaker.should_skip("gemini", "model", has_next=True)
    assert "reason=success" in caplog.text


def test_async_failures_open_once_without_losing_fallbacks(routing, monkeypatch, caplog):
    settings, _, calls, _, _ = routing

    async def run():
        ready = asyncio.Event()

        async def failing(*args, **kwargs):
            calls.append(("gemini", "model", 30))
            if len(calls) == 20:
                ready.set()
            await ready.wait()
            raise httpx.ReadTimeout("concurrent timeout")

        _, model_of, key_of = vision_llm._PROVIDERS["gemini"]
        monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (failing, model_of, key_of))
        results = await asyncio.gather(*(
            vision_llm.analyze_with_fallback(settings, "prompt", [], {}) for _ in range(20)))
        calls.clear()
        assert (await vision_llm.analyze_with_fallback(settings, "prompt", [], {}))[1] == "gpt"
        assert [item[0] for item in calls] == ["gpt"]
        return results

    assert all(provider == "gpt" for _, provider in asyncio.run(run()))
    assert caplog.text.count("breaker opened") == 1
