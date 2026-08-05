"""이미지 생성 실비 계측 — 호출 1건 = 행 1개 (`image_usage_events`).

배선 지점이 하나다: `GeminiImageClient.generate_content_image` 가 200 을 받은 직후.
QC 재생성·best-of 후보처럼 "손님에게 안 나가는 이미지"도 요금은 나가므로, 채택 여부와
무관하게 200 응답이면 전부 적는다. 그래야 "완성본 1장당 실비 = 총액 ÷ 완성본 수"가 성립한다.

계측이 생성을 막으면 안 되므로 이 모듈은 **어떤 경우에도 예외를 밖으로 내보내지 않는다**.
DB 가 없거나(스크립트·테스트) 인서트가 실패하면 로그만 남기고 조용히 넘어간다.
job 문맥은 contextvar 로 받는다 — 워커 시그니처를 전부 바꾸지 않으려는 선택이며,
dispatcher 가 워커 1회 실행을 `job_scope()` 로 감싸는 것으로 충분하다.
"""

import asyncio
import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass

from psycopg.types.json import Json

from .agents.image_cost import estimate_cost

log = logging.getLogger("wearless.image_usage")

_pool = None          # AsyncConnectionPool | None — lifespan 에서 주입
_persist = True       # DB 적재 스위치 (IMAGE_USAGE_LOG=log 면 로그만)
_tasks: set = set()   # fire-and-forget 태스크 강참조 (GC 로 취소되는 것 방지)


@dataclass(frozen=True)
class JobContext:
    job_id: str | None = None
    user_id: str | None = None
    stage: str | None = None


_ctx: contextvars.ContextVar[JobContext] = contextvars.ContextVar(
    "image_usage_ctx", default=JobContext())


def configure(pool=None, persist: bool = True) -> None:
    global _pool, _persist
    _pool = pool
    _persist = persist


@contextmanager
def job_scope(job_id: str | None = None, user_id: str | None = None, stage: str | None = None):
    """이 블록 안에서 일어난 이미지 호출에 job 문맥을 붙인다. 중첩 시 지정한 필드만 덮어쓴다."""
    cur = _ctx.get()
    token = _ctx.set(JobContext(
        job_id=job_id if job_id is not None else cur.job_id,
        user_id=user_id if user_id is not None else cur.user_id,
        stage=stage if stage is not None else cur.stage,
    ))
    try:
        yield
    finally:
        _ctx.reset(token)


def record(*, model: str, image_size: str, usage: dict | None, latency_ms: int,
           has_image: bool = True) -> None:
    """호출 1건 기록. 동기 함수 — 호출자를 절대 기다리게 하지 않는다."""
    try:
        cost = estimate_cost(model, image_size, usage)
        ctx = _ctx.get()
        log.info(
            "image_usage model=%s size=%s stage=%s job=%s in_tok=%d out_img_tok=%d "
            "usd=%s src=%s latency_ms=%d image=%s",
            model, image_size, ctx.stage or "-", ctx.job_id or "-",
            cost.input_tokens, cost.output_image_tokens,
            "?" if cost.usd is None else f"{cost.usd:.6f}",
            cost.source, latency_ms, has_image,
        )
        if not (_persist and _pool is not None):
            return
        task = asyncio.create_task(
            _insert(ctx, model, image_size, cost, latency_ms, has_image, usage))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # 계측 실패가 생성을 깨뜨리지 않게
        log.exception("image_usage record failed (ignored)")


async def _insert(ctx: JobContext, model: str, image_size: str, cost, latency_ms: int,
                  has_image: bool, usage: dict | None) -> None:
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into image_usage_events
                      (job_id, user_id, stage, model, image_size, input_tokens,
                       output_text_tokens, output_image_tokens, usd, cost_source,
                       latency_ms, has_image, usage_raw)
                    values (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (ctx.job_id, ctx.user_id, ctx.stage, model, image_size,
                     cost.input_tokens, cost.output_text_tokens, cost.output_image_tokens,
                     cost.usd, cost.source, latency_ms, has_image,
                     # 원본 usage 를 남긴다 — 단가가 바뀌면 과거 행을 재계산할 수 있어야 한다.
                     Json(usage or {})),
                )
            await conn.commit()
    except Exception:
        # 지워도 되는 데이터다 — 계측 유실이 생성 실패로 번지지 않게 로그만.
        log.warning("image_usage insert failed (ignored)", exc_info=True)
