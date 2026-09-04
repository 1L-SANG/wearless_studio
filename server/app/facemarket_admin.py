"""관리자 콘솔 전용 라우터 — 집계·모델 조회·권한 관리.

지원서 라우트는 facemarket_applications.py 에 그대로 둔다(그 도메인과 함께 산다).
여기 있는 것은 "콘솔이라서 필요한" 것들이다.

집계는 롤업 테이블 없이 라이브 SQL 이다. 지금 규모(각 테이블 수백~수천 행)에서는 충분하고,
숫자가 항상 진짜다. 대신 새로고침 연타가 DB 를 두드리지 않게 30초 프로세스 캐시를 둔다 —
태스크가 여러 개면 태스크마다 따로 캐시되지만, 30초짜리 불일치는 무해하다.
"""
import time
from datetime import datetime, time as dtime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import admin_guard
from .auth import require_user
from .db import get_conn
from .models import CamelModel

router = APIRouter(prefix="/v1/facemarket/admin", tags=["FaceMarket admin console"])

KST = timezone(timedelta(hours=9))
ALLOWED_PERIODS = (7, 30, 90)
OVERVIEW_TTL_SECONDS = 30

_OVERVIEW_CACHE: dict[int, tuple[float, dict]] = {}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def validate_days(days: int) -> int:
    if days not in ALLOWED_PERIODS:
        raise _err("invalid_period", "기간은 7·30·90일만 볼 수 있어요.")
    return days


def _period_start(days: int) -> datetime:
    """KST 오늘을 포함해 days 일 전 자정(KST) — UTC aware 로 돌려준다.

    운영자가 말하는 '오늘'은 서울 기준이다. UTC 로 자르면 오전 9시 전에 만든 지원서가
    어제로 잡혀 대시보드와 사람의 기억이 어긋난다.
    """
    today = datetime.now(KST).date()
    start = datetime.combine(today - timedelta(days=days - 1), dtime.min, tzinfo=KST)
    return start.astimezone(timezone.utc)


QUEUE_SQL = """
select
  (select count(*) from fm_model_applications where status = 'under_review')
    as applications_under_review,
  (select count(*) from fm_model_applications
     where status = 'under_review' and identity_mismatch_count > 0) as identity_mismatch,
  -- admin_resend_email 은 실패한 행을 고치지 않고 새 행을 INSERT 한다. 그래서 "이력 전체에서
  -- failed 가 한 번이라도 있었는지"로 세는 옛 방식으로는, 첫 발송이 실패하고 재발송이 성공한
  -- 지원서를 목록 배지(admin_list_applications 의 lateral, 최신 행만 봄)는 '발송됨'이라 하는데
  -- 이 큐는 영원히 센다 — 고쳐도 안 줄어드는 큐는 없는 큐보다 나쁘다. 그래서 여기도 신청서당
  -- 최신 메일 행만 lateral 로 보고, 그 행이 실패(또는 2분 넘은 pending)일 때만 센다. 목록의
  -- 규칙과 한 글자도 다르면 안 된다.
  (select count(*) from fm_model_applications a
     join lateral (
       select case when status = 'pending' and created_at < now() - interval '2 minutes'
                   then 'failed' else status end as last_status
       from fm_model_application_emails e
       where e.application_id = a.id order by e.created_at desc limit 1
     ) em on true
    where em.last_status = 'failed') as email_failed,
  (select count(*) from refund_requests where status = 'pending') as refunds_pending
"""

KPI_SQL = """
select
  (select count(*) from fm_model_applications where created_at >= %(from_ts)s)
    as applications_submitted,
  (select count(*) from fm_model_applications
     where status = 'approved' and reviewed_at >= %(from_ts)s) as applications_approved,
  (select count(*) from fm_model_applications
     where status = 'rejected' and reviewed_at >= %(from_ts)s) as applications_rejected,
  (select count(*) from fm_licenses where created_at >= %(from_ts)s) as licenses_issued,
  (select coalesce(sum(total_amount), 0) from fm_settlements
     where chain_status = 'confirmed' and created_at >= %(from_ts)s
       and payment_id not like 'sim:%%') as settlement_amount_krw,
  (select count(*) from fm_settlements
     where chain_status = 'failed' and created_at >= %(from_ts)s
       and payment_id not like 'sim:%%') as settlement_failed,
  (select coalesce(sum(amount), 0) from payment_history
     where status = 'paid' and provider <> 'test'
       and created_at >= %(from_ts)s) as credit_revenue_krw
"""

