"""자동 출처 추적 새 발견을 Slack 성공까지 재시도한다(2026-09-27).

fm_trace_findings 의 alert_* 열이 아웃박스다. 순찰이 발견을 기록한 트랜잭션에 alert_status='pending'
이 같이 들어가므로, 워커가 죽어도 알림을 잃지 않는다. 이미 아는 셀러 판매처로 자동 분류된 발견은
처음부터 'skipped' 라 여기 오지 않는다. Webhook 성공 뒤 sent 기록 전에 종료되면 한 번 더 갈 수 있다
(at least once) — 등록 완료 알림(fm_enrollment_completed_alert_reconciler)과 같은 구조다.

네이버 원문 21일 삭제(검색 API 특약 2.4)도 여기서 한 시간마다 돌린다. 순찰 워커는 FM_TRACE_PATROL
스위치로 꺼질 수 있지만, 이미 저장한 네이버 원문은 꺼진 뒤에도 기한에 지워져야 한다 — 이 워커는
추적 층(FM_PROVENANCE_ENABLED)이 켜져 있으면 항상 돈다.
"""

import asyncio
import contextlib
import logging
import time
import uuid

from .. import facemarket_notify, fm_trace_findings

log = logging.getLogger("wearless.fm_trace_finding_alert_reconciler")

_IDLE_SECONDS = 5
_LEASE_SECONDS = 60
_DELIVERY_TIMEOUT_SECONDS = 20
_PURGE_EVERY_SECONDS = 3600


class TraceFindingAlertReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()
        self._last_purge: float | None = None

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="fm-trace-finding-alerts")

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

    async def _purge_external(self) -> int:
        async with self.app.state.pool.connection() as conn:
            n = await fm_trace_findings.purge_expired_external(conn)
            await conn.commit()
        return n

    async def _maybe_purge(self):
        now = time.monotonic()
        if self._last_purge is not None and now - self._last_purge < _PURGE_EVERY_SECONDS:
            return
        self._last_purge = now
        try:
            n = await self._purge_external()
            if n:
                log.info("trace finding naver raw fields purged rows=%d", n)
        except Exception:
            log.warning("trace finding purge failed", exc_info=True)

    async def _run(self):
        while not self._stop.is_set():
            await self._maybe_purge()
            try:
                processed = await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("trace finding alert sweep failed")
                processed = False
            if processed:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _admin_link(self) -> str:
        base = self.app.state.settings.fm_application_public_base.replace(
            "facemarket.", "admin.").rstrip("/")
        return f"{base}/trace?tab=found"

    async def _sweep_once(self):
        job = await self._claim_one()
        if job is None:
            return False
        settings = self.app.state.settings
        try:
            async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
                delivered = await facemarket_notify.notify_slack_trace_finding(
                    settings,
                    source=job["source"],
                    platform=job["platform"],
                    model_name=job.get("model_name"),
                    method=job.get("method"),
                    confidence=job.get("confidence"),
                    matched=job.get("target") is not None,
                    admin_link=self._admin_link(),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("trace finding alert delivery failed")
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
                    """update fm_trace_findings
                          set alert_status = 'pending', alert_attempts = alert_attempts + 1,
                              alert_next_at = now(), alert_lease_token = null,
                              alert_lease_expires_at = null
                        where alert_status = 'processing' and alert_lease_expires_at <= now()"""
                )
                await cur.execute(
                    f"""with candidate as (
                           select id from fm_trace_findings
                            where alert_status = 'pending' and alert_next_at <= now()
                            order by alert_next_at, created_at
                            for update skip locked limit 1
                       )
                       update fm_trace_findings as f
                          set alert_status = 'processing', alert_lease_token = %s,
                              alert_lease_expires_at = now() + interval '{_LEASE_SECONDS} seconds'
                         from candidate
                        where f.id = candidate.id
                    returning f.id::text as id, f.source, f.platform, f.method, f.confidence,
                              f.target, f.alert_lease_token::text as lease_token,
                              (select m.display_name from fm_models m
                                where m.id = coalesce(f.reporter_model_id, f.model_id))
                                as model_name""",
                    (lease_token,),
                )
                job = await cur.fetchone()
            await conn.commit()
        return job

    async def _mark_retry(self, job):
        log.warning("trace finding alert retry scheduled finding=%s", job["id"])
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_trace_findings
                          set alert_status = 'pending', alert_attempts = alert_attempts + 1,
                              alert_next_at = now() + make_interval(
                                  secs => least(3600, power(2, least(alert_attempts + 1, 12)))::double precision
                              ),
                              alert_lease_token = null, alert_lease_expires_at = null
                        where id = %s and alert_status = 'processing' and alert_lease_token = %s""",
                    (job["id"], job["lease_token"]),
                )
            await conn.commit()

    async def _mark_sent(self, job):
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_trace_findings
                          set alert_status = 'sent', alert_sent_at = now(),
                              alert_lease_token = null, alert_lease_expires_at = null
                        where id = %s and alert_status = 'processing' and alert_lease_token = %s""",
                    (job["id"], job["lease_token"]),
                )
            await conn.commit()
