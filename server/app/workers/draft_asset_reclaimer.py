"""Periodic reclamation for draft-slot uploads that never became referenced."""

import asyncio
import logging

from .. import repo

log = logging.getLogger("wearless.draft_asset_reclaimer")
_INTERVAL_SECONDS = 3600


class DraftAssetReclaimer:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="draft-asset-reclaimer")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("draft asset reclamation sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _sweep_once(self):
        async with self.app.state.pool.connection() as conn:
            assets = await repo.reclaim_stale_unreferenced_draft_assets(conn)
            await conn.commit()
        for asset in assets:
            try:
                await asyncio.to_thread(self.app.state.r2.delete, asset["r2_key"])
            except Exception:
                log.exception("stale draft asset R2 cleanup failed: %s", asset["id"])