SERIES_SQL = """
with days as (
  select generate_series(
    (now() at time zone 'Asia/Seoul')::date - (%(days)s::int - 1),
    (now() at time zone 'Asia/Seoul')::date,
    interval '1 day'
  )::date as d
)
select d::text as date,
  (select count(*) from fm_model_applications a
     where (a.created_at at time zone 'Asia/Seoul')::date = d) as applications,
  (select count(*) from fm_licenses l
     where (l.created_at at time zone 'Asia/Seoul')::date = d) as licenses,
  (select coalesce(sum(s.total_amount), 0) from fm_settlements s
     where s.chain_status = 'confirmed' and s.payment_id not like 'sim:%%'
       and (s.created_at at time zone 'Asia/Seoul')::date = d) as settlement_amount_krw
from days order by d
"""

DISTRIBUTION_SQL = """
select
  (select count(*) from fm_models where status = 'pending') as models_pending,
  (select count(*) from fm_models where status = 'verified') as models_verified,
  (select count(*) from fm_models where status = 'suspended') as models_suspended,
  (select count(*) from fm_biometric_enrollments where status = 'passed') as enrollments_passed,
  (select count(*) from fm_biometric_enrollments
     where status in ('failed', 'cancelled', 'expired')) as enrollments_failed,
  (select count(*) from fm_biometric_enrollments
     where status not in ('passed', 'failed', 'cancelled', 'expired')) as enrollments_in_flight
"""


async def build_overview(conn, *, days: int) -> dict:
    from_ts = _period_start(days)
    # from 과 함께 한 번만 찍는다 — 캐시된 응답을 보는 클라이언트는 자기 시계로 끝 경계를
    # 못 구한다(30초 캐시라 응답을 받은 시각과 실제로 만들어진 시각이 다르다). 서버가
    # "이 숫자들이 어느 순간을 설명하는지"를 직접 말해줘야 한다.
    to_ts = datetime.now(timezone.utc)
    async with conn.cursor() as cur:
        await cur.execute(QUEUE_SQL)
        queue = await cur.fetchone() or {}
        await cur.execute(KPI_SQL, {"from_ts": from_ts})
        kpi = await cur.fetchone() or {}
        await cur.execute(SERIES_SQL, {"days": days})
        series = await cur.fetchall() or []
        await cur.execute(DISTRIBUTION_SQL)
        dist = await cur.fetchone() or {}

    return {
        "queue": {
            "applicationsUnderReview": queue.get("applications_under_review", 0),
            "identityMismatch": queue.get("identity_mismatch", 0),
            "emailFailed": queue.get("email_failed", 0),
            "refundsPending": queue.get("refunds_pending", 0),
        },
        "period": {"days": days, "from": from_ts.isoformat(), "to": to_ts.isoformat()},
        "kpi": {
            "applicationsSubmitted": kpi.get("applications_submitted", 0),
            "applicationsApproved": kpi.get("applications_approved", 0),
            "applicationsRejected": kpi.get("applications_rejected", 0),
            "licensesIssued": kpi.get("licenses_issued", 0),
            "settlementAmountKrw": int(kpi.get("settlement_amount_krw", 0)),
            "settlementFailed": kpi.get("settlement_failed", 0),
            "creditRevenueKrw": int(kpi.get("credit_revenue_krw", 0)),
        },
        "series": [
            {
                "date": row["date"],
                "applications": row["applications"],
                "licenses": row["licenses"],
                "settlementAmountKrw": int(row["settlement_amount_krw"]),
            }
            for row in series
        ],
        "distribution": {
            "models": {
                "pending": dist.get("models_pending", 0),
                "verified": dist.get("models_verified", 0),
                "suspended": dist.get("models_suspended", 0),
            },
            "enrollments": {
                "passed": dist.get("enrollments_passed", 0),
                "failed": dist.get("enrollments_failed", 0),
                "inFlight": dist.get("enrollments_in_flight", 0),
            },
        },
    }


def clear_overview_cache() -> None:
    _OVERVIEW_CACHE.clear()


async def overview_payload(conn, *, days: int) -> dict:
    hit = _OVERVIEW_CACHE.get(days)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    payload = await build_overview(conn, days=days)
    _OVERVIEW_CACHE[days] = (now + OVERVIEW_TTL_SECONDS, payload)
    return payload


