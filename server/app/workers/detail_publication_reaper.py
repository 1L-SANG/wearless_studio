"""Hourly cleanup of expired, abandoned, and explicitly deleted publications."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .. import repo
from ..detail_publications import delete_publication_objects


log = logging.getLogger("wearless.detail_publication_reaper")
_INTERVAL_SECONDS = 3600


class DetailPublicationReaper:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()
        self._last_expiry_recompute = None

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="detail-publication-reaper")

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
                log.exception("detail publication cleanup sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _sweep_once(self):
        now = datetime.now(timezone.utc)
        recompute = (self._last_expiry_recompute is None
                     or now - self._last_expiry_recompute >= timedelta(days=1))
        async with self.app.state.pool.connection() as conn:
            if recompute:
                await repo.recompute_detail_publication_expiry(conn, now=now)
                # Future expiry notifications hook into this daily calculation boundary.
            expired = await repo.list_expired_detail_publications(conn, now=now)
            pending = await repo.list_stale_pending_detail_publications(conn)
            await conn.commit()
        if recompute:
            self._last_expiry_recompute = now
        for candidate in expired + pending:
            try:
                async with self.app.state.pool.connection() as conn:
                    row = await repo.claim_detail_publication_deletion(
                        conn, user_id=candidate["user_id"], publication_id=candidate["id"],
                        now=datetime.now(timezone.utc))
                    await conn.commit()
                if row is None:
                    continue
                await delete_publication_objects(self.app.state.r2, row)
                async with self.app.state.pool.connection() as conn:
                    await repo.finalize_detail_publication_deletion(conn, publication_id=row["id"])
                    await conn.commit()
            except Exception:
                log.exception("detail publication cleanup deferred: %s", candidate["id"])
