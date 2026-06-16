"""SQL 레이어 (backend_integration_plan §4 HTTP 매핑 대상 함수).

원칙(§9): service-role 연결은 RLS를 우회하므로 모든 쿼리에 owner 조건
(user_id = JWT sub)을 **명시**한다. 타인 소유 행은 0건 반환 → 라우트에서 404.

uuid 컬럼은 ::text 캐스트해 Pydantic str 필드와 맞춘다.
"""

from psycopg import AsyncConnection

# patchProject가 DB에 반영할 수 있는 컬럼 (계약 §6 화이트리스트). 모델이 1차,
# 이 집합이 2차 가드 — 둘 중 하나라도 빠지면 임의 컬럼 갱신을 막는다.
PATCHABLE_COLUMNS = ("compose_mode", "copywriting", "selected_mannequin_id")

_PROJECT_COLS = (
    "id::text as id, status, title, compose_mode, copywriting, "
    "selected_mannequin_id, adjust_count, created_at, updated_at"
)


async def get_account(conn: AsyncConnection, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
                coalesce(p.display_name, '') as name,
                '' as avatar,
                coalesce(ca.balance, 0) - coalesce(ca.reserved, 0) as credits,
                p.plan
            from profiles p
            left join credit_accounts ca on ca.user_id = p.user_id
            where p.user_id = %s
            """,
            (user_id,),
        )
        return await cur.fetchone()


async def list_library(conn: AsyncConnection, user_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
                pr.id::text as id,
                pr.title,
                ''::text as cover,
                prod.clothing_type,
                case when jsonb_typeof(pr.editor_blocks) = 'array'
                     then jsonb_array_length(pr.editor_blocks) else 0 end as block_count,
                pr.status,
                pr.updated_at
            from projects pr
            left join products prod on prod.project_id = pr.id
            where pr.user_id = %s and pr.deleted_at is null
            order by pr.updated_at desc
            """,
            (user_id,),
        )
        return await cur.fetchall()


async def create_project(conn: AsyncConnection, user_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            f"insert into projects (user_id) values (%s) returning {_PROJECT_COLS}",
            (user_id,),
        )
        return await cur.fetchone()


async def get_project(conn: AsyncConnection, user_id: str, project_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_PROJECT_COLS} from projects "
            "where id = %s and user_id = %s and deleted_at is null",
            (project_id, user_id),
        )
        return await cur.fetchone()


async def create_asset(
    conn: AsyncConnection,
    *,
    asset_id: str,
    user_id: str,
    project_id: str,
    source: str,
    bucket: str,
    key: str,
    mime: str,
    size: int | None,
    original_filename: str | None,
) -> dict:
    """업로드 검증 후 asset 행 확정. complete 재호출(멱등)이면 기존 행 반환."""
    cols = "id::text as id, r2_key, mime_type, byte_size"
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            insert into assets
              (id, user_id, project_id, source, visibility, r2_bucket, r2_key,
               mime_type, byte_size, original_filename)
            values (%s, %s, %s, %s, 'private', %s, %s, %s, %s, %s)
            on conflict (id) do nothing
            returning {cols}
            """,
            (asset_id, user_id, project_id, source, bucket, key, mime, size, original_filename),
        )
        row = await cur.fetchone()
        if row is None:  # 이미 존재 → 소유권 확인하며 재조회
            await cur.execute(
                f"select {cols} from assets where id = %s and user_id = %s",
                (asset_id, user_id),
            )
            row = await cur.fetchone()
        return row


async def patch_project(
    conn: AsyncConnection, user_id: str, project_id: str, patch: dict
) -> dict | None:
    # 화이트리스트 컬럼만 (모델이 이미 걸렀지만 SQL 인젝션 면역 위해 컬럼명도 고정 집합으로 검증)
    sets = {k: v for k, v in patch.items() if k in PATCHABLE_COLUMNS}
    if not sets:
        return await get_project(conn, user_id, project_id)

    assignments = ", ".join(f"{col} = %s" for col in sets)
    params = [*sets.values(), project_id, user_id]
    async with conn.cursor() as cur:
        await cur.execute(
            f"update projects set {assignments} where id = %s and user_id = %s "
            f"and deleted_at is null returning {_PROJECT_COLS}",
            params,
        )
        return await cur.fetchone()
