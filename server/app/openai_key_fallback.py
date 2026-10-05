"""OpenAI 계정 키 선택과 영구 계정 오류 전환의 단일 진입점."""

import json
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal


log = logging.getLogger("wearless.openai_key_fallback")

OpenAIAccount = Literal["primary", "fallback"]

_primary_unavailable = False
_switch_alerted = False
_state_lock = threading.Lock()
_SWITCH_MESSAGE = "학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환"
_TERMINAL_429_CODES = {"credit_balance_exhausted", "insufficient_quota"}


@dataclass(frozen=True)
class OpenAIResponse:
    response: Any
    account: OpenAIAccount


class OpenAIKeyUnavailable(RuntimeError):
    pass


def _keys(settings) -> tuple[str | None, str | None]:
    return (
        getattr(settings, "openai_api_key", None) or None,
        getattr(settings, "openai_api_key_fallback", None) or None,
    )


def has_openai_api_key(settings) -> bool:
    primary, fallback = _keys(settings)
    return bool(primary or fallback)


def _select_key(settings) -> tuple[str, OpenAIAccount] | None:
    primary, fallback = _keys(settings)
    with _state_lock:
        primary_unavailable = _primary_unavailable
    if primary and not primary_unavailable:
        return primary, "primary"
    if fallback:
        return fallback, "fallback"
    if primary:
        return primary, "primary"
    return None


def _error_code(response) -> str | None:
    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError):
        try:
            payload = json.loads(getattr(response, "text", "") or "")
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
    else:
        code = payload.get("code")
    return code if isinstance(code, str) else None


def is_openai_terminal_account_error(response) -> bool:
    status = getattr(response, "status_code", None)
    code = _error_code(response)
    return (
        (status == 429 and code in _TERMINAL_429_CODES)
        or code == "billing_hard_limit_reached"
        or (status == 401 and code == "invalid_api_key")
    )


def _mark_primary_unavailable() -> None:
    global _primary_unavailable, _switch_alerted
    should_alert = False
    with _state_lock:
        _primary_unavailable = True
        if not _switch_alerted:
            _switch_alerted = True
            should_alert = True
    if should_alert:
        try:
            # 운영 CloudWatch 구독이 api와 detail-worker의 CRITICAL을 Slack으로 전달한다.
            log.critical(_SWITCH_MESSAGE)
        except Exception:
            # 알림 계층 문제로 실제 생성 요청을 막지 않는다.
            pass


async def _send_with_account(
    send: Callable[[str], Awaitable[Any]], key: str, account: OpenAIAccount
):
    try:
        return await send(key)
    except Exception as exc:
        # 과금됐을 수 있는 전송 실패도 이미지 원장에 계정만 남길 수 있게 한다.
        try:
            setattr(exc, "openai_account", account)
        except Exception:
            pass
        raise


async def request_with_openai_key_fallback(
    settings, send: Callable[[str], Awaitable[Any]]
) -> OpenAIResponse:
    """선택한 키로 전송하고 primary의 영구 계정 오류만 fallback으로 한 번 재전송한다."""
    selected = _select_key(settings)
    if selected is None:
        raise OpenAIKeyUnavailable("OPENAI_API_KEY 미설정")
    key, account = selected
    response = await _send_with_account(send, key, account)
    if account != "primary" or not is_openai_terminal_account_error(response):
        return OpenAIResponse(response=response, account=account)

    _, fallback_key = _keys(settings)
    if not fallback_key:
        return OpenAIResponse(response=response, account="primary")

    _mark_primary_unavailable()
    fallback_response = await _send_with_account(send, fallback_key, "fallback")
    return OpenAIResponse(response=fallback_response, account="fallback")