@router.get("/overview")
async def admin_overview(
    request: Request,
    days: int = Query(30),
    user_id: str = Depends(require_user),
):
    """콘솔 첫 화면 한 벌 — 운영 큐 + 기간 KPI + 추이 + 분포. 호출 1회."""
    validate_days(days)
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        payload = await overview_payload(conn, days=days)
    return JSONResponse(payload)


# fm_models_status_check(supabase/migrations/20260821010100_facemarket_biometric_runtime.sql)
# 가 실제로 허용하는 값 전체 — reverification_required 는 생체 재검증 대기 중인 모델이고
# facemarket_cutover.py 가 실제로 이 값을 쓴다. 여기서 빠지면 필터가 실재하는 상태를 400 으로
# 걷어차고, 정지 해제의 복원 화이트리스트도 이 값을 못 돌려줘 verified 처럼 조용히 pending 으로
# 깎인다.
MODEL_STATUSES = ("pending", "verified", "suspended", "reverification_required")
# 정지 해제가 복원해도 되는 목표 상태 — MODEL_STATUSES 에서 suspended 를 뺀 값이다
# ("정지 직전"이 다시 suspended 일 수는 없다). verified 창조 금지 규칙은 "콘솔이 새로
# verified 를 만드는 것"에 걸리는 규칙이라, 원장에 남은 값을 그대로 돌려주는 이 복원에는
# 걸리지 않는다.
RESTORABLE_MODEL_STATUSES = ("pending", "verified", "reverification_required")
MAX_LIST_LIMIT = 200


def validate_model_status(status: str | None) -> str | None:
    if status is not None and status not in MODEL_STATUSES:
        raise _err("invalid_status", "상태 필터가 올바르지 않습니다.")
    return status


LIST_MODELS_SQL = """
select m.id::text as id, m.display_name, m.status, m.created_at,
       u.email as email,
       (select count(*) from fm_licenses l where l.model_id = m.id) as license_count,
       (select max(s.created_at) from fm_settlements s
          join fm_licenses l2 on l2.id = s.license_id
         where l2.model_id = m.id) as last_settlement_at
from fm_models m
-- left join: 플랫폼 대행 온보딩은 user_id 가 null 이다(fm_models 주석). inner 로 묶으면
-- 그런 모델이 목록에서 통째로 사라져, 없는 걸 없다고 착각한다.
left join auth.users u on u.id = m.user_id
where (%(status)s::text is null or m.status = %(status)s)
  and (
    %(q)s::text is null
    or m.display_name ilike %(q_like)s
    or u.email = %(q)s
  )
order by m.created_at desc
limit %(limit)s
"""


def _model_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "displayName": row["display_name"],
        "status": row["status"],
        "email": row.get("email"),
        "licenseCount": row.get("license_count", 0),
        "lastSettlementAt": row["last_settlement_at"].isoformat() if row.get("last_settlement_at") else None,
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def list_models(conn, *, q: str | None, status: str | None, limit: int) -> dict:
    capped = max(1, min(limit, MAX_LIST_LIMIT))
    term = (q or "").strip() or None
    async with conn.cursor() as cur:
        await cur.execute(LIST_MODELS_SQL, {
            "status": status, "q": term, "q_like": f"%{term}%" if term else None, "limit": capped,
        })
        rows = await cur.fetchall() or []
    return {"items": [_model_row(r) for r in rows]}


# LIST_MODELS_SQL 을 문자열로 잘라 재사용하지 않는다 — 그 SQL 의 첫 where 는 서브쿼리
# (select count(*) from fm_licenses l where ...) 안에 있어서, split("where")[0] 은 본문
# where 가 아니라 서브쿼리 중간에서 잘린다. 전문을 따로 적는다.
DETAIL_MODEL_SQL = """
select m.id::text as id, m.display_name, m.status, m.created_at,
       u.email as email,
       (select count(*) from fm_licenses l where l.model_id = m.id) as license_count,
       (select max(s.created_at) from fm_settlements s
          join fm_licenses l2 on l2.id = s.license_id
         where l2.model_id = m.id) as last_settlement_at
from fm_models m
left join auth.users u on u.id = m.user_id
where m.id = %(model_id)s
"""

DETAIL_LICENSES_SQL = """
select id::text as id, status, unit_price, license_valid_until, vc_id
from fm_licenses where model_id = %(model_id)s order by created_at desc
"""

