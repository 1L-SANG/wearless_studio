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
  -- 'pending 인 채 2분 넘은 행'을 미발송으로 보는 규칙은 목록 라우트(admin_list_applications)의
  -- lateral 과 같아야 한다. 두 곳이 갈라지면 대시보드 숫자와 배지가 서로 다른 말을 한다.
  (select count(distinct application_id) from fm_model_application_emails
     where status = 'failed'
        or (status = 'pending' and created_at < now() - interval '2 minutes')) as email_failed,
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
        "period": {"days": days, "from": from_ts.isoformat()},
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


MODEL_STATUSES = ("pending", "verified", "suspended")
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
