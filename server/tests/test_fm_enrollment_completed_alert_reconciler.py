"""등록 완료 Slack 알림은 실패와 느린 전송 뒤에도 작업을 보존한다."""

import asyncio
from types import SimpleNamespace

import pytest

from app import facemarket_notify
from app.workers import fm_enrollment_completed_alert_reconciler as alert_module
from app.workers.fm_enrollment_completed_alert_reconciler import EnrollmentCompletedAlertReconciler


class _MemoryReconciler(EnrollmentCompletedAlertReconciler):
    def __init__(self, identity_method):
        settings = SimpleNamespace(
            fm_slack_webhook_url="https://hooks.example/enrollment",
            fm_application_public_base="https://facemarket.wearless.kr/",
        )
        super().__init__(SimpleNamespace(state=SimpleNamespace(settings=settings)))
        self.job = {
            "license_id": "license-1", "enrollment_id": "enrollment-1",
            "display_name": "홍*동", "identity_method": identity_method,
            "admin_link": ("https://admin.wearless.kr/review?tab=photos"
                           if identity_method == "mid" else "https://admin.wearless.kr/review"),
            "status": "pending", "attempts": 0,
        }

    async def _claim_one(self):
        if self.job["status"] != "pending":
            return None
        self.job["status"] = "processing"
        return dict(self.job, lease_token="lease-1")

    async def _mark_retry(self, job):
        assert job["lease_token"] == "lease-1"
        self.job["status"] = "pending"
        self.job["attempts"] += 1

    async def _mark_sent(self, job):
        assert job["lease_token"] == "lease-1"
        self.job["status"] = "sent"


@pytest.mark.parametrize("identity_method,link", [
    ("mid", "https://admin.wearless.kr/review?tab=photos"),
    ("simple_auth", "https://admin.wearless.kr/review"),
])
def test_failed_alert_retries_until_delivered_with_matching_admin_link(
    monkeypatch, identity_method, link,
):
    worker = _MemoryReconciler(identity_method)
    responses = iter([False, True])
    calls = []

    async def notify(_settings, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(facemarket_notify, "notify_slack_enrollment_completed", notify)

    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "pending"
    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "sent"
    assert asyncio.run(worker._sweep_once()) is False
    assert calls == [{
        "display_name": "홍*동", "identity_method": identity_method,
        "admin_link": link,
    }] * 2


def test_slow_alert_delivery_is_retried_before_lease_expires(monkeypatch):
    worker = _MemoryReconciler("mid")
    monkeypatch.setattr(alert_module, "_DELIVERY_TIMEOUT_SECONDS", 0.01)

    async def too_slow(_settings, **_kwargs):
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(facemarket_notify, "notify_slack_enrollment_completed", too_slow)

    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "pending"
    assert worker.job["attempts"] == 1
