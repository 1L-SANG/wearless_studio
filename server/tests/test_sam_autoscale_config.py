"""SAM_AUTOSCALE 은 기본 off 다 — 미설정·오타·공백 전부 off 로 떨어져야 로컬·테스트가 AWS 를 모른다."""

import pytest

from app.config import load_settings
from conftest import make_settings


def test_settings_default_to_off_without_env():
    s = make_settings()
    assert s.sam_autoscale == "off"
    assert s.sam_autoscale_idle_minutes == 30
    assert s.sam_alert_topic_arn is None


@pytest.mark.parametrize("raw,expected", [
    ("on", "on"), ("ON", "on"), (" on ", "on"),
    ("off", "off"), ("", "off"), ("yes", "off"), ("true", "off"), ("1", "off"),
])
def test_loader_normalises_the_flag(monkeypatch, raw, expected):
    monkeypatch.setenv("SAM_AUTOSCALE", raw)
    assert load_settings().sam_autoscale == expected


def test_loader_reads_idle_minutes_and_topic(monkeypatch):
    monkeypatch.setenv("SAM_AUTOSCALE_IDLE_MINUTES", "45")
    monkeypatch.setenv("SAM_ALERT_TOPIC_ARN", "arn:aws:sns:ap-northeast-2:1:t")
    s = load_settings()
    assert s.sam_autoscale_idle_minutes == 45
    assert s.sam_alert_topic_arn == "arn:aws:sns:ap-northeast-2:1:t"


def test_loader_falls_back_on_garbage_idle_minutes(monkeypatch):
    """보조 기능의 오타가 API 기동을 죽이면 안 된다 — 기본값으로 떨어진다."""
    monkeypatch.setenv("SAM_AUTOSCALE_IDLE_MINUTES", "soon")
    assert load_settings().sam_autoscale_idle_minutes == 30


def test_loader_treats_empty_topic_as_none(monkeypatch):
    monkeypatch.setenv("SAM_ALERT_TOPIC_ARN", "")
    assert load_settings().sam_alert_topic_arn is None


def test_loader_default_matches_dataclass_default_for_idle_minutes(monkeypatch):
    """`test_dataclass_defaults_match_loader_defaults` 는 `os.getenv("X", "d")` 직접 호출만
    정규식으로 긁는다 — `_int_env()` 경유 필드는 그 그물에 안 걸린다(PR #169 Codex 검토).
    여기서 직접 고정한다: 환경변수가 없을 때 로더와 dataclass 가 같은 값을 내야 한다."""
    from app.config import Settings
    monkeypatch.delenv("SAM_AUTOSCALE_IDLE_MINUTES", raising=False)
    assert load_settings().sam_autoscale_idle_minutes == Settings.sam_autoscale_idle_minutes == 30
