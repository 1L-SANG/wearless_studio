"""모델 입금 계좌와 월별 지급 장부. 실제 송금은 운영팀이 처리해요."""
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from . import admin_guard, facemarket_notify
from .auth import require_user
from .db import get_conn
from .models import CamelModel
from .services import payout_provider

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


# transferred_on(2026-09-26): 관리자가 은행 앱에서 확인한 이체일. 수동 지급은 이것과
# 참조번호(provider_ref) 없이 paid 가 될 수 없다(20260926230000 CHECK + 아래 paid 검증).
# kind·cutoff_at(2026-09-27): 중간 정산(interim)이 만든 확인서와 그 기준 시각(20260927090000).
_CONFIRMATION_FIELDS = """id::text, model_id::text, period_month, responsible_admin, amount, count,
    bank_code, holder_name, account_last4, account_version::text, status, provider, provider_ref,
    failure_reason, created_at, started_at, paid_at, cancelled_at, transferred_on, kind, cutoff_at"""


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


def mask_reference(value):
    """은행 거래 참조번호는 끝 4자리만 보여 준다(모델 화면·목록). 원문은 관리자 응답에만."""
    text = (value or "").strip()
    if not text:
        return None
    return "••••" + text[-4:] if len(text) > 4 else "••••"


def _as_datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def covered_through(cutoff):
    """중간 정산이 담은 마지막 날(KST). 기준 시각은 배타적이라 1µs 앞의 날짜다."""
    cutoff = _as_datetime(cutoff)
    if cutoff is None:
        return None
    return (cutoff - timedelta(microseconds=1)).astimezone(KST).date()


def _confirmation_view(row, user_id=None):
    month = row["period_month"]
    if isinstance(month, str):
        month = date.fromisoformat(month)
    provider = row.get("provider") or "manual"
    cutoff = _as_datetime(row.get("cutoff_at"))
    return {
        "id": row["id"], "modelId": row["model_id"], "periodMonth": month.strftime("%Y-%m"),
        "amount": int(row["amount"]), "count": row["count"], "status": row["status"],
        "responsibleAdmin": row["responsible_admin"] if user_id else None,
        "canManage": user_id == row["responsible_admin"],
        "bankName": PAYOUT_BANKS[row["bank_code"]], "holderName": row["holder_name"],
        "accountMasked": f"***-****-{row['account_last4']}",
        "createdAt": row["created_at"], "startedAt": row.get("started_at"),
        "paidAt": row.get("paid_at"), "cancelledAt": row.get("cancelled_at"),
        # 이 건을 무엇이 처리했는지. simulated 면 화면·알림이 "입금됐다"고 말하면 안 된다.
        "provider": provider,
        "simulated": provider != "manual",
        # 참조번호 원문은 관리자(user_id 있음)에게만. 모델 화면은 끝 4자리만 본다(2026-09-26).
        "providerRef": row.get("provider_ref") if user_id else None,
        "transferReferenceMasked": mask_reference(row.get("provider_ref")),
        "transferredOn": row.get("transferred_on"),
        "failureReason": row.get("failure_reason"),
        # 중간 정산(2026-09-27): 이 확인서가 담은 기간은 그 달 1일 ~ coveredThrough(KST).
        "kind": row.get("kind") or "monthly",
        "cutoffAt": cutoff,
        "coveredThrough": covered_through(cutoff),
    }


