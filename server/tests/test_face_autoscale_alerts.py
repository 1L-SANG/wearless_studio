"""파드 알림이 실제로 Slack 필터에 걸리는가 — 로그 포맷까지 실물로 확인한다.

api 의 CloudWatch SubscriptionFilter(copilot/environments/addons/log-slack-alerts.yml)는
`?"http error status=5" ?" CRITICAL " ?"CRITICAL:" ?"Application startup failed"` 만 고른다.
어댑터가 log.error 로 남기면 CloudWatch 에는 있고 **Slack 에는 안 간다** — "never became
healthy" 같은 알림이 조용히 사라지는 경로라 여기서 포맷째 고정한다.
"""

import asyncio
import io
import logging
import re
from pathlib import Path

import pytest

from app.services.face_autoscale import RunpodAutoscaleAdapter
from conftest import make_settings

ROOT = Path(__file__).resolve().parents[2]
FILTER_TOKENS = (" CRITICAL ", "CRITICAL:", "http error status=5", "Application startup failed")
#: app/main.py 의 운영 포맷과 같아야 한다 — 이 포맷이 " CRITICAL " 을 만든다.
PROD_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def test_prod_log_format_is_the_one_that_produces_the_token():
    text = (ROOT / "server/app/main.py").read_text(encoding="utf-8")
    assert PROD_FORMAT in text


def test_slack_filter_still_only_catches_critical():
    yml = (ROOT / "copilot/environments/addons/log-slack-alerts.yml").read_text(encoding="utf-8")
    assert '?"http error status=5" ?" CRITICAL " ?"CRITICAL:" ?"Application startup failed"' in yml
    assert '" ERROR "' not in yml.split("FilterPattern:")[1][:200]


def _capture(level: int = logging.INFO) -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(PROD_FORMAT))
    logger = logging.getLogger("wearless.face_autoscale")
    logger.addHandler(handler)
    logger.setLevel(level)
    return stream, handler


def _matches_filter(line: str) -> bool:
    return any(token in line for token in FILTER_TOKENS)


def test_notify_line_matches_the_slack_filter():
    stream, handler = _capture()
    try:
        adapter = RunpodAutoscaleAdapter(make_settings(gemini_api_key="x", r2_bucket="b"))
        asyncio.run(adapter.notify("face-render autoscale: never became healthy",
                                   "17분 동안 헬스가 통과하지 못했습니다."))
    finally:
        logging.getLogger("wearless.face_autoscale").removeHandler(handler)
    line = stream.getvalue().strip()
    assert " CRITICAL " in line, line          # 필터가 잡는 토큰이 실제로 찍힌다
    assert _matches_filter(line)
    assert "never became healthy" in line
    assert re.search(r"CRITICAL wearless\.face_autoscale ", line), line


@pytest.mark.parametrize("level,expected", [
    (logging.ERROR, False),      # 예전 동작 — 이 경로로 돌아가면 Slack 이 조용해진다
    (logging.CRITICAL, True),
])
def test_error_level_would_not_reach_slack(level, expected):
    stream, handler = _capture()
    logger = logging.getLogger("wearless.face_autoscale")
    try:
        logger.log(level, "face autoscale alert: %s — %s", "subject", "body")
    finally:
        logger.removeHandler(handler)
    assert _matches_filter(stream.getvalue()) is expected
