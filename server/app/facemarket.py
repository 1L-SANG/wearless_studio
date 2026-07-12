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

import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from .auth import require_user
from .db import get_conn
from .models import CamelModel, ErrorResponse
from .r2 import MIME_EXT, ext_for_mime, face_key, sha256_sri

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


class ModelCard(CamelModel):
    """카탈로그/마이페이지 카드 — 공개 화이트리스트 컬럼만(PII·ci_hash 제외)."""

    id: str
    display_name: str
    status: str
    cover_image_url: str | None = None
    created_at: datetime


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


# uuid 컬럼은 ::text 캐스트해 반환(repo.py 관례). psycopg 는 uuid 를 uuid.UUID 로 로드하는데
# CamelModel(id: str) 이 UUID 를 거부 → ResponseValidationError 500. 캐스트로 문자열화.
_MODEL_CARD_COLS = "id::text as id, display_name, status, cover_image_url, created_at"


@router.get(
    "/models",
    response_model=list[ModelCard],
    responses={401: {"model": ErrorResponse, "description": "인증 실패"}},
    tags=["FaceMarket"],
    summary="검증 모델 카탈로그 (셀러용)",
)
async def list_models(request: Request, user_id: str = Depends(require_user)):
    """검증(verified) 모델 목록. 셀러가 상세페이지 제작 시 고르는 카탈로그 피드.

    화이트리스트 컬럼만 반환 — `ci_hash`·`user_id`·`did` 등 PII/식별자는 노출하지 않는다.
    (FM-13 팀원 계약: 프론트 카탈로그가 이 shape를 소비.)
    """
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_MODEL_CARD_COLS} from fm_models
                    where status = 'verified'
                    order by created_at desc limit 200"""
            )
            return await cur.fetchall()


@router.get(
    "/models/me",
    response_model=list[ModelCard],
    responses={401: {"model": ErrorResponse, "description": "인증 실패"}},
    tags=["FaceMarket"],
    summary="내 모델 목록 (마이페이지)",
)
async def my_models(request: Request, user_id: str = Depends(require_user)):
    """로그인 사용자 본인이 소유한 모델(모든 상태). 모델 마이페이지용."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_MODEL_CARD_COLS} from fm_models
                    where user_id = %s
                    order by created_at desc""",
                (user_id,),
            )
            return await cur.fetchall()


# ── 얼굴 라이선스 (FM: 얼굴 업로드 + 조건) ─────────────────────────
# 얼굴 이미지 = 생체 PII. 공개 R2 URL 절대 노출 금지 → 비공개 버킷 저장 + 게이트 스트림.
# face_image_uri = 게이트 라우트 URL(공개 URL 아님). face_image_key = 내부 비공개 키(응답 제외).
MAX_FACE_BYTES = 15 * 1024 * 1024  # 15MB (routes.py MAX_UPLOAD_BYTES 미러)
MAX_USE_ITEMS = 20                 # allowed/forbidden 용도 태그 개수 상한
MAX_USE_LEN = 60                   # 용도 태그 1개 길이 상한
_EXT_TO_MIME = {ext: mime for mime, ext in MIME_EXT.items()}  # 게이트 응답 Content-Type 역매핑

# 응답 화이트리스트 — face_image_key(비공개)·모델 PII 제외. uuid(id/model_id)는 ::text 캐스트
# (psycopg→uuid.UUID, CamelModel str 필드가 거부 → 500 방지, repo.py 관례).
# RETURNING 용(단일 테이블, 별칭 없음).
_LICENSE_CARD_COLS = (
    "id::text as id, model_id::text as model_id, face_image_uri, face_image_digest, "
    "allowed_use, forbidden_use, unit_price, license_valid_until, status, vc_id, created_at"
)
# 목록 조인 쿼리용 — 모든 컬럼 l. 한정(fm_models 와 id/status/created_at 등 이름 충돌 → 모호성 500 방지).
_LICENSE_CARD_COLS_L = (
    "l.id::text as id, l.model_id::text as model_id, l.face_image_uri, l.face_image_digest, "
    "l.allowed_use, l.forbidden_use, l.unit_price, l.license_valid_until, l.status, l.vc_id, l.created_at"
)


class LicenseCard(CamelModel):
    """라이선스 카드 — 소유자 마이페이지/카탈로그용. 비공개 키·원본 얼굴 바이트 미포함."""

    id: str
    model_id: str
    face_image_uri: str        # 게이트 URL(GET /v1/facemarket/licenses/{id}/face)
    face_image_digest: str     # 'sha256-...' SRI
    allowed_use: list[str]
    forbidden_use: list[str]
    unit_price: int
    license_valid_until: datetime
    status: str
    vc_id: str | None = None
    created_at: datetime


def _r2_face(request: Request):
    """얼굴 전용 R2 클라이언트(app.state.r2_face). 미설정이면 503 (공개 버킷 폴백 금지)."""
    r2 = getattr(request.app.state, "r2_face", None)
    if r2 is None:
        raise _err("storage_unavailable", "얼굴 저장소가 설정되지 않았습니다.", status=503)
    return r2


def _clean_uses(items: list[str]) -> list[str]:
    """용도 태그 정규화: strip·빈값 제거·중복 제거(순서 유지)·개수/길이 상한."""
    out: list[str] = []
    for raw in items or []:
        v = (raw or "").strip()[:MAX_USE_LEN]
        if v and v not in out:
            out.append(v)
        if len(out) >= MAX_USE_ITEMS:
            break
    return out


async def _my_verified_model_id(request: Request, user_id: str) -> str | None:
    """호출자 본인의 verified 모델 id(가장 최근). 없으면 None."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select id from fm_models
                   where user_id = %s and status = 'verified'
                   order by created_at desc limit 1""",
                (user_id,),
            )
            row = await cur.fetchone()
    return row["id"] if row else None


@router.post(
    "/licenses",
    response_model=LicenseCard,
    status_code=201,
    responses={**_FM_RESPONSES, 413: {"model": ErrorResponse, "description": "파일이 너무 큼"}},
    tags=["FaceMarket"],
    summary="얼굴 라이선스 생성 (얼굴 업로드 + 조건)",
)
async def create_license(
    request: Request,
    face: UploadFile = File(..., description="라이선스 얼굴 이미지(비공개 저장)"),
    allowed_use: list[str] = Form(default=[], description="허용 용도 태그"),
    forbidden_use: list[str] = Form(default=[], description="금지 용도 태그"),
    unit_price: int = Form(default=10000, ge=0, le=100_000_000, description="건당 단가(KRW)"),
    valid_days: int = Form(default=365, ge=1, le=3650, description="사용권 유효기간(일)"),
    user_id: str = Depends(require_user),
):
    """검증(verified) 모델 본인이 얼굴 + 라이선스 조건을 등록한다.

    - **Bearer Token**: 필수 (검증 모델 본인)
    - **멀티파트**: `face`(이미지) + 조건 필드. 얼굴은 비공개 버킷에 저장되고
      응답/카탈로그에는 게이트 URL만 실린다(원본 바이트·내부 키 비노출).
    - **에지 케이스**: `400 no_verified_model`(본인확인 선행 필요) ·
      `400 bad_image`(허용 밖 형식) · `413 file_too_large`
    """
    r2 = _r2_face(request)

    mime = (face.content_type or "").lower()
    ext = ext_for_mime(mime)
    if not ext:
        raise _err("bad_image", "허용되지 않는 이미지 형식입니다. (png/jpg/webp)")

    data = await face.read()
    if not data:
        raise _err("bad_image", "빈 파일입니다.")
    if len(data) > MAX_FACE_BYTES:
        raise _err("file_too_large", "이미지는 15MB 이하만 가능합니다.", status=413)

    model_id = await _my_verified_model_id(request, user_id)
    if not model_id:
        raise _err(
            "no_verified_model",
            "먼저 모바일 신분증 본인확인을 완료해 주세요.",
            status=400,
        )

    license_id = str(uuid.uuid4())
    key = face_key(str(model_id), license_id, ext)
    digest = sha256_sri(data)
    # boto3 동기 → to_thread (이벤트 루프 보호)
    await asyncio.to_thread(r2.put_bytes, key, data, mime)

    gate_uri = f"/v1/facemarket/licenses/{license_id}/face"
    valid_until = datetime.now(timezone.utc) + timedelta(days=valid_days)
    allowed = _clean_uses(allowed_use)
    forbidden = _clean_uses(forbidden_use)

    try:
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""insert into fm_licenses
                        (id, model_id, face_image_uri, face_image_key, face_image_digest,
                         allowed_use, forbidden_use, unit_price, license_valid_until)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        returning {_LICENSE_CARD_COLS}""",
                    (
                        license_id, model_id, gate_uri, key, digest,
                        allowed, forbidden, unit_price, valid_until,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()
    except Exception:
        # DB 실패 시 방금 올린 얼굴 객체 best-effort 정리(고아 방지)
        try:
            await asyncio.to_thread(r2.delete, key)
        except Exception:
            logger.warning("face_orphan_cleanup_failed", extra={"key": key})
        raise
    return row


@router.get(
    "/licenses",
    response_model=list[LicenseCard],
    responses={401: {"model": ErrorResponse, "description": "인증 실패"}},
    tags=["FaceMarket"],
    summary="내 라이선스 목록",
)
async def list_licenses(request: Request, user_id: str = Depends(require_user)):
    """본인 소유 모델의 라이선스 목록. RLS 우회(service-role)라 SQL에서 소유 조인으로 스코프한다."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_LICENSE_CARD_COLS_L} from fm_licenses l
                    join fm_models m on m.id = l.model_id
                    where m.user_id = %s
                    order by l.created_at desc limit 200""",
                (user_id,),
            )
            return await cur.fetchall()