def _with_confirmations(item, confirmations):
    matching = [row for row in confirmations if row["periodMonth"] == item["periodMonth"]
                and (not item.get("modelId") or row["modelId"] == item["modelId"])]
    item["confirmations"] = matching
    # 지금까지 실제로 지급된 몫(중간 정산 포함)과 진행 중인 몫(2026-09-27). 스텁(simulated)이
    # paid 로 만든 건은 돈이 가지 않았으므로 지급액에 넣지 않는다. 남은 몫 = unpaidAmount.
    real_paid = [row for row in matching if row["status"] == "paid" and not row.get("simulated")]
    item["paidAmount"] = sum(int(row["amount"]) for row in real_paid)
    item["paidCount"] = sum(int(row["count"]) for row in real_paid)
    item["inProgressAmount"] = sum(int(row["amount"]) for row in matching
                                   if row["status"] in ("prepared", "transfer_started"))
    item["legacyPaid"] = item["status"] == "paid"
    if item["legacyPaid"]:
        # Older paid rows have no entry identities. Do not guess their allocations.
        item["unpaidAmount"] = max(0, item["unpaidAmount"] - item["amount"])
        item["unpaidCount"] = max(0, item["unpaidCount"] - item["count"])
    elif any(row["status"] in ("prepared", "transfer_started") for row in matching):
        item["status"] = "processing"
    elif (item["status"] != "held" and not item.get("open") and item["unpaidCount"] == 0
          and any(row["status"] == "paid" for row in matching)):
        # 마감 전 달은 중간 정산이 지금까지 몫을 다 지급해도 "지급 완료"가 아니다 — 달이 끝나야
        # 남은 몫이 정해진다(2026-09-27).
        item["status"] = "paid"
        item["paidAt"] = max(row["paidAt"] for row in matching if row["status"] == "paid")
    return item


class PayoutConfirmationRequest(CamelModel):
    confirmation_id: uuid.UUID


async def _prepare_confirmation(cur, request, *, model_id, month, confirmation_id, user_id, cutoff=None):
    """계좌 스냅샷 + 미배정 정산 항목으로 확인서(prepared)를 만든다. 모델 행 잠금은 호출부 몫.

    지급 확인(단건)과 월말 정산 실행(2026-09-26), 중간 정산(2026-09-27)이 같은 코드를 탄다 —
    금액·항목을 고르는 규칙이 두 벌로 갈라지면 한쪽만 고쳐지는 날이 온다.

    cutoff 가 있으면 중간 정산이다: 그 시각 전에 생긴 **체인 확정** 정산만 담고, 확인서에
    kind='interim' + cutoff_at 을 남긴다. 어느 쪽이든 이미 풀리지 않은 항목에 들어간 정산은
    고르지 않는다 — 중간 정산으로 담긴 정산을 월말 정산이 다시 담지 못하는 이유다(DB 에서도
    fm_payout_one_active_allocation 이 막는다). 반환: (확인서 행, 금액, 건수)."""
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
    until = min(cutoff, end) if cutoff else end
    confirmed_only = "and st.chain_status = 'confirmed'" if cutoff else ""
    await cur.execute(f"""select st.id::text, st.model_amount from fm_settlements st
        join fm_licenses l on l.id = st.license_id
        where l.model_id = %s and st.created_at >= %s and st.created_at < %s {confirmed_only}
          and not exists (select 1 from fm_payout_confirmation_entries e where e.settlement_id = st.id and e.released_at is null)
        order by st.id""", (model_id, start, until))
    entries = await cur.fetchall()
    amount = sum(int(entry["model_amount"]) for entry in entries)
    if not entries or amount <= 0:
        raise _err("payout_nothing_due", "새로 지급할 정산 내역이 없어요.", 409)
    # provider 는 **확인서가 태어날 때** 박힌다. 여기서 안 넣으면 DB 기본값 manual 로
    # 남아, 서버를 stub 으로 띄워도 화면의 시뮬레이션 버튼이 영영 안 뜬다
    # (화면은 confirmation.simulated 를 본다). 실제로 그렇게 빠뜨렸다.
    provider = payout_provider.resolve_provider(
        request.app.state.settings.fm_payout_provider).name
    await cur.execute(f"""insert into fm_payout_confirmations
        (id, model_id, period_month, responsible_admin, amount, count,
         bank_code, holder_name, account_number_enc, account_last4, account_version, provider,
         kind, cutoff_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning {_CONFIRMATION_FIELDS}""",
        (confirmation_id, model_id, month, user_id, amount, len(entries), account["bank_code"],
         account["holder_name"], account["account_number_enc"], account["account_last4"],
         account["account_version"], provider, "interim" if cutoff else "monthly", cutoff))
    result = await cur.fetchone()
    await cur.execute("""insert into fm_payout_confirmation_entries (confirmation_id, settlement_id, amount)
        select %s::uuid, entry_id, amount from unnest(%s::uuid[], %s::bigint[]) as selected(entry_id, amount)""",
        (confirmation_id, [entry["id"] for entry in entries], [int(entry["model_amount"]) for entry in entries]))
    return result, amount, len(entries)


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
            result, amount, count = await _prepare_confirmation(
                cur, request, model_id=model_id, month=month,
                confirmation_id=confirmation_id, user_id=user_id)
        await admin_guard.write_audit(conn, actor_user_id=user_id, action="payout_confirmation.prepare",
            target_type="model", target_id=model_id, after={"confirmationId": confirmation_id, "amount": amount, "count": count})
        await conn.commit()
        return _confirmation_view(result, user_id)


