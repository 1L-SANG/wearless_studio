"""관리자 권한 게이트와 감사 원장 기록 — 콘솔의 모든 쓰기가 지나는 문.

`repo.is_admin` 을 직접 부르는 곳은 이 파일 하나여야 한다. 예전에는 같은 판정이 6군데에
흩어져 있어 에러 코드·문구가 제각각이었고, 새 라우트를 추가할 때 가드를 빼먹어도 아무도
몰랐다(테스트가 그걸 못 본다).

write_audit 은 conn.commit() 을 하지 않는다 — 호출자(라우트)의 트랜잭션 안에서 조치와
함께 커밋돼야 한다. 따로 커밋하면 조치는 실패하고 기록만 남는 경우가 생긴다.
"""
from fastapi import HTTPException
from psycopg.types.json import Json

from . import repo


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=403, detail={"code": "forbidden", "message": "관리자만 가능해요."}
    )


async def require_admin(conn, user_id: str) -> None:
    if not await repo.is_admin(conn, user_id):
        raise forbidden()


async def is_admin_user(conn, user_id: str) -> bool:
    """예외 대신 판정만 필요한 호출자(cutover 는 자체 예외 타입을 쓴다)."""
    return await repo.is_admin(conn, user_id)


async def write_audit(
    conn,
    *,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    note: str | None = None,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into admin_audit_log "
            "(actor_user_id, action, target_type, target_id, before, after, note) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (
                actor_user_id, action, target_type, target_id,
                Json(before or {}), Json(after or {}), note,
            ),
        )
