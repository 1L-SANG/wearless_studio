"""SQL 레이어 (backend_integration_plan §4 HTTP 매핑 대상 함수).

원칙(§9): service-role 연결은 RLS를 우회하므로 모든 쿼리에 owner 조건
(user_id = JWT sub)을 **명시**한다. 타인 소유 행은 0건 반환 → 라우트에서 404.

uuid 컬럼은 ::text 캐스트해 Pydantic str 필드와 맞춘다.
"""

import uuid

from psycopg import AsyncConnection, errors
from psycopg.types.json import Json

from .agents.analysis import apply_swatch_fill, input_fingerprint
from .credits import allocate_fifo


class CreditError(Exception):
    """크레딧 도메인 에러 — 라우트가 code/status로 HTTP 매핑(토스트 가능한 한국어 message)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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
    """analysis patch를 payload jsonb에 **병합** upsert (계약 §3.2 · pl1 spec §7.5).

    전체 교체가 아니라 병합 — 부분 patch(예: {fit}만)가 다른 필드를 지우지 않는다.
    locked는 patch에 있을 때만 갱신. 전량 저장(폼 전체) 호출은 병합으로도 결과 동일.
    분석 재실행의 전체 덮어쓰기는 finalize_analyze_success가 별도로 수행한다."""
    locked = bool(analysis.get("locked", False))
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into analyses (project_id, payload, locked) values (%s, %s, %s) "
            "on conflict (project_id) do update set "
            "payload = coalesce(analyses.payload, '{}'::jsonb) || excluded.payload, "
            "locked = case when %s::boolean then excluded.locked else analyses.locked end "
            "returning project_id::text as project_id, payload, locked",
            (project_id, Json(analysis), locked, "locked" in analysis),
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


async def list_active_matching_items(conn: AsyncConnection) -> list[dict]:
    """활성 매칭의류 + 본/썸네일 R2 키 (URL은 라우트가 r2로 변환). 운영자 시드(무소유)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select mi.id, mi.name, mi.clothing_type, mi.gender, mi.category,
                   mi.color_name, mi.color_group, mi.style_tags, mi.fit, mi.length,
                   mi.color_brightness, mi.sort_order, mi.is_active,
                   img.r2_key as image_key, thb.r2_key as thumb_key
            from matching_items mi
            -- 썸네일은 표시 필수 → seed/public 자산만 inner join (비-seed·비공개·삭제 키 노출 차단,
            -- limit 정확도 보장). 본 이미지는 동일 조건 left join(선택).
            join assets thb on thb.id = mi.thumbnail_asset_id
              and thb.source = 'seed' and thb.visibility = 'public' and thb.deleted_at is null
            left join assets img on img.id = mi.image_asset_id
              and img.source = 'seed' and img.visibility = 'public' and img.deleted_at is null
            where mi.is_active
            """,
        )
        return await cur.fetchall()


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
        # INSERT-or-join을 bounded 재시도(3회): 충돌(활성중복/동시같은키)로 합류해야 하는데
        # 그 활성 job이 그 사이 완료돼 _by_key·_active가 모두 빈 결과면(레이스), 충돌 원인이
        # 사라진 것이므로 INSERT를 재시도하면 성공한다. (Codex: race → (None,False) → 라우트 500 방지)
        for _ in range(3):
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
            # 충돌 → 기존 job 합류: 키 우선, 없으면 활성
            if idempotency_key:
                existing = await _by_key(cur)
                if existing is not None:
                    return existing, False
            active = await _active(cur)
            if active is not None:
                return active, False
            # 합류 대상이 사라짐(충돌 job 완료) → 루프 재시도(이제 INSERT 성공)
        raise RuntimeError("create_job: 활성 합류 대상이 반복적으로 사라짐 (드문 레이스)")


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
              select id as nid from jobs
              where status = 'pending' and kind = any(%s)
              order by created_at for update skip locked limit 1
            )
            update jobs j set status = 'running', locked_by = %s, locked_at = now(),
              started_at = coalesce(j.started_at, now()), progress = greatest(j.progress, 5)
            from next_job where j.id = next_job.nid
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
                     jsonb_build_object('code', 'lease_recovered', 'message', %s::text)
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
    """available(=Σ active 버킷 remaining − reserved) ≥ amount 면 reserved += amount, available_after 반환.
    **account 행을 먼저 FOR UPDATE로 잠근 뒤** 버킷 합을 읽는다 — 버킷 변경 경로
    (_consume_buckets·grant·refund)가 전부 account를 먼저 잠그므로, reserve도 같은 순서를 따라야
    동시 confirm이 버킷을 비우는 사이 stale-high 읽기로 과예약(=settle 불가 doomed job)하는 race를 막는다.
    부족·계정없음이면 None (라우트가 402)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select reserved from credit_accounts where user_id = %s for update", (user_id,)
        )
        acct = await cur.fetchone()
        if acct is None:
            return None
        await cur.execute(
            "select coalesce(sum(remaining_credits), 0) as s from credit_sources "
            "where user_id = %s and status = 'active'",
            (user_id,),
        )
        bucket_sum = (await cur.fetchone())["s"]
        if bucket_sum - acct["reserved"] < amount:
            return None
        await cur.execute(
            "update credit_accounts set reserved = reserved + %s where user_id = %s",
            (amount, user_id),
        )
    return bucket_sum - acct["reserved"] - amount


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


