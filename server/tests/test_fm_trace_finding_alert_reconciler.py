"""자동 발견 슬랙 알림 — 실패해도 발견을 잃지 않고, 알림에 외부 원문·셀러 정보를 싣지 않는다."""

import asyncio
from types import SimpleNamespace

import pytest

from app import facemarket_notify
from app.workers.fm_trace_finding_alert_reconciler import TraceFindingAlertReconciler

SETTINGS = SimpleNamespace(
    fm_slack_webhook_url="https://hooks.example/trace",
    fm_application_public_base="https://facemarket.wearless.kr/",
)


class _MemoryReconciler(TraceFindingAlertReconciler):
    def __init__(self, job):
        super().__init__(SimpleNamespace(state=SimpleNamespace(settings=SETTINGS)))
        self.job = dict(job, alert_status="pending", alert_attempts=0)

    async def _claim_one(self):
        if self.job["alert_status"] != "pending":
            return None
        self.job["alert_status"] = "processing"
        return dict(self.job, lease_token="lease-1")

    async def _mark_retry(self, job):
        assert job["lease_token"] == "lease-1"
        self.job["alert_status"] = "pending"
        self.job["alert_attempts"] += 1

    async def _mark_sent(self, job):
        assert job["lease_token"] == "lease-1"
        self.job["alert_status"] = "sent"


PATROL = {"id": "f-1", "source": "patrol", "platform": "naver", "model_name": "김*연",
          "method": "watermark", "confidence": "high", "target": "publication"}


def test_failed_alert_retries_until_delivered(monkeypatch):
    worker = _MemoryReconciler(PATROL)
    responses = iter([False, True])
    calls = []

    async def notify(_settings, **kw):
        calls.append(kw)
        return next(responses)

    monkeypatch.setattr(facemarket_notify, "notify_slack_trace_finding", notify)
    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["alert_status"] == "pending"
    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["alert_status"] == "sent"
    assert asyncio.run(worker._sweep_once()) is False
    assert calls == [{
        "source": "patrol", "platform": "naver", "model_name": "김*연",
        "method": "watermark", "confidence": "high", "matched": True,
        "admin_link": "https://admin.wearless.kr/trace?tab=found",
    }] * 2


def test_unmatched_model_report_still_alerts(monkeypatch):
    job = {"id": "f-2", "source": "model_report", "platform": "report", "model_name": "박*민",
           "method": None, "confidence": None, "target": None}
    worker = _MemoryReconciler(job)
    calls = []

    async def notify(_settings, **kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(facemarket_notify, "notify_slack_trace_finding", notify)
    assert asyncio.run(worker._sweep_once()) is True
    assert calls[0]["matched"] is False and calls[0]["source"] == "model_report"


@pytest.mark.parametrize("kw,expect,absent", [
    ({"source": "patrol", "platform": "naver", "model_name": "김*연", "method": "watermark",
      "confidence": "high", "matched": True}, ["네이버 순찰", "김*연", "워터마크"], ["http"]),
    ({"source": "model_report", "platform": "report", "model_name": "<b>", "method": None,
      "confidence": None, "matched": False}, ["모델 제보", "&lt;b&gt;", "일치하는 배포본"], []),
])
def test_slack_text_has_no_external_urls_or_seller(monkeypatch, kw, expect, absent):
    sent = []

    async def post(_settings, text):
        sent.append(text)
        return True

    monkeypatch.setattr(facemarket_notify, "_post_slack", post)
    ok = asyncio.run(facemarket_notify.notify_slack_trace_finding(
        SETTINGS, admin_link="https://admin.wearless.kr/trace?tab=found", **kw))
    assert ok is True
    body, link = sent[0].rsplit("\n", 1)
    for word in expect:
        assert word in body
    for word in absent:
        assert word not in body          # 상품 주소 같은 외부 원문은 알림에 싣지 않는다
    assert link == "<https://admin.wearless.kr/trace?tab=found|관리자 출처 추적 열기>"


def test_no_webhook_means_not_delivered():
    ok = asyncio.run(facemarket_notify.notify_slack_trace_finding(
        SimpleNamespace(fm_slack_webhook_url=None), source="patrol", platform="zigzag",
        model_name=None, method="phash", confidence="low", matched=True, admin_link="x"))
    assert ok is False


def test_naver_purge_runs_from_the_always_on_alert_worker(monkeypatch):
    """순찰을 꺼도(FM_TRACE_PATROL=off) 네이버 원문 21일 삭제는 돌아야 한다 — 알림 워커가 맡는다."""
    worker = _MemoryReconciler(PATROL)
    worker.job["alert_status"] = "sent"
    purged = []

    async def fake_purge():
        purged.append(1)
        return 2

    monkeypatch.setattr(worker, "_purge_external", fake_purge)
    clock = [1000.0]
    monkeypatch.setattr("app.workers.fm_trace_finding_alert_reconciler.time.monotonic",
                        lambda: clock[0])
    asyncio.run(worker._maybe_purge())
    asyncio.run(worker._maybe_purge())          # 한 시간 안엔 다시 안 돈다
    clock[0] += 3601
    asyncio.run(worker._maybe_purge())
    assert purged == [1, 1]
