"""워커 공용 배관 (pl1_analysis_agent_spec §6.6 — 복붙 금지, 공통 1벌)."""

from .. import repo


async def emit(pool, job_id: str, event_type: str, payload: dict):
    """진행/단계 이벤트 append (비종결 — 짧은 독립 tx). 종결 done/error는 finalize가 원자로 남긴다."""
    try:
        async with pool.connection() as conn:
            await repo.append_job_event(conn, job_id, event_type, payload)
            await conn.commit()
    except Exception:  # 이벤트 실패가 작업 자체를 막지 않게
        pass
