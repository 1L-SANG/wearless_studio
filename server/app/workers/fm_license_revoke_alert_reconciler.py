"""라이선스 해지 알림 작업을 DB에서 찾아 Slack 성공까지 재시도한다."""

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from .. import facemarket_notify

log = logging.getLogger("wearless.fm_license_revoke_alert_reconciler")

_IDLE_SECONDS = 3
_LEASE_SECONDS = 60
_DELIVERY_TIMEOUT_SECONDS = 20


class LicenseRevokeAlertReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="fm-license-revoke-alerts")

    async def stop(self):
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                processed = await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("license revoke alert sweep failed")
                processed = False
            if processed:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _sweep_once(self):
        job = await self._claim_one()
        if job is None:
            return False
        settings = self.app.state.settings
        revoked_on = job["revoked_on"]
        admin_base = settings.fm_application_public_base.replace(
            "facemarket.", "admin."
        ).rstrip("/")
        try:
            # HTTPX timeout 은 청크 사이 대기 시간이라 느린 응답 전체를 제한하지 않는다.
            # 리스가 끝나기 전에 중단해 다른 워커의 동시 전송을 막는다.
            async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
                delivered = await facemarket_notify.notify_slack_license_revoked(
                    settings,
                    model_id=job["model_id"],
                    display_name=job["display_name"],
                    revoked_on=revoked_on.isoformat(),
                    purge_due_on=(revoked_on + timedelta(days=30)).isoformat(),
                    other_active_licenses=job["other_active_licenses"],
                    admin_link=f"{admin_base}/models",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("license revoke alert delivery failed")
            delivered = False
        if delivered:
            await self._mark_sent(job)
        else:
            await self._mark_retry(job)
        return True

    async def _claim_one(self):
        lease_token = str(uuid.uuid4())
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_license_revoke_alerts
                          set status = 'pending', attempts = attempts + 1,
                              next_attempt_at = now(), lease_token = null,
                              lease_expires_at = null
                        where status = 'processing' and lease_expires_at <= now()"""
                )
                await cur.execute(
                    f"""with candidate as (
                           select license_id from fm_license_revoke_alerts
                            where status = 'pending' and next_attempt_at <= now()
                            order by next_attempt_at, created_at
                            for update skip locked limit 1
                       )
                       update fm_license_revoke_alerts as job
                          set status = 'processing', lease_token = %s,
                              lease_expires_at = now() + interval '{_LEASE_SECONDS} seconds'
                         from candidate
                        where job.license_id = candidate.license_id
                    returning job.license_id::text as license_id,
                              job.model_id::text as model_id, job.display_name,
                              job.revoked_on, job.other_active_licenses,
                              job.lease_token::text as lease_token""",
                    (lease_token,),
                )
                job = await cur.fetchone()
            await conn.commit()
        return job

    async def _mark_retry(self, job):
        log.warning("license revoke alert retry scheduled license=%s", job["license_id"])
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_license_revoke_alerts
                          set status = 'pending', attempts = attempts + 1,
                              next_attempt_at = now() + make_interval(
                                  secs => least(3600, power(2, least(attempts + 1, 12)))::double precision
                              ),
                              lease_token = null, lease_expires_at = null
                        where license_id = %s and status = 'processing' and lease_token = %s""",
                    (job["license_id"], job["lease_token"]),
                )
            await conn.commit()

    async def _mark_sent(self, job):
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_license_revoke_alerts
                          set status = 'sent', sent_at = now(),
                              lease_token = null, lease_expires_at = null
                        where license_id = %s and status = 'processing' and lease_token = %s""",
                    (job["license_id"], job["lease_token"]),
                )
            await conn.commit()
