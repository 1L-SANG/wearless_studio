"""SQL 레이어 (backend_integration_plan §4 HTTP 매핑 대상 함수).

원칙(§9): service-role 연결은 RLS를 우회하므로 모든 쿼리에 owner 조건
(user_id = JWT sub)을 **명시**한다. 타인 소유 행은 0건 반환 → 라우트에서 404.

uuid 컬럼은 ::text 캐스트해 Pydantic str 필드와 맞춘다.
"""

import uuid

from psycopg import AsyncConnection, errors
from psycopg.types.json import Json

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
        row = await cur.fetchone()
        # project ↔ product 는 1:1 — 생성 시 함께 만든다(이후 getProduct는 순수 read).
        await cur.execute("insert into products (project_id) values (%s)", (row["id"],))
        return row


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


_PRODUCT_COLS = (
    "id::text as id, project_id::text as project_id, name, clothing_type, "
    "colors, measurements, measurements_unknown, upload_complete"
)
_PRODUCT_JSONB = ("colors", "measurements")
# saveProduct가 DB에 반영 가능한 컬럼 (계약 §3.1). 모델이 1차 가드, 이 집합이 2차.
PATCHABLE_PRODUCT_COLUMNS = (
    "name", "clothing_type", "colors", "measurements",
    "measurements_unknown", "upload_complete",
)


async def get_product(conn: AsyncConnection, project_id: str) -> dict | None:
    """순수 read. 소유권은 라우트가 get_project로 선검증. 행은 createProject가 생성함."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_PRODUCT_COLS} from products where project_id = %s", (project_id,)
        )
        return await cur.fetchone()


async def save_product(
    conn: AsyncConnection, project_id: str, user_id: str, patch: dict
) -> dict:
    """patch 적용 + name 변경 시 projects.title 동기화(계약 §3.1). 소유권은 라우트 선검증 +
    title UPDATE는 user_id 조건 명시(§9). 레거시(행 없음) 대비 행 보장 포함."""
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into products (project_id) values (%s) on conflict (project_id) do nothing",
            (project_id,),
        )
    sets = {
        k: (Json(v) if k in _PRODUCT_JSONB else v)
        for k, v in patch.items()
        if k in PATCHABLE_PRODUCT_COLUMNS
    }
    if not sets:
        return await get_product(conn, project_id)

    assignments = ", ".join(f"{col} = %s" for col in sets)
    async with conn.cursor() as cur:
        await cur.execute(
            f"update products set {assignments} where project_id = %s returning {_PRODUCT_COLS}",
            [*sets.values(), project_id],
        )
        row = await cur.fetchone()
        if "name" in patch:
            await cur.execute(
                "update projects set title = %s where id = %s and user_id = %s",
                (patch["name"], project_id, user_id),
            )
    return row


async def save_analysis(conn: AsyncConnection, project_id: str, analysis: dict) -> dict:
    """analysis 작업본을 payload jsonb 로 upsert (계약 §3.2). 소유권은 라우트 선검증."""
    locked = bool(analysis.get("locked", False))
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into analyses (project_id, payload, locked) values (%s, %s, %s) "
            "on conflict (project_id) do update set payload = excluded.payload, "
            "locked = excluded.locked "
            "returning project_id::text as project_id, payload, locked",
            (project_id, Json(analysis), locked),
        )
        return await cur.fetchone()


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


# ==================== Phase 4 — AI job / 크레딧 / 에셋 ====================
# 원칙 동일(§9): owner-스코프 쿼리에 user_id 조건 명시. uuid는 ::text 캐스트.


async def get_analysis(conn: AsyncConnection, project_id: str) -> dict:
    """analyses.payload (없으면 {}). 소유권은 라우트가 get_project로 선검증."""
    async with conn.cursor() as cur:
        await cur.execute("select payload from analyses where project_id = %s", (project_id,))
        row = await cur.fetchone()
    return (row or {}).get("payload") or {}


async def get_asset_for_user(conn: AsyncConnection, user_id: str, asset_id: str) -> dict | None:
    """asset 메타(소유 or seed). 베이스 마네킹 로드·파일 서빙·검증용."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_bucket, r2_key, mime_type, source "
            "from assets where id = %s and deleted_at is null "
            "and (user_id = %s or source = 'seed')",
            (asset_id, user_id),
        )
        return await cur.fetchone()


