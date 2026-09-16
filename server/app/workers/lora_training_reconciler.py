"""인물 LoRA 학습 큐 — 60초마다 가장 오래된 queued 한 건.

패턴은 workers/fm_vc_issue_reconciler.py 그대로다(start/stop·shield·취소). 다른 점은 무게다:
한 건이 GPU 파드를 3~4시간 쓰고 약 $11 이다. 그래서
  · 설정 FM_LORA_TRAINING 이 "on" 일 때만 돈다(기본 off).
  · **동시 1건**. 전역 partial unique index 가 두 번째를 DB 에서 막고, 여기서는 그 예외를
    "지금은 자리 없음" 으로 읽고 다음 회차를 기다린다.
  · 실패는 자동으로 **한 번만** 다시 태운다(attempts). 그 뒤엔 관리자에게 알린다 —
    같은 이유로 계속 태우면 돈만 나간다.
"""

import asyncio
import contextlib
import logging

from ..services import lora_dataset, lora_train_pod

log = logging.getLogger("wearless.lora_training_reconciler")

_IDLE_SECONDS = 60
_STOP_TIMEOUT_SECONDS = 10
#: 자동 재시도 상한. 1 = 처음 한 번 + 재시도 한 번.
MAX_ATTEMPTS = 2
#: 파드가 만드는 상태 문자열 중 "잘 끝났다".
STATUS_OK = "OK"


class LoraTrainingReconciler:
    def __init__(self, app, *, pod_factory=None, sleep=None):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()
        self._pod_factory = pod_factory
        self._sleep = sleep or asyncio.sleep

    @property
    def enabled(self) -> bool:
        return str(getattr(self.app.state.settings, "fm_lora_training", "off")).lower() == "on"

    async def start(self):
        if not self.enabled:
            log.info("lora training: FM_LORA_TRAINING 이 off — 큐를 돌리지 않는다")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="lora-training-reconciler")

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
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 한 회차 실패가 루프를 끝내면 안 된다
                log.warning("lora training tick 실패", exc_info=True)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), _IDLE_SECONDS)

    # ── 한 회차 ──
    async def tick(self) -> str:
        """큐에서 하나 집어 끝까지 돌린다. 반환은 사람이 읽을 한 줄(테스트가 본다)."""
        pool = getattr(self.app.state, "pool", None)
        if pool is None:
            return "no_pool"
        async with pool.connection() as conn:
            claimed = await claim_next(conn)
        if claimed is None:
            return "idle"
        run_id = claimed["id"]
        log.info("lora training: run=%s model=%s 시작 (attempt %d)",
                 run_id, claimed["model_id"], claimed["attempts"])
        outcome = await self._execute(claimed)
        async with pool.connection() as conn:
            await finish_run(conn, run_id, outcome, attempts=claimed["attempts"])
        if outcome.status != STATUS_OK and claimed["attempts"] >= MAX_ATTEMPTS:
            # 두 번 태우고도 안 되면 사람이 봐야 한다. 계속 태우면 돈만 나간다.
            log.critical("lora training alert: run=%s 두 번 실패 — %s (%s)",
                         run_id, outcome.status, outcome.detail or "")
        return f"{run_id}:{outcome.status}"

    async def _execute(self, claimed: dict):
        settings = self.app.state.settings
        r2_face = getattr(self.app.state, "r2_face", None)
        if r2_face is None:
            return lora_train_pod.RunOutcome("failed", detail="r2_face 없음")
        pod = (self._pod_factory(settings, r2_face) if self._pod_factory
               else lora_train_pod.LoraTrainPod(settings, r2_face))
        spec = lora_train_pod.PodSpec(
            run_id=claimed["id"], model_id=claimed["model_id"],
            config_name=f"fm_{str(claimed['id']).replace('-', '')[:12]}",
            dataset_key=claimed["dataset_key"],
            prefix=lora_train_pod.run_prefix(claimed["model_id"], claimed["id"]))

        async def on_started(pod_id, gpu_type):
            async with self.app.state.pool.connection() as conn:
                await mark_training(conn, claimed["id"], pod_id, gpu_type)

        try:
            return await pod.run(spec, claimed.get("sample_slots") or (),
                                 on_started=on_started)
        except lora_train_pod.BalanceTooLow as exc:
            # 큐에 돌려놓는다 — 잔액이 채워지면 다음 회차에 다시 집는다.
            log.critical("lora training alert: 잔액 때문에 시작하지 않는다 — %s", exc)
            return lora_train_pod.RunOutcome("balance_too_low", detail=str(exc))


# ── DB ──────────────────────────────────────────────────────────────────────


async def claim_next(conn) -> dict | None:
    """가장 오래된 queued 하나를 'preparing' 으로 집는다. 자리가 없으면 None.

    ★ 자리 판정은 **DB 인덱스**가 한다(fm_lora_training_runs_one_active). 여기서 세어 보고
      결정하면 두 프로세스가 같은 순간에 "자리 있음" 을 볼 수 있다 — 인덱스는 그럴 수 없다.
    """
    from psycopg.errors import UniqueViolation

    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_lora_training_runs
               set status = 'preparing', attempts = attempts + 1, started_at = now(),
                   error = null
             where id = (select id from fm_lora_training_runs
                          where status = 'queued'
                          order by created_at
                          limit 1
                          for update skip locked)
            returning id::text as id, model_id::text as model_id,
                      enrollment_id::text as enrollment_id, dataset_key, attempts
            """)
        try:
            row = await cur.fetchone()
        except UniqueViolation:
            row = None
    if row is None:
        await conn.rollback()
        return None
    await conn.commit()
    return dict(row)


async def mark_training(conn, run_id: str, pod_id: str, gpu_type: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update fm_lora_training_runs set status = 'training', pod_id = %s, gpu_type = %s "
            "where id = %s and status = 'preparing'",
            (pod_id, gpu_type, run_id))
    await conn.commit()


async def finish_run(conn, run_id: str, outcome, *, attempts: int) -> None:
    """결과를 적는다. 실패는 재시도 여지가 남았으면 다시 큐로, 아니면 failed."""
    ok = outcome.status == STATUS_OK
    retryable = not ok and attempts < MAX_ATTEMPTS
    status = "scoring" if ok else ("queued" if retryable else "failed")
    metrics = {"elapsed_seconds": outcome.elapsed_seconds, "gpu_type": outcome.gpu_type,
               "price_per_hour": outcome.price_per_hour,
               "checkpoints": len(outcome.ckpt_keys)}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_lora_training_runs
               set status = %s, ckpt_key = %s, metrics = %s::jsonb,
                   error = %s, pod_id = null,
                   finished_at = case when %s then null else now() end
             where id = %s
            """,
            (status, outcome.ckpt_keys[-1] if outcome.ckpt_keys else None,
             _json(metrics), None if ok else f"{outcome.status} {outcome.detail or ''}".strip(),
             retryable, run_id))
    await conn.commit()


def _json(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
