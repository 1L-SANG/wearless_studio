"""인물 LoRA 학습 큐 — 60초마다 가장 오래된 queued 한 건.

패턴은 workers/fm_vc_issue_reconciler.py 그대로다(start/stop·shield·취소). 다른 점은 무게다:
한 건이 GPU 파드를 3~4시간 쓰고 약 $11 이다. 그래서
  · 설정 FM_LORA_TRAINING 이 "on" 일 때만 돈다(기본 off).
  · **동시 1건**. 전역 partial unique index 가 두 번째를 DB 에서 막고, 여기서는 그 예외를
    "지금은 자리 없음" 으로 읽고 다음 회차를 기다린다.
  · 실패는 자동으로 **한 번만** 다시 태운다(attempts). 그 뒤엔 관리자에게 알린다 —
    같은 이유로 계속 태우면 돈만 나간다.

자동 큐잉(2026-09-28): 매 회차 첫머리에 **학습해도 되는 등록**을 훑어 한 건씩 넣는다
(enqueue_ready). 상태를 바꾸는 자리가 여럿이라(증서 발급·사진 확인·재촬영 뒤 재확인) 그 자리마다
훅을 거는 대신 **조건 하나**를 매번 본다 — 어느 경로로 준비가 끝나도 60초 안에 잡힌다.
큐가 켜질 때(재기동·배포) 첫 회차가 곧 백필이다. 한 등록에 런은 **평생 한 번**만 자동으로 넣는다 —
실패한 런을 스윕이 다시 넣으면 같은 이유로 $11 씩 계속 탄다. 다시 돌리는 건 사람이 한다.

학습셋은 **집은 뒤** 만든다(prepare_dataset). 큐에서 기다리는 사이 재촬영·정지가 있을 수 있어,
만들기 직전에 같은 조건을 다시 본다 — 정지된 모델에 $11 을 쓰지 않는다.
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
#: 잔액 미달·조회 실패. 실패가 아니라 **대기**다 — 시도 횟수를 쓰지 않고 큐로 돌려놓는다.
STATUS_BALANCE = "balance_too_low"
#: 다시 해도 같은 결과인 실패 — 재시도 없이 곧장 failed(돈이 나가기 전에 멈춘 것들이다).
STATUS_DATASET_FAILED = "dataset_failed"
STATUS_INELIGIBLE = "ineligible"
NON_RETRYABLE = frozenset({STATUS_DATASET_FAILED, STATUS_INELIGIBLE})
#: 한 회차에 자동으로 넣는 상한. 학습은 동시 1건이라 많이 넣어도 줄만 길어진다.
ENQUEUE_LIMIT = 20


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
            await self._enqueue_ready(conn)
            claimed = await claim_next(conn)
        if claimed is None:
            return "idle"
        run_id = claimed["id"]
        log.info("lora training: run=%s model=%s 시작 (attempt %d)",
                 run_id, claimed["model_id"], claimed["attempts"])
        try:
            outcome = await self._execute(claimed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 예상 못 한 예외로 행이 preparing 에 남으면 큐가 영영 막힌다
            log.warning("lora training: run=%s 실행 중 예외", run_id, exc_info=True)
            outcome = lora_train_pod.RunOutcome("error", detail=type(exc).__name__)
        if outcome.status == STATUS_BALANCE:
            # 실패가 아니라 대기다 — 시도 횟수를 돌려놓고 큐로. 채워지면 다음 회차에 다시 집는다.
            # (시도로 세면 잔액 조회가 두 번 흔들린 것만으로 런이 failed 가 되고, 스윕은 한 번 실패한
            # 등록을 다시 넣지 않으니 그 사람은 영영 학습되지 않는다.)
            async with pool.connection() as conn:
                await release_run(conn, run_id, f"{outcome.status} {outcome.detail or ''}".strip())
            return f"{run_id}:{outcome.status}"
        attempts = MAX_ATTEMPTS if outcome.status in NON_RETRYABLE else claimed["attempts"]
        async with pool.connection() as conn:
            await finish_run(conn, run_id, outcome, attempts=attempts)
        if outcome.status != STATUS_OK and attempts >= MAX_ATTEMPTS:
            # 두 번 태우고도 안 되면 사람이 봐야 한다. 계속 태우면 돈만 나간다.
            log.critical("lora training alert: run=%s 두 번 실패 — %s (%s)",
                         run_id, outcome.status, outcome.detail or "")
        return f"{run_id}:{outcome.status}"

    async def _enqueue_ready(self, conn) -> list[dict]:
        """학습해도 되는 등록을 큐에 넣는다. 실패해도 큐 소비는 계속한다(best-effort)."""
        try:
            added = await enqueue_ready(conn, limit=ENQUEUE_LIMIT)
        except Exception as exc:  # noqa: BLE001 — 스윕이 깨져도 이미 쌓인 큐는 돌아야 한다
            with contextlib.suppress(Exception):
                await conn.rollback()
            log.warning("lora training: 자동 큐잉 실패 (%s) — 이번 회차는 건너뛴다",
                        type(exc).__name__)
            return []
        for row in added:
            log.info("lora training: 자동 큐잉 run=%s model=%s enrollment=%s",
                     row["id"], row["model_id"], row["enrollment_id"])
        return added

    async def _execute(self, claimed: dict):
        settings = self.app.state.settings
        r2_face = getattr(self.app.state, "r2_face", None)
        if r2_face is None:
            return lora_train_pod.RunOutcome("failed", detail="r2_face 없음")
        pod = (self._pod_factory(settings, r2_face) if self._pod_factory
               else lora_train_pod.LoraTrainPod(settings, r2_face))
        try:
            # 학습셋을 만들기 **전에** 잔액부터 — 잔액이 모자라면 만들어 봐야 다시 만든다.
            await pod.assert_balance()
            prepared = await self._prepare_dataset(claimed, r2_face)
            if isinstance(prepared, lora_train_pod.RunOutcome):
                return prepared
            dataset_key, sample_slots, trigger = prepared
            spec = lora_train_pod.PodSpec(
                run_id=claimed["id"], model_id=claimed["model_id"],
                config_name=f"fm_{str(claimed['id']).replace('-', '')[:12]}",
                dataset_key=dataset_key,
                prefix=lora_train_pod.run_prefix(claimed["model_id"], claimed["id"]),
                trigger_token=trigger)

            async def on_started(pod_id, gpu_type):
                async with self.app.state.pool.connection() as conn:
                    await mark_training(conn, claimed["id"], pod_id, gpu_type)

            return await pod.run(spec, sample_slots, on_started=on_started)
        except lora_train_pod.BalanceTooLow as exc:
            # 큐에 돌려놓는다(tick 이 시도 횟수를 되돌린다) — 잔액이 채워지면 다음 회차에 다시 집는다.
            log.critical("lora training alert: 잔액 때문에 시작하지 않는다 — %s", exc)
            return lora_train_pod.RunOutcome(STATUS_BALANCE, detail=str(exc))

    async def _prepare_dataset(self, claimed: dict, r2_face):
        """(학습셋 키, 표본 칸, 트리거) 또는 RunOutcome(시작하지 않을 이유).

        ★ 만들기 직전에 조건을 **다시** 본다. 큐에서 몇 시간 기다리는 사이 정지·해지·재촬영이 있을 수
          있다 — 그 사람에게 $11 을 쓰면 안 된다. 사람이 손으로 넣은 런도 같은 문을 지난다.
        """
        run_id, model_id = claimed["id"], claimed["model_id"]
        async with self.app.state.pool.connection() as conn:
            source = await load_training_source(conn, model_id, claimed.get("enrollment_id"))
        if source is None:
            log.critical("lora training alert: run=%s model=%s 는 지금 학습할 수 없는 상태다"
                         "(정지·해지·사진 확인 전·현재 등록 아님) — 시작하지 않는다", run_id, model_id)
            return lora_train_pod.RunOutcome(
                STATUS_INELIGIBLE, detail="학습 조건 불충족(정지·해지·사진 확인 전·현재 등록 아님)")
        try:
            # 성별 → 트리거. 캡션·표본 프롬프트·나중의 fm_model_loras.trigger_token 이 같은 값이어야 한다.
            trigger = lora_dataset.trigger_token_for(source.get("gender"))
        except lora_dataset.DatasetBuildError as exc:
            log.critical("lora training alert: run=%s %s", run_id, exc)
            return lora_train_pod.RunOutcome(STATUS_DATASET_FAILED, detail=str(exc))
        preset = (claimed.get("dataset_key") or "").strip()
        own_key = lora_dataset.dataset_key(model_id, run_id)
        if preset and preset != own_key:
            # 사람이 따로 만들어 둔 학습셋 — 그대로 쓴다. 표본 칸을 모르니 표본은 비운다
            # (없는 칸을 설정에 적으면 표본 차례에 학습이 죽는다).
            log.info("lora training: run=%s 미리 만든 학습셋을 쓴다 (%s)", run_id, trigger)
            return preset, (), trigger
        settings = self.app.state.settings
        try:
            # 같은 사진이면 같은 바이트다(pack 이 시각·소유자를 고정) — 재시도에서 다시 만들어도 같은 키·같은 내용.
            result = await asyncio.to_thread(
                lora_dataset.build_and_upload, r2_face, model_id=model_id, run_id=run_id,
                rows=source["photos"], review_status=source["photo_review_status"],
                trigger_token=trigger,
                model_dir=getattr(settings, "fm_face_qc_dir", None), apply=True)
        except lora_dataset.DatasetBuildError as exc:
            log.critical("lora training alert: run=%s 학습셋을 만들 수 없다 — %s", run_id, exc)
            return lora_train_pod.RunOutcome(STATUS_DATASET_FAILED, detail=str(exc))
        async with self.app.state.pool.connection() as conn:
            await set_dataset_key(conn, run_id, result["key"])
        log.info("lora training: run=%s 학습셋 %d files · 표본 %d · %s", run_id, result["files"],
                 len(result.get("sample_slots") or ()), trigger)
        return result["key"], tuple(result.get("sample_slots") or ()), trigger


# ── DB ──────────────────────────────────────────────────────────────────────

#: 학습해도 되는 등록 — **자동 큐잉과 시작 직전 재확인이 같은 식**을 쓴다(m = fm_models, e = 등록).
#:   · 모델이 정지(suspended)·재인증 대기(reverification_required)가 아니다
#:   · 모델의 **현재** 등록이고, 증서까지 끝났다(status·decision 둘 다 passed)
#:   · 신원 심사가 끝났다(간편인증은 review_status 가 null — identity_cleared 와 같은 규칙)
#:   · 관리자 사진 확인이 끝났다 — 반려될 사진이 가중치에 들어가면 되돌릴 길이 재학습뿐이다
#:   · 그 등록의 라이선스가 살아 있고 VC 가 붙었다(해지·만료면 학습할 이유가 없다)
#:   · 성별을 안다 — 트리거 토큰("ohwx man" | "ohwx woman")이 거기서 나온다. 모르면 성별이 채워질
#:     때까지 기다린다(틀린 어휘로 $11 을 쓰느니 기다린다).
MODEL_GENDER = lora_dataset.model_gender_sql("m", "e")
ELIGIBLE_ENROLLMENT = """
    m.status not in ('suspended', 'reverification_required')
    and e.id = m.current_enrollment_id and e.model_id = m.id
    and e.status = 'passed' and e.decision = 'passed'
    and coalesce(e.review_status, 'approved') = 'approved'
    and e.photo_review_status = 'approved'
    and exists (
        select 1 from fm_licenses l
         where l.model_id = m.id and l.enrollment_id = e.id
           and l.status = 'active'
           and nullif(btrim(l.vc_id), '') is not null
           and (l.license_valid_until is null or l.license_valid_until > now()))
    and """ + MODEL_GENDER + """ in ('male', 'female')
