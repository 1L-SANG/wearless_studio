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
            await conn.commit()
    except Exception:  # 이벤트 실패가 워커 본 작업을 막지 않게
        pass
