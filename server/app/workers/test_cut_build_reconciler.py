"""테스트컷 12장 자동 생성 큐 — 60초마다 가장 오래된 queued 한 건.

패턴은 workers/lora_training_reconciler.py 그대로다(start/stop·claim·재시도). 다른 점:

  · GPU 파드를 **새로 만들지 않는다**. 얼굴 렌더 파드는 이미 있는 것(fm_face_render_pod)을
    쓰고, 없으면 face_autoscale 이 이 수요를 보고 켠다. 여기서는 **기다린다**.
  · 파드가 안 뜨는 건 실패가 아니라 대기다 — 상한(FM_TEST_CUT_POD_WAIT_SECONDS, 기본 20분)을
    넘겨야 실패로 남기고 관리자에게 알린다.
  · 12장 중 일부만 되면 **된 것만 저장하고 'partial'** 이다. 얼굴 패스는 게이트에 떨어지면
    빈 손으로 돌아오는데, 그건 한 장의 문제지 런 전체의 실패가 아니다.
"""

import asyncio
import contextlib
import logging

from ..agents import face_identity, identity_source
from ..services import test_cut_build

log = logging.getLogger("wearless.test_cut_build_reconciler")

_IDLE_SECONDS = 60
_STOP_TIMEOUT_SECONDS = 10
#: 자동 재시도 상한. 1 = 처음 한 번 + 재시도 한 번. 부분 성공은 재시도하지 않는다 —
#: 관리자가 보고 "다시 생성" 을 누르는 편이 낫다(같은 게이트에 또 떨어질 값이라면 돈만 나간다).
MAX_ATTEMPTS = 2


class TestCutBuildReconciler:
    def __init__(self, app, *, sleep=None):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()
        self._sleep = sleep or asyncio.sleep

    @property
    def enabled(self) -> bool:
        return str(getattr(self.app.state.settings, "fm_test_cut_build", "off")).lower() == "on"

    async def start(self):
        if not self.enabled:
            log.info("test cut build: FM_TEST_CUT_BUILD 이 off — 큐를 돌리지 않는다")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="test-cut-build-reconciler")

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
                log.warning("test cut build tick 실패", exc_info=True)
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
        build_id = claimed["id"]
        log.info("test cut build: build=%s model=%s 시작 (attempt %d)",
                 build_id, claimed["model_id"], claimed["attempts"])
        produced, error = await self._execute(claimed)
        async with pool.connection() as conn:
            status = await finish_build(conn, build_id, produced=produced, error=error,
                                        attempts=claimed["attempts"])
        if status == "failed":
            log.critical("test cut build alert: build=%s model=%s 실패 — %s",
                         build_id, claimed["model_id"], error or "")
        return f"{build_id}:{status}:{produced}"

    async def _execute(self, claimed: dict) -> tuple[int, str | None]:
        settings = self.app.state.settings
        model_id = claimed["model_id"]
        r2_face = getattr(self.app.state, "r2_face", None)
        if r2_face is None:
            return 0, "r2_face 없음"
        try:
            keys = test_cut_build.assert_sources(settings)
            sources = await test_cut_build.load_sources(r2_face, keys)
        except test_cut_build.SourcesMissing as exc:
            return 0, str(exc)

        async with self.app.state.pool.connection() as conn:
            spec = await test_cut_build.ready_lora_spec(conn, model_id)
            if spec is None:
                return 0, "이 모델에 status='ready' LoRA 행이 없다"
            # 동일인 검사 기준 — 없으면 게이트가 신원을 안 본다. 12장 전부가 "닮았는지 모르는"
            # 컷이 되면 관리자가 보고 고를 근거가 사라진다.
            refs = await identity_source.reference_face_bytes(self.app, conn, model_id, None)
            await test_cut_build.clear_cuts(conn, r2_face, model_id)
        if refs:
            spec = await asyncio.to_thread(face_identity.with_references, spec, refs,
                                           getattr(settings, "fm_face_qc_dir", None))
        else:
            log.warning("test cut build: model=%s 기준 사진이 없다 — 신원 검사 없이 그린다", model_id)

        # 파드가 없는 건 대기다. 이 런 자체가 수요라 face_autoscale 이 보고 켠다.
        budget = int(getattr(settings, "fm_test_cut_pod_wait_seconds", 20 * 60) or 0)
        render_url = await face_identity.wait_for_backend(
            settings, spec,
            lambda: identity_source.active_face_backend_url(self.app.state.pool, model_id),
            budget_seconds=budget)
        if render_url is None:
            return 0, f"얼굴 렌더 파드가 {budget}초 안에 뜨지 않았다"

        produced = 0
        model_dir = getattr(settings, "fm_face_qc_dir", None)
        for item in test_cut_build.plan(keys):
            image = sources.get(item["source_key"])
            if image is None:
                continue
            try:
                rendered = await test_cut_build.render_variant(
                    settings, spec, image, test_cut_build.FALLBACK_MIME, item["skin_finish"],
                    render_url=render_url, model_dir=model_dir)
            except Exception:  # noqa: BLE001 — 한 장이 죽어도 나머지는 만든다
                log.warning("test cut build: 한 장 렌더 실패 (%s/%s)",
                            item["skin_finish"], item["kind"], exc_info=True)
                continue
            if rendered is None:
                continue
            data, mime = rendered
            async with self.app.state.pool.connection() as conn:
                await test_cut_build.store_cut(conn, r2_face, model_id=model_id, item=item,
                                               data=data, mime=mime)
            produced += 1
        if produced == 0:
            return 0, "12장 모두 게이트를 넘지 못했다"
        return produced, None