async def _consume_buckets(
    conn: AsyncConnection,
    *,
    user_id: str,
    project_id: str | None,
    job_id: str | None,
    reserved: int,
    charge: int,
    action_key: str,
    metadata: dict,
) -> int:
    """성공 confirm 차감 (버킷 인지, credit_system_design §3.3). active 버킷서 charge를
    FIFO(구독먼저→오래된순)로 깎고 **버킷별 ledger 행**(settle:{job}:{bucket} 멱등키) +
    credit_accounts(balance-=charge, reserved-=reserved)를 갱신한다.
    커버 실패 시 raise → tx rollback(무음 미달차감 금지, 불변식 5). available_after 반환.
    멱등 경계 = job.status — 호출측 finalize가 jobs FOR UPDATE + status='running'으로 보장."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update",
            (user_id,),
        )
        acct = await cur.fetchone()
        if acct is None:
            raise ValueError("credit_account missing")
        await cur.execute(
            "select id::text as id, remaining_credits from credit_sources "
            "where user_id = %s and status = 'active' and remaining_credits > 0 "
            "order by source_type, created_at for update",  # 'subscription' < 'topup' → 구독 먼저
            (user_id,),
        )
        buckets = await cur.fetchall()
        allocations, uncovered = allocate_fifo(buckets, charge)
        if uncovered != 0:  # 불변식 5: active 잔액이 charge 미달 → hard error → rollback
            raise ValueError(f"insufficient active credits to settle charge ({uncovered} uncovered)")
        new_reserved = max(0, acct["reserved"] - reserved)
        running = acct["balance"]
        for a in allocations:
            await cur.execute(
                "update credit_sources set remaining_credits = remaining_credits - %s where id = %s",
                (a["take"], a["id"]),
            )
            running -= a["take"]
            await cur.execute(
                """
                insert into credit_ledger (user_id, project_id, job_id, credit_source_id, action_key,
                  delta, balance_after, available_after, idempotency_key, metadata)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (idempotency_key) do nothing
                """,
                (user_id, project_id, job_id, a["id"], action_key, -a["take"], running,
                 running - new_reserved, f"credit:job:{job_id}:settle:{a['id']}", Json(metadata)),
            )
        await cur.execute(
            "update credit_accounts set balance = %s, reserved = %s where user_id = %s",
            (running, new_reserved, user_id),
        )
    return running - new_reserved


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
    # 크레딧 확정 — 버킷 FIFO 차감(구독먼저→topup), 같은 tx·jobs 락 유지.
    # 멱등 = job.status(위 status='running' FOR UPDATE) → 재진입 없음. settle_key는 release 전용.
    available = await _consume_buckets(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=charge, action_key="mannequinGenerate", metadata=metadata,
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


# ---------- 분석 job 종결 (pl1_analysis_agent_spec §3.7·§6.7 — 크레딧 없음) ----------


async def has_active_analyze_job(
    conn: AsyncConnection, user_id: str, project_id: str
) -> bool:
    """이 프로젝트에 진행 중(pending|running) analyze job이 있는가 — rate limit 면제 판정.
    있으면 재호출은 create_job이 합류시켜 새 Gemini 작업이 없다(멱등 §6 ①)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select 1 from jobs where user_id = %s and project_id = %s "
            "and kind = 'analyze' and status in ('pending', 'running') limit 1",
            (user_id, project_id),
        )
        return await cur.fetchone() is not None


