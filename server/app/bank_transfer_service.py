"""계좌이체(무통장입금) 신청·확인·만료의 DB 로직.

지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md (§10 결정 우선).
라우트(bank_transfer.py, bank_transfer_admin.py)와 워커(workers/bank_transfer_expirer.py)가
같이 쓴다. **커밋은 호출자가 한다.** 여기서는 트랜잭션을 열거나 닫지 않는다.

돈을 다루므로 지키는 것:
  ① 지급은 언제나 신청 행의 스냅샷(amount·credits)으로 한다. 확인 시점 카탈로그를 다시 읽지 않는다.
  ② 신청 ID 가 지급의 유일 식별자다. 이미 paid 인 신청을 다시 확인하면 저장된 결과를 그대로 돌려주고
     아무것도 다시 하지 않는다.
  ③ 사용자 단위 직렬화는 credit_accounts 행 잠금이다(기존 지급 함수가 잠그는 바로 그 행).
  ④ 토스 구독(active·past_due·canceled)과 수동 이용권은 동시에 갖지 못한다.
  ⑤ 다른 플랜으로의 중도 교체는 받지 않는다(1차). 같은 플랜 연장만 허용한다.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import repo
from .repo import CreditError

log = logging.getLogger("wearless.bank_transfer")
_SEOUL = ZoneInfo("Asia/Seoul")

REQUEST_TTL_DAYS = 3
#: 이 상태의 토스 구독이 있으면 수동 구독 이용권을 신청·지급하지 않는다. canceled 는 아직
#: 주기가 남아 있는 상태라(만료 워커가 ended 로 바꾸기 전) 포함한다.
TOSS_BLOCKING_STATUSES = ("active", "past_due", "canceled")
_EXPIRE_BATCH = 50

_REQUEST_COLUMNS = (
    "id::text as id, user_id::text as user_id, plan_code, kind, amount, credits, payer_name, phone, "
    "tax_invoice, business_no, business_name, representative_name, invoice_email, note, status, "
    "expires_at, paid_at, confirmed_at, admin_note, payment_id::text as payment_id, "
    "credit_source_id::text as credit_source_id, manual_plan_grant_id::text as manual_plan_grant_id, "
    "created_at"
)


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def request_to_json(row: dict, *, admin: bool = False) -> dict:
    """사용자용 응답에는 관리자 메모·확인자를 내보내지 않는다."""
    out = {
        "id": row["id"],
        "planCode": row["plan_code"],
        "kind": row["kind"],
        "amount": row["amount"],
        "credits": row["credits"],
        "payerName": row["payer_name"],
        "phone": row.get("phone"),
        "taxInvoice": bool(row.get("tax_invoice")),
        "businessNo": row.get("business_no"),
        "businessName": row.get("business_name"),
        "representativeName": row.get("representative_name"),
        "invoiceEmail": row.get("invoice_email"),
        "note": row.get("note"),
        "status": row["status"],
        "expiresAt": _iso(row.get("expires_at")),
        "paidAt": _iso(row.get("paid_at")),
        "confirmedAt": _iso(row.get("confirmed_at")),
        "createdAt": _iso(row.get("created_at")),
    }
    if admin:
        out.update({
            "userId": row.get("user_id"),
            "email": row.get("email"),
            "displayName": row.get("display_name"),
            "planName": row.get("plan_name"),
            "adminNote": row.get("admin_note"),
            "paymentId": row.get("payment_id"),
            "creditSourceId": row.get("credit_source_id"),
            "manualPlanGrantId": row.get("manual_plan_grant_id"),
        })
    return out


async def _lock_account(cur, user_id: str) -> dict:
    await cur.execute(
        "select balance, reserved from credit_accounts where user_id = %s for update", (user_id,)
    )
    acct = await cur.fetchone()
    if acct is None:
        raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
    return acct


async def _toss_subscription_status(cur, user_id: str) -> str | None:
    await cur.execute("select status from subscriptions where user_id = %s", (user_id,))
    row = await cur.fetchone()
    return row["status"] if row else None


async def _active_manual_grant(cur, user_id: str, *, lock: bool = False) -> dict | None:
    await cur.execute(
        "select id::text as id, plan_code, starts_at, ends_at from manual_plan_grants "
        "where user_id = %s and status = 'active'" + (" for update" if lock else ""),
        (user_id,),
    )
    return await cur.fetchone()


def _plan_change_error(grant: dict) -> CreditError:
    ends = grant.get("ends_at")
    # 컨테이너는 UTC 라 astimezone() 기본값이면 새벽에 끝나는 이용권이 하루 이른 날짜로 보인다.
    when = ends.astimezone(_SEOUL).strftime("%m/%d") if isinstance(ends, datetime) else str(ends)
    return CreditError(
        "plan_change_not_supported",
        f"지금 이용권({grant['plan_code']})이 끝난 뒤 다른 요금제를 신청할 수 있어요. 종료일 {when}",
        409,
    )


async def create_request(
    conn, *, user_id: str, plan_code: str, payer_name: str, phone: str | None,
    tax_invoice: bool, business_no: str | None, business_name: str | None,
    representative_name: str | None, invoice_email: str | None, note: str | None,
) -> dict:
    """신청 생성. 같은 종류의 기한 지난 requested 는 먼저 expired 로 닫는다.

    잠금 순서는 신청 행 → 계정이다. confirm_request 가 신청 → 이용권 → 계정 순이라, 계정을 먼저 잡은
    채 기한 지난 신청 행을 갱신하면 관리자가 마침 그 신청을 늦게 확인하는 순간 서로 교착한다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, kind, price, credits from pricing_plans "
            "where code = %s and is_active",
            (plan_code,),
        )
        plan = await cur.fetchone()
        if plan is None:
            raise CreditError("unknown_plan", "판매 중인 상품이 아니에요.", 404)
        await cur.execute(
            "update bank_transfer_requests set status = 'expired' "
            "where user_id = %s and kind = %s and status = 'requested' and expires_at <= now()",
            (user_id, plan["kind"]),
        )
        await _lock_account(cur, user_id)
        if plan["kind"] == "subscription":
            toss = await _toss_subscription_status(cur, user_id)
            if toss in TOSS_BLOCKING_STATUSES:
                raise CreditError(
                    "toss_subscription_active",
                    "정기결제 구독이 있는 계정이에요. 구독이 끝난 뒤 계좌이체 이용권을 신청할 수 있어요.",
                    409,
                )
            grant = await _active_manual_grant(cur, user_id)
            if grant is not None and grant["plan_code"] != plan_code:
                raise _plan_change_error(grant)
        await cur.execute(
            "select id::text as id from bank_transfer_requests "
            "where user_id = %s and kind = %s and status = 'requested'",
            (user_id, plan["kind"]),
        )
        if await cur.fetchone() is not None:
            raise CreditError("request_already_open", "확인 중인 신청이 있어요.", 409)
        await cur.execute(
            "insert into bank_transfer_requests (user_id, plan_code, kind, amount, credits, "
            "payer_name, phone, tax_invoice, business_no, business_name, representative_name, "
            "invoice_email, note, expires_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            f"now() + interval '{REQUEST_TTL_DAYS} days') "
            f"returning {_REQUEST_COLUMNS}",
            (user_id, plan_code, plan["kind"], plan["price"], plan["credits"], payer_name, phone,
             tax_invoice, business_no, business_name, representative_name, invoice_email, note),
        )
        return await cur.fetchone()


