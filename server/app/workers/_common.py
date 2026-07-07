"""워커 공용 헬퍼 — job_event append (mannequin_job·analyze_job 공유).

진행/단계 이벤트는 비종결(짧은 독립 tx)로 append 한다. 종결 done/error는 각 워커의 finalize가
원자로 남긴다. 이벤트 append 실패가 생성/분석 자체를 막지 않도록 예외를 삼킨다.
"""

import logging

from .. import repo

log = logging.getLogger("wearless.workers")


async def emit_job_event(pool, job_id: str, event_type: str, payload: dict) -> None:
    try:
        async with pool.connection() as conn:
            await repo.append_job_event(conn, job_id, event_type, payload)
            # progress 이벤트는 jobs.progress 컬럼도 함께 갱신한다. GET /jobs 는 컬럼값을 반환하므로,
            # 이벤트만 append 하면 폴링 progress 가 claim값(5)에 고정돼 있다가 done 에서 100 으로 점프한다
            # (= 생성 내내 "5% 멈춤"으로 보임). 같은 tx 로 갱신해 폴링이 실제 진행을 따라가게 한다.
            p = payload.get("progress") if event_type == "progress" else None
            if isinstance(p, (int, float)):
                await repo.set_job_progress(conn, job_id, max(0, min(100, int(p))))
            await conn.commit()
    except Exception:  # 이벤트 실패가 워커 본 작업을 막지 않게
        pass