# ── DB ──────────────────────────────────────────────────────────────────────


async def claim_next(conn) -> dict | None:
    """가장 오래된 queued 하나를 'running' 으로 집는다. 자리가 없으면 None.

    ★ 자리 판정은 **DB 인덱스**가 한다(fm_test_cut_builds_one_active) — 학습 큐와 같은 이유다.
    """
    from psycopg.errors import UniqueViolation

    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_test_cut_builds
               set status = 'running', attempts = attempts + 1, started_at = now(), error = null
             where id = (select id from fm_test_cut_builds
                          where status = 'queued'
                          order by created_at
                          limit 1
                          for update skip locked)
            returning id::text as id, model_id::text as model_id, attempts
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


async def finish_build(conn, build_id: str, *, produced: int, error: str | None,
                       attempts: int) -> str:
    """결과를 적고 최종 상태를 돌려준다.

    12장 다 되면 done · 한 장이라도 되면 partial · 0장이면 재시도 여지에 따라 queued/failed.
    partial 을 자동 재시도하지 않는 이유는 위 MAX_ATTEMPTS 주석 참조.
    """
    if produced >= test_cut_build.EXPECTED_CUTS:
        status = "done"
    elif produced > 0:
        status = "partial"
    elif attempts < MAX_ATTEMPTS:
        status = "queued"
    else:
        status = "failed"
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_test_cut_builds
               set status = %s, produced = %s, error = %s,
                   finished_at = case when %s = 'queued' then null else now() end
             where id = %s
            """,
            (status, produced, error, status, build_id))
    await conn.commit()
    return status


async def latest_build(conn, model_id: str) -> dict | None:
    """관리자 화면이 보는 최근 한 건. 테이블이 없으면 None(화면은 그냥 진행 표시가 없다)."""
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """select id::text as id, status, requested, produced, error, attempts,
                          started_at, finished_at, created_at
                     from fm_test_cut_builds
                    where model_id = %s order by created_at desc limit 1""",
                (model_id,))
            return await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — 테이블 부재 등
        with contextlib.suppress(Exception):
            await conn.rollback()
        log.info("fm_test_cut_builds unavailable (%s) — 진행 표시 없이 간다", type(exc).__name__)
        return None
