"""Job dispatcher (§5). web 프로세스 lifespan에서 background 태스크로 시작.

jobs 큐를 폴링해 pending job을 FOR UPDATE SKIP LOCKED로 claim → 워커 실행. 주기적으로
lease 초과(고착) job을 복구하고, 복구로 error 처리된 job의 예약 크레딧을 해제한다.
요청 핸들러 밖에서 실행 — HTTP 취소·이탈이 job을 끊지 않게 한다.
"""

import asyncio
import contextlib
import logging
import time

from .. import image_usage, repo
from .analyze_job import run_analyze_job
from .detail_page_job import run_detail_page_job
from .editor_image_job import run_editor_image_job
from .mannequin_adjust_job import run_mannequin_adjust_job
from .mannequin_job import run_mannequin_job
from .fm_model_asset_job import run_fm_model_asset_job
from .personalization_generation_job import run_personalization_generation_job
from .personalization_purge_job import run_personalization_purge_job
from .base_fidelity_observe_job import run_base_fidelity_observe_job
from .editor_garment_mask_job import run_editor_garment_mask_job
from .sam_preprocess_job import run_sam_preprocess_job
from .matching_cutout_job import run_matching_cutout_job

log = logging.getLogger("wearless.dispatcher")

# kind → 워커. claim 대상(_KINDS)과 라우팅을 한 곳에서 관리 — 새 job 종류는 여기에 추가.
_WORKERS = {
    "mannequin": run_mannequin_job,
    "analyze": run_analyze_job,  # AG-01 상품 분석 (무과금)
    "detail_page": run_detail_page_job,  # PL-4 상세페이지 생성 (AG-06→02→03→M-02)
    "mannequin_adjust": run_mannequin_adjust_job,  # @deprecated AG-05 — 툼스톤(legacy 잡 드레인 전용, AI 미호출)
    "editor_image": run_editor_image_job,  # AG-06/07 에디터 이미지 (PL-5/6, mode:'new'|'vary')
    "personalization_generation": run_personalization_generation_job,  # 개인화 생성 경로 α (api-spec §4)
    "personalization_purge": run_personalization_purge_job,  # 개인화 파기 캐스케이드 (api-spec §3.5)
    # 캐노니컬 컷아웃 전처리(무과금). analyze 와 독립 — 소스 사진만 있으면 돈다.
    "sam_preprocess": run_sam_preprocess_job,
    # 거부된 컷 관측(무과금·이미지 생성 없음). 재생성 요청과 병렬로 돈다.
    "base_fidelity_observe": run_base_fidelity_observe_job,
    # 톤 에디터용 착장 마스크 전처리(무과금). 컷이 이미 화면에 뜬 뒤에 돈다.
    "editor_garment_mask": run_editor_garment_mask_job,
    # 커스텀 매칭 의류 누끼(무과금·이미지 생성 없음). 커스텀 매칭 등록 후 백그라운드로 돈다.
    "matching_cutout": run_matching_cutout_job,
    "fm_model_asset_build": run_fm_model_asset_job,  # 실존 모델 자산 빌드(합성+QC, handoff fork)
}
_KINDS = tuple(_WORKERS)
_SWEEP_INTERVAL = 60.0  # lease 복구 점검 주기(초)


class JobDispatcher:
    def __init__(self, app):
        self.app = app
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    def wake(self):
        """job 생성 직후 라우트가 호출 — 유휴 폴링 대기(최대 poll_interval초)를 건너뛰고
        즉시 claim 하게 한다 (2026-07-07 속도 개선: 분석 시작 전 0~3초 공회전 제거).
        같은 이벤트 루프(웹 프로세스 lifespan) 안이라 스레드 안전 문제 없음."""
        self._wake.set()

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
                    # 고정 sleep 대신 wake 이벤트 대기(상한 = poll_interval) — 라우트가
                    # wake()를 쏘면 즉시 다음 claim, 아니면 기존 주기 폴링과 동일.
                    try:
                        await asyncio.wait_for(
                            self._wake.wait(), timeout=s.job_poll_interval_seconds)
                    except asyncio.TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                worker = _WORKERS.get(job["kind"])
                if worker is None:  # _KINDS 로 claim 을 걸러도 방어(설정 오류 대비)
                    log.error("no worker for job kind=%s (job %s)", job["kind"], job["id"])
                    continue
                # 이 잡이 도는 동안 일어난 이미지 호출에 job·user·kind 를 붙인다
                # (워커 시그니처를 바꾸지 않고 실비를 잡별로 귀속시키는 유일한 지점).
                heartbeat = asyncio.create_task(self._keep_lease(s, pool, job))
                try:
                    with image_usage.job_scope(
                        job_id=job["id"], user_id=job.get("user_id"), stage=job["kind"]
                    ):
                        await worker(self.app, job)
                finally:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dispatcher loop error")
                await asyncio.sleep(s.job_poll_interval_seconds)

    async def _keep_lease(self, s, pool, job):
        """잡이 도는 동안 lease 시각을 갱신한다.

        lease 는 '워커가 죽었는지'를 보려는 장치인데 기준이 시작 시각이라, 정상적으로
        오래 걸리는 잡(상세페이지 13컷)이 그대로 재큐돼 **전 컷을 처음부터 다시 생성**했다
        — 페이지 단위로 프로바이더 실비가 두 번 나간다(2026-08-17 검증).
        갱신 주기는 타임아웃의 1/3 — 한두 번 실패해도 회수 전에 회복할 여유가 있다.
        """
        token = job.get("lease_token")
        if not token:
            return
        interval = max(5, int(s.job_lease_timeout_seconds) // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                async with pool.connection() as conn:
                    renewed = await repo.renew_job_lease(conn, job["id"], token)
                    await conn.commit()
                # 이미 회수돼 다른 워커가 집어간 잡이면 하트비트를 멈춘다(양쪽에서 도는 것 방지).
                if not renewed:
                    log.warning("lease lost for job %s — heartbeat stops", job["id"])
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("lease renew failed for job %s", job["id"])

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
