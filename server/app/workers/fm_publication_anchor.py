"""배포본 온체인 앵커 reconciler (층③).

jobs 테이블을 쓰지 않는다 — jobs_active_unique_idx 가 (project_id, kind) 동시 1건이라
같은 프로젝트에서 연달아 내려받으면 앵커가 서로를 막는다. fm_vc_revocation_reconciler
패턴(전용 큐 + 루프 + 재시도 상한)을 복제한다.
"""

import asyncio
import contextlib
import logging

log = logging.getLogger("wearless.fm_publication_anchor")

_IDLE_SECONDS = 5
_STOP_TIMEOUT_SECONDS = 10
_LEASE_SECONDS = 240
#: 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01 prod 실측).
_MAX_ATTEMPTS = 50


async def anchor_one(conn, chain, job: dict) -> str:
    """앵커 1건. 반환 = 'anchored' | 'retry' | 'dead'."""
    publication_id = str(job["publication_id"])
    try:
        result = await asyncio.to_thread(
            chain.record_publication,
            publication_id=publication_id,
            image_sha256=job["image_sha256"],
            license_id=str(job["license_ref"]),
        )
    except Exception:
        # 중복 revert 는 "이미 기록됨"이다. 재기록하지 말고 저장값으로 화해한다.
        stored = await asyncio.to_thread(chain.wait_for_publication, publication_id, 5.0)
        if not stored or not stored.get("exists"):
            stored = await asyncio.to_thread(chain.get_publication, publication_id)
        if not stored or not stored.get("exists"):
            attempts = int(job.get("attempts") or 0) + 1
            status = "dead" if attempts > _MAX_ATTEMPTS else "retry"
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_anchor_jobs
                          set status = %s, attempts = %s, last_error = %s, lease_until = null
                        where publication_id = %s""",
                    (status, attempts, "record_failed", publication_id),
                )
            if status == "dead":
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update fm_publication_records set chain_status = 'failed' "
                        "where id = %s",
                        (publication_id,),
                    )
                log.error("publication anchor gave up (dead): %s", publication_id)
            await conn.commit()
            return status
        result = {
            "tx_hash": None, "block": stored["block"], "chain_id": chain.chain_id,
        }

    async with conn.cursor() as cur:
        await cur.execute(
            """update fm_publication_records
                  set chain_status = %s, tx_hash = %s, chain_id = %s,
                      recorded_block = %s
                where id = %s""",
            ("confirmed", result.get("tx_hash"), str(result.get("chain_id")),
             result.get("block"), publication_id),
        )
        await cur.execute(
            "update fm_publication_anchor_jobs set status = 'anchored', lease_until = null "
            "where publication_id = %s",
            (publication_id,),
        )
    await conn.commit()
    return "anchored"


class PublicationAnchorReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="facemarket-publication-anchor"
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
            pass
        finally:
            self._task = None

    async def _claim(self, conn) -> dict | None:
        """lease 로 한 건 집는다. 만료 lease 는 회수한다(크래시 복구)."""
        async with conn.cursor() as cur:
            await cur.execute(
                f"""update fm_publication_anchor_jobs j
                       set status = 'processing',
                           lease_until = now() + interval '{_LEASE_SECONDS} seconds',
                           attempted_at = now()
                      from fm_publication_records r
                     where r.id = j.publication_id
                       and j.publication_id = (
                             select publication_id from fm_publication_anchor_jobs
                              where status in ('pending', 'retry')
                                 or (status = 'processing' and lease_until < now())
                              order by created_at
                              for update skip locked
                              limit 1)
                 returning j.publication_id::text as publication_id, j.attempts,
                           r.image_sha256, r.license_ref::text as license_ref"""
            )
            row = await cur.fetchone()
        await conn.commit()
        return row

    async def _run(self):
        while not self._stop.is_set():
            chain = getattr(self.app.state, "fm_chain", None)
            if chain is None or not getattr(chain, "provenance_enabled", False):
                await self._sleep()
                continue
            try:
                async with self.app.state.pool.connection() as conn:
                    job = await self._claim(conn)
                    if job is None:
                        await self._sleep()
                        continue
                    await anchor_one(conn, chain, job)
            except Exception:
                log.warning("publication anchor sweep failed", exc_info=True)
                await self._sleep()

    async def _sleep(self):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
