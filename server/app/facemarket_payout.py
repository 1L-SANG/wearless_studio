"""모델 입금 계좌와 월별 지급 장부. 실제 송금은 운영팀이 처리해요."""
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from . import admin_guard
from .auth import require_user
from .db import get_conn
from .models import CamelModel

router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket payouts"])

PAYOUT_BANKS = {
    "shinhan": "신한은행", "kb": "국민은행", "woori": "우리은행", "hana": "하나은행",
    "nh": "NH농협은행", "ibk": "IBK기업은행", "kakao": "카카오뱅크", "toss": "토스뱅크",
}
_ACCOUNT_FIELDS = "bank_code, account_last4, holder_name, updated_at"
KST = ZoneInfo("Asia/Seoul")


def _err(code, message, status=400):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _cipher(request):
    key = request.app.state.settings.fm_payout_account_key
    try:
        if key:
            return Fernet(key.encode())
    except (ValueError, TypeError):
        pass
    raise _err("payout_account_unconfigured", "입금 계좌 등록은 준비 중이에요", 503)


def _model_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise _err("model_not_found", "모델을 찾을 수 없어요.", 404) from None


async def _owned_model(conn, user_id):
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text from fm_models where user_id = %s "
            "and status in ('pending', 'verified', 'suspended') "
            "order by created_at desc, id desc limit 1",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise _err("model_not_found", "모델을 찾을 수 없어요.", 404)
    return row["id"]


class PayoutAccountRequest(CamelModel):
    bank_code: str
    account_number: str = Field(repr=False)
    holder_name: str


class PayoutAccountView(CamelModel):
    bank_code: str
    bank_name: str
    holder_name: str
    account_masked: str
    updated_at: datetime


class PayoutAccountReveal(PayoutAccountView):
    account_number: str = Field(repr=False)


def _account_view(row):
    return {
        "bank_code": row["bank_code"], "bank_name": PAYOUT_BANKS[row["bank_code"]],
        "holder_name": row["holder_name"], "account_masked": f"***-****-{row['account_last4']}",
        "updated_at": row["updated_at"],
    }