async def get_matching_item_asset(conn: AsyncConnection, item_id: str) -> str | None:
    """매칭의류(하의) 이미지 asset id — 활성 항목만. 운영자 시드 데이터(matching_items)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select image_asset_id::text as asset_id from matching_items "
            "where id = %s and is_active",
            (item_id,),
        )
        row = await cur.fetchone()
    return row["asset_id"] if row else None


async def list_mannequin_cuts(conn: AsyncConnection, user_id: str, project_id: str) -> list[dict]:
    """프로젝트 마네킹컷 + 에셋 키 (URL은 라우트가 r2로 변환). 소유권 join."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select mc.candidate, mc.version, mc.base_fit, mc.fit_adjust,
                   mc.length_adjust, mc.match_adjust, a.id::text as asset_id, a.r2_key
            from mannequin_cuts mc
            join projects pr on pr.id = mc.project_id
            join assets a on a.id = mc.asset_id
            where mc.project_id = %s and pr.user_id = %s and pr.deleted_at is null
            order by mc.candidate, mc.version
            """,
            (project_id, user_id),
        )
        return await cur.fetchall()


# ---------- jobs ----------

_JOB_COLS = (
    "id::text as id, user_id::text as user_id, project_id::text as project_id, kind, "
    "status, progress, steps, payload, result, error_message, credits_reserved, "
    "credits_charged, metadata, created_at, updated_at, finished_at"
)


async def get_job(conn: AsyncConnection, user_id: str, job_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_JOB_COLS} from jobs where id = %s and user_id = %s",
            (job_id, user_id),
        )
        return await cur.fetchone()


async def create_job(
    conn: AsyncConnection,
    *,
    user_id: str,
    project_id: str,
    kind: str,
    payload: dict,
    idempotency_key: str | None,
    credits_reserved: int,
    metadata: dict,
) -> tuple[dict, bool]:
    """job 생성. (row, created) 반환 — created=False면 동시 활성 중복으로 **기존 활성 job 합류**.

    dedupe_key는 재사용하지 않는다(NULL) — canonical 키를 재사용하면 과거 실패/완료 job이
    전역 unique를 점유해 재시도가 막힌다. 동시 활성 중복은 jobs_active_unique_idx(부분 unique)가
    원자적으로 막고, 충돌 시 기존 활성 job을 합류 반환(check-then-act race 제거). 호출자(라우트)는
    created=False면 방금 예약한 크레딧을 release해야 한다.

    멱등(계약 §6): ① 같은 Idempotency-Key = 같은 job(상태 무관) — 선조회로 합류 ② 진행 중 중복 =
    활성 job 합류(ON CONFLICT) ③ 실패 후 재호출(키 없음·새 키) = 새 job. **완료 job 재호출(기존
    결과 반환·무차감)은 라우트가 create 전에 list_mannequin_cuts로 확인**(여기 아님)."""
    async def _by_key(cur):
        await cur.execute(
            f"select {_JOB_COLS} from jobs where idempotency_key = %s and user_id = %s",
            (idempotency_key, user_id),
        )
        return await cur.fetchone()

    async def _active(cur):
        await cur.execute(
            f"select {_JOB_COLS} from jobs where project_id = %s and user_id = %s "
            "and kind = %s and status in ('pending', 'running')",
            (project_id, user_id, kind),
        )
        return await cur.fetchone()

    async with conn.cursor() as cur:
        if idempotency_key:  # ① 같은 Idempotency-Key 재시도(순차) → 같은 job
            existing = await _by_key(cur)
            if existing is not None:
                return existing, False
        # 직접 SAVEPOINT — conn.transaction()은 열린 tx가 없으면 스스로 COMMIT해 라우트의
        # 커밋 제어(예약+생성 원자)를 빼앗을 수 있다. SAVEPOINT/RELEASE는 절대 커밋 안 함.
        row = None
        await cur.execute("savepoint create_job_insert")
        try:
            await cur.execute(
                f"""
                insert into jobs (user_id, project_id, kind, status, payload, idempotency_key,
                                  credits_reserved, metadata)
                values (%s, %s, %s, 'pending', %s, %s, %s, %s)
                on conflict (project_id, kind)
                  where status in ('pending', 'running') and kind <> 'editor_image'
                  do nothing
                returning {_JOB_COLS}
                """,
                (user_id, project_id, kind, Json(payload), idempotency_key, credits_reserved,
                 Json(metadata)),
            )
            row = await cur.fetchone()
        except errors.UniqueViolation:
            await cur.execute("rollback to savepoint create_job_insert")
            row = None  # 동시 같은 Idempotency-Key → 아래에서 키로 재조회 합류
        else:
            await cur.execute("release savepoint create_job_insert")
        if row is not None:
            return row, True
        # 충돌(동시 같은 키 또는 활성 중복) → 기존 job 합류: 키 우선, 없으면 활성
        if idempotency_key:
            existing = await _by_key(cur)
            if existing is not None:
                return existing, False
        return await _active(cur), False


async def claim_next_job(conn: AsyncConnection, kinds: tuple[str, ...], worker_id: str) -> dict | None:
    """pending job 1건을 FOR UPDATE SKIP LOCKED로 점유 → running + lease (§5).

    locked_by에 **클레임마다 고유한 lease 토큰**(worker_id:uuid)을 넣는다 — 단순 worker_id면
    복구 후 같은 프로세스의 재클레임과 옛 stale 실행을 구분 못 한다. 워커는 반환된 row의
    locked_by(=토큰)로 종결을 펜싱(lock_owned_running_job)해야 한다."""
    lease_token = f"{worker_id}:{uuid.uuid4()}"
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            with next_job as (
              select id from jobs
              where status = 'pending' and kind = any(%s)
              order by created_at for update skip locked limit 1
            )
            update jobs j set status = 'running', locked_by = %s, locked_at = now(),
              started_at = coalesce(j.started_at, now()), progress = greatest(j.progress, 5)
            from next_job where j.id = next_job.id
            returning {_JOB_COLS}, locked_by as lease_token
            """,
            (list(kinds), lease_token),
        )
        return await cur.fetchone()


