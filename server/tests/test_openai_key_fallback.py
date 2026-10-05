import asyncio
import importlib
import logging
from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.main import create_app
from conftest import make_settings


class FakeResponse:
    def __init__(self, status_code: int, code: str | None = None):
        self.status_code = status_code
        self._code = code
        self.text = "ok" if code is None else f'{{"error":{{"code":"{code}"}}}}'

    def json(self):
        if self._code is None:
            return {}
        return {"error": {"code": self._code}}


def _module(monkeypatch):
    module = importlib.import_module("app.openai_key_fallback")
    monkeypatch.setattr(module, "_primary_unavailable", False)
    monkeypatch.setattr(module, "_switch_alerted", False)
    return module


def _settings(*, primary="school-key", fallback="owner-key"):
    return SimpleNamespace(
        openai_api_key=primary,
        openai_api_key_fallback=fallback,
    )


def test_terminal_quota_error_retries_with_fallback_and_sticks(monkeypatch):
    fallback = _module(monkeypatch)
    calls = []

    async def first_send(key):
        calls.append(key)
        if key == "school-key":
            return FakeResponse(429, "insufficient_quota")
        return FakeResponse(200)

    first = asyncio.run(
        fallback.request_with_openai_key_fallback(_settings(), first_send)
    )

    async def next_send(key):
        calls.append(key)
        return FakeResponse(200)

    second = asyncio.run(
        fallback.request_with_openai_key_fallback(_settings(), next_send)
    )

    assert (first.response.status_code, first.account) == (200, "fallback")
    assert (second.response.status_code, second.account) == (200, "fallback")
    assert calls == ["school-key", "owner-key", "owner-key"]


def test_rate_limit_exceeded_does_not_switch_accounts(monkeypatch):
    fallback = _module(monkeypatch)
    calls = []

    async def send(key):
        calls.append(key)
        return FakeResponse(429, "rate_limit_exceeded")

    first = asyncio.run(fallback.request_with_openai_key_fallback(_settings(), send))
    second = asyncio.run(fallback.request_with_openai_key_fallback(_settings(), send))

    assert first.account == second.account == "primary"
    assert calls == ["school-key", "school-key"]


def test_missing_fallback_returns_the_original_terminal_response(monkeypatch):
    fallback = _module(monkeypatch)
    response = FakeResponse(429, "credit_balance_exhausted")
    calls = []

    async def send(key):
        calls.append(key)
        return response

    result = asyncio.run(
        fallback.request_with_openai_key_fallback(
            _settings(fallback=None), send
        )
    )

    assert result.response is response
    assert result.account == "primary"
    assert calls == ["school-key"]


def test_both_terminal_responses_stop_after_one_fallback_attempt(monkeypatch):
    fallback = _module(monkeypatch)
    primary_error = FakeResponse(429, "insufficient_quota")
    owner_error = FakeResponse(429, "credit_balance_exhausted")
    calls = []

    async def send(key):
        calls.append(key)
        return primary_error if key == "school-key" else owner_error

    result = asyncio.run(
        fallback.request_with_openai_key_fallback(_settings(), send)
    )

    assert result.response is owner_error
    assert result.account == "fallback"
    assert calls == ["school-key", "owner-key"]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "invalid_api_key"),
        (400, "billing_hard_limit_reached"),
        (429, "billing_hard_limit_reached"),
    ],
)
def test_other_permanent_account_errors_switch(status, code, monkeypatch):
    fallback = _module(monkeypatch)
    calls = []

    async def send(key):
        calls.append(key)
        return FakeResponse(status, code) if key == "school-key" else FakeResponse(200)

    result = asyncio.run(
        fallback.request_with_openai_key_fallback(_settings(), send)
    )

    assert result.account == "fallback"
    assert calls == ["school-key", "owner-key"]


@pytest.mark.parametrize(
    "status,code",
    [(401, "account_deactivated"), (429, "rate_limit_exceeded"), (500, None)],
)
def test_transient_or_unlisted_errors_do_not_switch(status, code, monkeypatch):
    fallback = _module(monkeypatch)
    calls = []

    async def send(key):
        calls.append(key)
        return FakeResponse(status, code)

    result = asyncio.run(
        fallback.request_with_openai_key_fallback(_settings(), send)
    )

    assert result.account == "primary"
    assert calls == ["school-key"]


def test_switch_alert_is_emitted_once_without_key_values(monkeypatch, caplog):
    fallback = _module(monkeypatch)

    async def send(key):
        if key == "school-key":
            return FakeResponse(429, "insufficient_quota")
        return FakeResponse(200)

    with caplog.at_level(logging.CRITICAL, logger="wearless.openai_key_fallback"):
        asyncio.run(fallback.request_with_openai_key_fallback(_settings(), send))
        asyncio.run(fallback.request_with_openai_key_fallback(_settings(), send))

    messages = [
        record.getMessage()
        for record in caplog.records
        if "학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환" in record.getMessage()
    ]
    assert messages == ["학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환"]
    assert "school-key" not in caplog.text
    assert "owner-key" not in caplog.text


def test_fallback_only_configuration_is_usable_without_transition_alert(monkeypatch, caplog):
    fallback = _module(monkeypatch)
    calls = []

    async def send(key):
        calls.append(key)
        return FakeResponse(200)

    with caplog.at_level(logging.CRITICAL, logger="wearless.openai_key_fallback"):
        result = asyncio.run(
            fallback.request_with_openai_key_fallback(
                _settings(primary=None), send
            )
        )

    assert fallback.has_openai_api_key(_settings(primary=None)) is True
    assert result.account == "fallback"
    assert calls == ["owner-key"]
    assert "오너 계정으로 전환" not in caplog.text


def test_settings_loads_fallback_key_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY_FALLBACK", "owner-env-key")

    settings = load_settings()

    assert settings.openai_api_key_fallback == "owner-env-key"


def test_app_builds_openai_image_client_with_fallback_only_key():
    app = create_app(make_settings(
        gemini_api_key=None,
        openai_api_key=None,
        openai_api_key_fallback="owner-key",
    ))

    assert app.state.gemini is not None