async def count_recent_analyze_jobs(
    conn: AsyncConnection, user_id: str, window_seconds: int
) -> int:
    """window 내 이 사용자가 만든 analyze job 수 — 비용 남용 rate limit용 (pl1 spec §5.1).
    job row가 곧 Gemini 호출 1건이라 프로젝트 무관·전 status 합산(보수적)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select count(*) as n from jobs "
            "where user_id = %s and kind = 'analyze' "
            "and created_at > now() - make_interval(secs => %s)",
            (user_id, window_seconds),
        )
        row = await cur.fetchone()
    return (row or {}).get("n", 0)


async def get_last_analyze_fingerprint(conn: AsyncConnection, project_id: str) -> str | None:
    """마지막 done analyze job의 입력 지문 — 재분석 판정(§3.7). finalize가 기록한 실측값.
    소유권은 라우트가 get_project로 선검증(비스코프 read — get_analysis와 동일 면제)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select metadata->>'fingerprint' as fp from jobs "
            "where project_id = %s and kind = 'analyze' and status = 'done' "
            "order by finished_at desc nulls last limit 1",
            (project_id,),
        )
        row = await cur.fetchone()
    return (row or {}).get("fp")


async def finalize_analyze_success(
    conn: AsyncConnection,
    *,
    job_id: str,
    lease_token: str,
    user_id: str,
    project_id: str,
    clothing_type: str,
    swatch_suggestions: list[dict],
    payload: dict,
    metadata: dict,
    actual_fingerprint: str,
) -> dict | None:
    """성공 종결(원자·lease 펜스·지문 가드). None = 결과 미기록(lease 상실 또는 가드 폐기).

    ⓪ lease 펜스 통과 후 products 행 재조회(FOR UPDATE) — 가드·스와치 병합의 기준.
    ① 지문 가드(§3.7 쓰기 관문 불변식): 현재 product의 지문 ≠ actual_fingerprint이면
       결과 폐기 — products·analyses 무변경, job error(superseded_stale) + error 이벤트.
       분석 중 입력이 바뀌는 어떤 동시성 시나리오에서도 stale 결과가 현재 입력의
       분석·편집을 덮어쓸 수 없게 하는 유일한 정합성 관문.
    ② products 갱신 — clothing_type 기록 + 현재 colors에 스와치 null-fill 병합.
    ③ analyses 전체 교체 upsert(재분석은 덮어씀이 의도) ④ jobs done ⑤ done 이벤트."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return None  # lease 상실 — 부수효과 0 (워커는 폐기)

        await cur.execute(
            "select colors from products where project_id = %s for update", (project_id,)
        )
        row = await cur.fetchone()
        # 행 없음(극단: 워커 실행 중 삭제) → colors=[] → 아래 가드 불일치 → 보수적 폐기
        current_colors = (row or {}).get("colors") or []

        if input_fingerprint({"colors": current_colors}) != actual_fingerprint:
            message = "입력이 변경되어 이전 분석을 중단했어요."
            await cur.execute(
                "update jobs set status = 'error', error_message = %s, "
                "locked_by = null, locked_at = null, finished_at = now(), "
                "metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb where id = %s",
                (message, Json({**metadata, "error": "superseded_stale"}), job_id),
            )
            await cur.execute(
                "insert into job_events (job_id, event_type, payload) values (%s, 'error', %s)",
                (job_id, Json({"code": "superseded_stale", "message": message})),
            )
            return None

        filled = apply_swatch_fill(current_colors, swatch_suggestions)
        await cur.execute(
            "update products set clothing_type = %s, colors = %s where project_id = %s",
            (clothing_type, Json(filled), project_id),
        )
        await cur.execute(
            "insert into analyses (project_id, payload, locked) values (%s, %s, false) "
            "on conflict (project_id) do update set payload = excluded.payload, "
            "locked = excluded.locked",
            (project_id, Json(payload)),
        )

    account = await get_account(conn, user_id)
    credits = (account or {}).get("credits", 0)
    data = {"projectId": project_id, "clothingType": clothing_type, **payload}
    envelope = {"data": data, "credits": credits, "creditsCharged": 0}
    async with conn.cursor() as cur:
        await cur.execute(
            "update jobs set status = 'done', result = %s, credits_charged = 0, progress = 100, "
            "locked_by = null, locked_at = null, finished_at = now(), "
            "metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb where id = %s",
            (Json(envelope), Json(metadata), job_id),
        )
        # 종결 이벤트 — 같은 tx (SSE replay 원본). result와 동일 shape (마네킹과 동일 규약).
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
            (job_id, Json(envelope)),
        )
    return {"data": data, "available": credits}


async def finalize_analyze_failure(
    conn: AsyncConnection,
    *,
    job_id: str,
    lease_token: str,
    message: str,
    metadata: dict,
    code: str = "analysis_failed",
) -> bool:
    """실패 종결(원자·lease 펜스). 크레딧 settle 없음(예약 0). False = lease 상실."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return False
        await cur.execute(
            "update jobs set status = 'error', error_message = %s, "
            "locked_by = null, locked_at = null, finished_at = now(), "
            "metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb where id = %s",
            (message, Json(metadata), job_id),
        )
        # 종결 이벤트 — 같은 tx. 토스트 가능한 한국어 message (계약 §6).
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'error', %s)",
            (job_id, Json({"code": code, "message": message})),
        )
    return True