# job 종결(에셋·컷·크레딧·done)은 finalize_mannequin_success/failure 한 함수에서 원자적으로 —
# 개별 종결 헬퍼를 따로 두면 워커가 여러 tx로 쪼개 락이 풀린 사이 복구가 끼어들 수 있다(§5).


async def set_job_progress(conn: AsyncConnection, job_id: str, progress: int):
    async with conn.cursor() as cur:
        await cur.execute("update jobs set progress = %s where id = %s", (progress, job_id))


async def recover_stale_leases(conn: AsyncConnection, lease_timeout_seconds: int) -> list[dict]:
    """lease 초과 running job: 1회차 pending 재큐, 2회차 error (§5 고착 방지).
    error 전환 시 같은 statement(ev CTE)로 error job_event도 원자 append (SSE 종결 신호)."""
    msg = "작업 서버가 응답하지 않아 작업을 중단했어요. 다시 시도해 주세요."
    async with conn.cursor() as cur:
        await cur.execute(
            """
            with stale as (
              select id, coalesce((metadata->>'leaseRecoveries')::int, 0) as recoveries
              from jobs
              where status = 'running' and locked_at < now() - make_interval(secs => %s)
              for update skip locked
            ),
            updated as (
              update jobs j
              set status = case when stale.recoveries >= 1 then 'error' else 'pending' end,
                  locked_by = null, locked_at = null,
                  error_message = case when stale.recoveries >= 1 then %s else null end,
                  metadata = jsonb_set(j.metadata, '{leaseRecoveries}',
                    to_jsonb(stale.recoveries + 1), true),
                  finished_at = case when stale.recoveries >= 1 then now() else null end
              from stale where j.id = stale.id
              returning j.id as id, j.user_id as user_id, j.status as status,
                        j.credits_reserved as credits_reserved
            ),
            ev as (
              insert into job_events (job_id, event_type, payload)
              select id, 'error',
                     jsonb_build_object('code', 'lease_recovered', 'message', %s)
              from updated where status = 'error'
              returning 1
            )
            select id::text as id, user_id::text as user_id, status, credits_reserved
            from updated
            """,
            (lease_timeout_seconds, msg, msg),
        )
        return await cur.fetchall()