# ── 월말 정산 실행(2026-09-26) ─────────────────────────────────────────────────
# 마감된 달 하나를 골라 한 번에: 모델마다 그 달의 체인 확정 정산을 모아 지급 명세를 새로
# 고치고(금액·건수), 새로 지급할 몫이 있으면 지급 확인서(prepared)를 만든다. 거기까지다 —
# **돈은 움직이지 않는다.** 담당자가 송금 시작 → 은행 앱 이체 → 참조번호·금액·이체일 기록을
# 해야 paid 가 된다(advance_payout_confirmation). 상태머신·CHECK·잠금은 단건 지급 확인과 같다.
#
# 멱등: 다시 눌러도 진행 중 확인서가 있으면 그걸 보여 주고, 다 지급됐으면 "새로 지급할 몫
# 없음"이다. 확인서는 모델·월당 활성 1건(fm_payout_one_active_confirmation)이 DB 에서도 막는다.

RUN_MODELS_SQL = """
select m.id::text as model_id, m.display_name as model_name
  from fm_models m
 where exists (select 1 from fm_settlements st join fm_licenses l on l.id = st.license_id
                where l.model_id = m.id and st.created_at >= %s and st.created_at < %s)
    or exists (select 1 from fm_payout_statements s where s.model_id = m.id and s.period_month = %s)
 order by m.id
"""

UNCONFIRMED_SQL = """
select count(*) as count from fm_settlements st join fm_licenses l on l.id = st.license_id
 where l.model_id = %s and st.created_at >= %s and st.created_at < %s
   and st.chain_status <> 'confirmed'
"""

# 명세는 scheduled 일 때만 금액을 새로 고친다. held·paid(이전 방식) 명세와 메모는 건드리지 않는다.
REFRESH_STATEMENT_SQL = """
insert into fm_payout_statements (model_id, period_month, amount, count, status, scheduled_for)
values (%s, %s, %s, %s, 'scheduled', %s)
on conflict (model_id, period_month) do update set amount = excluded.amount, count = excluded.count
 where fm_payout_statements.status = 'scheduled'
"""