# ---------- 크레딧 충전·환불·조회 (credit_system_design.md §3.1·§3.2·§3.4·§6) ----------
# 모든 쓰기는 credit_accounts FOR UPDATE로 직렬화. balance = Σ active 버킷 remaining,
# balance 변화엔 항상 ledger 행(원장-잔액 일관성). 호출측(라우트)이 conn.commit().


async def is_admin(conn: AsyncConnection, user_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("select role from profiles where user_id = %s", (user_id,))
        row = await cur.fetchone()
    return bool(row and row.get("role") == "admin")


async def grant_subscription(
    conn: AsyncConnection, *, user_id: str, plan_code: str, metadata: dict | None = None
) -> dict:
    """구독 월 충전(§3.1): 기존 active 구독 버킷 소멸 → 새 버킷(plan.credits, 1달 만료).
    full plan 지급이라 balance가 reserved를 항상 상회(불변식 5 backstop은 credit_accounts CHECK)."""
    metadata = metadata or {}
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, credits from pricing_plans "
            "where code = %s and kind = 'subscription' and is_active",
            (plan_code,),
        )
        plan = await cur.fetchone()
        if plan is None:
            raise CreditError("unknown_plan", f"요금제를 찾을 수 없어요: {plan_code}", 404)
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update", (user_id,)
        )
        acct = await cur.fetchone()
        if acct is None:
            raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
        running = acct["balance"]
        await cur.execute(
            "select id::text as id, remaining_credits from credit_sources "
            "where user_id = %s and source_type = 'subscription' and status = 'active' for update",
            (user_id,),
        )
        for old in await cur.fetchall():
            await cur.execute(
                "update credit_sources set status = 'expired', remaining_credits = 0 where id = %s",
                (old["id"],),
            )
            running -= old["remaining_credits"]
            await cur.execute(
                "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
                "balance_after, available_after, metadata) values (%s,%s,'expire_subscription',%s,%s,%s,%s)",
                (user_id, old["id"], -old["remaining_credits"], running,
                 running - acct["reserved"], Json(metadata)),
            )
        await cur.execute(
            "insert into credit_sources (user_id, source_type, plan_id, initial_credits, "
            "remaining_credits, status, period_end) "
            "values (%s, 'subscription', %s, %s, %s, 'active', now() + interval '1 month') "
            "returning id::text as id",
            (user_id, plan["id"], plan["credits"], plan["credits"]),
        )
        src_id = (await cur.fetchone())["id"]
        running += plan["credits"]
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, metadata) values (%s,%s,'grant_subscription',%s,%s,%s,%s)",
            (user_id, src_id, plan["credits"], running, running - acct["reserved"], Json(metadata)),
        )
        await cur.execute(
            "update credit_accounts set balance = %s where user_id = %s", (running, user_id)
        )
    return {"creditSourceId": src_id, "credits": plan["credits"], "available": running - acct["reserved"]}


