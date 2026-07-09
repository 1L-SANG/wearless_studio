"""FaceMarket — 검증 실명 모델 마켓 (2026 블록체인·AI 해커톤).

`FACEMARKET_ENABLED` 게이트: off면 main.py가 이 라우터를 아예 등록하지 않는다
(기존 셀러 플로우 무영향 — 프로드 보호).

FM-11 본인확인(CX 표준인증창 ENT_MID):
프론트는 위젯 성공 콜백의 **token만** 백엔드로 보낸다(원문 PII는 절대 클라→서버 신뢰 안 함).
백엔드가 CX `trans/{token}`을 **서버발** 호출해 실 신원을 받고:
  · dedup = HMAC-SHA256(ci, pepper) → fm_models.ci_hash 단일 보관(원문 CI 미저장)
  · 리플레이 차단 = fm_identity_verifications.cx_tx_id UNIQUE(같은 token 재사용 시 409)
  · 화이트리스트 마스킹 필드만 감사 저장(이름 마스킹·생년(연도)·VC종류 — 원문 생년월일 미보관)
FM-03 실측(2026-07-09): ENT_MID 응답에 `ci` 존재 확인 → ci HMAC 채택.
"""

import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from .auth import require_user
from .db import get_conn
from .models import CamelModel, ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket"])

CX_TRANS_TIMEOUT = 10.0

_FM_RESPONSES = {
    400: {"model": ErrorResponse, "description": "본인확인 실패 (토큰 무효·CI 누락)"},
    401: {"model": ErrorResponse, "description": "인증 실패"},
    409: {"model": ErrorResponse, "description": "이미 처리된 인증 (토큰 재사용)"},
}


class IdentityVerifyRequest(CamelModel):
    """CX 표준인증창(ENT_MID) 성공 콜백의 token. 이것만 신뢰한다."""

    token: str


class IdentityVerifyResult(CamelModel):
    verified: bool
    model_id: str
    status: str
    name_masked: str  # 마스킹된 이름만 반환 — 원문 PII는 응답에도 싣지 않음


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _fetch_trans(base_url: str, token: str) -> dict:
    """CX `trans/{token}` 서버발 호출 → 실 신원 필드(dict). 테스트 monkeypatch 지점."""
    url = f"{base_url}/oacx/api/v1.0/trans/{token}"
    async with httpx.AsyncClient(timeout=CX_TRANS_TIMEOUT) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        logger.warning("cx_trans_failed", extra={"status": resp.status_code})
        raise _err("cx_verify_failed", "본인확인에 실패했어요. 다시 시도해 주세요.")
    try:
        return resp.json()
    except ValueError:
        raise _err("cx_verify_failed", "본인확인 응답을 해석하지 못했어요.")


def _dig(data: dict, *keys):
    """flat 또는 result/data 중첩 응답 모두 대응 — 첫 존재 키 값 반환."""
    scopes = [data]
    for wrap in ("result", "data"):
        inner = data.get(wrap)
        if isinstance(inner, dict):
            scopes.append(inner)
    for scope in scopes:
        for k in keys:
            v = scope.get(k)
            if v not in (None, ""):
                return v
    return None


def _mask_name(name: str) -> str:
    name = (name or "").strip()
    if len(name) <= 1:
        return name or "익명"
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def _ci_hmac(ci: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), ci.encode(), hashlib.sha256).hexdigest()


@router.post(
    "/identity/verify",
    response_model=IdentityVerifyResult,
    responses={**_FM_RESPONSES},
    tags=["FaceMarket"],
    summary="모바일 신분증 본인확인 → 모델 등록",
)
async def identity_verify(
    request: Request,
    body: IdentityVerifyRequest,
    user_id: str = Depends(require_user),
):
    """CX 표준인증창 성공 token으로 서버가 본인확인을 완료하고 모델을 verified 등록한다.

    - **Bearer Token**: 필수 (모델 본인 계정)
    - **입력**: `{ token }` — 위젯 콜백 token만. 원문 신원은 서버가 CX에서 받는다.
    - **에지 케이스**: `400 ci_missing`(신원 확인 불가) · `409 identity_replay`(토큰 재사용)
    """
    settings = request.app.state.settings
    pepper = settings.fm_ci_pepper
    if not pepper:
        raise _err("facemarket_misconfigured", "서비스 설정 오류입니다.", status=503)

    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")

    trans = await _fetch_trans(settings.cx_trans_base_url, token)

    ci = _dig(trans, "ci")
    if not ci:
        raise _err("ci_missing", "본인확인 정보를 확인하지 못했어요.")
    ci_hash = _ci_hmac(str(ci), pepper)

    # 화이트리스트 마스킹 필드만 — 원문 CI/생년월일 미보관(생년=연도만).
    raw_name = _dig(trans, "utf8Nm", "nm", "name", "userName", "engnm") or ""
    name_masked = _mask_name(raw_name)
    birth = _dig(trans, "birth", "birthdate")
    fields = {
        "nameMasked": name_masked,
        "birthYear": str(birth)[:4] if birth else None,
        "vcType": _dig(trans, "vcTypeCodeList"),
    }

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # dedup: 같은 사람(ci_hash) 재인증이면 기존 모델 재사용, 아니면 신규 verified 생성.
            await cur.execute(
                "select id, status from fm_models where ci_hash = %s", (ci_hash,)
            )
            existing = await cur.fetchone()
            if existing:
                model_id = existing["id"]
                await cur.execute(
                    "update fm_models set status = 'verified', user_id = %s where id = %s",
                    (user_id, model_id),
                )
            else:
                await cur.execute(
                    """insert into fm_models (user_id, display_name, status, ci_hash)
                       values (%s, %s, 'verified', %s) returning id""",
                    (user_id, name_masked, ci_hash),
                )
                model_id = (await cur.fetchone())["id"]

            # 리플레이 차단: cx_tx_id(token) UNIQUE — 같은 인증 토큰 재사용은 409.
            try:
                await cur.execute(
                    """insert into fm_identity_verifications (model_id, cx_tx_id, fields)
                       values (%s, %s, %s)""",
                    (model_id, token, Json(fields)),
                )
            except UniqueViolation:
                raise _err("identity_replay", "이미 처리된 인증입니다.", status=409)
        await conn.commit()

    return {
        "verified": True,
        "modelId": str(model_id),
        "status": "verified",
        "nameMasked": name_masked,
    }
