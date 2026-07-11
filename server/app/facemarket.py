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
