"""일시 장애로 끝난 SAM 잡의 다음 세대를 민다.

톤 마스크는 셀러가 톤 에디터를 열고 있는 동안 상태 라우트가 재시도를 민다
(`routes._enqueue_tone_mask_generations`). 그런데 `sam_preprocess` 와 `matching_cutout` 은
백그라운드 잡이라 **폴링하는 화면이 없다.** 아무도 밀지 않으면 재시도는 일어나지 않는다 —
그래서 2026-08-21 이전까지 이 둘은 SAM 이 잠깐 없으면 영영 포기했다.

디스패처 스윕에 얹지 않는 이유: 디스패처는 워커를 `await` 한 뒤 다음 반복으로 간다. 평균
563초짜리 `detail_page` 가 도는 동안 스윕도 멈추므로, 285초 예산을 지키는 타이머로 쓸 수 없다.
여기서 예외가 나도 디스패처는 계속 돈다 — 분리해 둔 이유가 그것이다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app import repo as _repo
from app.services import sam_retry

log = logging.getLogger("wearless.sam_retry")

#: 폴링하는 화면이 없는 잡만. `editor_garment_mask` 는 톤 에디터가 이미 민다 — 여기서 또 밀면
#: 셀러가 보고 있지 않은 컷까지 예산을 태운다.
PUSH_KINDS = ("sam_preprocess", "matching_cutout")

#: 가장 짧은 백오프(15초)를 따라간다. 인덱스(jobs_sam_retry_idx)가 받쳐 주는 단일 조회라
#: 비용이 미미하다.
POLL_SECONDS = float(min(sam_retry.BACKOFF_SECONDS))


class SamRetryPusher:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sam-retry-pusher")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run(self):
        pool = self.app.state.pool
        while not self._stop.is_set():
            try:
                async with pool.connection() as conn:
                    pushed = await self._push_once(_repo, conn)
                    await conn.commit()
                if pushed and hasattr(self.app.state, "dispatcher"):
                    # 새 세대를 걸었으면 디스패처의 유휴 대기를 깨운다 — 라우트가 잡을 만들 때와
                    # 같은 규율. 없으면 최대 poll_interval 만큼 더 기다린다(치명적이진 않다).
                    with contextlib.suppress(Exception):
                        self.app.state.dispatcher.wake()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sam retry pusher error")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)

    async def _push_once(self, repo, conn) -> int:
        """다음 세대를 걸 수 있는 잡을 전부 걸고, 건 개수를 돌려준다.

        한 후보가 깨져 있어도 나머지는 민다 — best-effort 다. 세대가 두 갈래로 갈라지는 것만은
        막는다: base 키의 **최신** 세대가 바로 이 잡일 때만 다음 세대를 건다.
        """
        candidates = await repo.list_retryable_sam_jobs(
            conn, PUSH_KINDS, max_retries=sam_retry.MAX_RETRIES,
            min_age_seconds=min(sam_retry.BACKOFF_SECONDS))
        pushed = 0
        ready_cache: list[bool] = []

        async def sam_ready() -> bool:
            """주기당 한 번만 묻는다. 후보가 백오프에 걸렸을 때만 불리므로 조용한 주기에는
            AWS·HTTP 호출이 0이다."""
            if not ready_cache:
                ready_cache.append(await self._sam_ready())
            return ready_cache[0]

        for job in candidates:
            try:
                if await self._push_one(repo, conn, job, sam_ready):
                    pushed += 1
            except Exception:  # noqa: BLE001 - 한 행의 문제가 전체 주기를 막지 않는다
                log.exception("sam retry push failed job=%s", (job or {}).get("id"))
        return pushed

    async def _sam_ready(self) -> bool:
        """sam2 가 지금 답하는가. 리졸버가 없으면(플래그 off·테스트) 모른다 = False."""
        resolver = getattr(getattr(self.app, "state", None), "sam_endpoint", None)
        if resolver is None:
            return False
        try:
            return await resolver.ready()
        except Exception:  # noqa: BLE001 - 최적화 신호가 재시도를 막으면 안 된다
            log.warning("sam readiness probe failed", exc_info=True)
            return False

    async def _push_one(self, repo, conn, job: dict, sam_ready=None) -> bool:
        if not sam_retry.job_is_retryable(job):
            return False                       # 판정 실패 — 다시 돌려도 같은 답이다
        if not sam_retry.backoff_elapsed(job):
            # 백오프는 "sam2 가 언제 살아날지 모른다"를 대신하는 추측이었다. 살아난 걸 실제로
            # 확인했으면 더 기다릴 이유가 없다 — 예산(MAX_RETRIES)은 그대로 지킨다.
            # 실측(2026-08-27): 사다리가 15/60/90/120 이라 sam2 가 준비된 뒤에도 최대 80초를
            # 그냥 흘려보냈다.
            if sam_ready is None or not await sam_ready():
                return False
            log.info("sam retry backoff skipped — sam2 is answering (job=%s)",
                     (job or {}).get("id"))
        base = sam_retry.base_key(job.get("idempotency_key"))
        if not base:
            return False
        latest = await repo.get_latest_job_generation(conn, job["user_id"], base)
        if latest is None or str(latest.get("id")) != str(job.get("id")):
            return False                       # 이미 다음 세대가 있다 — 갈래를 만들지 않는다
        if str(latest.get("status") or "") not in sam_retry.TERMINAL_STATUSES:
            return False                       # 아직 도는 중이다
        retry = sam_retry.job_retry_count(job) + 1
        if retry > sam_retry.MAX_RETRIES:
            return False
        payload = {k: v for k, v in (job.get("payload") or {}).items() if k != "retry"}
        payload["retry"] = retry
        await repo.create_job(
            conn, user_id=job["user_id"], project_id=job["project_id"],
            kind=job["kind"], payload=payload,
            idempotency_key=sam_retry.generation_key(base, retry),
            credits_reserved=0, metadata={})
        log.info("sam retry queued kind=%s base=%s generation=%s", job["kind"], base, retry)
        return True