DETAIL_SETTLEMENTS_SQL = """
select s.id::text as id, s.total_amount, s.chain_status, s.tx_hash, s.created_at
from fm_settlements s join fm_licenses l on l.id = s.license_id
where l.model_id = %(model_id)s order by s.created_at desc limit 10
"""

DETAIL_ENROLLMENT_SQL = """
select id::text as id, status, completed_at
from fm_biometric_enrollments where model_id = %(model_id)s
order by created_at desc limit 1
"""


async def model_detail(conn, *, model_id: str) -> dict:
    params = {"model_id": model_id}
    async with conn.cursor() as cur:
        await cur.execute(DETAIL_MODEL_SQL, params)
        model = await cur.fetchone()
        if model is None:
            raise _err("not_found", "모델을 찾을 수 없어요.", status=404)
        await cur.execute(DETAIL_LICENSES_SQL, params)
        licenses = await cur.fetchall() or []
        await cur.execute(DETAIL_SETTLEMENTS_SQL, params)
        settlements = await cur.fetchall() or []
        await cur.execute(DETAIL_ENROLLMENT_SQL, params)
        enrollment = await cur.fetchone()

    return {
        "model": _model_row(model),
        "licenses": [
            {
                "id": r["id"], "status": r["status"], "unitPrice": r["unit_price"],
                "validUntil": r["license_valid_until"].isoformat() if r.get("license_valid_until") else None,
                "vcId": r.get("vc_id"),
            }
            for r in licenses
        ],
        "settlements": [
            {
                "id": r["id"], "totalAmount": int(r["total_amount"]), "chainStatus": r["chain_status"],
                "txHash": r.get("tx_hash"),
                "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in settlements
        ],
        "enrollment": (
            {
                "id": enrollment["id"], "status": enrollment["status"],
                "completedAt": enrollment["completed_at"].isoformat() if enrollment.get("completed_at") else None,
            }
            if enrollment else None
        ),
    }


@router.get("/models")
async def admin_list_models(
    request: Request,
    q: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50),
    user_id: str = Depends(require_user),
):
    validate_model_status(status)
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_models(conn, q=q, status=status, limit=limit))


@router.get("/models/{model_id}")
async def admin_model_detail(
    request: Request, model_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await model_detail(conn, model_id=model_id))


class SuspendRequest(CamelModel):
    reason: str


async def _model_status(cur, model_id: str) -> str:
    await cur.execute("select status from fm_models where id = %s", (model_id,))
    row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "모델을 찾을 수 없어요.", status=404)
    return row["status"]


async def suspend_model(conn, *, model_id: str, actor: str, reason: str) -> dict:
    note = (reason or "").strip()
    if not note:
        raise _err("reason_required", "정지 사유를 입력해 주세요.")
    async with conn.cursor() as cur:
        previous = await _model_status(cur, model_id)
        # 이미 정지된 모델을 또 정지시키면 이번 read 의 previous 가 'suspended' 가 되어,
        # 감사 원장에 남을 before.status 가 진짜 이전 상태(예: verified)를 덮어써 버린다.
        # 그러면 해제할 때 복원할 값 자체가 사라진다 — 여기서 미리 막는다.
        if previous == "suspended":
            raise _err("already_suspended", "이미 정지된 모델이에요.", status=409)
        # 가드 UPDATE — where 에 방금 읽은 이전 상태를 그대로 건다(admin_approve_application
        # 과 같은 낙관적 동시성 모양). 그 사이 다른 요청이 상태를 바꿨으면(동시 정지) 0-row 가
        # 되어 충돌로 걸린다. 안 걸면 두 요청 다 "성공"한 것처럼 보이면서 감사 원장의 before
        # 중 하나는 거짓이 되고, 그 거짓 값이 나중에 해제가 복원할 상태가 되어 버린다.
        await cur.execute(
            "update fm_models set status = 'suspended', updated_at = now() "
            "where id = %s and status = %s returning 1",
            (model_id, previous),
        )
        if await cur.fetchone() is None:
            raise _err("already_suspended", "이미 정지된 모델이에요.", status=409)
    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="model.suspend",
        target_type="model",
        target_id=model_id,
        before={"status": previous},
        after={"status": "suspended"},
        note=note,
    )
    return {"id": model_id, "status": "suspended"}