@router.get("/payout-account", response_model=PayoutAccountView)
async def get_payout_account(request: Request, response: Response, user_id: str = Depends(require_user)):
    _cipher(request)
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        model_id = await _owned_model(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(f"select {_ACCOUNT_FIELDS} from fm_payout_accounts where model_id = %s", (model_id,))
            row = await cur.fetchone()
    if row is None:
        raise _err("payout_account_not_found", "등록한 입금 계좌가 없어요.", 404)
    return _account_view(row)


@router.put("/payout-account", response_model=PayoutAccountView)
async def save_payout_account(
    request: Request, response: Response, body: PayoutAccountRequest,
    user_id: str = Depends(require_user),
):
    cipher = _cipher(request)
    response.headers["Cache-Control"] = "no-store"
    digits = re.sub(r"[\s-]", "", body.account_number)
    holder = body.holder_name.strip()
    if body.bank_code not in PAYOUT_BANKS or not re.fullmatch(r"[0-9]{8,16}", digits) or not 1 <= len(holder) <= 40:
        raise _err("invalid_payout_account", "은행, 계좌번호와 예금주를 확인해 주세요.")
    encrypted = cipher.encrypt(digits.encode()).decode()
    async with get_conn(request) as conn:
        model_id = await _owned_model(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"""insert into fm_payout_accounts
                    (model_id, bank_code, account_number_enc, account_last4, holder_name)
                    values (%s, %s, %s, %s, %s)
                    on conflict (model_id) do update set bank_code = excluded.bank_code,
                        account_number_enc = excluded.account_number_enc,
                        account_last4 = excluded.account_last4, holder_name = excluded.holder_name
                    returning {_ACCOUNT_FIELDS}""",
                (model_id, body.bank_code, encrypted, digits[-4:], holder),
            )
            row = await cur.fetchone()
        await conn.commit()
    return _account_view(row)


@router.get("/admin/models/{model_id}/payout-account", response_model=PayoutAccountReveal)
async def reveal_payout_account(
    model_id: str, request: Request, response: Response, user_id: str = Depends(require_user),
):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        cipher = _cipher(request)
        model_id = _model_id(model_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {_ACCOUNT_FIELDS}, account_number_enc from fm_payout_accounts where model_id = %s",
                (model_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise _err("payout_account_not_found", "등록한 입금 계좌가 없어요.", 404)
        await admin_guard.write_audit(
            conn, actor_user_id=user_id, action="payout_account.reveal",
            target_type="model", target_id=model_id,
        )
        try:
            revealed = cipher.decrypt(row["account_number_enc"].encode()).decode()
        except (InvalidToken, UnicodeError, ValueError):
            raise _err("payout_account_unconfigured", "입금 계좌를 확인할 수 없어요.", 503) from None
        await conn.commit()
    return {**_account_view(row), "account_number": revealed}


def month_bounds(month):
    start = datetime(month.year, month.month, 1, tzinfo=KST)
    return start, (start + timedelta(days=32)).replace(day=1)


def scheduled_for(month):
    return month_bounds(month)[1].date().replace(day=10)


def _parse_month(value):
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            raise ValueError
        month = date.fromisoformat(value + "-01")
        scheduled_for(month)
        return month
    except (ValueError, TypeError, OverflowError):
        raise _err("invalid_period_month", "월은 YYYY-MM 형식으로 입력해 주세요.") from None


_CONFIRMATION_FIELDS = """id::text, model_id::text, period_month, responsible_admin, amount, count,
    bank_code, holder_name, account_last4, account_version::text, status, created_at, started_at, paid_at, cancelled_at"""


def _history_sql(model, month):
    return f"""(select coalesce(jsonb_agg(to_jsonb(history) order by history.created_at, history.id), '[]'::jsonb)
      from (select {_CONFIRMATION_FIELDS} from fm_payout_confirmations
             where model_id = {model} and period_month = {month}) history)"""


_MODEL_HISTORY_SQL = _history_sql("%s", "coalesce(s.period_month, t.period_month)")
_ADMIN_HISTORY_SQL = _history_sql("coalesce(s.model_id, t.model_id)", "%s")

MODEL_STATEMENTS_SQL = """
with totals as (
    select date_trunc('month', st.created_at at time zone 'Asia/Seoul')::date as period_month,
           sum(st.model_amount) as amount, count(*) as count,
           coalesce(sum(st.model_amount) filter (where e.settlement_id is null), 0) as unpaid_amount,
           count(*) filter (where e.settlement_id is null) as unpaid_count
      from fm_settlements st join fm_licenses l on l.id = st.license_id
      left join fm_payout_confirmation_entries e on e.settlement_id = st.id and e.released_at is null
     where l.model_id = %s
     group by 1
), saved as (
    select period_month, amount, count, status, scheduled_for, paid_at
      from fm_payout_statements where model_id = %s
)
select coalesce(s.period_month, t.period_month) as period_month,
       case when s.status = 'paid' then s.amount else coalesce(t.amount, 0) end as amount,
       case when s.status = 'paid' then s.count else coalesce(t.count, 0) end as count,
       coalesce(t.unpaid_amount, 0) as unpaid_amount, coalesce(t.unpaid_count, 0) as unpaid_count,
       coalesce(s.status, 'scheduled') as status, s.scheduled_for, s.paid_at,
       __HISTORY__ as confirmations
  from totals t full outer join saved s on s.period_month = t.period_month
 order by period_month desc
""".replace("__HISTORY__", _MODEL_HISTORY_SQL)

MONTH_TOTALS_SQL = """
select coalesce(sum(st.model_amount), 0) as amount, count(*) as count,
       coalesce(sum(st.model_amount) filter (where e.settlement_id is null), 0) as unpaid_amount,
       count(*) filter (where e.settlement_id is null) as unpaid_count
  from fm_settlements st join fm_licenses l on l.id = st.license_id
  left join fm_payout_confirmation_entries e on e.settlement_id = st.id and e.released_at is null
 where l.model_id = %s and st.created_at >= %s and st.created_at < %s
"""

ADMIN_STATEMENTS_SQL = """
with totals as (
    select l.model_id, sum(st.model_amount) as amount, count(*) as count,
           coalesce(sum(st.model_amount) filter (where e.settlement_id is null), 0) as unpaid_amount,
           count(*) filter (where e.settlement_id is null) as unpaid_count
      from fm_settlements st join fm_licenses l on l.id = st.license_id
      left join fm_payout_confirmation_entries e on e.settlement_id = st.id and e.released_at is null
     where st.created_at >= %s and st.created_at < %s
     group by l.model_id
), saved as (
    select model_id, amount, count, status, scheduled_for, paid_at
      from fm_payout_statements where period_month = %s
)
select m.id::text as model_id, m.display_name as model_name,
       case when s.status = 'paid' then s.amount else coalesce(t.amount, 0) end as amount,
       case when s.status = 'paid' then s.count else coalesce(t.count, 0) end as count,
       coalesce(t.unpaid_amount, 0) as unpaid_amount, coalesce(t.unpaid_count, 0) as unpaid_count,
       coalesce(s.status, 'scheduled') as status, s.scheduled_for, s.paid_at,
       a.bank_code, a.account_last4, a.holder_name, __HISTORY__ as confirmations
  from totals t full outer join saved s on s.model_id = t.model_id
  join fm_models m on m.id = coalesce(s.model_id, t.model_id)
  left join fm_payout_accounts a on a.model_id = m.id
 order by m.display_name, m.id
""".replace("__HISTORY__", _ADMIN_HISTORY_SQL)


def _statement_view(row, current_month):
    month = row["period_month"]
    if isinstance(month, str):
        month = date.fromisoformat(month)
    return {
        "periodMonth": month.strftime("%Y-%m"), "amount": int(row["amount"]),
        "count": int(row["count"]), "status": row.get("status") or "scheduled",
        "scheduledFor": row.get("scheduled_for") or scheduled_for(month),
        "paidAt": row.get("paid_at"), "open": month == current_month,
        "unpaidAmount": int(row.get("unpaid_amount", row["amount"])),
        "unpaidCount": int(row.get("unpaid_count", row["count"])),
    }


def _admin_statement_view(row, month, current_month):
    bank = row.get("bank_code")
    return {
        **_statement_view({**row, "period_month": month}, current_month),
        "modelId": row["model_id"], "modelName": row["model_name"],
        "bankName": PAYOUT_BANKS.get(bank),
        "accountMasked": f"***-****-{row['account_last4']}" if bank else None,
        "holderName": row.get("holder_name"),
    }


@router.get("/payout-statements")
async def get_payout_statements(request: Request, response: Response, user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    current_month = datetime.now(KST).date().replace(day=1)
    async with get_conn(request) as conn:
        model_id = await _owned_model(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(MODEL_STATEMENTS_SQL, (model_id, model_id, model_id))
            rows = await cur.fetchall()
    items = [_with_confirmations(_statement_view(row, current_month), [_confirmation_view(value) for value in row.get("confirmations", [])]) for row in rows]
    current_key = current_month.strftime("%Y-%m")
    if not any(item["periodMonth"] == current_key for item in items):
        items.append(_statement_view({"period_month": current_month, "amount": 0, "count": 0}, current_month))
    items.sort(key=lambda item: item["periodMonth"], reverse=True)
    closed = [item for item in items if item["status"] == "scheduled" and item["periodMonth"] < current_key and item["unpaidAmount"] > 0]
    upcoming = min(closed, key=lambda item: item["periodMonth"]) if closed else next(
        (item for item in items if item["open"] and item["status"] == "scheduled"), None,
    )
    next_payout = {key: upcoming[key] for key in ("scheduledFor", "amount", "periodMonth")} if upcoming else None
    if next_payout:
        next_payout["amount"] = upcoming["unpaidAmount"]
    return {"items": items[:24], "nextPayout": next_payout}


@router.get("/admin/payout-statements")
async def list_admin_payout_statements(
    month: str, request: Request, response: Response, user_id: str = Depends(require_user),
):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        month = _parse_month(month)
        start, end = month_bounds(month)
        async with conn.cursor() as cur:
            await cur.execute(ADMIN_STATEMENTS_SQL, (start, end, month, month))
            rows = await cur.fetchall()
    current_month = datetime.now(KST).date().replace(day=1)
    return {"viewerId": user_id, "items": [_with_confirmations(_admin_statement_view(row, month, current_month), [_confirmation_view(value, user_id) for value in row.get("confirmations", [])]) for row in rows]}


class PayoutStatusRequest(CamelModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)
    expected_confirmation_id: uuid.UUID | None = None


@router.post("/admin/payout-statements/{model_id}/{period_month}/status")
async def set_payout_statement_status(
    model_id: str, period_month: str, body: PayoutStatusRequest,
    request: Request, response: Response, user_id: str = Depends(require_user),
):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        model_id = _model_id(model_id)
        month = _parse_month(period_month)
        if body.status not in ("paid", "held", "scheduled"):
            raise _err("invalid_payout_status", "지급 상태를 확인해 주세요.")
        if body.status == "paid":
            raise _err("payout_confirmation_required", "지급 확인 후 송금 결과를 기록해 주세요.", 409)
        start, end = month_bounds(month)
        async with conn.cursor() as cur:
            # 아직 명세가 없는 첫 변경도 모델 행 잠금으로 순서대로 처리해요.
            await cur.execute("select id::text, display_name from fm_models where id = %s for update", (model_id,))
            model = await cur.fetchone()
            if model is None:
                raise _err("model_not_found", "모델을 찾을 수 없어요.", 404)
            await cur.execute("select status from fm_payout_statements where model_id = %s and period_month = %s", (model_id, month))
            previous = await cur.fetchone()
            if previous and previous["status"] == "paid":
                raise _err("payout_already_paid", "기존 지급 기록은 되돌릴 수 없어요.", 409)
            await cur.execute("select id::text, status from fm_payout_confirmations where model_id = %s and period_month = %s order by created_at desc, id desc limit 1", (model_id, month))
            latest = await cur.fetchone()
            expected_id = str(body.expected_confirmation_id) if body.expected_confirmation_id else None
            if (latest["id"] if latest else None) != expected_id:
                raise _err("payout_statement_changed", "지급 상태가 변경됐어요. 목록을 새로 확인해 주세요.", 409)
            if latest and latest["status"] in ("prepared", "transfer_started"):
                raise _err("payout_confirmation_active", "진행 중인 지급 확인 건에서 처리해 주세요.", 409)
            await cur.execute(MONTH_TOTALS_SQL, (model_id, start, end))
            totals = await cur.fetchone()
            await cur.execute(
                """insert into fm_payout_statements
                    (model_id, period_month, amount, count, status, scheduled_for, paid_at, note)
                    values (%s, %s, %s, %s, %s, %s, case when %s = 'paid' then now() else null end, %s)
                    on conflict (model_id, period_month) do update set
                        amount = excluded.amount, count = excluded.count, status = excluded.status,
                        scheduled_for = excluded.scheduled_for, paid_at = excluded.paid_at, note = excluded.note
                    returning period_month, amount, count, status, scheduled_for, paid_at""",
                (model_id, month, int(totals["amount"]), int(totals["count"]), body.status,
                 scheduled_for(month), body.status, (body.note or "").strip() or None),
            )
            await cur.fetchone()
            # Return the same atomic month view as GET, including historical payments.
            await cur.execute(ADMIN_STATEMENTS_SQL.replace("order by m.display_name, m.id", "where m.id = %s order by m.display_name, m.id"),
                              (start, end, month, month, model_id))
            current = await cur.fetchone()
        await admin_guard.write_audit(
            conn, actor_user_id=user_id, action="payout_statement.status", target_type="model", target_id=model_id,
            before={"periodMonth": period_month, "status": previous["status"] if previous else "scheduled"},
            after={"periodMonth": period_month, "status": body.status, "amount": int(totals["amount"]), "count": int(totals["count"])},
        )
        await conn.commit()
    return _with_confirmations(_admin_statement_view(
        current,
        month, datetime.now(KST).date().replace(day=1),
    ), [_confirmation_view(value, user_id) for value in current.get("confirmations", [])])


def _confirmation_view(row, user_id=None):
    month = row["period_month"]
    if isinstance(month, str):
        month = date.fromisoformat(month)
    return {
        "id": row["id"], "modelId": row["model_id"], "periodMonth": month.strftime("%Y-%m"),
        "amount": int(row["amount"]), "count": row["count"], "status": row["status"],
        "responsibleAdmin": row["responsible_admin"] if user_id else None,
        "canManage": user_id == row["responsible_admin"],
        "bankName": PAYOUT_BANKS[row["bank_code"]], "holderName": row["holder_name"],
        "accountMasked": f"***-****-{row['account_last4']}",
        "createdAt": row["created_at"], "startedAt": row.get("started_at"),
        "paidAt": row.get("paid_at"), "cancelledAt": row.get("cancelled_at"),
    }


def _with_confirmations(item, confirmations):
    matching = [row for row in confirmations if row["periodMonth"] == item["periodMonth"]
                and (not item.get("modelId") or row["modelId"] == item["modelId"])]
    item["confirmations"] = matching
    item["legacyPaid"] = item["status"] == "paid"
    if item["legacyPaid"]:
        # Older paid rows have no entry identities. Do not guess their allocations.
        item["unpaidAmount"] = max(0, item["unpaidAmount"] - item["amount"])
        item["unpaidCount"] = max(0, item["unpaidCount"] - item["count"])
    elif any(row["status"] in ("prepared", "transfer_started") for row in matching):
        item["status"] = "processing"
    elif item["status"] != "held" and item["unpaidCount"] == 0 and any(row["status"] == "paid" for row in matching):
        item["status"] = "paid"
        item["paidAt"] = max(row["paidAt"] for row in matching if row["status"] == "paid")
    return item


class PayoutConfirmationRequest(CamelModel):
    confirmation_id: uuid.UUID


@router.post("/admin/payout-statements/{model_id}/{period_month}/confirm")
async def confirm_payout_statement(model_id: str, period_month: str, body: PayoutConfirmationRequest,
                                   request: Request, response: Response, user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        model_id, month = _model_id(model_id), _parse_month(period_month)
        if month >= datetime.now(KST).date().replace(day=1):
            raise _err("payout_month_open", "마감된 월만 지급 확인할 수 있어요.", 409)
        confirmation_id = str(body.confirmation_id)
        async with conn.cursor() as cur:
            # Same lock as hold/release, including the first confirmation of a month.
            await cur.execute("select id from fm_models where id = %s for update", (model_id,))
            if not await cur.fetchone():
                raise _err("model_not_found", "모델을 찾을 수 없어요.", 404)
            await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where id = %s", (confirmation_id,))
            existing = await cur.fetchone()
            if existing:
                if existing["model_id"] != model_id or existing["period_month"] != month or existing["responsible_admin"] != user_id:
                    raise _err("payout_confirmation_conflict", "다른 지급 확인에 사용한 식별자예요.", 409)
                return _confirmation_view(existing, user_id)
            await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where model_id = %s and period_month = %s and status in ('prepared','transfer_started')", (model_id, month))
            existing = await cur.fetchone()
            if existing:
                return _confirmation_view(existing, user_id)
            await cur.execute("select status from fm_payout_statements where model_id = %s and period_month = %s", (model_id, month))
            statement = await cur.fetchone()
            if statement and statement["status"] in ("held", "paid"):
                raise _err("payout_statement_unavailable", "보류를 해제해 주세요. 이전 지급 기록이 있는 월은 정산 항목 확인이 필요해요.", 409)
            await cur.execute("select bank_code, holder_name, account_number_enc, account_last4, account_version::text from fm_payout_accounts where model_id = %s for share", (model_id,))
            account = await cur.fetchone()
            if not account:
                raise _err("payout_account_not_found", "입금 계좌를 먼저 등록해 주세요.", 409)
            # Verify the snapshot is usable before reserving any money, without logging plaintext.
            try:
                _cipher(request).decrypt(account["account_number_enc"].encode())
            except (InvalidToken, ValueError):
                raise _err("payout_account_unconfigured", "입금 계좌를 확인할 수 없어요.", 503) from None
            start, end = month_bounds(month)
            await cur.execute("""select st.id::text, st.model_amount from fm_settlements st
                join fm_licenses l on l.id = st.license_id
                where l.model_id = %s and st.created_at >= %s and st.created_at < %s
                  and not exists (select 1 from fm_payout_confirmation_entries e where e.settlement_id = st.id and e.released_at is null)
                order by st.id""", (model_id, start, end))
            entries = await cur.fetchall()
            amount = sum(int(entry["model_amount"]) for entry in entries)
            if not entries or amount <= 0:
                raise _err("payout_nothing_due", "새로 지급할 정산 내역이 없어요.", 409)
            await cur.execute(f"""insert into fm_payout_confirmations
                (id, model_id, period_month, responsible_admin, amount, count,
                 bank_code, holder_name, account_number_enc, account_last4, account_version)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning {_CONFIRMATION_FIELDS}""",
                (confirmation_id, model_id, month, user_id, amount, len(entries), account["bank_code"],
                 account["holder_name"], account["account_number_enc"], account["account_last4"], account["account_version"]))
            result = await cur.fetchone()
            await cur.execute("""insert into fm_payout_confirmation_entries (confirmation_id, settlement_id, amount)
                select %s::uuid, entry_id, amount from unnest(%s::uuid[], %s::bigint[]) as selected(entry_id, amount)""",
                (confirmation_id, [entry["id"] for entry in entries], [int(entry["model_amount"]) for entry in entries]))
        await admin_guard.write_audit(conn, actor_user_id=user_id, action="payout_confirmation.prepare",
            target_type="model", target_id=model_id, after={"confirmationId": confirmation_id, "amount": amount, "count": len(entries)})
        await conn.commit()
        return _confirmation_view(result, user_id)


async def _locked_confirmation(conn, confirmation_id, user_id):
    async with conn.cursor() as cur:
        await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where id = %s for update", (_model_id(confirmation_id),))
        row = await cur.fetchone()
    if not row:
        raise _err("payout_confirmation_not_found", "지급 확인을 찾을 수 없어요.", 404)
    if row["responsible_admin"] != user_id:
        raise _err("payout_confirmation_owner", "이 지급 건을 확인한 관리자만 처리할 수 있어요.", 403)
    return row


@router.post("/admin/payout-confirmations/{confirmation_id}/{action}")
async def advance_payout_confirmation(confirmation_id: str, action: str, request: Request,
                                      response: Response, user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    if action not in ("start", "paid", "cancel"):
        raise _err("invalid_payout_action", "지급 동작을 확인해 주세요.")
    target = {"start": "transfer_started", "paid": "paid", "cancel": "cancelled"}[action]
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        row = await _locked_confirmation(conn, confirmation_id, user_id)
        if row["status"] == target or (action == "start" and row["status"] == "paid"):
            return _confirmation_view(row, user_id)
        required = "transfer_started" if action == "paid" else "prepared"
        if row["status"] != required:
            raise _err("payout_invalid_transition", "송금이 시작된 건은 취소할 수 없어요. 현재 지급 상태를 다시 확인해 주세요.", 409)
        async with conn.cursor() as cur:
            if action == "start":
                await cur.execute("select account_version::text from fm_payout_accounts where model_id = %s for share", (row["model_id"],))
                account = await cur.fetchone()
                if not account or account["account_version"] != row["account_version"]:
                    raise _err("payout_account_changed", "계좌가 변경됐어요. 송금 전 확인을 취소하고 새 계좌로 다시 확인해 주세요.", 409)
            timestamp = {"start": "started_at", "paid": "paid_at", "cancel": "cancelled_at"}[action]
            await cur.execute(f"update fm_payout_confirmations set status = %s, {timestamp} = now() where id = %s returning {_CONFIRMATION_FIELDS}", (target, row["id"]))
            result = await cur.fetchone()
            if action == "cancel":
                await cur.execute("update fm_payout_confirmation_entries set released_at = now() where confirmation_id = %s and released_at is null", (row["id"],))
        await admin_guard.write_audit(conn, actor_user_id=user_id, action=f"payout_confirmation.{action}",
            target_type="model", target_id=row["model_id"], before={"confirmationId": row["id"], "status": row["status"]},
            after={"confirmationId": row["id"], "status": target, "amount": int(row["amount"])})
        await conn.commit()
        return _confirmation_view(result, user_id)


@router.get("/admin/payout-confirmations/{confirmation_id}/account", response_model=PayoutAccountReveal)
async def reveal_confirmation_account(confirmation_id: str, request: Request, response: Response,
                                      user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        async with conn.cursor() as cur:
            await cur.execute(f"select {_CONFIRMATION_FIELDS}, account_number_enc from fm_payout_confirmations where id = %s", (_model_id(confirmation_id),))
            row = await cur.fetchone()
        if not row:
            raise _err("payout_confirmation_not_found", "지급 확인을 찾을 수 없어요.", 404)
        if row["responsible_admin"] != user_id:
            raise _err("payout_confirmation_owner", "이 지급 건을 확인한 관리자만 계좌를 볼 수 있어요.", 403)
        try:
            number = _cipher(request).decrypt(row["account_number_enc"].encode()).decode()
        except (InvalidToken, UnicodeError, ValueError):
            raise _err("payout_account_unconfigured", "입금 계좌를 확인할 수 없어요.", 503) from None
        await admin_guard.write_audit(conn, actor_user_id=user_id, action="payout_confirmation.reveal", target_type="model", target_id=row["model_id"], after={"confirmationId": row["id"]})
        await conn.commit()
    return {**_account_view({**row, "updated_at": row["created_at"]}), "account_number": number}
