"""Durably reconcile local FaceMarket revocations with the signed Holder API."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Mapping

import httpx

from .. import holder_client

log = logging.getLogger("wearless.fm_vc_revocation_reconciler")

_IDLE_SECONDS = 3
_STOP_TIMEOUT_SECONDS = 10
# ponytail: one long lease avoids a heartbeat; add renewal only if live revokes exceed
# 210s or crash-recovery SLO must be shorter.
_LEASE_SECONDS = 240
_OPERATION_SECONDS = 210
_VERIFY_TIMEOUT = 5.0
_REVOKE_TIMEOUT = 180.0
_ERROR_CODES = frozenset({"transport", "http_status", "invalid_body", "not_revoked"})


class _ReconcileFailure(Exception):
    def __init__(self, code: str):
        self.code = code


class FaceVcRevocationReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="facemarket-vc-revocation-reconciler"
        )

    async def stop(self):
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        finally:
            if task.done():
                self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                processed = await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("facemarket VC revocation sweep failed")
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
        try:
            async with asyncio.timeout(_OPERATION_SECONDS):
                status = await self._holder_status(job)
                if status != "revoked":
                    await self._request_revoke(job)
                    if await self._holder_status(job) != "revoked":
                        raise _ReconcileFailure("not_revoked")
        except asyncio.CancelledError:
            raise
        except _ReconcileFailure as error:
            await self._mark_retry(job, error.code)
        except (httpx.HTTPError, TimeoutError):
            await self._mark_retry(job, "transport")
        except Exception:
            await self._mark_retry(job, "transport")
        else:
            await self._mark_revoked(job)
        return True

    async def _claim_one(self) -> dict | None:
        lease_token = str(uuid.uuid4())
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_vc_revocation_jobs
                          set status = 'retry', attempts = attempts + 1,
                              next_attempt_at = now(), lease_token = null,
                              lease_expires_at = null, last_error_code = 'transport'
                        where status = 'processing'
                          and lease_expires_at <= now()"""
                )
                await cur.execute(
                    f"""with candidate as (
                           select id from fm_vc_revocation_jobs
                            where status in ('pending', 'retry')
                              and next_attempt_at <= now()
                            order by next_attempt_at, created_at
                            for update skip locked
                            limit 1
                       )
                       update fm_vc_revocation_jobs as job
                          set status = 'processing', lease_token = %s,
                              lease_expires_at = now() + interval '{_LEASE_SECONDS} seconds'
                         from candidate
                        where job.id = candidate.id
                    returning job.id::text as id,
                              job.license_id::text as license_id,
                              job.model_id::text as model_id,
                              job.vc_id, job.attempts,
                              job.lease_token::text as lease_token,
                              job.status""",
                    (lease_token,),
                )
                job = await cur.fetchone()
            await conn.commit()
        return job

    async def _holder_status(self, job) -> str:
        settings = self.app.state.settings
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT) as client:
            response = await holder_client.post(
                client,
                base_url=settings.opendid_holder_url,
                secret=settings.opendid_holder_hmac_secret,
                path="/holder/vc/verify",
                payload={"vcId": job["vc_id"]},
            )
        if response.status_code != 200:
            raise _ReconcileFailure("http_status")
        try:
            body = response.json()
        except Exception:
            raise _ReconcileFailure("invalid_body") from None
        if not isinstance(body, Mapping) or not isinstance(body.get("status"), str):
            raise _ReconcileFailure("invalid_body")
        return body["status"]

    async def _request_revoke(self, job) -> None:
        settings = self.app.state.settings
        async with httpx.AsyncClient(timeout=_REVOKE_TIMEOUT) as client:
            response = await holder_client.post(
                client,
                base_url=settings.opendid_holder_url,
                secret=settings.opendid_holder_hmac_secret,
                path=f"/holder/models/{job['model_id']}/revoke-vc",
                payload={"vcId": job["vc_id"]},
            )
        if response.status_code != 200:
            raise _ReconcileFailure("http_status")
        try:
            body = response.json()
        except Exception:
            raise _ReconcileFailure("invalid_body") from None
        if not (
            isinstance(body, Mapping)
            and body.get("revoked") is True
            and body.get("status") == "revoked"
        ):
            raise _ReconcileFailure("invalid_body")

    async def _mark_retry(self, job, code) -> None:
        code = code if code in _ERROR_CODES else "transport"
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_vc_revocation_jobs
                          set status = 'retry', attempts = attempts + 1,
                              next_attempt_at = now() +
                                  make_interval(secs => least(
                                      300, power(2, least(attempts + 1, 9))
                                  )::double precision),
                              lease_token = null, lease_expires_at = null,
                              last_error_code = %s
                        where id = %s and status = 'processing' and lease_token = %s""",
                    (code, job["id"], job["lease_token"]),
                )
            await conn.commit()

    async def _mark_revoked(self, job) -> None:
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_vc_revocation_jobs
                          set status = 'revoked', revoked_at = now(),
                              lease_token = null, lease_expires_at = null,
                              last_error_code = null
                        where id = %s and status = 'processing' and lease_token = %s""",
                    (job["id"], job["lease_token"]),
                )
            await conn.commit()
