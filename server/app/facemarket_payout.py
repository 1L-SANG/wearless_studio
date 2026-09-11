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


MODEL_STATEMENTS_SQL = """
with totals as (
    select date_trunc('month', st.created_at at time zone 'Asia/Seoul')::date as period_month,
           sum(st.model_amount) as amount, count(*) as count
      from fm_settlements st join fm_licenses l on l.id = st.license_id
     where l.model_id = %s
     group by 1
), saved as (
    select period_month, amount, count, status, scheduled_for, paid_at
      from fm_payout_statements where model_id = %s
)
select coalesce(s.period_month, t.period_month) as period_month,
       coalesce(s.amount, t.amount, 0) as amount,
       coalesce(s.count, t.count, 0) as count,
       coalesce(s.status, 'scheduled') as status, s.scheduled_for, s.paid_at
  from totals t full outer join saved s on s.period_month = t.period_month
 order by period_month desc
"""

MONTH_TOTALS_SQL = """
select coalesce(sum(st.model_amount), 0) as amount, count(*) as count
  from fm_settlements st join fm_licenses l on l.id = st.license_id
 where l.model_id = %s and st.created_at >= %s and st.created_at < %s
"""

ADMIN_STATEMENTS_SQL = """
with totals as (
    select l.model_id, sum(st.model_amount) as amount, count(*) as count
      from fm_settlements st join fm_licenses l on l.id = st.license_id
     where st.created_at >= %s and st.created_at < %s
     group by l.model_id
), saved as (
    select model_id, amount, count, status, scheduled_for, paid_at
      from fm_payout_statements where period_month = %s
)
select m.id::text as model_id, m.display_name as model_name,
       coalesce(s.amount, t.amount, 0) as amount, coalesce(s.count, t.count, 0) as count,
       coalesce(s.status, 'scheduled') as status, s.scheduled_for, s.paid_at,
       a.bank_code, a.account_last4, a.holder_name
  from totals t full outer join saved s on s.model_id = t.model_id
  join fm_models m on m.id = coalesce(s.model_id, t.model_id)
  left join fm_payout_accounts a on a.model_id = m.id
 order by m.display_name, m.id
"""


def _statement_view(row, current_month):
    month = row["period_month"]
    if isinstance(month, str):
        month = date.fromisoformat(month)
    return {
        "periodMonth": month.strftime("%Y-%m"), "amount": int(row["amount"]),
        "count": int(row["count"]), "status": row.get("status") or "scheduled",
        "scheduledFor": row.get("scheduled_for") or scheduled_for(month),
        "paidAt": row.get("paid_at"), "open": month == current_month,
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
            await cur.execute(MODEL_STATEMENTS_SQL, (model_id, model_id))
            rows = await cur.fetchall()
    items = [_statement_view(row, current_month) for row in rows]
    current_key = current_month.strftime("%Y-%m")
    if not any(item["periodMonth"] == current_key for item in items):
        items.append(_statement_view({"period_month": current_month, "amount": 0, "count": 0}, current_month))
    items.sort(key=lambda item: item["periodMonth"], reverse=True)
    closed = [item for item in items if item["status"] == "scheduled" and item["periodMonth"] < current_key]
    upcoming = min(closed, key=lambda item: item["periodMonth"]) if closed else next(
        (item for item in items if item["open"] and item["status"] == "scheduled"), None,
    )
    next_payout = {key: upcoming[key] for key in ("scheduledFor", "amount", "periodMonth")} if upcoming else None
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
            await cur.execute(ADMIN_STATEMENTS_SQL, (start, end, month))
            rows = await cur.fetchall()
    current_month = datetime.now(KST).date().replace(day=1)
    return {"items": [_admin_statement_view(row, month, current_month) for row in rows]}


class PayoutStatusRequest(CamelModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


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
        start, end = month_bounds(month)
        async with conn.cursor() as cur:
            # 아직 명세가 없는 첫 변경도 모델 행 잠금으로 순서대로 처리해요.
            await cur.execute("select id::text, display_name from fm_models where id = %s for update", (model_id,))
            model = await cur.fetchone()
            if model is None:
                raise _err("model_not_found", "모델을 찾을 수 없어요.", 404)
            await cur.execute("select status from fm_payout_statements where model_id = %s and period_month = %s", (model_id, month))
            previous = await cur.fetchone()
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
            saved = await cur.fetchone()
            await cur.execute("select bank_code, account_last4, holder_name from fm_payout_accounts where model_id = %s", (model_id,))
            account = await cur.fetchone()
        await admin_guard.write_audit(
            conn, actor_user_id=user_id, action="payout_statement.status", target_type="model", target_id=model_id,
            before={"periodMonth": period_month, "status": previous["status"] if previous else "scheduled"},
            after={"periodMonth": period_month, "status": body.status, "amount": int(totals["amount"]), "count": int(totals["count"])},
        )
        await conn.commit()
    return _admin_statement_view(
        {**saved, **(account or {}), "model_id": model_id, "model_name": model["display_name"]},
        month, datetime.now(KST).date().replace(day=1),
    )