"""

#: 자동 큐잉 직렬화. 새 유니크 인덱스를 만들지 않고 트랜잭션 advisory lock 으로 줄 세운다 —
#: 손으로 넣은 행이 이미 겹쳐 있으면 인덱스 마이그레이션이 배포 자체를 세운다.
ENQUEUE_LOCK = "fm_lora_training_enqueue"


async def enqueue_ready(conn, *, limit: int = ENQUEUE_LIMIT) -> list[dict]:
    """학습해도 되는 등록마다 queued 런을 **한 번** 넣는다. 넣은 행을 돌려준다.

    건너뛰는 것:
      · 그 등록(또는 등록이 안 적힌 손 런)에 런이 **하나라도** 있다 — 상태를 가리지 않는다.
        실패한 런을 다시 넣으면 같은 이유로 $11 씩 계속 탄다. 다시 돌리는 건 사람이 한다.
      · 그 모델에 ready LoRA 가 이미 있다 — 켜져 있거나, 이 등록에서 나왔거나, 출처가 비었거나
        (손으로 seed 한 v1 은 source_enrollment_id 가 빌 수 있다).
    """
    async with conn.cursor() as cur:
        await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (ENQUEUE_LOCK,))
        await cur.execute(
            f"""
            insert into fm_lora_training_runs (model_id, enrollment_id, status)
            select m.id, e.id, 'queued'
              from fm_models m
              join fm_biometric_enrollments e on e.id = m.current_enrollment_id
             where {ELIGIBLE_ENROLLMENT}
               and not exists (
                   select 1 from fm_lora_training_runs r
                    where r.model_id = m.id
                      and (r.enrollment_id = e.id or r.enrollment_id is null))
               and not exists (
                   select 1 from fm_model_loras x
                    where x.model_id = m.id and x.status = 'ready'
                      and (x.enabled or x.source_enrollment_id is null
                           or x.source_enrollment_id = e.id))
             order by e.completed_at nulls last, e.id
             limit %s
            returning id::text as id, model_id::text as model_id,
                      enrollment_id::text as enrollment_id
            """,
            (int(limit),))
        rows = await cur.fetchall()
    await conn.commit()
    return [dict(row) for row in rows]


async def load_training_source(conn, model_id: str, enrollment_id: str | None) -> dict | None:
    """학습셋을 만들 등록과 사진 행. 지금 학습할 수 없는 상태면 None.

    런에 등록이 안 적혀 있으면(손으로 넣은 런) 모델의 **현재** 등록을 쓴다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            select e.id::text as enrollment_id,
                   coalesce(e.photo_review_status, 'pending') as photo_review_status,
                   {MODEL_GENDER} as gender
              from fm_models m
              join fm_biometric_enrollments e on e.id = m.current_enrollment_id
             where m.id = %s
               and (%s::uuid is null or e.id = %s::uuid)
               and {ELIGIBLE_ENROLLMENT}
            """,
            (model_id, enrollment_id, enrollment_id))
        found = await cur.fetchone()
        if found is None:
            await conn.rollback()
            return None
        await cur.execute(
            "select angle, r2_key, normalized_r2_key, mime_type, qc_status, storage_state "
            "from fm_biometric_enrollment_photos where enrollment_id = %s",
            (found["enrollment_id"],))
        photos = [dict(row) for row in await cur.fetchall()]
    await conn.commit()
    return {"enrollment_id": found["enrollment_id"],
            "photo_review_status": found["photo_review_status"],
            "gender": found["gender"], "photos": photos}


async def set_dataset_key(conn, run_id: str, key: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update fm_lora_training_runs set dataset_key = %s where id = %s", (key, run_id))
    await conn.commit()


async def release_run(conn, run_id: str, detail: str) -> None:
    """집은 런을 **시도로 세지 않고** 큐에 돌려놓는다(잔액 대기). 이유는 error 에 남긴다."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_lora_training_runs
               set status = 'queued', attempts = greatest(attempts - 1, 0),
                   started_at = null, pod_id = null, error = %s
             where id = %s and status = 'preparing'
            """,
            (detail, run_id))
    await conn.commit()


async def claim_next(conn) -> dict | None:
    """가장 오래된 queued 하나를 'preparing' 으로 집는다. 자리가 없으면 None.

    ★ 자리 판정은 **DB 인덱스**가 한다(fm_lora_training_runs_one_active). 여기서 세어 보고
      결정하면 두 프로세스가 같은 순간에 "자리 있음" 을 볼 수 있다 — 인덱스는 그럴 수 없다.
    """
    from psycopg.errors import UniqueViolation

    async with conn.cursor() as cur:
        # ★ 인덱스 위반은 UPDATE **실행** 시점에 난다(fetchone 이 아니다). 둘 다 감싸야 "자리 없음" 이
        #   회차 예외(매분 WARNING + traceback)가 아니라 조용한 대기로 읽힌다.
        try:
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
