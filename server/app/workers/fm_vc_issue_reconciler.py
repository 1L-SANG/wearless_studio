"""Continue pending FaceMarket VC issuance after the request has gone away."""

import asyncio
import contextlib
import logging

from ..facemarket import issue_and_activate_pending_face_vc

log = logging.getLogger("wearless.fm_vc_issue_reconciler")

_IDLE_SECONDS = 30
_STOP_TIMEOUT_SECONDS = 10


class FaceVcIssueReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="facemarket-vc-issue-reconciler")

    async def stop(self):
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), _STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            if self._task.done():
                self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("facemarket VC issue sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _sweep_once(self):
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """select l.id::text as license_id, l.model_id::text as model_id,
                              m.user_id::text as user_id
                         from fm_licenses l
                         join fm_models m on m.id = l.model_id
                         join fm_biometric_enrollments e on e.id = l.enrollment_id
                        where l.status = 'pending' and l.vc_id is null
                          and e.status = 'vc_pending'
                          and e.user_id = m.user_id and e.model_id = m.id
                          and l.updated_at < now() - interval '15 seconds'
                        order by l.updated_at
                        for update of l skip locked
                        limit 20"""
                )
                rows = await cur.fetchall()
            await conn.commit()
        if not rows:
            return False
        for row in rows:
            try:
                await issue_and_activate_pending_face_vc(self.app, **row)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("facemarket VC issue failed license=%s", row["license_id"])
        return True