async def purchase_topup(
    conn: AsyncConnection, *, user_id: str, plan_code: str,
    idempotency_key: str | None = None, metadata: dict | None = None
) -> dict:
    """추가구매(§3.2, 테스트용 provider='test'): payment + topup 버킷 + grant 원장.
    멱등: Idempotency-Key 주면 중복 지급 방지(더블클릭/재시도) — account FOR UPDATE가 동시
    호출을 직렬화하고, 같은 키의 grant_topup 원장이 이미 있으면 기존 구매를 반환. (※topup SKU·
    가격은 §5 TBD — 시드 전이라 unknown_plan(404). 실 결제 멱등은 PG 단계서 provider_ref로 보강.)"""
    metadata = metadata or {}
    # 멱등 키는 user 스코프 — credit_ledger.idempotency_key가 전역 unique라 다른 유저의 동일
    # 키 문자열 충돌(unique 위반→500)을 막는다 (create_job scoped_key 패턴과 동일).
    scoped_key = f"topup:{user_id}:{idempotency_key}" if idempotency_key else None
    async with conn.cursor() as cur:
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update", (user_id,)
        )
        acct = await cur.fetchone()
        if acct is None:
            raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
        if scoped_key:  # 재호출 → 원 구매 반환(현재 plan_code 무관). account 락이 동시호출 직렬화
            await cur.execute(
                "select cs.id::text as src, cs.initial_credits from credit_ledger cl "
                "join credit_sources cs on cs.id = cl.credit_source_id "
                "where cl.idempotency_key = %s",
                (scoped_key,),
            )
            existing = await cur.fetchone()
            if existing is not None:
                return {"creditSourceId": existing["src"], "credits": existing["initial_credits"],
                        "available": acct["balance"] - acct["reserved"], "idempotent": True}
        await cur.execute(
            "select id::text as id, credits, price from pricing_plans "
            "where code = %s and kind = 'topup' and is_active",
            (plan_code,),
        )
        plan = await cur.fetchone()
        if plan is None:
            raise CreditError("unknown_plan", f"추가구매 상품을 찾을 수 없어요: {plan_code}", 404)
        await cur.execute(
            "insert into payment_history (user_id, plan_id, amount, kind, provider, status) "
            "values (%s, %s, %s, 'topup', 'test', 'paid') returning id::text as id",
            (user_id, plan["id"], plan["price"]),
        )
        pay_id = (await cur.fetchone())["id"]
        await cur.execute(
            "insert into credit_sources (user_id, source_type, plan_id, initial_credits, "
            "remaining_credits, status, payment_id) "
            "values (%s, 'topup', %s, %s, %s, 'active', %s) returning id::text as id",
            (user_id, plan["id"], plan["credits"], plan["credits"], pay_id),
        )
        src_id = (await cur.fetchone())["id"]
        running = acct["balance"] + plan["credits"]
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, idempotency_key, metadata) "
            "values (%s,%s,'grant_topup',%s,%s,%s,%s,%s)",
            (user_id, src_id, plan["credits"], running, running - acct["reserved"],
             scoped_key, Json(metadata)),
        )
        await cur.execute(
            "update credit_accounts set balance = %s where user_id = %s", (running, user_id)
        )
    return {"creditSourceId": src_id, "paymentId": pay_id, "credits": plan["credits"],
            "available": running - acct["reserved"]}