async def unsuspend_model(conn, *, model_id: str, actor: str) -> dict:
    """정지 직전 상태로 되돌린다.

    콘솔은 verified 를 **새로 만들지 못한다** — 그 배지는 생체등록 통과가 붙이는 것이다.
    다만 정지 한 번으로 검증된 모델이 배지를 영구히 잃는 것도 틀렸다. 그래서 원장에 남은
    정지 직전 값만 복원한다. 기록이 없으면(콘솔 밖에서 정지된 경우) pending 으로 내린다.
    """
    async with conn.cursor() as cur:
        current = await _model_status(cur, model_id)
        if current != "suspended":
            raise _err("not_suspended", "정지 상태인 모델만 해제할 수 있어요.")
        await cur.execute(
            "select before->>'status' as prev from admin_audit_log "
            "where action = 'model.suspend' and target_type = 'model' and target_id = %s "
            "order by created_at desc limit 1",
            (model_id,),
        )
        row = await cur.fetchone()
        restored = (row or {}).get("prev")
        # 원장 값이 오염됐거나 기록이 없으면 pending — 스키마 밖 값을 넣으면 check 제약이
        # 터진다. reverification_required 도 정지 직전 값일 수 있다(생체 재검증 대기 중
        # 정지된 모델) — 원장에 있던 값을 그대로 되돌리는 것뿐이라 verified 창조 금지 규칙에
        # 걸리지 않는다.
        if restored not in RESTORABLE_MODEL_STATUSES:
            restored = "pending"
        # 가드 UPDATE — suspend 와 같은 이유다. 방금 확인한 'suspended' 를 where 에 다시
        # 건다: 그 사이 다른 요청이 먼저 해제했으면(동시 해제) 0-row 로 걸려 조용한 이중
        # 성공을 막는다.
        await cur.execute(
            "update fm_models set status = %s, updated_at = now() "
            "where id = %s and status = 'suspended' returning 1",
            (restored, model_id),
        )
        if await cur.fetchone() is None:
            raise _err("not_suspended", "정지 상태인 모델만 해제할 수 있어요.")
    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="model.unsuspend",
        target_type="model",
        target_id=model_id,
        before={"status": "suspended"},
        after={"status": restored},
    )
    return {"id": model_id, "status": restored}