async def list_unsettled_errored_jobs(conn: AsyncConnection) -> list[dict]:
    """예약 크레딧이 아직 해제 안 된 error job들 (정산 ledger 없음). dispatcher가 release 재시도."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, user_id::text as user_id,
                   project_id::text as project_id, credits_reserved
            from jobs j
            where status = 'error' and credits_reserved > 0
              and not exists (
                select 1 from credit_ledger l
                where l.idempotency_key = 'credit:job:' || j.id::text || ':settle'
              )
            limit 50
            """,
        )
        return await cur.fetchall()


# ---------- job_events (SSE/폴링 원본) ----------


async def append_job_event(conn: AsyncConnection, job_id: str, event_type: str, payload: dict):
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, %s, %s)",
            (job_id, event_type, Json(payload)),
        )


async def list_job_events(
    conn: AsyncConnection, user_id: str, job_id: str, after_id: int = 0
) -> list[dict]:
    """소유 job 이벤트(after_id 이후). SSE replay·폴링."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id, e.event_type, e.payload, e.created_at
            from job_events e join jobs j on j.id = e.job_id
            where e.job_id = %s and j.user_id = %s and e.id > %s
            order by e.id
            """,
            (job_id, user_id, after_id),
        )
        return await cur.fetchall()


# ---------- 크레딧 (reserve-then-confirm §6) ----------


async def reserve_credits(conn: AsyncConnection, user_id: str, amount: int) -> int | None:
    """available(=balance-reserved) ≥ amount 면 reserved += amount, available_after 반환.
    부족·계정없음이면 None (라우트가 402)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "update credit_accounts set reserved = reserved + %s "
            "where user_id = %s and balance - reserved >= %s "
            "returning balance - reserved as available_after",
            (amount, user_id, amount),
        )
        row = await cur.fetchone()
    return None if row is None else row["available_after"]


async def _settle_credits(
    conn: AsyncConnection,
    *,
    user_id: str,
    project_id: str,
    job_id: str,
    reserved: int,
    charge: int,  # 확정 차감액 (release면 0)
    action_key: str,
    settle_key: str,  # job당 1개 — confirm·release 공유 → "한 job 1회만 정산"
    metadata: dict,
) -> int:
    """재시도-안전 정산. ledger(settle_key unique) insert가 성공할 때만 계정을 변경한다.
    재실행·lease복구 중복 호출은 conflict로 no-op (이중 차감/해제 방지). available_after 반환."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update",
            (user_id,),
        )
        acct = await cur.fetchone()
        if acct is None:
            return 0
        cur_available = acct["balance"] - acct["reserved"]
        new_balance = acct["balance"] - charge
        new_reserved = max(0, acct["reserved"] - reserved)
        new_available = new_balance - new_reserved
        await cur.execute(
            """
            insert into credit_ledger (user_id, project_id, job_id, action_key, delta,
              balance_after, available_after, idempotency_key, metadata)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do nothing
            returning id
            """,
            (user_id, project_id, job_id, action_key, -charge, new_balance, new_available,
             settle_key, Json(metadata)),
        )
        inserted = await cur.fetchone()
        if inserted is None:  # 이미 정산됨 → 계정 불변
            return cur_available
        await cur.execute(
            "update credit_accounts set balance = %s, reserved = %s where user_id = %s",
            (new_balance, new_reserved, user_id),
        )
    return new_available


async def release_credits(
    conn: AsyncConnection,
    *,
    user_id: str,
    project_id: str | None,
    job_id: str | None,
    reserved: int,
    settle_key: str,
    metadata: dict | None = None,
) -> int:
    """실패/취소/lease복구 (재시도-안전): reserved만 해제(잔액 불변), delta=0 ledger 가드."""
    return await _settle_credits(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=0, action_key="mannequinGenerate.release", settle_key=settle_key,
        metadata=metadata or {},
    )


