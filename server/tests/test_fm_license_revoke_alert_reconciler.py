"""해지 알림은 전송에 실패해도 대기 작업을 남겨 재시도한다."""

import asyncio
from datetime import date
from types import SimpleNamespace

from app import facemarket_notify
from app.workers import fm_license_revoke_alert_reconciler as alert_module
from app.workers.fm_license_revoke_alert_reconciler import LicenseRevokeAlertReconciler


class _MemoryReconciler(LicenseRevokeAlertReconciler):
    def __init__(self):
        settings = SimpleNamespace(
            fm_slack_webhook_url="https://hooks.example/revoke",
            fm_application_public_base="https://facemarket.wearless.kr",
        )
        super().__init__(SimpleNamespace(state=SimpleNamespace(settings=settings)))
        self.job = {
            "license_id": "license-1", "model_id": "model-1",
            "display_name": "홍*동", "revoked_on": date(2026, 9, 25),
            "other_active_licenses": 0, "status": "pending", "attempts": 0,
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


def test_failed_delivery_is_retried_and_only_success_marks_sent(monkeypatch):
    worker = _MemoryReconciler()
    responses = iter([False, True])
    calls = []

    async def notify(_settings, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(facemarket_notify, "notify_slack_license_revoked", notify)

    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "pending"
    assert worker.job["attempts"] == 1

    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "sent"
    assert asyncio.run(worker._sweep_once()) is False
    assert len(calls) == 2
    assert calls[0] == calls[1] == {
        "model_id": "model-1", "display_name": "홍*동",
        "revoked_on": "2026-09-25", "purge_due_on": "2026-10-25",
        "other_active_licenses": 0,
        "admin_link": "https://admin.wearless.kr/models",
    }


def test_slow_delivery_cannot_outlive_job_lease(monkeypatch):
    worker = _MemoryReconciler()
    monkeypatch.setattr(alert_module, "_DELIVERY_TIMEOUT_SECONDS", 0.01, raising=False)

    async def too_slow(_settings, **_kwargs):
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(facemarket_notify, "notify_slack_license_revoked", too_slow)

    assert asyncio.run(worker._sweep_once()) is True
    assert worker.job["status"] == "pending"
    assert worker.job["attempts"] == 1
