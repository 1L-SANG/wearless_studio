"""DB 접근 레이어 (backend_integration_plan §1·§9).

FastAPI는 service-role 연결(DATABASE_URL)로 PG에 직접 붙는다 — RLS를 우회하므로
**모든 쿼리에 owner 조건(user_id = JWT sub)을 명시**하는 것이 1차 방어선이다.
RLS는 운영 실수·미래 직접 조회에 대한 2차 방어선(§2).

연결은 async 풀로 관리하고 lifespan에서 열고 닫는다. DATABASE_URL이 없으면
풀을 만들지 않는다(JWT 검증만 하는 healthz·인증 테스트는 DB 불필요).
"""

from contextlib import asynccontextmanager

import os

from fastapi import HTTPException, Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

_DB_UNAVAILABLE = {"code": "db_unavailable", "message": "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요."}


SESSION_TIME_ZONE = "Asia/Seoul"


async def _configure(conn) -> None:
    """새 커넥션마다 세션 시간대를 KST 로 맞춘다.

    저장은 그대로다 — 모든 시각 컬럼이 timestamptz(절대시각)이고, 세션 시간대는 **텍스트로
    렌더할 때와 달력 하루로 자를 때(::date)만** 작용한다. 비교·정렬·기간 계산은 어느
    시간대에서든 같은 답을 낸다.

    그런데도 이걸 두는 이유는 사람이다. API 응답의 ISO 문자열과 psql 로 직접 본 원본이
    서로 다른 눈금이면, 운영자가 매번 9시간을 암산해야 하고 그게 실수가 된다. 여기서
    맞춰 두면 우리가 내보내는 모든 시각이 `+09:00` 을 달고 나간다.

    **DB 전체(ALTER DATABASE)나 역할(ALTER ROLE)에는 걸지 마라.** 같은 DB 에 Supabase 의
    auth·realtime·storage 가 붙어 있고, 그쪽에는 시간대 정보가 없는 naive `timestamp`
    컬럼이 8개 있다(예: `auth.one_time_tokens.created_at`, 기본값 `now()`). 세션 시간대가
    KST 면 `timestamptz → timestamp` 암묵 변환이 KST 로 일어나 그 컬럼에 9시간 밀린 값이
    들어가고, naive 라 나중에 어느 눈금인지 구분할 방법이 없다. one_time_tokens 는
    매직링크·비밀번호 재설정 만료 판정에 쓰이므로 인증이 조용히 깨진다. 우리 public
    스키마에는 naive timestamp 가 0개라 **우리 커넥션에만** 거는 것은 안전하다.

    화면은 이 설정을 믿지 않는다 — src/lib/datetime.js 가 표시 시간대를 따로 고정한다.
    여기가 꺼져도 사용자에게 보이는 시각은 안 바뀐다.
    """
    async with conn.cursor() as cur:
        await cur.execute(f"set time zone '{SESSION_TIME_ZONE}'")


def create_pool(database_url: str) -> AsyncConnectionPool:
    # open=False → lifespan에서 명시적 open (psycopg_pool 권장).
    # timeout/connect_timeout: DB 불가 시 기본 30s 대기 대신 ~10s 안에 빨리 실패
    # (정상 연결은 <1s라 false-positive 없음 — 오설정·DB 다운만 잡힌다).
    # max_size env 오버라이드. "session pooler 상한 15" 라는 옛 근거는 틀렸다 —
    # 2026-08-27 실측(us-east-1 이전 후 Micro 인스턴스): max_connections=60,
    # Supavisor 클라이언트 상한 200, 당시 사용 18. 잡 동시 실행(JOB_CONCURRENCY)이
    # 켜지면 잡 하나가 컷 8개를 병렬로 돌리며 emit_job_event·image_usage 마다 커넥션을
    # 잡으므로 풀이 3 이면 즉시 경합한다. 여유는 2N+4 를 기준으로 잡는다.
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
        timeout=10,  # pool.connection() 연결 획득 최대 대기 (기본 30)
        kwargs={"row_factory": dict_row, "connect_timeout": 10},  # 각 연결 시도 상한(초)
        # DSN 의 options 파라미터가 아니라 configure 훅으로 건다 — 풀러(Supavisor)가 startup
        # 파라미터를 어떻게 다루든 `set time zone` 은 평범한 SQL 이라 그냥 통한다.
        configure=_configure,
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