# ---------- 종결 (원자) ----------
# 에셋·컷 insert + 크레딧 정산 + job done/error를 **한 함수 = 한 tx = 한 락**으로 처리.
# 시작에서 jobs 행을 FOR UPDATE로 잠그고(lease 토큰 확인) 커밋까지 유지 → lease 복구의
# FOR UPDATE와 직렬화되어, 락 풀린 틈에 stale 결과가 끼어들 여지가 없다. 호출 후 conn.commit().


async def finalize_mannequin_success(
    conn: AsyncConnection,
    *,
    job_id: str,
    lease_token: str,
    user_id: str,
    project_id: str,
    candidates: list[dict],  # [{asset_id, bucket, key, mime, size, width, height, candidate, base_fit}]
    reserved: int,
    charge: int,
    settle_key: str,
    metadata: dict,
) -> dict | None:
    """성공 종결(원자·lease 펜스). None = lease 상실(복구·재클레임) → 아무것도 쓰지 않음."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return None  # lease 빼앗김 — 부수효과 0 (워커는 폐기)
        cuts = []
        for c in candidates:
            await cur.execute(
                "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
                "r2_key, mime_type, byte_size, width, height) "
                "values (%s, %s, %s, 'ai', 'private', %s, %s, %s, %s, %s, %s)",
                (c["asset_id"], user_id, project_id, c["bucket"], c["key"], c["mime"],
                 c.get("size"), c.get("width"), c.get("height")),
            )
            await cur.execute(
                "select coalesce(max(version), 0) + 1 as v from mannequin_cuts "
                "where project_id = %s and candidate = %s",
                (project_id, c["candidate"]),
            )
            version = (await cur.fetchone())["v"]
            await cur.execute(
                "insert into mannequin_cuts (project_id, candidate, version, asset_id, base_fit) "
                "values (%s, %s, %s, %s, %s)",
                (project_id, c["candidate"], version, c["asset_id"], c["base_fit"]),
            )
            cuts.append({  # MannequinCut shape (계약 §3.3) — /jobs·SSE done에서 그대로 직렬화
                "id": f"{c['candidate']}-{version}",
                "src": f"/v1/assets/{c['asset_id']}/file",  # 안정 앱 URL (만료 없음, §3). assetId 인코딩됨
                "candidate": c["candidate"], "version": version, "baseFit": c["base_fit"],
                "fitAdjust": None, "lengthAdjust": None, "matchAdjust": None,
            })
    # 크레딧 확정 (settle_key 멱등) — 같은 tx, jobs 락 유지
    available = await _settle_credits(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=charge, action_key="mannequinGenerate", settle_key=settle_key, metadata=metadata,
    )
    # 폴링(jobs.result)과 SSE done이 **같은 봉투**({data, credits, creditsCharged}) — 계약 §6
    envelope = {"data": cuts, "credits": available, "creditsCharged": charge}
    async with conn.cursor() as cur:
        await cur.execute(
            "update jobs set status = 'done', result = %s, credits_charged = %s, progress = 100, "
            "locked_by = null, locked_at = null, finished_at = now() where id = %s",
            (Json(envelope), charge, job_id),
        )
        # 종결 이벤트 — 같은 tx (SSE replay 원본). 상태 변경과 원자적. result와 동일 shape.
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
            (job_id, Json(envelope)),
        )
    return {"cuts": cuts, "available": available}


async def finalize_mannequin_failure(
    conn: AsyncConnection,
    *,
    job_id: str,
    lease_token: str,
    user_id: str,
    project_id: str,
    reserved: int,
    settle_key: str,
    message: str,
    metadata: dict,
    code: str = "generation_failed",
) -> bool:
    """실패 종결(원자·lease 펜스): 예약 해제 + job error + error 이벤트. False = lease 상실."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return False
    await _settle_credits(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=0, action_key="mannequinGenerate.release", settle_key=settle_key, metadata=metadata,
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "update jobs set status = 'error', error_message = %s, "
            "locked_by = null, locked_at = null, finished_at = now() where id = %s",
            (message, job_id),
        )
        # 종결 이벤트 — 같은 tx (SSE replay 원본). 토스트 가능한 한국어 message (계약 §6).
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'error', %s)",
            (job_id, Json({"code": code, "message": message})),
        )
    return True
