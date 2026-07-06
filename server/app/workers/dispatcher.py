"""Job dispatcher (§5). web 프로세스 lifespan에서 background 태스크로 시작.

jobs 큐를 폴링해 pending job을 FOR UPDATE SKIP LOCKED로 claim → 워커 실행. 주기적으로
lease 초과(고착) job을 복구하고, 복구로 error 처리된 job의 예약 크레딧을 해제한다.
요청 핸들러 밖에서 실행 — HTTP 취소·이탈이 job을 끊지 않게 한다.
"""

import asyncio
import logging
import time

from .. import repo
from .analyze_job import run_analyze_job
from .detail_page_job import run_detail_page_job
from .mannequin_job import run_mannequin_job

log = logging.getLogger("wearless.dispatcher")

# kind → 워커. claim 대상(_KINDS)과 라우팅을 한 곳에서 관리 — 새 job 종류는 여기에 추가.
_WORKERS = {
    "mannequin": run_mannequin_job,
    "analyze": run_analyze_job,  # AG-01 상품 분석 (무과금)
    "detail_page": run_detail_page_job,  # PL-4 상세페이지 생성 (AG-06→02→03→M-02)
}
_KINDS = tuple(_WORKERS)  # 이후 mannequin_adjust/detail_page/editor_image 추가
_SWEEP_INTERVAL = 60.0  # lease 복구 점검 주기(초)


class JobDispatcher:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="job-dispatcher")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run(self):
        s = self.app.state.settings
        pool = self.app.state.pool
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - last_sweep >= _SWEEP_INTERVAL:
                    last_sweep = now
                    await self._recover_stale(s, pool)
                async with pool.connection() as conn:
                    job = await repo.claim_next_job(conn, _KINDS, s.job_worker_id)
                    await conn.commit()
                if job is None:
                    await asyncio.sleep(s.job_poll_interval_seconds)
                    continue
                worker = _WORKERS.get(job["kind"])
                if worker is None:  # _KINDS 로 claim 을 걸러도 방어(설정 오류 대비)
                    log.error("no worker for job kind=%s (job %s)", job["kind"], job["id"])
                    continue
                await worker(self.app, job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dispatcher loop error")
                await asyncio.sleep(s.job_poll_interval_seconds)

    async def _recover_stale(self, s, pool):
        async with pool.connection() as conn:
            await repo.recover_stale_leases(conn, s.job_lease_timeout_seconds)
            await conn.commit()
        # 예약 크레딧 미정산 error job 해제 — 이번 복구분 + 과거 해제 실패분까지 재시도.
        # release는 settle_key 멱등이라 중복 안전. 해제 실패 시 다음 sweep이 다시 잡는다.
        async with pool.connection() as conn:
            unsettled = await repo.list_unsettled_errored_jobs(conn)
            await conn.commit()
        for j in unsettled:
            try:
                async with pool.connection() as conn:
                    await repo.release_credits(
                        conn, user_id=j["user_id"], project_id=j["project_id"], job_id=j["id"],
                        reserved=j["credits_reserved"],
                        settle_key=f"credit:job:{j['id']}:settle",
                        metadata={"reason": "lease_recovery"})
                    await conn.commit()
            except Exception:
                log.exception("stale credit release failed for job %s", j["id"])
