"""FaceMarket cutover writer quiescence primitives.

No freeze, purge, R2, Holder, approval, or production action lives here.
"""

import asyncio
from dataclasses import dataclass

from psycopg.types.json import Json

from . import repo
from .facemarket_enrollment import _PHOTO_FENCE_NAMESPACE

_CUTOVER_CANCEL_MESSAGE = "실물 모델 보안 전환으로 작업을 취소하고 크레딧을 돌려드렸어요."
_PERSONALIZATION_CANCEL_MESSAGE = "개인화 파기로 작업을 취소하고 크레딧을 돌려드렸어요."


@dataclass(frozen=True, slots=True, repr=False)
class WriterQuiescence:
    cancelled_count: int
    pending_count: int
    running_count: int


class CutoverBlocked(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def close_initial_cutover_writers(pool, *, batch_id: str) -> None:
    """Atomically close new biometric writers for one approved cutover batch."""
    async with pool.connection() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_cutover_batches
                   set status = 'draining', started_at = coalesce(started_at, now())
                 where id = %s and status = 'approved'
                 returning id::text as id
                """,
                (batch_id,),
            )
            if await cur.fetchone() is None:
                await cur.execute(
                    "select status from fm_cutover_batches where id = %s",
                    (batch_id,),
                )
                row = await cur.fetchone()
                if row is None or row["status"] not in {"draining", "failed"}:
                    raise CutoverBlocked("cutover_batch_not_approved")
        await conn.commit()


async def _pending_cutover_jobs(conn) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id from jobs
            where status='pending' and kind = any(%s)
            order by created_at
            for update skip locked
            limit 50
            """,
            (list(repo.FACEMARKET_CUTOVER_JOB_KINDS),),
        )
        return await cur.fetchall()


async def _cutover_job_counts(conn) -> tuple[int, int]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              count(*) filter (where status='pending')::int as pending_count,
              count(*) filter (where status='running')::int as running_count
            from jobs
            where kind = any(%s) and status in ('pending', 'running')
            """,
            (list(repo.FACEMARKET_CUTOVER_JOB_KINDS),),
        )
        row = await cur.fetchone()
    return int(row["pending_count"]), int(row["running_count"])


async def _photo_lock_count(conn) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id from fm_biometric_enrollments
            where status in ('photos_pending', 'liveness_pending', 'processing')
            order by id
            """
        )
        rows = await cur.fetchall()
        locked = 0
        for row in rows:
            enrollment_id = str(row["id"]).lower()
            await cur.execute(
                "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
                (_PHOTO_FENCE_NAMESPACE, enrollment_id),
            )
            if (await cur.fetchone())["locked"]:
                await cur.execute(
                    "select pg_advisory_unlock(%s, hashtext(%s))",
                    (_PHOTO_FENCE_NAMESPACE, enrollment_id),
                )
            else:
                locked += 1
    return locked


async def quiesce_initial_cutover_writers(
    pool,
    *,
    batch_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.25,
) -> WriterQuiescence:
    """Cancel pending cutover jobs and wait until running/photo writers drain."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    cancelled = 0
    last_pending = 0
    last_running = 0
    while True:
        async with pool.connection() as conn:
            rows = await _pending_cutover_jobs(conn)
            for row in rows:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="facemarket_cutover",
                    message=_CUTOVER_CANCEL_MESSAGE,
                )
                cancelled += int(changed)
                if changed:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update jobs set metadata = metadata || %s::jsonb where id = %s",
                            (Json({"cutoverBatchId": batch_id}), row["id"]),
                        )
            last_pending, last_running = await _cutover_job_counts(conn)
            photo_locks = await _photo_lock_count(conn)
            await conn.commit()
        if last_pending == 0 and last_running == 0 and photo_locks == 0:
            return WriterQuiescence(cancelled, 0, 0)
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("writers_not_drained")
        await asyncio.sleep(poll_interval_seconds)


async def _load_purging_profile(conn, user_id: str) -> str:
    async with conn.cursor() as cur:
        await cur.execute(
            "select p.status from personalization_profiles p where p.user_id = %s for update",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None or row["status"] != "purging":
        raise CutoverBlocked("personalization_profile_not_purging")
    return user_id


async def quiesce_personalization_writers(
    pool,
    *,
    user_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.25,
) -> WriterQuiescence:
    """Cancel pending and drain running personalization_generation for one purging user."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    cancelled = 0
    last_pending = 0
    last_running = 0
    while True:
        async with pool.connection() as conn:
            await _load_purging_profile(conn, user_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select j.id::text as id, g.id::text as generation_id
                    from jobs j
                    left join personalization_generations g on g.job_id = j.id
                    where j.status='pending'
                      and j.kind='personalization_generation'
                      and j.user_id=%s
                    order by j.created_at
                    for update of j skip locked
                    limit 50
                    """,
                    (user_id,),
                )
                pending = await cur.fetchall()
            for row in pending:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="personalization_purge",
                    message=_PERSONALIZATION_CANCEL_MESSAGE,
                )
                cancelled += int(changed)
                if changed and row.get("generation_id"):
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            update personalization_generations
                            set status='error', error_code='personalization_purge'
                            where id=%s
                            """,
                            (row["generation_id"],),
                        )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select
                      count(*) filter (where status='pending')::int as pending_count,
                      count(*) filter (where status='running')::int as running_count
                    from jobs
                    where kind='personalization_generation'
                      and user_id=%s
                      and status in ('pending', 'running')
                    """,
                    (user_id,),
                )
                row = await cur.fetchone()
            last_pending = int(row["pending_count"])
            last_running = int(row["running_count"])
            await conn.commit()
        if last_pending == 0 and last_running == 0:
            return WriterQuiescence(cancelled, 0, 0)
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("writers_not_drained")
        await asyncio.sleep(poll_interval_seconds)
