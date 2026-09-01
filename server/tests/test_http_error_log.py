"""http_error_log 미들웨어(main.py) — 실패 응답의 상태코드·경로 로깅.

이 로그가 Slack 알림의 입력이다(copilot/environments/addons/log-slack-alerts.yml).
알림 필터는 ERROR 만 보므로, 어떤 레벨로 남기느냐가 곧 "알림이 가느냐"다.
"""

import logging
from contextlib import contextmanager

from fastapi import HTTPException


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):  # noqa: D102
        self.records.append(record)


@contextmanager
def _capture_api_logs():
    """`wearless.api` 로거에 직접 붙는다.

    create_app 이 _configure_logging 으로 **root 핸들러를 통째로 교체**하므로
    pytest caplog(root 에 붙는다)는 client 픽스처 뒤에 지워진다. 이름 있는 로거에
    직접 붙이면 그 교체와 무관하다.
    """
    handler = _Capture()
    logger = logging.getLogger("wearless.api")
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)


def _errors(records):
    return [r for r in records if r.levelno >= logging.ERROR]


def _warnings(records):
    return [r for r in records if r.levelno == logging.WARNING]


def test_intentional_5xx_is_logged_with_status_and_path(client):
    """HTTPException 으로 **의도해서** 돌려준 5xx.

    예외 봉투는 파이썬 예외가 터진 경우만 로그를 남기므로 이 경로는 여태 로그가
    한 줄도 없었다 = Slack 알림도 가지 않았다. 이 미들웨어가 막는 구멍이 이것이다.
    """

    @client.app.get("/_test/unavailable")
    async def unavailable():
        raise HTTPException(status_code=503, detail="payment_not_configured")

    with _capture_api_logs() as records:
        res = client.get("/_test/unavailable")

    assert res.status_code == 503
    errors = _errors(records)
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "status=503" in message
    assert "method=GET" in message
    assert "path=/_test/unavailable" in message


def test_unhandled_exception_500_is_logged_with_status(client):
    """봉투가 만든 500 도 본다 = 미들웨어가 봉투 **바깥**에 등록됐다는 뜻.

    등록 순서가 뒤집히면 봉투가 예외를 삼킨 뒤라 이 미들웨어는 500 을 못 본다.
    """

    @client.app.get("/_test/boom")
    async def boom():
        raise RuntimeError("boom")

    with _capture_api_logs() as records:
        res = client.get("/_test/boom")

    assert res.status_code == 500
    assert any("status=500" in r.getMessage() for r in _errors(records))


def test_4xx_stays_below_error_level(client):
    """4xx 는 WARNING 이다.

    만료 토큰·오탈자 URL 같은 일상 실패라 ERROR 로 올리면 알림 채널이 죽는다.
    CloudWatch 에는 남고 Slack 만 조용해야 한다.
    """
    with _capture_api_logs() as records:
        res = client.get("/_test/no-such-route")

    assert res.status_code == 404
    assert _errors(records) == []
    warnings = _warnings(records)
    assert len(warnings) == 1
    assert "status=404" in warnings[0].getMessage()


def test_successful_response_is_not_logged(client):
    with _capture_api_logs() as records:
        assert client.get("/healthz").status_code == 200

    assert records == []