async def request_refund(
    conn: AsyncConnection, *, user_id: str, credit_source_id: str, reason: str | None = None
) -> dict:
    """환불 요청(§3.4): topup·미사용(remaining==initial)·7일내·예약없음(MVP)만. 적격이면
    버킷 pending_refund + 가용서 즉시 제외(불변식 1) + 원장 행(잔액 일관성) + 요청 행."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update", (user_id,)
        )
        acct = await cur.fetchone()
        if acct is None:
            raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
        await cur.execute(
            "select id::text as id, source_type, status, initial_credits, remaining_credits, "
            "(now() - created_at) <= interval '7 days' as within_window "
            "from credit_sources where id = %s and user_id = %s for update",
            (credit_source_id, user_id),
        )
        b = await cur.fetchone()
        if b is None:
            raise CreditError("refund_not_found", "구매 내역을 찾을 수 없어요.", 404)
        if b["source_type"] != "topup":
            raise CreditError("subscription_not_refundable", "구독 크레딧은 환불 대상이 아니에요.", 400)
        if b["status"] != "active":
            raise CreditError("not_refundable_status", "환불할 수 없는 상태예요.", 409)
        if b["remaining_credits"] != b["initial_credits"]:
            raise CreditError("partially_used", "이미 사용한 구매건은 환불할 수 없어요.", 409)
        if not b["within_window"]:
            raise CreditError("refund_window_expired", "환불 가능 기간(7일)이 지났어요.", 409)
        if acct["reserved"] != 0:  # MVP 과보수적 (불변식 2)
            raise CreditError("in_flight_job", "진행 중인 생성이 끝난 뒤 다시 시도해 주세요.", 409)
        new_balance = acct["balance"] - b["remaining_credits"]
        await cur.execute(
            "update credit_sources set status = 'pending_refund' where id = %s", (b["id"],)
        )
        await cur.execute(
            "update credit_accounts set balance = %s where user_id = %s", (new_balance, user_id)
        )
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, metadata) values (%s,%s,'refund_request',%s,%s,%s,%s)",
            (user_id, b["id"], -b["remaining_credits"], new_balance,
             new_balance - acct["reserved"], Json({"reason": reason} if reason else {})),
        )
        await cur.execute(
            "insert into refund_requests (user_id, credit_source_id, status, reason) "
            "values (%s, %s, 'pending', %s) returning id::text as id",
            (user_id, b["id"], reason),
        )
        req = await cur.fetchone()
    return {"refundRequestId": req["id"], "creditSourceId": b["id"], "status": "pending",
            "credits": b["remaining_credits"], "available": new_balance - acct["reserved"]}


async def _load_refund_for_resolve(cur, request_id: str) -> tuple[dict, dict, dict]:
    """승인/거부 공통: 요청·계정·버킷을 FOR UPDATE 로드 + 종결 비가역 가드(불변식 4)."""
    await cur.execute(
        "select id::text as id, user_id::text as user_id, credit_source_id::text as credit_source_id, "
        "status from refund_requests where id = %s for update",
        (request_id,),
    )
    req = await cur.fetchone()
    if req is None:
        raise CreditError("refund_not_found", "환불 요청을 찾을 수 없어요.", 404)
    if req["status"] != "pending":  # 종결 요청 비가역
        raise CreditError("refund_not_pending", "이미 처리된 요청이에요.", 409)
    await cur.execute(
        "select balance, reserved from credit_accounts where user_id = %s for update", (req["user_id"],)
    )
    acct = await cur.fetchone()
    await cur.execute(
        "select id::text as id, status, remaining_credits, payment_id::text as payment_id "
        "from credit_sources where id = %s for update",
        (req["credit_source_id"],),
    )
    b = await cur.fetchone()
    if b is None or b["status"] != "pending_refund":  # pending_refund 버킷만 (refunded 재활성 차단)
        raise CreditError("bucket_not_pending", "버킷 상태가 올바르지 않아요.", 409)
    return req, acct, b


async def approve_refund(conn: AsyncConnection, *, request_id: str, resolved_by: str) -> dict:
    """관리자 승인(§3.4): 버킷 refunded(가용 재차감 없음 — 요청 시 이미 제외, 불변식 4) +
    delta=0 원장 마커 + payment refunded. 실제 환불은 PG 단계."""
    async with conn.cursor() as cur:
        req, acct, b = await _load_refund_for_resolve(cur, request_id)
        await cur.execute("update credit_sources set status = 'refunded' where id = %s", (b["id"],))
        if b["payment_id"]:
            await cur.execute(
                "update payment_history set status = 'refunded' where id = %s", (b["payment_id"],)
            )
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, metadata) values (%s,%s,'refund_approved',0,%s,%s,%s)",
            (req["user_id"], b["id"], acct["balance"], acct["balance"] - acct["reserved"],
             Json({"requestId": req["id"]})),
        )
        await cur.execute(
            "update refund_requests set status = 'approved', resolved_at = now(), resolved_by = %s "
            "where id = %s",
            (resolved_by, req["id"]),
        )
    return {"refundRequestId": req["id"], "status": "approved", "creditsRefunded": b["remaining_credits"]}


async def reject_refund(conn: AsyncConnection, *, request_id: str, resolved_by: str) -> dict:
    """관리자 거부(§3.4): 버킷 active 복귀 + 가용 복원(balance += remaining) + 원장 행."""
    async with conn.cursor() as cur:
        req, acct, b = await _load_refund_for_resolve(cur, request_id)
        new_balance = acct["balance"] + b["remaining_credits"]
        await cur.execute("update credit_sources set status = 'active' where id = %s", (b["id"],))
        await cur.execute(
            "update credit_accounts set balance = %s where user_id = %s", (new_balance, req["user_id"])
        )
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, metadata) values (%s,%s,'refund_rejected',%s,%s,%s,%s)",
            (req["user_id"], b["id"], b["remaining_credits"], new_balance,
             new_balance - acct["reserved"], Json({"requestId": req["id"]})),
        )
        await cur.execute(
            "update refund_requests set status = 'rejected', resolved_at = now(), resolved_by = %s "
            "where id = %s",
            (resolved_by, req["id"]),
        )
    return {"refundRequestId": req["id"], "status": "rejected", "creditsRestored": b["remaining_credits"]}


async def list_pricing_plans(conn: AsyncConnection) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, code, kind, name, credits, price, billing_period, sort_order "
            "from pricing_plans where is_active order by sort_order"
        )
        return await cur.fetchall()


async def list_credit_sources(conn: AsyncConnection, user_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, source_type, status, initial_credits, remaining_credits, "
            "period_end, created_at, plan_id::text as plan_id "
            "from credit_sources where user_id = %s order by created_at desc",
            (user_id,),
        )
        return await cur.fetchall()


async def list_credit_history(conn: AsyncConnection, user_id: str, limit: int = 500) -> list[dict]:
    """사용 내역(§6) — 프론트가 project_id로 묶고 펼쳐 세부 표시. 최신순."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, project_id::text as project_id, job_id::text as job_id, "
            "credit_source_id::text as credit_source_id, action_key, delta, balance_after, "
            "available_after, created_at from credit_ledger "
            "where user_id = %s order by created_at desc limit %s",
            (user_id, limit),
        )
        return await cur.fetchall()