@router.get(
    "/licenses/{license_id}/face",
    responses={
        401: {"description": "인증 실패"},
        404: {"description": "없음/권한없음/폐기·만료"},
    },
    tags=["FaceMarket"],
    summary="라이선스 얼굴 이미지 (게이트)",
)
async def get_license_face(
    request: Request,
    license_id: str,
    user_id: str = Depends(require_user),
):
    """얼굴 이미지 바이트 스트림. 소유자(검증 모델 본인)만·active·미만료일 때만.

    비존재/비소유/폐기·만료 모두 **404**(존재 노출 방지). 공개 URL을 절대 만들지 않고
    인증된 이 라우트로만 바이트를 흘린다(<img>는 Bearer 불가 → 프론트는 fetch+objectURL).
    """
    r2 = _r2_face(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select l.face_image_key, l.status, l.license_valid_until
                   from fm_licenses l
                   join fm_models m on m.id = l.model_id
                   where l.id = %s and m.user_id = %s""",
                (license_id, user_id),
            )
            row = await cur.fetchone()

    if not row or not row["face_image_key"]:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    if row["status"] != "active":
        raise _err("not_found", "찾을 수 없습니다.", status=404)  # revoked/expired = 접근 차단
    valid_until = row["license_valid_until"]
    if valid_until and valid_until <= datetime.now(timezone.utc):
        raise _err("not_found", "찾을 수 없습니다.", status=404)

    key = row["face_image_key"]
    mime = _EXT_TO_MIME.get(key.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    # 비공개 — 캐시·색인 금지
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store, private"})


# ============================================================================
# 온체인 정산 (선택과제2 — OmniOne Chain record-only). 훅=record_license_settlement.
# canonical 산식=컨트랙트, DB는 반환값 미러(이중장부). 체인 미설정이면 조용히 no-op.
# ============================================================================

_SETTLEMENT_COLS = (
    "id::text, payment_id, license_id::text, job_id::text, model_ref, "
    "total_amount, model_amount, platform_amount, ops_amount, "
    "chain_status, tx_hash, chain_id, recorded_block, created_at"
)


class SettlementCard(CamelModel):
    """정산 미러 레코드 — 영수증 UI/이중장부. canonical 값은 컨트랙트에서 온다."""

    id: str
    payment_id: str
    license_id: str | None = None
    job_id: str | None = None
    model_ref: str
    total_amount: int
    model_amount: int
    platform_amount: int
    ops_amount: int
    chain_status: str
    tx_hash: str | None = None
    chain_id: str | None = None
    recorded_block: int | None = None
    created_at: datetime


class SimulateRequest(CamelModel):
    """데모/부하 정산(장면④, KPI '시뮬' 집계). 실 상세페이지 잡 없이 라이선스 1건 정산."""

    license_id: str


async def record_license_settlement(
    app,
    *,
    payment_key: str,
    license_id: str,
    model_id: str,
    total: int,
    job_id: str | None = None,
    credit_ledger_id: str | None = None,
) -> dict | None:
    """라이선스 사용 1건을 온체인 기록 + fm_settlements 미러. best-effort(생성 흐름 비파손).

    payment_key = 결정적(멱등) 문자열. 컨트랙트 중복 revert + DB payment_id UNIQUE 가 쌍.
    체인 미설정(app.state.fm_chain None)이면 None 반환(no-op). 온체인 성공 시에만 미러 기록.
    """
    chain = getattr(app.state, "fm_chain", None)
    if chain is None:
        logger.info("settlement_skipped_no_chain", extra={"payment_key": payment_key})
        return None

    pool = app.state.pool
    # DB 선확인 — 이미 미러된 payment 면 재기록 없이 반환(재시도 멱등).
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {_SETTLEMENT_COLS} from fm_settlements where payment_id = %s",
                (payment_key,),
            )
            existing = await cur.fetchone()
    if existing:
        return existing

    try:
        result = await asyncio.to_thread(
            chain.record_settlement, payment_key=payment_key, model_uuid=model_id, total=int(total)
        )
    except Exception:
        # 중복 paymentId revert 등 — 이미 온체인에 있는지 getSettlement 로 대조 후 미러 복구.
        try:
            stored = await asyncio.to_thread(chain.get_settlement, payment_key)
        except Exception:
            logger.exception("settlement_record_failed", extra={"payment_key": payment_key})
            return None
        if not stored.get("exists"):
            logger.exception("settlement_record_failed", extra={"payment_key": payment_key})
            return None
        result = {
            "tx_hash": None, "block": stored["block"], "chain_id": chain.chain_id,
            "model_ref": stored["model_ref"], "model_amount": stored["model_amount"],
            "platform_amount": stored["platform_amount"], "ops_amount": stored["ops_amount"],
            "total": stored["total"],
        }

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""insert into fm_settlements
                    (payment_id, job_id, license_id, credit_ledger_id, model_ref,
                     total_amount, model_amount, platform_amount, ops_amount,
                     chain_status, tx_hash, chain_id, recorded_block)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s, %s)
                    on conflict (payment_id) do nothing
                    returning {_SETTLEMENT_COLS}""",
                (
                    payment_key, job_id, license_id, credit_ledger_id, result["model_ref"],
                    int(result["total"]), result["model_amount"], result["platform_amount"],
                    result["ops_amount"], result["tx_hash"], str(result["chain_id"]),
                    result["block"],
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    logger.info(
        "settlement_recorded",
        extra={"payment_key": payment_key, "tx_hash": result["tx_hash"], "total": total},
    )
    return row


@router.get(
    "/settlements",
    response_model=list[SettlementCard],
    responses={401: {"model": ErrorResponse, "description": "인증 실패"}},
    tags=["FaceMarket"],
    summary="내 정산 내역 (온체인 미러)",
)
async def list_settlements(request: Request, user_id: str = Depends(require_user)):
    """본인 소유 모델의 라이선스 정산 내역. 이중장부의 DB측(canonical=컨트랙트)."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_SETTLEMENT_COLS} from fm_settlements st
                    join fm_licenses l on l.id = st.license_id
                    join fm_models m on m.id = l.model_id
                    where m.user_id = %s
                    order by st.created_at desc limit 200""",
                (user_id,),
            )
            return await cur.fetchall()


@router.get(
    "/settlements/{payment_id}/confirm",
    responses={
        401: {"model": ErrorResponse, "description": "인증 실패"},
        404: {"model": ErrorResponse, "description": "온체인 미기록/체인 미설정"},
    },
    tags=["FaceMarket"],
    summary="정산 온체인 확인 (eth_call 프록시)",
)
async def confirm_settlement(
    request: Request, payment_id: str, user_id: str = Depends(require_user)
):
    """getSettlement eth_call 백엔드 프록시(콘솔 키·RPC 브라우저 비노출). 영수증 confirmed 표시용."""
    chain = getattr(request.app.state, "fm_chain", None)
    if chain is None:
        raise _err("chain_unavailable", "체인이 설정되지 않았습니다.", status=404)
    stored = await asyncio.to_thread(chain.get_settlement, payment_id)
    if not stored.get("exists"):
        raise _err("not_found", "온체인 기록을 찾을 수 없습니다.", status=404)
    # 나머지 FM API 와 동일하게 camelCase 로 — 영수증 UI 가 그대로 소비.
    return {
        "exists": stored["exists"], "modelRef": stored["model_ref"], "total": stored["total"],
        "modelAmount": stored["model_amount"], "platformAmount": stored["platform_amount"],
        "opsAmount": stored["ops_amount"], "block": stored["block"],
    }


@router.post(
    "/settlements/simulate",
    response_model=SettlementCard,
    status_code=201,
    responses={
        **_FM_RESPONSES,
        404: {"model": ErrorResponse, "description": "라이선스 없음/비소유/체인 미설정"},
    },
    tags=["FaceMarket"],
    summary="정산 시뮬레이션 (데모/부하 — 실 TX)",
)
async def simulate_settlement(
    request: Request, body: SimulateRequest, user_id: str = Depends(require_user)
):
    """라이선스 1건 사용을 온체인에 실제 기록(장면④·KPI '시뮬' 집계용). 실 상세페이지 잡과 분리.

    소유 라이선스만·active 만. payment_id 는 매 호출 고유(실 TX 다건 생성). 실거래(워커 훅)와
    구분되는 '정산 검증용' 경로 — 제안서 KPI 는 실/시뮬 분리 집계.
    """
    chain = getattr(request.app.state, "fm_chain", None)
    if chain is None:
        raise _err("chain_unavailable", "체인이 설정되지 않았습니다.", status=404)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select l.id::text as id, l.model_id::text as model_id, l.unit_price, l.status
                   from fm_licenses l join fm_models m on m.id = l.model_id
                   where l.id = %s and m.user_id = %s""",
                (body.license_id, user_id),
            )
            lic = await cur.fetchone()
    if not lic:
        raise _err("not_found", "라이선스를 찾을 수 없습니다.", status=404)
    if lic["status"] != "active":
        raise _err("license_inactive", "활성 라이선스만 정산할 수 있습니다.", status=400)

    payment_key = f"sim:{body.license_id}:{uuid.uuid4().hex}"
    row = await record_license_settlement(
        request.app,
        payment_key=payment_key,
        license_id=lic["id"],
        model_id=lic["model_id"],
        total=int(lic["unit_price"]),
    )
    if row is None:
        raise _err("settlement_failed", "온체인 정산 기록에 실패했습니다.", status=502)
    return row