async def list_open_requests(conn, *, user_id: str) -> dict:
    """열린 신청(종류별 최대 1건)과 최근 14일 안에 닫힌 신청 1건."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_REQUEST_COLUMNS} from bank_transfer_requests "
            "where user_id = %s and status = 'requested' and expires_at > now() "
            "order by created_at desc",
            (user_id,),
        )
        open_rows = await cur.fetchall()
        await cur.execute(
            f"select {_REQUEST_COLUMNS} from bank_transfer_requests "
            "where user_id = %s and status in ('paid', 'rejected', 'expired') "
            "and created_at > now() - interval '14 days' "
            "order by coalesce(confirmed_at, expires_at) desc limit 1",
            (user_id,),
        )
        recent = await cur.fetchone()
    return {
        "open": [request_to_json(r) for r in open_rows],
        "recent": request_to_json(recent) if recent else None,
    }


async def cancel_request(conn, *, user_id: str, request_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "update bank_transfer_requests set status = 'canceled' "
            "where id = %s and user_id = %s and status = 'requested' "
            f"returning {_REQUEST_COLUMNS}",
            (request_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise CreditError("not_cancelable", "취소할 수 있는 신청이 없어요.", 409)
    return row


async def get_entitlement(conn, *, user_id: str) -> dict:
    """활성 수동 이용권. 없으면 {"active": False}."""
    async with conn.cursor() as cur:
        grant = await _active_manual_grant(cur, user_id)
    if grant is None:
        return {"active": False}
    summary = await repo.subscription_bucket_summary(conn, user_id)
    return {
        "active": True,
        "planCode": grant["plan_code"],
        "startsAt": _iso(grant.get("starts_at")),
        "endsAt": _iso(grant["ends_at"]),
        "credits": summary["credits"],
        "autoRenew": False,
    }


async def list_requests_admin(conn, *, status: str, limit: int) -> list[dict]:
    where = "" if status == "all" else "where r.status = %(status)s "
    async with conn.cursor() as cur:
        await cur.execute(
            "select r.id::text as id, r.user_id::text as user_id, u.email, p.display_name, "
            "pp.name as plan_name, r.plan_code, r.kind, r.amount, r.credits, r.payer_name, r.phone, "
            "r.tax_invoice, r.business_no, r.business_name, r.representative_name, r.invoice_email, "
            "r.note, r.status, r.expires_at, r.paid_at, r.confirmed_at, r.admin_note, "
            "r.payment_id::text as payment_id, r.credit_source_id::text as credit_source_id, "
            "r.manual_plan_grant_id::text as manual_plan_grant_id, r.created_at "
            "from bank_transfer_requests r "
            "left join profiles p on p.user_id = r.user_id "
            "left join auth.users u on u.id = r.user_id "
            "left join pricing_plans pp on pp.code = r.plan_code "
            f"{where}order by r.created_at desc limit %(limit)s",
            {"status": status, "limit": limit},
        )
        rows = await cur.fetchall()
    return [request_to_json(r, admin=True) for r in rows]


def _confirm_result(req: dict, *, credits: int, available: int, ends_at=None,
                    idempotent: bool = False) -> dict:
    out = {
        "requestId": req["id"],
        "status": "paid",
        "kind": req["kind"],
        "planCode": req["plan_code"],
        "credits": credits,
        "available": available,
        "paymentId": req.get("payment_id"),
        "creditSourceId": req.get("credit_source_id"),
        "manualPlanGrantId": req.get("manual_plan_grant_id"),
        "endsAt": _iso(ends_at),
    }
    if idempotent:
        out["idempotent"] = True
    return out


async def _grant_manual_plan(conn, *, req: dict, actor_user_id: str) -> dict:
    """구독 플랜 1개월 수동 이용권. 결제 기록·이용권·버킷·원장·등급이 한 트랜잭션이다."""
    user_id = req["user_id"]
    async with conn.cursor() as cur:
        toss = await _toss_subscription_status(cur, user_id)
        if toss in TOSS_BLOCKING_STATUSES:
            raise CreditError(
                "toss_subscription_active",
                "정기결제 구독이 있는 계정이에요. 구독이 끝난 뒤에 이용권을 줄 수 있어요.",
                409,
            )
        grant = await _active_manual_grant(cur, user_id, lock=True)
        if grant is not None and grant["plan_code"] != req["plan_code"]:
            raise _plan_change_error(grant)
        if grant is None:
            # 신규 시작 = 확인 시각부터 달력상 한 달
            await cur.execute("select now() + interval '1 month' as ends_at")
        else:
            # 같은 플랜 연장 = 기존 종료일부터 한 달
            await cur.execute(
                "select %s::timestamptz + interval '1 month' as ends_at", (grant["ends_at"],)
            )
        ends_at = (await cur.fetchone())["ends_at"]

    granted = await repo.grant_subscription(
        conn, user_id=user_id, plan_code=req["plan_code"], credits=req["credits"],
        metadata={"provider": "bank_transfer", "request_id": req["id"], "granted_by": actor_user_id},
        period_end_sql="%s", period_end_params=(ends_at,), allow_inactive=True,
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id from pricing_plans where code = %s and kind = 'subscription'",
            (req["plan_code"],),
        )
        plan = await cur.fetchone()
        plan_id = plan["id"] if plan else None
        await cur.execute(
            "insert into payment_history (user_id, plan_id, amount, kind, provider, provider_ref, "
            "status) values (%s, %s, %s, 'subscription', 'bank_transfer', %s, 'paid') "
            "returning id::text as id",
            (user_id, plan_id, req["amount"], req["id"]),
        )
        payment_id = (await cur.fetchone())["id"]
        await cur.execute(
            "update credit_sources set payment_id = %s where id = %s",
            (payment_id, granted["creditSourceId"]),
        )
        if grant is None:
            await cur.execute(
                "insert into manual_plan_grants (user_id, plan_code, request_id, ends_at) "
                "values (%s, %s, %s, %s) returning id::text as id",
                (user_id, req["plan_code"], req["id"], ends_at),
            )
            grant_id = (await cur.fetchone())["id"]
        else:
            await cur.execute(
                "update manual_plan_grants set ends_at = %s, request_id = %s where id = %s",
                (ends_at, req["id"], grant["id"]),
            )
            grant_id = grant["id"]
            # 이전 버킷의 표시 종료일도 함께 민다 — 이용권과 버킷이 같은 날짜를 말해야 한다.
            await cur.execute(
                "update credit_sources set period_end = %s where user_id = %s "
                "and source_type = 'subscription' and status = 'active' and id <> %s",
                (ends_at, user_id, granted["creditSourceId"]),
            )
        await cur.execute(
            "update profiles set plan = %s where user_id = %s", (req["plan_code"], user_id)
        )
    return {
        "paymentId": payment_id, "creditSourceId": granted["creditSourceId"],
        "manualPlanGrantId": grant_id, "credits": granted["credits"],
        "available": granted["available"], "endsAt": ends_at,
    }


async def confirm_request(
    conn, *, request_id: str, actor_user_id: str, paid_at: str, admin_note: str | None,
) -> dict:
    """관리자 입금 확인 → 지급. 같은 신청을 다시 확인하면 저장된 결과를 200 으로 돌려준다."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_REQUEST_COLUMNS} from bank_transfer_requests where id = %s for update",
            (request_id,),
        )
        req = await cur.fetchone()
        if req is None:
            raise CreditError("request_not_found", "신청을 찾을 수 없어요.", 404)
        if req["status"] == "paid":
            acct = await _lock_account(cur, req["user_id"])
            ends_at = None
            if req.get("manual_plan_grant_id"):
                await cur.execute(
                    "select ends_at from manual_plan_grants where id = %s",
                    (req["manual_plan_grant_id"],),
                )
                row = await cur.fetchone()
                ends_at = row["ends_at"] if row else None
            return _confirm_result(
                req, credits=req["credits"], available=acct["balance"] - acct["reserved"],
                ends_at=ends_at, idempotent=True,
            )
        if req["status"] in ("rejected", "canceled"):
            raise CreditError("request_closed", "이미 닫힌 신청이에요.", 409)
        if req["status"] == "expired" and not (admin_note or "").strip():
            raise CreditError(
                "note_required_for_expired", "기한이 지난 신청은 확인 사유를 적어야 해요.", 400
            )
        # 잠금 순서는 신청 → 이용권 → 계정. 만료 워커가 이용권 → 계정 순으로 잠그므로 같은 방향이어야
        # 같은 사용자의 확인과 만료가 겹쳐도 교착이 나지 않는다.
        if req["kind"] == "subscription":
            await _active_manual_grant(cur, req["user_id"], lock=True)
        await _lock_account(cur, req["user_id"])

    if req["kind"] == "topup":
        granted = await repo.purchase_topup(
            conn, user_id=req["user_id"], plan_code=req["plan_code"],
            idempotency_key=f"bank-transfer:{req['id']}", provider="bank_transfer",
            provider_ref=req["id"],
            metadata={"request_id": req["id"], "granted_by": actor_user_id,
                      "payer_name": req["payer_name"], "amount_krw": req["amount"],
                      "paid_at": paid_at},
            snapshot={"credits": req["credits"], "price": req["amount"]},
        )
        result = {
            "paymentId": granted.get("paymentId"), "creditSourceId": granted["creditSourceId"],
            "manualPlanGrantId": None, "credits": granted["credits"],
            "available": granted["available"], "endsAt": None,
        }
    else:
        result = await _grant_manual_plan(conn, req=req, actor_user_id=actor_user_id)

    async with conn.cursor() as cur:
        await cur.execute(
            "update bank_transfer_requests set status = 'paid', paid_at = %s, confirmed_by = %s, "
            "confirmed_at = now(), admin_note = %s, payment_id = %s, credit_source_id = %s, "
            "manual_plan_grant_id = %s where id = %s",
            (paid_at, actor_user_id, (admin_note or "").strip() or None, result["paymentId"],
             result["creditSourceId"], result["manualPlanGrantId"], req["id"]),
        )
    req = {**req, "payment_id": result["paymentId"], "credit_source_id": result["creditSourceId"],
           "manual_plan_grant_id": result["manualPlanGrantId"]}
    return _confirm_result(req, credits=result["credits"], available=result["available"],
                           ends_at=result["endsAt"])


