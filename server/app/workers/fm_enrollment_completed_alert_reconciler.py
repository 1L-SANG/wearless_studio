"""등록 완료 알림을 DB에서 찾아 Slack 성공까지 재시도한다.

Webhook 성공 후 sent 기록 전 종료되면 중복 전송될 수 있으므로 delivery는 at least once다.
"""

import asyncio
import contextlib
import logging
import uuid

from .. import facemarket_notify

log = logging.getLogger("wearless.fm_enrollment_completed_alert_reconciler")

_IDLE_SECONDS = 3
_LEASE_SECONDS = 60
_DELIVERY_TIMEOUT_SECONDS = 20


class EnrollmentCompletedAlertReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="fm-enrollment-completed-alerts")

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
                log.exception("enrollment completed alert sweep failed")
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
        try:
            async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
                delivered = await facemarket_notify.notify_slack_enrollment_completed(
                    settings,
                    display_name=job["display_name"],
                    identity_method=job["identity_method"],
                    admin_link=job["admin_link"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("enrollment completed alert delivery failed")
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
                    """update fm_enrollment_completed_alerts
                          set status = 'pending', attempts = attempts + 1,
                              next_attempt_at = now(), lease_token = null,
                              lease_expires_at = null
                        where status = 'processing' and lease_expires_at <= now()"""
                )
                await cur.execute(
                    f"""with candidate as (
                           select license_id from fm_enrollment_completed_alerts
                            where status = 'pending' and next_attempt_at <= now()
                            order by next_attempt_at, created_at
                            for update skip locked limit 1
                       )
                       update fm_enrollment_completed_alerts as job
                          set status = 'processing', lease_token = %s,
                              lease_expires_at = now() + interval '{_LEASE_SECONDS} seconds'
                         from candidate
                        where job.license_id = candidate.license_id
                    returning job.license_id::text as license_id,
                              job.display_name, job.identity_method, job.admin_link,
                              job.lease_token::text as lease_token""",
                    (lease_token,),
                )
                job = await cur.fetchone()
            await conn.commit()
        return job

    async def _mark_retry(self, job):
        log.warning("enrollment completed alert retry scheduled license=%s", job["license_id"])
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_enrollment_completed_alerts
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
                    """update fm_enrollment_completed_alerts
                          set status = 'sent', sent_at = now(),
                              lease_token = null, lease_expires_at = null
                        where license_id = %s and status = 'processing' and lease_token = %s""",
                    (job["license_id"], job["lease_token"]),
                )
            await conn.commit()
