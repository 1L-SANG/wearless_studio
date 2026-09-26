"""정산 한 건을 DB 와 체인에서 나란히 읽어 대조한다 (2026-09-26).

fm_settlements 는 컨트랙트(FaceMarketSettlement.sol) 반환값의 **미러**다. 미러가 정말
체인과 같은지는 보여 주기 전까지 아무도 모른다. 그래서 조회 버튼을 누를 때마다 서버가
getSettlement 를 **그 자리에서 eth_call** 로 읽고, DB 값과 칸마다 비교해 돌려준다.

  · 셀러 — 자기 잡의 정산만(잡 소유자 = jobs.user_id, 영수증 라우트와 같은 기준)
  · 모델 — 자기 얼굴의 정산만(fm_licenses → fm_models.user_id, 미리보기 라우트와 같은 기준)
  · 관리자 — 전체 목록 + 행마다 조회. admin_guard(기기 게이트 포함)를 지난다.

🔴 체인을 못 읽으면 **실패라고 말한다.** 캐시·DB 값으로 "일치"를 흉내 내지 않는다 —
   RPC 가 죽었는데 초록 배지가 뜨는 순간 이 화면 전체가 거짓말이 된다. 체인에 기록이 없으면
   (exists=False) "일치"가 아니라 "체인에 기록 없음"이다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import admin_guard
from .auth import require_user
from .db import get_conn

logger = logging.getLogger("facemarket.settlement_chain")

router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket settlement chain"])

#: web3 HTTPProvider 자체 타임아웃은 20초다(facemarket_chain.py). 스레드가 그보다 오래
#: 붙잡히면 화면이 멈춘 것처럼 보이므로 바깥에서 한 번 더 끊는다.
CHAIN_READ_TIMEOUT = 25.0

#: 컨트랙트 원문에서 그대로 옮긴 분배 규칙. 화면이 "70/20/10" 을 자기 말로 지어내지 않고
#: 이 줄들을 인용한다 — tests/test_facemarket_settlement_chain.py 가 원문과 한 글자씩 맞춰 본다.
CONTRACT_RULE_LINES = (
    "uint256 public constant MODEL_BPS = 7_000;",
    "uint256 public constant PLATFORM_BPS = 2_000;",
    "modelAmount = (total * MODEL_BPS) / 10_000;",
    "platformAmount = (total * PLATFORM_BPS) / 10_000;",
    "opsAmount = total - modelAmount - platformAmount; // remainder — dust-safe",
)
CONTRACT_RULE_SUMMARY = "모델 70% · 플랫폼 20% · 운영 = 나머지(나눗셈 잔돈은 운영에)"

# (응답 키, DB 컬럼, getSettlement 키, 화면 이름)
_FIELDS = (
    ("total", "total_amount", "total", "총액"),
    ("model", "model_amount", "model_amount", "모델 정산 70%"),
    ("platform", "platform_amount", "platform_amount", "플랫폼 20%"),
    ("ops", "ops_amount", "ops_amount", "운영 10%(잔돈 포함)"),
    ("block", "recorded_block", "block", "기록 블록"),
    ("modelRef", "model_ref", "model_ref", "모델 참조(해시)"),
)

_ROW_COLS = """st.id::text as id, st.payment_id, st.tx_hash, st.chain_id, st.recorded_block,
    st.model_ref, st.total_amount, st.model_amount, st.platform_amount, st.ops_amount,
    st.chain_status, st.created_at"""


def _err(code, message, status):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _norm(key, value):
    if value is None:
        return None
    if key == "modelRef":
        text = str(value).strip().lower()
        return text if text.startswith("0x") else "0x" + text
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def compare_settlement(row: dict, stored: dict, *, chain_id) -> dict:
    """DB 행과 getSettlement 결과를 칸마다 비교한다. 순수 함수 — 체인·DB 를 부르지 않는다.

    판정은 셋이다: match(전부 같음) · mismatch(하나라도 다름) · not_found(체인에 기록 없음).
    체인에 기록이 없으면 칸 비교를 하지 않는다 — 0 과 0 이 같다고 "일치"가 되면 안 된다.
    """
    exists = bool(stored.get("exists"))
    fields = []
    for key, db_key, chain_key, label in _FIELDS:
        db_value = _norm(key, row.get(db_key))
        chain_value = _norm(key, stored.get(chain_key)) if exists else None
        fields.append({
            "key": key, "label": label, "db": db_value, "chain": chain_value,
            "match": exists and db_value is not None and db_value == chain_value,
        })
    db_chain_id = _norm("chainId", row.get("chain_id"))
    live_chain_id = _norm("chainId", chain_id)
    fields.append({
        "key": "chainId", "label": "체인 ID", "db": db_chain_id, "chain": live_chain_id,
        "match": exists and db_chain_id is not None and db_chain_id == live_chain_id,
    })
    if not exists:
        verdict = "not_found"
    elif all(field["match"] for field in fields):
        verdict = "match"
    else:
        verdict = "mismatch"
    return {"exists": exists, "match": verdict == "match", "verdict": verdict, "fields": fields}


async def live_check(request: Request, row: dict) -> dict:
    """getSettlement 를 지금 eth_call 로 읽어 row 와 대조한다. 실패는 실패로 올린다."""
    chain = getattr(request.app.state, "fm_chain", None)
    if chain is None:
        raise _err("chain_unavailable", "체인 연결이 설정되지 않아 지금은 조회할 수 없어요.", 503)
    try:
        stored = await asyncio.wait_for(
            asyncio.to_thread(chain.get_settlement, row["payment_id"]), CHAIN_READ_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — RPC·타임아웃·디코딩 전부 "읽지 못함"
        # RPC 주소에 키가 섞일 수 있어 예외 메시지는 남기지 않고 종류만 남긴다.
        logger.warning("settlement_chain_read_failed", extra={
            "settlement_id": row.get("id"), "error_type": type(exc).__name__})
        raise _err("chain_rpc_failed",
                   "체인 노드에서 응답을 받지 못했어요. 잠시 후 다시 조회해 주세요.", 502) from None
    result = compare_settlement(row, stored, chain_id=getattr(chain, "chain_id", None))
    return {
        "settlementId": row["id"], "paymentId": row["payment_id"], "txHash": row.get("tx_hash"),
        "chainId": getattr(chain, "chain_id", None),
        "contractAddress": getattr(chain, "address", None),
        "method": "eth_call getSettlement",
        "checkedAt": datetime.now(timezone.utc),
        **result,
    }


def _settlement_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise _err("not_found", "정산 기록을 찾을 수 없어요.", 404) from None


@router.get("/settlements/{payment_id}/chain-check", summary="셀러: 내 잡 정산을 체인에서 다시 읽기")
async def seller_chain_check(payment_id: str, request: Request, response: Response,
                             user_id: str = Depends(require_user)):
    """영수증의 [체인에서 확인]. 잡 소유자(셀러)만 — 남의 결제 id 는 404."""
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_ROW_COLS}
                      from fm_settlements st join jobs j on j.id = st.job_id
                     where st.payment_id = %s and j.user_id = %s""",
                (payment_id, user_id),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "정산 기록을 찾을 수 없어요.", 404)
    return await live_check(request, row)


