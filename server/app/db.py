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
from psycopg_pool import AsyncConnectionPool


def create_pool(database_url: str) -> AsyncConnectionPool:
    # open=False → lifespan에서 명시적 open (psycopg_pool 권장)
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=False,
    )


@asynccontextmanager
async def get_conn(request: Request):
    """요청 핸들러용 커넥션 컨텍스트. 풀 미구성 시 503."""
    pool: AsyncConnectionPool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "db_unavailable", "message": "데이터베이스 연결이 설정되지 않았습니다."},
        )
    async with pool.connection() as conn:
        yield conn