# ---------- 컷 생성 job 종결 + wardrobe (ADR-0004 — kind='editor_image') ----------


async def finalize_cut_success(
    conn: AsyncConnection,
    *,
    job_id: str,
    lease_token: str,
    user_id: str,
    project_id: str,
    image: dict,  # {asset_id, bucket, key, mime, size, width, height}
    color_id: str | None,
    cut_type: str,
    reserved: int,
    charge: int,
    metadata: dict,
) -> dict | None:
    """컷 생성 성공 종결(원자·lease 펜스) — asset + wardrobe_images 기록 + 크레딧 확정.
    None = lease 상실(복구·재클레임) → 아무것도 쓰지 않음. envelope shape은 마네킹과 동일(§6)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return None
        await cur.execute(
            "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
            "r2_key, mime_type, byte_size, width, height) "
            "values (%s, %s, %s, 'ai', 'private', %s, %s, %s, %s, %s, %s)",
            (image["asset_id"], user_id, project_id, image["bucket"], image["key"],
             image["mime"], image.get("size"), image.get("width"), image.get("height")),
        )
        await cur.execute(
            "select coalesce(max(sort_order), 0) + 1 as so from wardrobe_images where project_id = %s",
            (project_id,),
        )
        sort_order = (await cur.fetchone())["so"]
        await cur.execute(
            "insert into wardrobe_images (project_id, color_id, asset_id, ai, cut_type, sort_order) "
            "values (%s, %s, %s, true, %s, %s) returning id::text as id",
            (project_id, color_id, image["asset_id"], cut_type, sort_order),
        )
        wardrobe_id = (await cur.fetchone())["id"]
    wardrobe = {  # WardrobeImage shape (계약 §3.6) — src는 안정 앱 URL
        "id": wardrobe_id,
        "src": f"/v1/assets/{image['asset_id']}/file",
        "ai": True, "cutType": cut_type, "colorId": color_id,
    }
    available = await _consume_buckets(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=charge, action_key="editorImage", metadata=metadata,
    )
    envelope = {"data": wardrobe, "credits": available, "creditsCharged": charge}
    async with conn.cursor() as cur:
        await cur.execute(
            "update jobs set status = 'done', result = %s, credits_charged = %s, progress = 100, "
            "locked_by = null, locked_at = null, finished_at = now() where id = %s",
            (Json(envelope), charge, job_id),
        )
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
            (job_id, Json(envelope)),
        )
    return {"wardrobe": wardrobe, "available": available}


async def finalize_cut_failure(
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
    """컷 생성 실패 종결 — 예약 해제 + job error + error 이벤트 (마네킹 실패와 동형)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from jobs where id = %s and locked_by = %s and status = 'running' for update",
            (job_id, lease_token),
        )
        if await cur.fetchone() is None:
            return False
    await _settle_credits(
        conn, user_id=user_id, project_id=project_id, job_id=job_id, reserved=reserved,
        charge=0, action_key="editorImage.release", settle_key=settle_key, metadata=metadata,
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "update jobs set status = 'error', error_message = %s, "
            "locked_by = null, locked_at = null, finished_at = now() where id = %s",
            (message, job_id),
        )
        await cur.execute(
            "insert into job_events (job_id, event_type, payload) values (%s, 'error', %s)",
            (job_id, Json({"code": code, "message": message})),
        )
    return True


async def list_wardrobe(conn: AsyncConnection, user_id: str, project_id: str) -> list[dict]:
    """프로젝트의 생성/업로드 이미지 목록 (계약 §3.6) — 소유 프로젝트만, sort_order 순."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select w.id::text as id, w.color_id, w.cut_type, w.ai, w.asset_id::text as asset_id "
            "from wardrobe_images w join projects p on p.id = w.project_id "
            "where w.project_id = %s and p.user_id = %s and w.deleted_at is null "
            "order by w.sort_order, w.created_at",
            (project_id, user_id),
        )
        rows = await cur.fetchall()
    return [
        {"id": r["id"], "src": f"/v1/assets/{r['asset_id']}/file",
         "ai": bool(r["ai"]), "cutType": r["cut_type"], "colorId": r["color_id"]}
        for r in rows
    ]
