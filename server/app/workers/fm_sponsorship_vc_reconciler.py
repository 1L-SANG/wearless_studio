"""협찬 동의 VC 발급 워커 — pending 증서를 holder 로 발급해 active 로 바꾼다.

협찬 토글은 holder 를 기다리지 않는다(scale-to-zero 콜드부트 ~2분). 켜는 순간 pending 행만
남기고, 이 워커가 30초마다 집어 발급한다. 폐기는 기존 FaceVcRevocationReconciler 가 맡는다.
"""

import asyncio
import contextlib
import logging

from ..facemarket_sponsorship_vc import backfill_missing, claim_pending, issue_one

log = logging.getLogger("wearless.fm_sponsorship_vc_reconciler")

_IDLE_SECONDS = 30
_STOP_TIMEOUT_SECONDS = 10


class FmSponsorshipVcReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="facemarket-sponsorship-vc-reconciler")

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
                log.exception("sponsorship VC sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _prewarm_holder(self):
        scaler = getattr(self.app.state, "opendid_autoscaler", None)
        if scaler is not None:
            with contextlib.suppress(Exception):
                scaler.prewarm_soon()

    async def _sweep_once(self) -> bool:
        await backfill_missing(self.app.state.pool)
        rows = await claim_pending(self.app.state.pool)
        if not rows:
            return False
        self._prewarm_holder()
        for row in rows:
            try:
                await issue_one(self.app, row)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sponsorship VC issue failed credential=%s", row["id"])
        return True