async def _run_model_month(conn, cur, request, *, model, month, start, end, user_id):
    model_id = model["model_id"]
    base = {"modelId": model_id, "modelName": model.get("model_name"), "amount": 0, "count": 0,
            "confirmationId": None}
    # 단건 지급 확인·보류와 같은 잠금 — 같은 모델을 두 경로가 동시에 만지지 못한다.
    await cur.execute("select id from fm_models where id = %s for update", (model_id,))
    await cur.fetchone()
    await cur.execute("select status from fm_payout_statements where model_id = %s and period_month = %s",
                      (model_id, month))
    statement = await cur.fetchone()
    if statement and statement["status"] in ("held", "paid"):
        return {**base, "outcome": statement["status"]}
    await cur.execute(UNCONFIRMED_SQL, (model_id, start, end))
    unconfirmed = int((await cur.fetchone() or {}).get("count") or 0)
    if unconfirmed:
        # 체인에 확정되지 않은 정산은 모으지 않는다 — "정산은 체인 기록" 이라는 말이 참이려면.
        return {**base, "outcome": "unconfirmed", "unconfirmedCount": unconfirmed}
    await cur.execute(MONTH_TOTALS_SQL, (model_id, start, end))
    totals = await cur.fetchone()
    await cur.execute(REFRESH_STATEMENT_SQL, (model_id, month, int(totals["amount"]),
                                              int(totals["count"]), scheduled_for(month)))
    await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where model_id = %s and period_month = %s and status in ('prepared','transfer_started')", (model_id, month))
    active = await cur.fetchone()
    if active:
        return {**base, "outcome": "in_progress", "confirmationId": active["id"],
                "amount": int(active["amount"]), "count": int(active["count"])}
    if int(totals["unpaid_count"]) == 0:
        return {**base, "outcome": "nothing_due"}
    confirmation_id = str(uuid.uuid4())
    try:
        result, amount, count = await _prepare_confirmation(
            cur, request, model_id=model_id, month=month,
            confirmation_id=confirmation_id, user_id=user_id)
    except HTTPException as exc:
        code = (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else None
        if code == "payout_account_not_found":
            return {**base, "outcome": "no_account",
                    "amount": int(totals["unpaid_amount"]), "count": int(totals["unpaid_count"])}
        if code == "payout_nothing_due":
            return {**base, "outcome": "nothing_due"}
        raise  # 계좌 암호 키 문제(503) 같은 설정 오류는 달 전체를 멈춘다.
    await admin_guard.write_audit(
        conn, actor_user_id=user_id, action="payout_confirmation.prepare",
        target_type="model", target_id=model_id,
        after={"confirmationId": confirmation_id, "amount": amount, "count": count, "via": "monthly_run"})
    return {**base, "outcome": "prepared", "confirmationId": result["id"], "amount": amount, "count": count}


# ── 중간 정산(2026-09-27) ──────────────────────────────────────────────────────
# 마감 전인 **이번 달**을 지금(기준 시각 cutoff)까지 먼저 정산한다. 월말 정산과 다른 점은 둘:
#   1) 체인 미확정 정산이 있어도 모델을 건너뛰지 않고, 확정된 정산만 담는다(미확정은 남는다).
#   2) 확인서에 kind='interim' + cutoff_at 을 남긴다.
# 나머지 — 모델 잠금, 보류·이전 방식 지급 건너뛰기, 진행 중 확인서 재사용(멱등), 계좌 스냅샷,
# 항목 배정, 송금 시작 → 실제 이체 기록 → paid — 는 월말 정산·단건 확인과 같은 코드다.
# 달이 끝나고 월말 정산을 돌리면 이미 배정된(중간 정산) 정산은 빠지고 남은 몫만 모인다.

INTERIM_DUE_SQL = """
select count(*) filter (where st.chain_status = 'confirmed') as due_count,
       coalesce(sum(st.model_amount) filter (where st.chain_status = 'confirmed'), 0) as due_amount,
       count(*) filter (where st.chain_status <> 'confirmed') as unconfirmed_count
  from fm_settlements st join fm_licenses l on l.id = st.license_id
 where l.model_id = %s and st.created_at >= %s and st.created_at < %s
   and not exists (select 1 from fm_payout_confirmation_entries e
                    where e.settlement_id = st.id and e.released_at is null)
"""


async def _run_model_interim(conn, cur, request, *, model, month, start, cutoff, user_id):
    model_id = model["model_id"]
    base = {"modelId": model_id, "modelName": model.get("model_name"), "amount": 0, "count": 0,
            "confirmationId": None, "unconfirmedCount": 0}
    await cur.execute("select id from fm_models where id = %s for update", (model_id,))
    await cur.fetchone()
    await cur.execute("select status from fm_payout_statements where model_id = %s and period_month = %s",
                      (model_id, month))
    statement = await cur.fetchone()
    if statement and statement["status"] in ("held", "paid"):
        return {**base, "outcome": statement["status"]}
    await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where model_id = %s and period_month = %s and status in ('prepared','transfer_started')", (model_id, month))
    active = await cur.fetchone()
    if active:
        return {**base, "outcome": "in_progress", "confirmationId": active["id"],
                "amount": int(active["amount"]), "count": int(active["count"])}
    await cur.execute(INTERIM_DUE_SQL, (model_id, start, cutoff))
    due = await cur.fetchone() or {}
    base["unconfirmedCount"] = int(due.get("unconfirmed_count") or 0)
    if int(due.get("due_count") or 0) == 0:
        return {**base, "outcome": "nothing_due"}
    confirmation_id = str(uuid.uuid4())
    try:
        result, amount, count = await _prepare_confirmation(
            cur, request, model_id=model_id, month=month,
            confirmation_id=confirmation_id, user_id=user_id, cutoff=cutoff)
    except HTTPException as exc:
        code = (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else None
        if code == "payout_account_not_found":
            return {**base, "outcome": "no_account",
                    "amount": int(due["due_amount"]), "count": int(due["due_count"])}
        if code == "payout_nothing_due":
            return {**base, "outcome": "nothing_due"}
        raise
    await admin_guard.write_audit(
        conn, actor_user_id=user_id, action="payout_confirmation.prepare",
        target_type="model", target_id=model_id,
        after={"confirmationId": confirmation_id, "amount": amount, "count": count,
               "via": "interim_run", "cutoffAt": cutoff.isoformat()})
    return {**base, "outcome": "prepared", "confirmationId": result["id"], "amount": amount, "count": count}


@router.post("/admin/payout-statements/{period_month}/run")
async def run_monthly_payout(period_month: str, request: Request, response: Response,
                             user_id: str = Depends(require_user), interim: bool = False):
    """월말 정산 실행 — 마감된 달의 확정 정산을 모델별 지급 확인서로. 돈은 움직이지 않는다.

    ?interim=true (2026-09-27): **이번 달**이면 중간 정산 — 지금까지 체인에 확정된 정산 중 아직
    어느 확인서에도 없는 것만 모은다. 마감된 달이면 월말 정산과 똑같고, 다음 달 이후는 거절한다."""
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        month = _parse_month(period_month)
        now = datetime.now(KST)
        current_month = now.date().replace(day=1)
        if interim and month > current_month:
            raise _err("payout_month_future", f"{period_month}분은 아직 시작하지 않았어요. 이번 달만 중간 정산할 수 있어요.", 409)
        if month >= current_month and not interim:
            opens = month_bounds(month)[1].date().isoformat()
            raise _err("payout_month_open",
                       f"마감된 월만 정산을 실행할 수 있어요. {period_month}분은 {opens} 00:00(KST)부터 실행돼요.", 409)
        interim_run = month == current_month
        start, end = month_bounds(month)
        # 기준 시각은 초 단위로 — 화면·감사 기록·확인서가 같은 값을 말한다.
        cutoff = now.replace(microsecond=0) if interim_run else None
        results = []
        async with conn.cursor() as cur:
            await cur.execute(RUN_MODELS_SQL, (start, cutoff or end, month))
            models = await cur.fetchall()
            for model in models:
                # 감사 기록도 같은 트랜잭션(conn)에 — 확인서와 원장이 함께 커밋되거나 함께 사라진다.
                if interim_run:
                    results.append(await _run_model_interim(
                        conn, cur, request, model=model, month=month, start=start, cutoff=cutoff, user_id=user_id))
                else:
                    results.append(await _run_model_month(
                        conn, cur, request, model=model, month=month, start=start, end=end, user_id=user_id))
        summary = {outcome: sum(1 for row in results if row["outcome"] == outcome)
                   for outcome in ("prepared", "in_progress", "nothing_due", "held", "paid",
                                   "unconfirmed", "no_account")}
        await admin_guard.write_audit(
            conn, actor_user_id=user_id,
            action="payout_statement.interim_run" if interim_run else "payout_statement.run",
            target_type="payout_month", target_id=period_month,
            after={"models": len(results), **summary,
                   **({"cutoffAt": cutoff.isoformat()} if interim_run else {})})
        await conn.commit()
    return {"periodMonth": period_month, "kind": "interim" if interim_run else "monthly",
            "cutoffAt": cutoff, "coveredThrough": covered_through(cutoff),
            "results": results, "summary": summary, "moneyMoved": False}


async def _locked_confirmation(conn, confirmation_id, user_id):
    async with conn.cursor() as cur:
        await cur.execute(f"select {_CONFIRMATION_FIELDS} from fm_payout_confirmations where id = %s for update", (_model_id(confirmation_id),))
        row = await cur.fetchone()
    if not row:
        raise _err("payout_confirmation_not_found", "지급 확인을 찾을 수 없어요.", 404)
    if row["responsible_admin"] != user_id:
        raise _err("payout_confirmation_owner", "이 지급 건을 확인한 관리자만 처리할 수 있어요.", 403)
    return row


# ⚠️ 이 라우트는 아래 `/{action}` 와일드카드보다 **먼저** 서야 한다 — FastAPI 는 등록
# 순서로 매칭해서, 뒤에 두면 /simulate 가 action='simulate' 로 잡혀 400 이 된다.
class PayoutSimulateRequest(CamelModel):
    """데모에서 고르는 결과. 실패도 고를 수 있어야 한다 — 성공만 보여주면 실패 경로가
    있는지 아무도 모른다."""

    outcome: Literal["paid", "account_error", "limit_exceeded"] = "paid"


@router.post("/admin/payout-confirmations/{confirmation_id}/simulate")
async def simulate_payout_confirmation(
    confirmation_id: str, body: PayoutSimulateRequest, request: Request,
    response: Response, user_id: str = Depends(require_user),
):
    """지급 시뮬레이션 — **돈을 옮기지 않고** 기존 상태머신을 그대로 걷는다(데모 전용).

    실물 지급대행을 붙일 수 없어서 만든 자리다(services/payout_provider.py 의 배경 참고).
    수동 경로(manual)는 손대지 않는다 — 프로덕션 기본값이라 여기로 들어오면 409 로 막힌다.

    성공: prepared → transfer_started → paid. 실패: prepared → cancelled + 사유, 그리고
    정산 항목을 풀어 다음 달에 다시 지급할 수 있게 한다. 실패를 '송금 시작 뒤'로 두지 않는
    이유는 스키마다 — started_at 이 찍힌 뒤 cancelled 는 CHECK 제약이 금지한다
    (20260911170000). 실물 지급대행도 계좌 오류·한도 초과는 요청 시점에 거절하므로 맞다."""
    response.headers["Cache-Control"] = "no-store"
    provider = payout_provider.resolve_provider(
        request.app.state.settings.fm_payout_provider)
    if not provider.simulated:
        raise _err(
            "payout_provider_manual",
            "지금은 수동 지급 모드예요. 시뮬레이션은 데모 설정에서만 쓸 수 있어요.", 409)
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        row = await _locked_confirmation(conn, confirmation_id, user_id)
        if row["status"] != "prepared":
            raise _err("payout_invalid_transition",
                       "아직 송금하지 않은 확인 건에서만 시뮬레이션할 수 있어요.", 409)
        try:
            outcome = provider.execute(amount=int(row["amount"]), outcome=body.outcome)
        except payout_provider.ProviderUnsupported as exc:
            raise _err("payout_provider_unsupported", str(exc), 409) from None

        async with conn.cursor() as cur:
            if outcome.paid:
                await cur.execute(
                    f"""update fm_payout_confirmations set status = 'transfer_started',
                        started_at = now() where id = %s returning {_CONFIRMATION_FIELDS}""",
                    (row["id"],))
                await cur.fetchone()
                await cur.execute(
                    f"""update fm_payout_confirmations set status = 'paid', paid_at = now(),
                        provider_ref = %s where id = %s returning {_CONFIRMATION_FIELDS}""",
                    (outcome.reference, row["id"]))
            else:
                await cur.execute(
                    f"""update fm_payout_confirmations set status = 'cancelled',
                        cancelled_at = now(), failure_reason = %s
                        where id = %s returning {_CONFIRMATION_FIELDS}""",
                    (outcome.failure_reason, row["id"]))
            result = await cur.fetchone()
            if not outcome.paid:
                await cur.execute(
                    """update fm_payout_confirmation_entries set released_at = now()
                       where confirmation_id = %s and released_at is null""", (row["id"],))
        await admin_guard.write_audit(
            conn, actor_user_id=user_id, action="payout_confirmation.simulate",
            target_type="model", target_id=row["model_id"],
            before={"confirmationId": row["id"], "status": row["status"]},
            after={"confirmationId": row["id"], "provider": provider.name,
                   "outcome": body.outcome, "amount": int(row["amount"])})
        await conn.commit()

    view = _confirmation_view(result or row, user_id)
    # 알림은 커밋 뒤에. 실패해도 상태 전이는 이미 끝났다(알림이 원장을 되돌리지 않는다).
    await facemarket_notify.notify_slack_payout_simulated(
        request.app.state.settings, period_month=view["periodMonth"],
        amount=view["amount"], count=view["count"], paid=outcome.paid,
        failure_reason=outcome.failure_reason, reference=outcome.reference)
    return view


class PayoutTransferRecord(CamelModel):
    """지급 완료 기록에 필요한 **실제 이체의 증거**(2026-09-26).

    은행 앱 이체 화면·내역에서 그대로 옮겨 적는다. 서버는 돈을 보내지 않고, 이 기록이
    확인서와 맞는지(금액·날짜)만 본다. 참조번호는 은행 거래번호나 적요 등 나중에 은행 내역에서
    이 이체를 다시 찾을 수 있는 값이면 된다."""

    transfer_reference: str = Field(max_length=200)
    amount: int
    transferred_on: date


_REFERENCE_PATTERN = re.compile(r"[^\x00-\x1f\x7f]{4,64}")


def _validated_transfer(row, body):
    if body is None:
        raise _err("payout_transfer_record_required",
                   "은행 앱에서 이체한 참조번호·금액·이체일을 적어 주세요.", 400)
    reference = body.transfer_reference.strip()
    if not _REFERENCE_PATTERN.fullmatch(reference):
        raise _err("invalid_transfer_reference", "이체 참조번호를 4~64자로 적어 주세요.", 400)
    if int(body.amount) != int(row["amount"]):
        raise _err("payout_amount_mismatch",
                   f"확인서 금액({int(row['amount']):,}원)과 이체 금액이 달라요. 은행 앱에서 보낸 금액을 다시 확인해 주세요.",
                   409)
    today = datetime.now(KST).date()
    created = row["created_at"]
    created_on = created.astimezone(KST).date() if hasattr(created, "astimezone") else today
    if body.transferred_on > today or body.transferred_on < created_on:
        raise _err("invalid_transfer_date",
                   "이체일은 지급 확인을 만든 날부터 오늘 사이여야 해요.", 400)
    return reference, body.transferred_on


@router.post("/admin/payout-confirmations/{confirmation_id}/{action}")
async def advance_payout_confirmation(confirmation_id: str, action: str, request: Request,
                                      response: Response, user_id: str = Depends(require_user),
                                      body: PayoutTransferRecord | None = None):
    """송금 시작 · 지급 완료 기록 · 취소. 상태머신은 prepared → transfer_started → paid 그대로다.

    지급 완료(paid)는 2026-09-26 부터 **실제 이체 기록(body)** 이 있어야 한다 — 참조번호·금액·
    이체일. 이게 없으면 "버튼을 눌렀다"와 "돈이 갔다"를 구분할 방법이 원장에 없었다. 이미 paid 인
    건의 재시도는 기록을 다시 요구하지 않고 원래 기록을 돌려준다(응답 유실 복구)."""
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
        reference = transferred_on = None
        if action == "paid":
            reference, transferred_on = _validated_transfer(row, body)
        async with conn.cursor() as cur:
            if action == "start":
                await cur.execute("select account_version::text from fm_payout_accounts where model_id = %s for share", (row["model_id"],))
                account = await cur.fetchone()
                if not account or account["account_version"] != row["account_version"]:
                    raise _err("payout_account_changed", "계좌가 변경됐어요. 송금 전 확인을 취소하고 새 계좌로 다시 확인해 주세요.", 409)
            if action == "paid":
                # 참조번호·이체일은 paid 와 **같은 UPDATE** 로 — 기록 없는 paid 가 한순간도 없다.
                await cur.execute(
                    f"""update fm_payout_confirmations set status = 'paid', paid_at = now(),
                        provider_ref = %s, transferred_on = %s where id = %s returning {_CONFIRMATION_FIELDS}""",
                    (reference, transferred_on, row["id"]))
            else:
                timestamp = {"start": "started_at", "cancel": "cancelled_at"}[action]
                await cur.execute(f"update fm_payout_confirmations set status = %s, {timestamp} = now() where id = %s returning {_CONFIRMATION_FIELDS}", (target, row["id"]))
            result = await cur.fetchone()
            if action == "cancel":
                await cur.execute("update fm_payout_confirmation_entries set released_at = now() where confirmation_id = %s and released_at is null", (row["id"],))
        await admin_guard.write_audit(conn, actor_user_id=user_id, action=f"payout_confirmation.{action}",
            target_type="model", target_id=row["model_id"], before={"confirmationId": row["id"], "status": row["status"]},
            after={"confirmationId": row["id"], "status": target, "amount": int(row["amount"]),
                   **({"transferredOn": transferred_on.isoformat(),
                       "transferReference": mask_reference(reference)} if action == "paid" else {})})
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