async def reject_request(conn, *, request_id: str, reason: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "update bank_transfer_requests set status = 'rejected', admin_note = %s "
            "where id = %s and status in ('requested', 'expired') "
            f"returning {_REQUEST_COLUMNS}",
            (reason, request_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise CreditError("request_closed", "거절할 수 있는 상태가 아니에요.", 409)
    return row


async def user_email(conn, user_id: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute("select email from auth.users where id = %s", (user_id,))
        row = await cur.fetchone()
    return row["email"] if row else None


# ---- 만료(워커) ----

async def expire_stale_requests(conn) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            "update bank_transfer_requests set status = 'expired' "
            "where status = 'requested' and expires_at <= now()"
        )
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


#: 같은 플랜의 연장 신청이 열려 있으면(신청 후 3일) 이용권 만료를 미룬다. 화면이 "종료 전에 다시
#: 신청하면 이어진다"고 약속하는데, 관리자 확인이 주말을 넘기면 이월 크레딧이 먼저 사라졌다.
#: **종료일 전에 낸 신청만** 사유로 친다. 종료 뒤에 낸 신청까지 치면 입금 없이 3일마다 신청만 다시
#: 내서 이용권을 무한정 붙들 수 있다.
_OPEN_RENEWAL = (
    "exists (select 1 from bank_transfer_requests r where r.user_id = g.user_id "
    "and r.kind = 'subscription' and r.plan_code = g.plan_code and r.status = 'requested' "
    "and r.created_at < g.ends_at and r.expires_at > now())"
)


async def expire_manual_grants(conn) -> dict:
    """종료일이 지난 수동 이용권을 정리한다. 사용자마다 따로 커밋하고, 하나가 실패해도
    (진행 중 예약 때문에 reserved <= balance 가 깨지는 경우) 다음 사용자를 계속 본다.

    행마다 **자기 트랜잭션에서 다시 잠그고 조건을 확인**한다. 후보 50건을 한 번에 잠그던 방식은
    첫 commit 이 나머지 행 잠금까지 풀어서, 그 틈에 관리자가 확인한 같은 플랜 연장(늘어난 ends_at 과
    새 버킷)을 다음 차례에서 지워 버릴 수 있었다. 잠금 순서는 이용권 → 계정으로
    confirm_request(신청 → 이용권 → 계정)와 같은 방향이라 교착이 없다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select g.id::text as id from manual_plan_grants g "
            f"where g.status = 'active' and g.ends_at <= now() and not {_OPEN_RENEWAL} "
            "order by g.ends_at limit %s",
            (_EXPIRE_BATCH,),
        )
        candidates = [row["id"] for row in await cur.fetchall()]
    await conn.rollback()  # 후보 조회는 잠금이 없다 — 열린 트랜잭션만 닫는다.
    ended = skipped = 0
    for grant_id in candidates:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select g.id::text as id, g.user_id::text as user_id, g.plan_code "
                    "from manual_plan_grants g where g.id = %s and g.status = 'active' "
                    f"and g.ends_at <= now() and not {_OPEN_RENEWAL} for update of g skip locked",
                    (grant_id,),
                )
                row = await cur.fetchone()
            if row is None:
                # 그 사이 연장됐거나(ends_at 이 미래), 연장 신청이 열렸거나, 다른 워커가 잡았다.
                await conn.rollback()
                continue
            # 상호배제(④) 덕분에 이 사용자의 활성 구독 버킷은 전부 수동 이용권 것이다.
            await repo.expire_subscription_buckets(
                conn, user_id=row["user_id"], reason="manual_plan_expired")
            async with conn.cursor() as cur:
                await cur.execute(
                    "update manual_plan_grants set status = 'ended', ended_reason = 'expired' "
                    "where id = %s and status = 'active'",
                    (row["id"],),
                )
                toss = await _toss_subscription_status(cur, row["user_id"])
                if toss not in TOSS_BLOCKING_STATUSES:
                    await cur.execute(
                        "update profiles set plan = 'free' where user_id = %s", (row["user_id"],)
                    )
            await conn.commit()
            ended += 1
        except Exception:
            log.exception("manual plan grant expire failed grant=%s", grant_id)
            await conn.rollback()
            skipped += 1
    return {"ended": ended, "skipped": skipped}
