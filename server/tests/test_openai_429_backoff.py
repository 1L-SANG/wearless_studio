"""OpenAI 이미지 경로의 429 백오프 — 레이트리밋을 실패가 아니라 지연으로 흡수한다.

2026-08-28 프로덕션: 상세페이지 14컷 중 4컷이 `OpenAI 429: Rate limit reached ...
on input-images per min: Limit 5, Used 5` 로 죽었다. 로그의 `after 1 attempts (1s)` 가
말해주듯 **재시도를 아예 안 했다.**

Gemini 경로에는 429 백오프 루프가 있는데(`gemini_image.py` 의 `for attempt in range(3)`),
`_openai_generate` 는 그 앞에서 갈라져 나가 안전망이 없었다. 상세페이지 컷은
`MODEL_ROUTING_DETAIL_CUT=gpt-image-2` 라 **전부 OpenAI 경로**다 — 즉 컷 생성 전체가
안전망 없이 돌고 있었다.

OpenAI 는 429 본문에 `Please try again in 12s` 로 대기 시간을 알려주고 헤더에도
`retry-after` 를 준다. 그걸 읽어 기다렸다 재시도하면 컷이 빠지는 대신 늦어질 뿐이다.
"""
import asyncio
import types

import pytest

from app.agents import gemini_image
from app.agents.gemini_image import GeminiError


class _Res:
    def __init__(self, status, text="", headers=None, payload=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


_RATE_LIMIT_BODY = (
    '{"error": {"message": "Rate limit reached for gpt-image-2-2026-04-21 in '
    'organization org-x on input-images per min: Limit 5, Used 5, Requested 1. '
    'Please try again in 12s.", "type": "requests", "code": "rate_limit_exceeded"}}'
)


def test_retry_delay_is_read_from_the_body():
    """본문의 'try again in 12s' 를 대기 시간으로 읽는다."""
    assert gemini_image.openai_retry_delay(_Res(429, _RATE_LIMIT_BODY)) == pytest.approx(12.0)


def test_retry_delay_prefers_retry_after_header():
    """헤더가 있으면 헤더를 쓴다 — 프로바이더가 주는 가장 권위 있는 값이다."""
    res = _Res(429, _RATE_LIMIT_BODY, headers={"retry-after": "3"})
    assert gemini_image.openai_retry_delay(res) == pytest.approx(3.0)


def test_retry_delay_handles_milliseconds_form():
    """'try again in 1.5s' 같은 소수도 읽는다."""
    body = '{"error": {"message": "Rate limit reached. Please try again in 1.5s."}}'
    assert gemini_image.openai_retry_delay(_Res(429, body)) == pytest.approx(1.5)


def test_retry_delay_falls_back_when_unparseable():
    """대기 시간을 못 읽어도 0 이 아니라 기본값을 준다 — 즉시 재시도는 또 429 다."""
    d = gemini_image.openai_retry_delay(_Res(429, "no numbers here"))
    assert d >= 1.0


def test_retry_delay_is_capped():
    """프로바이더가 비정상적으로 긴 값을 줘도 잡을 무한정 붙들지 않는다."""
    body = '{"error": {"message": "Please try again in 9999s."}}'
    assert gemini_image.openai_retry_delay(_Res(429, body)) <= 60.0


def test_non_429_has_no_delay():
    """429 가 아니면 대기 대상이 아니다 — 파라미터 오류는 재시도해도 같다."""
    assert gemini_image.openai_retry_delay(_Res(400, "bad request")) is None


def test_credit_exhausted_429_is_not_retried():
    """잔액 소진 429 는 기다려도 안 풀린다 — 재시도하면 잡만 길어진다.

    2026-08-27 프로덕션에서 실제로 겪은 코드다(`credit_balance_exhausted`).
    """
    body = ('{"error": {"message": "You have no credits remaining.", '
            '"code": "credit_balance_exhausted"}}')
    assert gemini_image.openai_retry_delay(_Res(429, body)) is None
