"""DB 접근 레이어 (backend_integration_plan §1·§9).

FastAPI는 service-role 연결(DATABASE_URL)로 PG에 직접 붙는다 — RLS를 우회하므로
**모든 쿼리에 owner 조건(user_id = JWT sub)을 명시**하는 것이 1차 방어선이다.
RLS는 운영 실수·미래 직접 조회에 대한 2차 방어선(§2).

연결은 async 풀로 관리하고 lifespan에서 열고 닫는다. DATABASE_URL이 없으면
풀을 만들지 않는다(JWT 검증만 하는 healthz·인증 테스트는 DB 불필요).
"""

from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

_DB_UNAVAILABLE = {"code": "db_unavailable", "message": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."}


def create_pool(database_url: str) -> AsyncConnectionPool:
    # open=False → lifespan에서 명시적 open (psycopg_pool 권장).
    # timeout/connect_timeout: DB 불가 시 기본 30s 대기 대신 ~10s 안에 빨리 실패
    # (정상 연결은 <1s라 false-positive 없음 — 오설정·DB 다운만 잡힌다).
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=10,
        timeout=10,  # pool.connection() 연결 획득 최대 대기 (기본 30)
        kwargs={"row_factory": dict_row, "connect_timeout": 10},  # 각 연결 시도 상한(초)
        open=False,
    )


@asynccontextmanager
async def get_conn(request: Request):
    """요청 핸들러용 커넥션 컨텍스트. 풀 미구성·연결 실패 시 명확한 503."""
    pool: AsyncConnectionPool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE)
    try:
        async with pool.connection() as conn:
            yield conn
    except PoolTimeout:
        # 연결 획득 실패(오설정·DB 다운) → 30s hang+raw 500 대신 즉시 503 봉투.
        # 쿼리 자체 오류는 여기서 안 잡고 일반 핸들러(500)로 보낸다.
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