@router.get("/model/settlements/{settlement_id}/chain-check", summary="모델: 내 정산을 체인에서 다시 읽기")
async def model_chain_check(settlement_id: str, request: Request, response: Response,
                            user_id: str = Depends(require_user)):
    """정산 내역의 [체인 확인]. 그 얼굴의 모델 본인만 — 남의 정산은 404."""
    response.headers["Cache-Control"] = "no-store"
    settlement_id = _settlement_id(settlement_id)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_ROW_COLS}
                      from fm_settlements st
                      join fm_licenses l on l.id = st.license_id
                      join fm_models m on m.id = l.model_id
                     where st.id = %s and m.user_id = %s""",
                (settlement_id, user_id),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "정산 기록을 찾을 수 없어요.", 404)
    return await live_check(request, row)


def contract_info(request: Request) -> dict:
    settings = request.app.state.settings
    chain = getattr(request.app.state, "fm_chain", None)
    return {
        "address": getattr(settings, "fm_settlement_address", None),
        "chainId": getattr(settings, "fm_chain_id", None) or getattr(chain, "chain_id", None),
        "connected": chain is not None,
        "ruleSummary": CONTRACT_RULE_SUMMARY,
        "ruleSource": "contracts/FaceMarketSettlement.sol",
        "ruleLines": list(CONTRACT_RULE_LINES),
        "modelBps": 7000, "platformBps": 2000,
    }


@router.get("/admin/settlements", summary="관리자: 정산 미러 목록(최신순)과 컨트랙트 정보")
async def admin_list_settlements(request: Request, response: Response, limit: int = 100,
                                 user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    limit = max(1, min(int(limit), 200))
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_ROW_COLS}, m.id::text as model_id, m.display_name as model_name,
                           coalesce(nullif(p.name, ''), nullif(pr.title, '')) as product_name
                      from fm_settlements st
                      left join fm_licenses l on l.id = st.license_id
                      left join fm_models m on m.id = l.model_id
                      left join jobs j on j.id = st.job_id
                      left join projects pr on pr.id = j.project_id
                      left join products p on p.project_id = pr.id
                     order by st.created_at desc, st.id desc limit %s""",
                (limit,),
            )
            rows = await cur.fetchall()
            await cur.execute(
                """select count(*) as count,
                          count(*) filter (where chain_status = 'confirmed') as confirmed_count,
                          coalesce(sum(total_amount), 0) as total_amount,
                          coalesce(sum(model_amount), 0) as model_amount
                     from fm_settlements""",
                (),
            )
            totals = await cur.fetchone() or {}
    items = [{
        "id": row["id"], "paymentId": row["payment_id"], "createdAt": row["created_at"],
        "modelId": row.get("model_id"), "modelName": row.get("model_name"),
        "productName": row.get("product_name"),
        # sim: 은 관리자 시뮬레이션 정산(장면④) — 실제 상세페이지 생성과 섞어 보이지 않게 구분한다.
        "kind": "simulation" if str(row["payment_id"]).startswith("sim:") else "generation",
        "totalAmount": int(row["total_amount"]), "modelAmount": int(row["model_amount"]),
        "platformAmount": int(row["platform_amount"]), "opsAmount": int(row["ops_amount"]),
        "chainStatus": row["chain_status"], "txHash": row.get("tx_hash"),
        "chainId": row.get("chain_id"), "recordedBlock": row.get("recorded_block"),
    } for row in rows]
    return {
        "items": items,
        "totals": {
            "count": int(totals.get("count") or 0),
            "confirmedCount": int(totals.get("confirmed_count") or 0),
            "totalAmount": int(totals.get("total_amount") or 0),
            "modelAmount": int(totals.get("model_amount") or 0),
        },
        "contract": contract_info(request),
    }


@router.get("/admin/settlements/{settlement_id}/chain-check", summary="관리자: 정산 한 건을 체인에서 다시 읽기")
async def admin_chain_check(settlement_id: str, request: Request, response: Response,
                            user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        settlement_id = _settlement_id(settlement_id)
        async with conn.cursor() as cur:
            await cur.execute(f"select {_ROW_COLS} from fm_settlements st where st.id = %s",
                              (settlement_id,))
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "정산 기록을 찾을 수 없어요.", 404)
    return await live_check(request, row)