@router.post("/models/{model_id}/suspend")
async def admin_suspend_model(
    request: Request, model_id: str, body: SuspendRequest,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await suspend_model(conn, model_id=model_id, actor=user_id, reason=body.reason)
        await conn.commit()
    return JSONResponse(result)


@router.post("/models/{model_id}/unsuspend")
async def admin_unsuspend_model(
    request: Request, model_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await unsuspend_model(conn, model_id=model_id, actor=user_id)
        await conn.commit()
    return JSONResponse(result)


ROLES = ("admin", "user")


class RoleRequest(CamelModel):
    role: str


LIST_ADMINS_SQL = """
select p.user_id::text as user_id, p.display_name, p.role, u.email
from profiles p left join auth.users u on u.id = p.user_id
where p.role = 'admin' order by p.created_at
"""

SEARCH_USER_SQL = """
select p.user_id::text as user_id, p.display_name, p.role, u.email
from profiles p join auth.users u on u.id = p.user_id
where u.email = %(email)s limit 5
"""


def _staff_row(row: dict) -> dict:
    return {
        "userId": row["user_id"],
        "email": row.get("email"),
        "displayName": row.get("display_name"),
        "role": row.get("role") or "user",
    }


async def list_staff(conn, *, q: str | None) -> dict:
    """현재 관리자 + (이메일 정확일치) 검색 결과.

    검색을 부분일치로 열면 콘솔이 이메일 스캐너가 된다 — 관리자라도 가입자 목록을
    훑을 이유는 없다. 승격은 이미 이메일을 아는 사람에게 하는 일이다.
    """
    email = (q or "").strip().lower() or None
    async with conn.cursor() as cur:
        await cur.execute(LIST_ADMINS_SQL)
        admins = await cur.fetchall() or []
        matches = []
        if email:
            await cur.execute(SEARCH_USER_SQL, {"email": email})
            matches = await cur.fetchall() or []
    return {
        "admins": [_staff_row(r) for r in admins],
        "matches": [_staff_row(r) for r in matches],
    }


async def set_role(conn, *, target_user_id: str, actor: str, role: str) -> dict:
    if role not in ROLES:
        raise _err("invalid_role", "역할 값이 올바르지 않습니다.")
    # 가드 1: 자기 강등 금지 — 되돌릴 방법이 콘솔에 없다(DB 직접 UPDATE 뿐).
    if role == "user" and target_user_id == actor:
        raise _err("cannot_demote_self", "자기 자신의 관리자 권한은 내릴 수 없어요.")

    async with conn.cursor() as cur:
        # 관리자 전원 + 대상 행을 한 쿼리로, user_id 순으로 정렬해 한 번에 잠근다.
        # 예전에는 대상 행을 먼저 잠그고(강등일 때만) 관리자 집합을 그 다음에 잠갔다 —
        # A 가 B 를, 동시에 B 가 A 를 내리면 각자 상대의 대상 행을 쥔 채 상대가 쥔
        # 관리자-집합 잠금을 기다리게 되어 순환이 생기고, Postgres 가 한 쪽을 데드락으로
        # 죽인다. "관리자 0명 방지" 불변식 자체는 지켜지지만, 죽는 쪽은 의도한 last_admin
        # 400 이 아니라 처리 안 된 500 을 받는다 — 무슨 일이 있었는지, 처리가 됐는지조차
        # 알 수 없다. 필요한 행을 전부 같은 순서로 한 번에 잠그면 동시 트랜잭션들은 죽지
        # 않고 그 순서대로 줄을 선다.
        await cur.execute(
            "select user_id::text as user_id, role from profiles "
            "where role = 'admin' or user_id = %s order by user_id for update",
            (target_user_id,),
        )
        rows = await cur.fetchall() or []
        target_row = next((r for r in rows if r["user_id"] == target_user_id), None)
        # 가드 3: 미가입 계정 승격 금지 — 초대 흐름을 만들지 않기로 했다(설계 §4.1).
        if target_row is None:
            raise _err("user_not_found", "가입된 계정을 찾을 수 없어요.", status=404)
        previous = target_row.get("role") or "user"

        # 가드 2: 최후 관리자 강등 금지. 위 쿼리가 이미 관리자 전원을 잠갔으니 그 결과
        # 집합에서 그대로 센다 — 별도 count 쿼리를 다시 던지면 그 쿼리가 또 다른 잠금
        # 순서를 만들어 데드락 위험이 되돌아온다.
        if role == "user" and previous == "admin":
            admin_count = sum(1 for r in rows if r.get("role") == "admin")
            if admin_count <= 1:
                raise _err("last_admin", "마지막 관리자는 내릴 수 없어요.")

        await cur.execute(
            "update profiles set role = %s, updated_at = now() where user_id = %s",
            (role, target_user_id),
        )

    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="staff.role.grant" if role == "admin" else "staff.role.revoke",
        target_type="user",
        target_id=target_user_id,
        before={"role": previous},
        after={"role": role},
    )
    return {"userId": target_user_id, "role": role}


LIST_AUDIT_SQL = """
select l.id::text as id, l.action, l.target_type, l.target_id, l.note, l.created_at,
       l.actor_user_id::text as actor_user_id, u.email as actor_email
from admin_audit_log l left join auth.users u on u.id = l.actor_user_id
where (%(target_type)s::text is null or l.target_type = %(target_type)s)
  and (%(target_id)s::text is null or l.target_id = %(target_id)s)
order by l.created_at desc
limit %(limit)s
"""


async def list_audit(conn, *, limit: int, target_type: str | None, target_id: str | None) -> dict:
    capped = max(1, min(limit, MAX_LIST_LIMIT))
    async with conn.cursor() as cur:
        await cur.execute(LIST_AUDIT_SQL, {
            "limit": capped, "target_type": target_type, "target_id": target_id,
        })
        rows = await cur.fetchall() or []
    return {
        "items": [
            {
                "id": r["id"], "action": r["action"], "targetType": r["target_type"],
                "targetId": r.get("target_id"), "note": r.get("note"),
                "actorEmail": r.get("actor_email"),
                "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
    }


@router.get("/staff")
async def admin_list_staff(
    request: Request, q: str | None = Query(None), user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_staff(conn, q=q))


@router.post("/staff/{target_user_id}/role")
async def admin_set_role(
    request: Request, target_user_id: str, body: RoleRequest,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await set_role(conn, target_user_id=target_user_id, actor=user_id, role=body.role)
        await conn.commit()
    return JSONResponse(result)


@router.get("/audit")
async def admin_list_audit(
    request: Request,
    limit: int = Query(20),
    target_type: str | None = Query(None, alias="targetType"),
    target_id: str | None = Query(None, alias="targetId"),
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_audit(
            conn, limit=limit, target_type=target_type, target_id=target_id,
        ))
