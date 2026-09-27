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


def test_successful_health_check_is_not_logged(client):
    # ALB 가 10초마다 찌른다 — 요청별 줄(아래)에서도 빠져야 로그가 헬스체크로 덮이지 않는다.
    with _capture_api_logs() as records:
        assert client.get("/healthz").status_code == 200

    assert records == []


# ── 2026-09-26: 요청별 소요시간 + 4xx 에러 코드 ───────────────────────────
# 실트래픽 분석에서 두 구멍이 드러났다. (1) 성공 요청은 소요시간이 어디에도 안 남아
# "어느 API 가 몇 ms 인가"를 못 잰다. (2) 실패 줄에 에러 코드가 없어 콘티 저장 400 폭주
# (0.3초 간격 2,871회)의 거절 사유를 끝내 못 가렸다.


def _requests(records):
    return [r for r in records if r.getMessage().startswith("http request ")]


def test_every_request_logs_route_template_and_duration(client):
    @client.app.get("/_test/items/{item_id}")
    async def item(item_id: str):
        return {"id": item_id}

    with _capture_api_logs() as records:
        res = client.get("/_test/items/0f9815a4-dacd-4bce-82d6-016a71a6805b")

    assert res.status_code == 200
    lines = _requests(records)
    assert len(lines) == 1
    message = lines[0].getMessage()
    assert lines[0].levelno == logging.INFO          # 알림 필터(ERROR)에 안 걸린다
    assert "status=200" in message and "method=GET" in message
    # 경로 대신 라우트 틀 — ID 가 그대로 박히면 경로별 집계가 요청 수만큼 흩어진다.
    assert "route=/_test/items/{item_id}" in message
    assert "0f9815a4" not in message
    assert "duration_ms=" in message


def test_health_checks_and_preflight_are_not_logged(client):
    with _capture_api_logs() as records:
        client.get("/healthz")
        client.get("/readyz")
        client.options("/v1/me/ping", headers={
            "Origin": "https://ai.wearless.kr", "Access-Control-Request-Method": "GET"})
    assert _requests(records) == []


def test_4xx_line_carries_the_error_code(client):
    @client.app.put("/_test/board")
    async def board():
        raise HTTPException(status_code=400, detail={
            "code": "example_gender_mismatch", "message": "이 상품에 맞지 않는 생성예시예요."})

    with _capture_api_logs() as records:
        res = client.put("/_test/board")

    assert res.status_code == 400
    warning = _warnings(records)[0].getMessage()
    assert warning.startswith("http error status=400")   # 알림 필터가 기대는 앞부분은 그대로
    assert "code=example_gender_mismatch" in warning
    request_line = _requests(records)[0].getMessage()
    assert "code=example_gender_mismatch" in request_line
    assert "route=/_test/board" in request_line


def test_unmatched_path_is_logged_without_inventing_a_route(client):
    with _capture_api_logs() as records:
        client.get("/.env")
    line = _requests(records)[0].getMessage()
    assert "status=404" in line
    assert "route=-" in line   # 봇이 찌른 임의 경로로 라우트 집계를 오염시키지 않는다
