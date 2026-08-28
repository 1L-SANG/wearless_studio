"""FaceMarket — 검증 실명 모델 마켓 (2026 블록체인·AI 해커톤).

`FACEMARKET_ENABLED` 게이트: off면 main.py가 이 라우터를 아예 등록하지 않는다
(기존 셀러 플로우 무영향 — 프로드 보호).

FM-11 본인확인(CX 표준인증창 ENT_MID):
프론트는 위젯 성공 콜백의 **token만** 백엔드로 보낸다(원문 PII는 절대 클라→서버 신뢰 안 함).
백엔드가 CX `trans/{token}`을 **서버발** 호출해 실 신원을 받고:
  · dedup = HMAC-SHA256(ci, pepper) → fm_models.ci_hash 단일 보관(원문 CI 미저장)
  · 리플레이 차단 = fm_identity_verifications.cx_tx_id UNIQUE(값은 SHA-256 digest, 같은 token 재사용 시 409)
  · 화이트리스트 마스킹 필드만 감사 저장(이름 마스킹·생년(연도)·VC종류 — 원문 생년월일 미보관)
FM-03 실측(2026-07-09): ENT_MID 응답에 `ci` 존재 확인 → ci HMAC 채택.
"""

import asyncio
import hashlib
import hmac
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json
from pydantic import Field, ValidationError

from . import cx_identity, holder_client
from . import repo
from .auth import require_user
from .db import get_conn
from .facemarket_enrollment import BIOMETRIC_CONSENT_VERSION
from .models import CamelModel, ErrorResponse
from .r2 import MIME_EXT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket"])

CX_TRANS_TIMEOUT = 10.0
_SIMULATION_RATE_LIMIT_PER_MINUTE = 5
_SETTLEMENT_SIGNER_LOCK_ID = 0x57464D5349474E52
_SETTLEMENT_LOCK_RETRY_SECONDS = 0.05

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
    """카탈로그/마이페이지 카드 — 공개 화이트리스트 컬럼만(PII·ci_hash 제외).

    카탈로그(list_models)는 현재 생체 등록·VC·라이선스·비공개 자산 증거가 모두 유효한 모델만
    반환한다. 얼굴/레거시 cover는 내보내지 않고 고정 placeholder URI만 제공한다.
    """

    id: str
    display_name: str
    status: str
    cover_image_url: str | None = None
    created_at: datetime
    license_id: str | None = None
    unit_price: int | None = None
    has_active_license: bool = False
    vc_id: str | None = None
    assets_ready: bool = False  # 실존 모델 그리드 자산 빌드 완료 → 셀러 선택 가능(assetsReady)
    # 비생체 고정 placeholder 게이트 URL. 모델별 얼굴/cover 바이트를 뜻하지 않는다.
    face_thumb_uri: str | None = None


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _assert_account_open(conn, user_id: str) -> None:
    if await repo.user_account_purge_closed(conn, user_id):
        raise _err("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", status=404)


def _wake_dispatcher(request: Request) -> None:
    """잡 생성 직후 디스패처 즉시 기상(personalization._wake_dispatcher 미러)."""
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()


def _wake_opendid(app) -> None:
    """holder(opendid)가 필요해졌다 — scale-to-zero 로 0대면 지금 깨운다(reconciler 60초 대기 스킵).
    autoscaler 는 off 여도 state 에 올라 있고 prewarm_soon 은 즉시 return 하므로 분기 없이 부른다."""
    autoscaler = getattr(app.state, "opendid_autoscaler", None)
    if autoscaler is not None:
        autoscaler.prewarm_soon()


async def _fetch_trans(base_url: str, token: str) -> dict:
    """CX `trans/{token}` 서버발 호출 → 실 신원 필드(dict). 테스트 monkeypatch 지점.

    token 은 URL 인코딩 후 보간(cx_identity.fetch_trans 와 동일 근거 — 미인코딩 보간은
    `x/../..`·`x?a=b` 로 CX 호스트 내 경로 이탈/쿼리 주입 가능).
    """
    url = f"{base_url}/oacx/api/v1.0/trans/{quote(token, safe='')}"
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


def _gender_from_trans(trans) -> str | None:
    """OACX 응답에서 성별을 정규화(M/F/1/2/남/여 → male/female)."""
    raw = _dig(trans, "gender", "sex", "sexCd", "sexCode", "genderCd")
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("m", "male", "1", "남", "남자"):
        return "male"
    if s in ("f", "female", "2", "여", "여자"):
        return "female"
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


# 만 나이 기준 시각 = KST(한국 법 기준). 컨테이너 TZ(UTC) 의존 시 하루 밀린다(cx_identity 미러).
_KST = ZoneInfo("Asia/Seoul")


def _age_from_birth_year(birth_year) -> int | None:
    """`fields.birthYear`(연도만) → 만 나이. 파생 불가면 None(공개 검증에서 age: null).

    fm_identity_verifications 는 최소수집으로 **연도만** 남긴다(`str(birth)[:4]`) → 생일 미상이라
    만 나이는 [연도차-1, 연도차] 구간으로만 특정된다. **하한(연도차-1)** 을 택한다:
      · cx_identity.is_adult_from_birth 의 4자리 경로가 성인 판정에 `연도차 >= min_age+1` 을
        요구한다 = 그 판정이 가정하는 나이가 곧 연도차-1 이다. 상한을 쓰면 연령 게이트가
        미성년으로 막은 사람이 공개 검증에서 만 19세로 보이는 모순이 생긴다.
      · 과대 표기(실나이보다 많게)는 이 라우트가 무인증 공개라 되돌릴 수 없다 → 안전측 하한.
    연도 범위 검증은 필수 — 'MMDD' 같은 4자리가 들어오면 연도차가 1900+ 로 튄다(cx_identity 선례).
    """
    digits = "".join(ch for ch in str(birth_year or "") if ch.isdigit())
    if len(digits) != 4:
        return None
    year = int(digits)
    today = datetime.now(_KST).date()
    if not (1900 <= year <= today.year):
        return None
    return max(today.year - year - 1, 0)


async def _record_personalization_adult(conn, user_id: str, token: str, birth) -> None:
    """CX 인증 1회로 **개인화 성인 인증도 성립**시킨다(Level 1 통합).

    사용자가 같은 CX 표준인증창을 FaceMarket·개인화에서 두 번 겪지 않게 한다. 호출 시점은
    FaceMarket 모델 등록이 **이미 commit 된 뒤**이고, 이 함수는 **비치명적**이다 — 실패해도
    (테이블 부재·중복·개인화 미배포 등) FaceMarket 등록·응답은 그대로 유효하다.

    개인화 최소수집 규칙 준수(api-spec §3.0): **is_adult 불리언만** 저장한다. 생년월일·CI·이름은
    개인화 테이블에 넣지 않고, 원본 토큰 대신 sha256 해시를 쓴다(원본 토큰은 CX 에서 CI·생년월일을
    재조회할 수 있는 라이브 capability 라 보관 금지).
    """
    try:
        is_adult = cx_identity.is_adult_from_birth(birth)
    except cx_identity.CxIdentityError:
        return  # 연령 파싱 불가 → 미기록. 개인화 게이트가 자체 인증을 요구하게 둔다.
    cx_tx_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into personalization_identity_verifications "
                "(user_id, cx_tx_hash, is_adult) values (%s, %s, %s) "
                "on conflict (cx_tx_hash) do nothing",
                (user_id, cx_tx_hash, is_adult),
            )
        await conn.commit()
    except Exception:
        # 개인화 기록 실패가 FaceMarket 을 깨선 안 된다(무회귀 원칙). 사유코드도 PII 없음.
        await conn.rollback()
        logger.warning("personalization_adult_record_failed")


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
    if getattr(settings, "fm_biometric_enrollment_enabled", False):
        raise _err(
            "biometric_enrollment_required",
            "생체 등록 플로우를 완료해 주세요.",
            status=409,
        )
    cx_tx_id = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"

    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)

    # ⚠️ 원문 신원 조기 폐기 미적용 지점(api-spec §3.0). trans·ci·birth 가 함수 끝까지 프레임
    # 로컬로 남아, 예외 전파 시 traceback 이 이 프레임을 잡으면 CX 원문(CI·이름·생년월일)이
    # 프레임-로컬 캡처형 에러 트래커로 나갈 수 있다. 현재 에러 트래커 미배선이라 실위험은 0이나,
    # **Sentry 등을 붙이기 전에 personalization.identity_verify 처럼 try/finally 로
    # `del trans, ci, birth` 를 적용할 것.**
    trans = await _fetch_trans(settings.cx_trans_base_url, token)

    ci = _dig(trans, "ci")
    if not ci:
        raise _err("ci_missing", "본인확인 정보를 확인하지 못했어요.")
    ci_hash = _ci_hmac(str(ci), pepper)

    # 화이트리스트 마스킹 필드만 — 원문 CI/생년월일 미보관(생년=연도만).
    raw_name = _dig(trans, "utf8Nm", "nm", "name", "userName", "engnm") or ""
    name_masked = _mask_name(raw_name)
    birth = _dig(trans, "birth", "birthdate")
    gender = _gender_from_trans(trans)
    fields = {
        "nameMasked": name_masked,
        "birthYear": str(birth)[:4] if birth else None,
        "vcType": _dig(trans, "vcTypeCodeList"),
        "gender": gender,
    }

    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            # dedup: 같은 사람(ci_hash) 재인증이면 기존 모델 재사용, 아니면 신규 verified 생성.
            await cur.execute(
                "select id, status from fm_models where ci_hash = %s", (ci_hash,)
            )
            existing = await cur.fetchone()
            if existing:
                model_id = existing["id"]
                await cur.execute(
                    "update fm_models set status = 'verified', user_id = %s, gender = coalesce(%s, gender) where id = %s",
                    (user_id, gender, model_id),
                )
            else:
                await cur.execute(
                    """insert into fm_models (user_id, display_name, status, ci_hash, gender)
                       values (%s, %s, 'verified', %s, %s) returning id""",
                    (user_id, name_masked, ci_hash, gender),
                )
                model_id = (await cur.fetchone())["id"]

            # 원본 CX capability는 저장하지 않고 digest UNIQUE로 같은 토큰 재사용만 차단한다.
            try:
                await cur.execute(
                    """insert into fm_identity_verifications
                       (model_id, cx_tx_id, cx_tx_id_format, fields)
                       values (%s, %s, %s, %s)""",
                    (model_id, cx_tx_id, "sha256-v1", Json(fields)),
                )
            except UniqueViolation:
                raise _err("identity_replay", "이미 처리된 인증입니다.", status=409)
        await conn.commit()

        # Level 1 통합 — 같은 CX 인증을 개인화에서 또 요구하지 않도록 성인 여부를 함께 기록한다.
        # FaceMarket commit **이후** 비치명적 실행 → 실패해도 위 모델 등록은 확정된 채로 유지.
        if settings.personalization_enabled:
            await _record_personalization_adult(conn, user_id, token, birth)

    return {
        "verified": True,
        "modelId": str(model_id),
        "status": "verified",
        "nameMasked": name_masked,
    }


# uuid 컬럼은 ::text 캐스트해 반환(repo.py 관례). psycopg 는 uuid 를 uuid.UUID 로 로드하는데
# CamelModel(id: str) 이 UUID 를 거부 → ResponseValidationError 500. 캐스트로 문자열화.
_MODEL_CARD_COLS = ("id::text as id, display_name, status, cover_image_url, created_at, "
                    "(assets_status = 'ready') as assets_ready")

_CURRENT_CARD_JOINS = """
join fm_biometric_enrollments e
  on e.id = m.current_enrollment_id and e.model_id = m.id
join fm_licenses l
  on l.model_id = m.id and l.enrollment_id = e.id
join fm_biometric_enrollment_photos p
  on p.enrollment_id = e.id and p.angle = 'front'
"""

_CURRENT_CARD_ELIGIBILITY = """
m.status = 'verified'
and m.assets_status = 'ready'
and e.status = 'passed'
and e.decision = 'passed'
and e.consent_version = %s
and nullif(btrim(e.match_policy_version), '') is not null
and l.status = 'active'
and (l.license_valid_until is null or l.license_valid_until > now())
and nullif(btrim(l.vc_id), '') is not null
and l.vc_id = e.vc_id
and p.storage_state = 'approved'
and p.mime_type like 'image/%%'
and nullif(btrim(p.r2_key), '') is not null
and nullif(btrim(l.face_image_key), '') is not null
and p.r2_key = l.face_image_key
and exists (
  select 1 from fm_model_assets a
  where a.model_id = m.id and a.view = 'face_front'
    and nullif(btrim(a.r2_key), '') is not null
    and a.bucket = 'face' and a.mime like 'image/%%'
    and a.source_enrollment_id = e.id
    and a.evidence_version = e.match_policy_version
)
and exists (
  select 1 from fm_model_assets a
  where a.model_id = m.id and a.view = 'grid_sedcard'
    and nullif(btrim(a.r2_key), '') is not null
    and a.bucket = 'face' and a.mime like 'image/%%'
    and a.source_enrollment_id = e.id
    and a.evidence_version = e.match_policy_version
)
"""

_MODEL_CARD_COLS_ENRICHED = (
    "m.id::text as id, m.display_name, m.status, "
    "null::text as cover_image_url, m.created_at, "
    "l.id::text as license_id, l.unit_price, l.vc_id, "
    "true as has_active_license, true as assets_ready, "
    "('/v1/facemarket/models/' || m.id::text || '/thumbnail') as face_thumb_uri"
)

_MODEL_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" '
    b'viewBox="0 0 400 400"><rect width="400" height="400" fill="#efeef0"/>'
    b'<circle cx="200" cy="142" r="58" fill="#aaa8b2"/>'
    b'<path d="M92 352c12-82 55-124 108-124s96 42 108 124" '
    b'fill="#aaa8b2"/></svg>'
)


def _private_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": "찾을 수 없습니다."}},
        headers={"Cache-Control": "no-store, private"},
    )


@router.get(
    "/models",
    response_model=list[ModelCard],
    responses={401: {"model": ErrorResponse, "description": "인증 실패"}},
    tags=["FaceMarket"],
    summary="검증 모델 카탈로그 (셀러용)",
)
async def list_models(
    request: Request,
    response: Response,
    user_id: str = Depends(require_user),
):
    """검증(verified) 모델 목록. 셀러가 상세페이지 제작 시 고르는 카탈로그 피드.

    화이트리스트 컬럼만 반환 — `ci_hash`·`user_id`·`did` 등 PII/식별자는 노출하지 않는다.
    (FM-13 팀원 계약: 프론트 카탈로그가 이 shape를 소비.)
    """
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_MODEL_CARD_COLS_ENRICHED} from fm_models m
                    {_CURRENT_CARD_JOINS}
                    where {_CURRENT_CARD_ELIGIBILITY}
                    order by m.created_at desc limit 200""",
                (BIOMETRIC_CONSENT_VERSION,),
            )
            rows = await cur.fetchall()
    response.headers["Cache-Control"] = "no-store, private"
    return rows


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


@router.post(
    "/models/me/build-assets",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    tags=["FaceMarket"],
    summary="내 모델 아이덴티티 자산 빌드(그리드+QC)",
    status_code=202,
)
async def build_my_model_assets(request: Request, user_id: str = Depends(require_user)):
    """검증된 내 모델의 얼굴 3장 → 2×2 그리드 자산 생성 잡을 큐잉한다(멱등).

    전제: 본인확인(fm_models verified) + 개인화 얼굴 3장 완비. 진행 중 빌드가 있으면 그 jobId 반환.
    얼굴 대조 QC 통과 시에만 자산이 등록된다(handoff §03 필수 게이트). payload=modelId 만(PII 금지 —
    워커가 modelId 로 서버측 재조회). 모델 행 FOR UPDATE 로 동시 요청 직렬화(_start_purge 선례).
    """
    if getattr(request.app.state.settings, "fm_biometric_enrollment_enabled", False):
        raise _err(
            "biometric_enrollment_required",
            "생체 등록 플로우를 완료해 주세요.",
            status=409,
        )
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id from fm_models "
                "where user_id = %s and status = 'verified' "
                "order by created_at desc limit 1 for update",
                (user_id,),
            )
            m = await cur.fetchone()
            if m is None:
                raise _err("model_not_verified", "먼저 본인확인을 완료해 주세요.", 400)
            model_id = m["id"]
            await cur.execute(
                "select count(distinct pf.angle) as n from personalization_profiles p "
                "join personalization_face_photos pf on pf.profile_id = p.id "
                "where p.user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if (row["n"] if row else 0) < 3:
                raise _err("face_photos_incomplete", "얼굴 사진 3장을 먼저 업로드해 주세요.", 400)
            await cur.execute(
                "select id::text as id from jobs where kind = 'fm_model_asset_build' "
                "and payload->>'modelId' = %s and status in ('pending', 'running') limit 1",
                (model_id,),
            )
            existing = await cur.fetchone()
            if existing:
                await conn.commit()
                return {"jobId": existing["id"], "modelId": model_id}
        job, _created = await repo.create_job(
            conn, user_id=user_id, project_id=None, kind="fm_model_asset_build",
            payload={"modelId": model_id}, idempotency_key=None,
            credits_reserved=0, metadata={},
        )
        await conn.commit()
    _wake_dispatcher(request)
    return {"jobId": str(job["id"]), "modelId": model_id}


# ── 얼굴 라이선스 (FM: 얼굴 업로드 + 조건) ─────────────────────────
# 얼굴 이미지 = 생체 PII. 공개 R2 URL 절대 노출 금지 → 비공개 버킷 저장 + 게이트 스트림.
# face_image_uri = 게이트 라우트 URL(공개 URL 아님). face_image_key = 내부 비공개 키(응답 제외).
ALLOWED_BRAND_USE_CATEGORIES = (
    "상의",
    "하의",
    "아우터",
    "원피스",
    "니트·스웨터",
    "데님",
    "셋업·수트",
    "스커트",
    "트레이닝·애슬레저",
    "잡화·액세서리",
    "뷰티·화장품",
)
FORBIDDEN_BRAND_USE_CATEGORIES = (
    "속옷·란제리",
    "수영복·비키니",
)
_EXT_TO_MIME = {ext: mime for mime, ext in MIME_EXT.items()}  # 상세컷 워커 Content-Type 역매핑

# 응답 화이트리스트 — face_image_key(비공개)·모델 PII 제외. uuid(id/model_id)는 ::text 캐스트
# (psycopg→uuid.UUID, CamelModel str 필드가 거부 → 500 방지, repo.py 관례).
# RETURNING 용(단일 테이블, 별칭 없음).
# RETURNING 용(단일 테이블, 별칭 없음). cover_image_url 은 fm_models 소유라 여기(조인 없음)엔 없다 —
# LicenseCard.cover_image_url 기본값(None)으로 직렬화되고, 대표 이미지는 목록 조회(_L)에서 실린다.
_LICENSE_CARD_COLS = (
    "id::text as id, model_id::text as model_id, face_image_uri, face_image_digest, "
    "allowed_use, forbidden_use, unit_price, license_valid_until, status, vc_id, created_at"
)
# 목록 조인 쿼리용 — 모든 컬럼 l. 한정(fm_models 와 id/status/created_at 등 이름 충돌 → 모호성 500 방지).
# cover_image_url 만 m. — 대표 이미지는 fm_models 소유(조인 전제, 아래 3개 사용처 모두 join fm_models).
_LICENSE_CARD_COLS_L = (
    "l.id::text as id, l.model_id::text as model_id, l.face_image_uri, l.face_image_digest, "
    "l.allowed_use, l.forbidden_use, l.unit_price, l.license_valid_until, l.status, l.vc_id, l.created_at, "
    "m.cover_image_url"
)


class LicenseCard(CamelModel):
    """라이선스 카드 — 소유자 마이페이지/카탈로그용. 비공개 키·원본 얼굴 바이트 미포함."""

    id: str
    model_id: str
    face_image_uri: str        # 게이트 URL(GET /v1/facemarket/licenses/{id}/face)
    face_image_digest: str | None  # 'sha256-...' SRI; nullable during reverification cutover
    allowed_use: list[str]
    forbidden_use: list[str]
    unit_price: int
    license_valid_until: datetime
    status: str
    vc_id: str | None = None
    created_at: datetime
    cover_image_url: str | None = None  # 모델 대표 이미지 — VC 카드 프로필. RETURNING 경로는 null(목록 조회 시 채워짐)


class CreateLicenseRequest(CamelModel):
    enrollment_id: str
    allowed_use: list[str] = Field(default_factory=list)
    forbidden_use: list[str] = Field(default_factory=list)
    unit_price: int = Field(default=10000, ge=0, le=100_000_000)
    valid_days: int = Field(default=365, ge=1, le=3650)


def _r2_face(request: Request):
    """얼굴 전용 R2 클라이언트(app.state.r2_face). 미설정이면 503 (공개 버킷 폴백 금지)."""
    r2 = getattr(request.app.state, "r2_face", None)
    if r2 is None:
        raise _err("storage_unavailable", "얼굴 저장소가 설정되지 않았습니다.", status=503)
    return r2


def _clean_uses(items: list[str], accepted: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in items or []:
        value = (raw or "").strip()
        if not value:
            continue
        if value not in accepted:
            raise _err("invalid_use_category", "정해진 브랜드 유형만 선택할 수 있어요.")
        if value not in out:
            out.append(value)
    return out


async def _reject_cutover_closed(conn) -> None:
    await repo.lock_facemarket_writer_boundary(conn)
    if await repo.facemarket_writer_boundary_closed(conn):
        raise _err(
            "facemarket_cutover_in_progress",
            "실물 모델 보안 전환 중이라 잠시 후 다시 시도해 주세요.",
            status=409,
        )


async def _find_license_by_enrollment(conn, user_id: str, enrollment_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""select {_LICENSE_CARD_COLS_L} from fm_licenses l
                join fm_models m on m.id = l.model_id
                where l.enrollment_id = %s and m.user_id = %s
                limit 1""",
            (enrollment_id, user_id),
        )
        return await cur.fetchone()


async def _find_license_for_update(conn, user_id: str, license_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""select {_LICENSE_CARD_COLS_L}, l.enrollment_id::text as enrollment_id,
                      l.face_image_key
                from fm_licenses l
                join fm_models m on m.id = l.model_id
                where l.id = %s and m.user_id = %s
                limit 1
                for update of l""",
            (license_id, user_id),
        )
        return await cur.fetchone()


async def _load_license_evidence(conn, user_id: str, enrollment_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """select e.id::text as enrollment_id, e.status as enrollment_status,
                      e.match_policy_version, m.id::text as model_id, m.status as model_status,
                      m.did as model_did, m.assets_status, m.current_enrollment_id::text,
                      p.r2_key as front_key, p.image_digest as front_digest,
                      p.storage_state as front_storage_state,
                      fa.r2_key as face_asset_key,
                      fa.source_enrollment_id::text as face_asset_source_enrollment_id,
                      fa.evidence_version as face_asset_evidence_version,
                      ga.r2_key as grid_asset_key,
                      ga.source_enrollment_id::text as grid_asset_source_enrollment_id,
                      ga.evidence_version as grid_asset_evidence_version
                 from fm_biometric_enrollments e
                 join fm_models m on m.id = e.model_id and m.user_id = e.user_id
                 left join fm_biometric_enrollment_photos p
                   on p.enrollment_id = e.id and p.angle = 'front'
                 left join fm_model_assets fa
                   on fa.model_id = m.id and fa.view = 'face_front'
                 left join fm_model_assets ga
                   on ga.model_id = m.id and ga.view = 'grid_sedcard'
                where e.id = %s and e.user_id = %s
                limit 1""",
            (enrollment_id, user_id),
        )
        return await cur.fetchone()


async def _load_activation_evidence_for_update(
    conn, user_id: str, license_id: str, enrollment_id: str
) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """select e.id::text as enrollment_id, e.status as enrollment_status,
                      e.match_policy_version, m.id::text as model_id, m.status as model_status,
                      m.did as model_did, m.assets_status, m.current_enrollment_id::text,
                      m.reverification_batch_id::text as model_reverification_batch_id,
                      b.status as batch_status, b.completed_at as batch_completed_at,
                      e.created_at as enrollment_created_at,
                      l.created_at as license_created_at,
                      l.reverification_batch_id::text as license_reverification_batch_id,
                      p.r2_key as front_key, p.image_digest as front_digest,
                      p.storage_state as front_storage_state,
                      fa.r2_key as face_asset_key,
                      fa.source_enrollment_id::text as face_asset_source_enrollment_id,
                      fa.evidence_version as face_asset_evidence_version,
                      ga.r2_key as grid_asset_key,
                      ga.source_enrollment_id::text as grid_asset_source_enrollment_id,
                      ga.evidence_version as grid_asset_evidence_version
                 from fm_licenses l
                 join fm_biometric_enrollments e
                   on e.id = l.enrollment_id
                 join fm_models m
                   on m.id = e.model_id and m.user_id = e.user_id
                 left join fm_cutover_batches b on b.id = m.reverification_batch_id
                 join fm_biometric_enrollment_photos p
                   on p.enrollment_id = e.id and p.angle = 'front'
                 join fm_model_assets fa
                   on fa.model_id = m.id and fa.view = 'face_front'
                 join fm_model_assets ga
                   on ga.model_id = m.id and ga.view = 'grid_sedcard'
                where l.id = %s and e.id = %s and e.user_id = %s
                limit 1
                for update of l, e, m, p, fa, ga""",
            (license_id, enrollment_id, user_id),
        )
        return await cur.fetchone()


def _checked_license_evidence(row: dict | None) -> tuple[str, str, str]:
    if row is None:
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
    enrollment_id = str(row["enrollment_id"])
    if row["enrollment_status"] not in {"license_pending", "vc_pending"}:
        raise _err("enrollment_not_ready", "라이선스 발급 가능한 등록 상태가 아닙니다.", status=409)
    if str(row.get("current_enrollment_id") or "") != enrollment_id:
        raise _err("enrollment_not_current", "최신 등록으로 다시 시도해 주세요.", status=409)
    if row.get("assets_status") != "ready":
        raise _err("model_assets_not_ready", "모델 자산 준비가 완료되지 않았습니다.", status=409)
    if row.get("front_storage_state") != "approved" or not row.get("front_key"):
        raise _err("approved_front_missing", "승인된 정면 사진을 찾을 수 없습니다.", status=409)
    evidence_version = row.get("match_policy_version")
    for view in ("face", "grid"):
        if not row.get(f"{view}_asset_key"):
            raise _err("model_assets_not_ready", "모델 자산 준비가 완료되지 않았습니다.", status=409)
        if str(row.get(f"{view}_asset_source_enrollment_id") or "") != enrollment_id:
            raise _err("model_assets_stale", "현재 등록으로 생성된 모델 자산이 아닙니다.", status=409)
        if not evidence_version or row.get(f"{view}_asset_evidence_version") != evidence_version:
            raise _err("model_assets_stale", "현재 증거 버전으로 생성된 모델 자산이 아닙니다.", status=409)
    return str(row["model_id"]), row["front_key"], row["front_digest"]


async def enqueue_vc_revocation(
    conn, *, license_id: str, model_id: str, vc_id: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """insert into fm_vc_revocation_jobs (license_id, model_id, vc_id)
               values (%s, %s, %s)
               on conflict (vc_id) do nothing""",
            (license_id, model_id, vc_id),
        )


async def _enqueue_issued_vc_revocation(connect, *, license_id, model_id, vc_id) -> None:
    async with connect() as conn:
        await enqueue_vc_revocation(
            conn, license_id=license_id, model_id=model_id, vc_id=vc_id
        )
        await conn.commit()


def _license_card(row: dict) -> dict:
    # .get — cover_image_url 은 fm_models 소유라 RETURNING(단일 테이블) row 엔 없다. 없으면 None
    # (LicenseCard 기본값). 필수 필드가 빠지면 response_model 검증이 잡는다.
    return {key: row.get(key) for key in LicenseCard.model_fields}


async def finalize_issued_face_vc(
    connect,
    *,
    user_id,
    license_id,
    model_id,
    enrollment_id,
    issued,
) -> dict:
    """Bind one issued VC to current biometric evidence or durably revoke the loser."""
    stale_error = None
    winner = None
    model_id = str(model_id)
    async with connect() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        if await repo.facemarket_writer_boundary_closed(conn):
            await enqueue_vc_revocation(
                conn, license_id=license_id, model_id=model_id, vc_id=issued.vc_id
            )
            await conn.commit()
            raise _err(
                "facemarket_cutover_in_progress",
                "실물 모델 보안 전환 중이라 잠시 후 다시 시도해 주세요.",
                status=409,
            )
        await _assert_account_open(conn, user_id)
        try:
            locked = await _find_license_for_update(conn, user_id, license_id)
            if locked is None or str(locked.get("enrollment_id") or "") != enrollment_id:
                raise _err(
                    "license_activation_stale",
                    "라이선스 활성화 상태가 변경되었습니다.",
                    status=409,
                )
            if locked["status"] == "active":
                winner = _license_card(locked)
                await conn.commit()
            elif locked["status"] != "pending" or locked.get("vc_id") is not None:
                raise _err(
                    "license_activation_stale",
                    "라이선스 활성화 상태가 변경되었습니다.",
                    status=409,
                )
            else:
                evidence = await _load_activation_evidence_for_update(
                    conn, user_id, license_id, enrollment_id
                )
                model_id, key, fresh_digest = _checked_license_evidence(evidence)
                batch_id = evidence.get("model_reverification_batch_id")
                if batch_id:
                    completed_at = evidence.get("batch_completed_at")
                    if (
                        evidence.get("batch_status") != "completed"
                        or completed_at is None
                        or evidence.get("enrollment_created_at") <= completed_at
                        or evidence.get("license_created_at") <= completed_at
                        or evidence.get("license_reverification_batch_id") is not None
                    ):
                        await enqueue_vc_revocation(
                            conn,
                            license_id=license_id,
                            model_id=model_id,
                            vc_id=issued.vc_id,
                        )
                        await conn.commit()
                        raise _err(
                            "license_activation_stale",
                            "라이선스 활성화 상태가 변경되었습니다.",
                            status=409,
                        )
                if evidence["model_status"] not in {"pending", "reverification_required"}:
                    raise _err(
                        "model_not_activatable",
                        "활성화 가능한 모델 상태가 아닙니다.",
                        status=409,
                    )
                if (
                    locked["face_image_key"] != key
                    or locked["face_image_digest"] != fresh_digest
                ):
                    raise _err(
                        "license_activation_stale",
                        "라이선스 활성화 상태가 변경되었습니다.",
                        status=409,
                    )
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""update fm_licenses set status = 'active', vc_id = %s
                            where id = %s and status = 'pending' and vc_id is null
                            returning {_LICENSE_CARD_COLS}""",
                        (issued.vc_id, license_id),
                    )
                    active = await cur.fetchone()
                    await cur.execute(
                        """update fm_models set status = 'verified',
                                  did = coalesce(nullif(did, ''), %s)
                            where id = %s and current_enrollment_id = %s
                              and status in ('pending', 'reverification_required')
                            returning id""",
                        (issued.user_did, model_id, enrollment_id),
                    )
                    model_updated = await cur.fetchone()
                    await cur.execute(
                        """update fm_biometric_enrollments
                              set status = 'passed', decision = 'passed',
                                  vc_id = %s, completed_at = now()
                            where id = %s and status = 'vc_pending'
                            returning id""",
                        (issued.vc_id, enrollment_id),
                    )
                    enrollment_updated = await cur.fetchone()
                if active is None or model_updated is None or enrollment_updated is None:
                    raise _err(
                        "license_activation_stale",
                        "라이선스 활성화 상태가 변경되었습니다.",
                        status=409,
                    )
                await conn.commit()
                return active
        except HTTPException as error:
            await conn.rollback()
            stale_error = error
        except Exception:
            await conn.rollback()
            raise

    if winner is None and stale_error is not None:
        async with connect() as conn:
            current = await _find_license_for_update(conn, user_id, license_id)
            if current is not None and current["status"] == "active":
                winner = _license_card(current)
            await conn.commit()

    if winner is not None:
        if winner.get("vc_id") != issued.vc_id:
            await _enqueue_issued_vc_revocation(
                connect,
                license_id=license_id,
                model_id=model_id,
                vc_id=issued.vc_id,
            )
        return winner

    await _enqueue_issued_vc_revocation(
        connect,
        license_id=license_id,
        model_id=model_id,
        vc_id=issued.vc_id,
    )
    raise stale_error


async def _await_post_issue_finalization(awaitable):
    task = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
        except Exception:
            logger.exception("post_issue_finalization_failed_after_cancellation")
        raise


@router.post(
    "/licenses",
    response_model=LicenseCard,
    status_code=201,
    responses={
        **_FM_RESPONSES,
        502: {"model": ErrorResponse, "description": "VC 발급 계약 오류"},
        503: {"model": ErrorResponse, "description": "VC 발급 지연"},
    },
    tags=["FaceMarket"],
    summary="등록 증거 기반 얼굴 라이선스 생성",
)
async def create_license(
    request: Request,
    user_id: str = Depends(require_user),
):
    """JSON-only enrollment contract. Multipart/direct face/profile never creates a license."""
    if not getattr(request.app.state.settings, "fm_biometric_enrollment_enabled", False):
        raise _err(
            "biometric_enrollment_required",
            "생체 등록 플로우를 완료해 주세요.",
            status=409,
        )
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        raise _err("json_required", "JSON 요청만 허용됩니다.", status=415)
    try:
        body = CreateLicenseRequest.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise _err("invalid_license_request", "라이선스 요청 형식이 올바르지 않습니다.", status=400)
    try:
        enrollment_id = str(uuid.UUID(str(body.enrollment_id)))
    except (TypeError, ValueError):
        raise _err("invalid_enrollment_id", "등록 ID 형식이 올바르지 않습니다.", status=400)
    valid_until = datetime.now(timezone.utc) + timedelta(days=body.valid_days)
    allowed = _clean_uses(body.allowed_use, ALLOWED_BRAND_USE_CATEGORIES)
    forbidden = _clean_uses(body.forbidden_use, FORBIDDEN_BRAND_USE_CATEGORIES)
    unit_price = body.unit_price

    license_id = str(uuid.uuid4())
    row = None
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        existing = await _find_license_by_enrollment(conn, user_id, enrollment_id)
        if existing and existing["status"] == "active":
            return existing

        model_id, key, digest = _checked_license_evidence(
            await _load_license_evidence(conn, user_id, enrollment_id)
        )
        await _reject_cutover_closed(conn)
        if existing:
            row = existing
            license_id = existing["id"]
            allowed = list(existing["allowed_use"] or [])
            forbidden = list(existing["forbidden_use"] or [])
            unit_price = int(existing["unit_price"])
            valid_until = existing["license_valid_until"]
            digest = existing["face_image_digest"]
        else:
            gate_uri = f"/v1/facemarket/licenses/{license_id}/face"
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""insert into fm_licenses
                        (id, model_id, enrollment_id, face_image_uri, face_image_key,
                         face_image_digest, allowed_use, forbidden_use, unit_price,
                         license_valid_until, status)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        on conflict (enrollment_id) where enrollment_id is not null do nothing
                        returning {_LICENSE_CARD_COLS}""",
                    (
                        license_id, model_id, enrollment_id, gate_uri, key, digest,
                        allowed, forbidden, unit_price, valid_until,
                    ),
                )
                row = await cur.fetchone()
            if row is None:
                row = await _find_license_by_enrollment(conn, user_id, enrollment_id)
                if row is None:
                    raise _err("license_create_conflict", "라이선스 생성 상태를 확인할 수 없습니다.", status=409)
                license_id = row["id"]
                if row["status"] == "active":
                    await conn.commit()
                    return row
                allowed = list(row["allowed_use"] or [])
                forbidden = list(row["forbidden_use"] or [])
                unit_price = int(row["unit_price"])
                valid_until = row["license_valid_until"]
                digest = row["face_image_digest"]

        allowed = _clean_uses(allowed, ALLOWED_BRAND_USE_CATEGORIES)
        forbidden = _clean_uses(forbidden, FORBIDDEN_BRAND_USE_CATEGORIES)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set status = 'vc_pending' "
                "where id = %s and status in ('license_pending', 'vc_pending') returning id",
                (enrollment_id,),
            )
            if await cur.fetchone() is None:
                await conn.rollback()
                raise _err("enrollment_not_ready", "라이선스 발급 가능한 등록 상태가 아닙니다.", status=409)
        await conn.commit()

    # opendid 가 scale-to-zero(0대)면 지금 깨운다 — reconciler 60초 대기를 앞당긴다. off/미설정이면
    # 무해. 콜드스타트(4 JVM ~2분)는 아래 vc_issue_delayed 재시도 UX 가 흡수한다.
    _wake_opendid(request.app)
    try:
        issued = await issue_face_vc(
            request.app, license_id=license_id, model_id=str(model_id),
            allowed=allowed, forbidden=forbidden, unit_price=unit_price,
            valid_until=valid_until, digest=digest,
        )
    except FaceVcIssueError as error:
        _wake_opendid(request.app)   # 콜드/재확보로 실패했다면 재시도가 홀더를 찾게 다시 깨운다
        raise _err(
            "vc_issue_delayed",
            "VC 발급이 지연되었습니다. 잠시 후 다시 시도해 주세요.",
            status=error.status_code,
        )

    try:
        return await _await_post_issue_finalization(
            finalize_issued_face_vc(
                lambda: get_conn(request),
                user_id=user_id,
                license_id=license_id,
                model_id=model_id,
                enrollment_id=enrollment_id,
                issued=issued,
            )
        )
    except HTTPException:
        raise
    except Exception:
        raise _err(
            "vc_issue_delayed",
            "VC 발급이 지연되었습니다. 잠시 후 다시 시도해 주세요.",
            status=503,
        )


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
    """현재 생체 등록의 승인 정면 얼굴을 검증 모델 본인에게만 스트림한다.

    비존재/비소유/폐기·만료 모두 **404**(존재 노출 방지). 공개 URL을 절대 만들지 않고
    인증된 이 라우트로만 바이트를 흘린다(<img>는 Bearer 불가 → 프론트는 fetch+objectURL).
    """
    try:
        license_id = str(uuid.UUID(str(license_id)))
    except (TypeError, ValueError):
        return _private_not_found()
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select l.face_image_key, p.mime_type from fm_models m
                    {_CURRENT_CARD_JOINS}
                    where {_CURRENT_CARD_ELIGIBILITY}
                      and l.id = %s and m.user_id = %s
                    limit 1""",
                (BIOMETRIC_CONSENT_VERSION, license_id, user_id),
            )
            row = await cur.fetchone()

    if not row:
        return _private_not_found()

    r2 = _r2_face(request)
    key = row["face_image_key"]
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        return _private_not_found()
    return Response(
        content=data,
        media_type=row["mime_type"],
        headers={"Cache-Control": "no-store, private"},
    )


@router.get(
    "/models/{model_id}/thumbnail",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    tags=["FaceMarket"],
    summary="모델 카탈로그 썸네일 (게이트)",
)
async def get_model_thumbnail(
    request: Request,
    model_id: str,
    user_id: str = Depends(require_user),
):
    """현재 사용 가능한 모델에 동일한 서버 소유 비생체 placeholder를 반환한다."""
    try:  # uuid 형식 가드 — 쓰레기 입력은 500 아닌 404
        model_id = str(uuid.UUID(str(model_id)))
    except (TypeError, ValueError):
        return _private_not_found()
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select 1 as eligible from fm_models m
                    {_CURRENT_CARD_JOINS}
                    where {_CURRENT_CARD_ELIGIBILITY}
                      and m.id = %s
                    limit 1""",
                (BIOMETRIC_CONSENT_VERSION, model_id),
            )
            row = await cur.fetchone()
    if not row:
        return _private_not_found()
    return Response(
        content=_MODEL_PLACEHOLDER_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store, private"},
    )


# ============================================================================
# step02 공개 검증 (QR) — **무인증**. 심사위원·구매자가 QR(`{origin}/verify/{licenseId}`)을 찍어
# "이 얼굴 라이선스가 진짜 유효한가"를 로그인 없이 확인한다. 선례 = routes.get_asset_file
# (무인증 공개 라우트 — capability URL). license_id(UUIDv4)가 능력 토큰.
#
# 🔴 하드룰(위반 = 영구 유출):
#   이 라우트에 실리는 값은 무인증이라 한 번 나가면 회수 불가다. 절대 미노출 —
#   얼굴 이미지·face_image_key·face_image_uri·face_image_digest·CI·ci_hash·생년월일(원문)·
#   user_id·model_id·내부 R2 키.
#   3중 방어로 못 새게 막는다:
#     ① SELECT 자체를 화이트리스트로 좁힌다 — 안 읽으면 못 샌다(얼굴·식별자 컬럼 미조회).
#     ② response_model=PublicVerifyResult — FastAPI 가 선언 밖 필드를 직렬화에서 **탈락**시킨다.
#     ③ 신원은 파생값만 — 이름은 마스킹(_mask_name), 생년월일은 연도조차 안 싣고 만 나이 int 로만.
#   필드 추가 요청이 오면 이 주석을 먼저 읽을 것. 확장은 계약 변경이다.
# ============================================================================


class PublicVerifyModel(CamelModel):
    """공개 검증의 모델 신원 — **파생·마스킹 값만**. 실명·생년월일·식별자 금지."""

    name_masked: str
    age: int | None = None  # 만 나이(birthYear 파생). 연도 미보관·파싱 불가면 null


class PublicVerifyResult(CamelModel):
    """QR 공개 검증 응답 화이트리스트. **이 필드가 전부** — 확장 금지(위 하드룰)."""

    valid: bool
    status: str  # 'active' | 'revoked' | 'expired'
    allowed_use: list[str]
    forbidden_use: list[str]
    unit_price: int
    valid_until: datetime
    vc_id: str | None = None
    model: PublicVerifyModel


@router.get(
    "/verify/{license_id}",
    response_model=PublicVerifyResult,
    responses={404: {"model": ErrorResponse, "description": "라이선스 없음/잘못된 id"}},
    tags=["FaceMarket"],
    summary="얼굴 라이선스 공개 검증 (QR — 무인증)",
)
async def verify_license_public(request: Request, license_id: str, response: Response):
    """QR 스캔 대상 공개 검증 페이지의 데이터 소스. **인증 없음**(심사위원이 즉석에서 스캔).

    - **인증 없음 (capability URL)**: license_id(UUIDv4)가 능력 토큰. 얼굴·신원 원문은 한 톨도
      싣지 않으므로 무인증 노출이 성립한다(노출 목록은 위 하드룰 참조).
    - **valid**: 실시간 판정 = `status=='active' AND license_valid_until > now`. DB status 가
      active 라도 기간이 지났으면 `status='expired'` + `valid=false` 로 내린다 — 두 필드가
      어긋나면(`status:'active', valid:false`) 스캔한 사람이 이유를 알 수 없다.
    - **에지 케이스**: `404 not_found`(비존재·잘못된 uuid — 존재 여부 노출 방지)
    """
    try:  # 공개 라우트 — 쓰레기 입력은 DB 전에 404로 컷(get_asset_file 선례)
        lic_uuid = uuid.UUID(str(license_id))
    except (ValueError, TypeError):
        raise _err("not_found", "라이선스를 찾을 수 없습니다.", status=404)
    # 파싱 결과를 써야 한다 — 원문을 그대로 쿼리에 넣으면 uuid.UUID() 가 받아주는 별칭 표기
    # (`urn:uuid:…`·중괄호 형태)가 가드를 통과한 뒤 PG 캐스팅에서 터져 404 대신 500 이 된다.
    lic_id = str(lic_uuid)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 방어 ① — 화이트리스트 SELECT. 얼굴(face_image_*)·식별자(user_id·model_id·ci_hash)는
            # 조회조차 하지 않는다. birthYear 는 만 나이 파생에만 쓰고 응답에 싣지 않는다.
            await cur.execute(
                """select l.status, l.allowed_use, l.forbidden_use, l.unit_price,
                          l.license_valid_until, l.vc_id, m.display_name,
                          (select v.fields->>'birthYear' from fm_identity_verifications v
                           where v.model_id = m.id
                           order by v.verified_at desc limit 1) as birth_year
                   from fm_licenses l
                   join fm_models m on m.id = l.model_id
                   where l.id = %s""",
                (lic_id,),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "라이선스를 찾을 수 없습니다.", status=404)

    # 실시간 상태 — 만료 판정은 게이트(verify_license)와 같은 _is_expired 를 쓴다(단일 소스).
    status = row["status"]
    if status == "active" and _is_expired(row):
        status = "expired"

    # 공개 캐시·CDN·브라우저 저장 금지 — 해지가 즉시 반영돼야 한다(캐시된 valid=true = 사고).
    response.headers["Cache-Control"] = "no-store"
    return {
        "valid": status == "active",
        "status": status,
        "allowedUse": row["allowed_use"] or [],
        "forbiddenUse": row["forbidden_use"] or [],
        "unitPrice": row["unit_price"],
        "validUntil": row["license_valid_until"],
        "vcId": row["vc_id"],
        # display_name 은 등록 시 이미 마스킹돼 저장되지만(_mask_name), 무인증 노출 지점이라
        # 한 번 더 통과시킨다 — 상류가 언젠가 실명을 넣어도 여기서 새지 않게(멱등: 홍*동→홍*동).
        "model": {
            "nameMasked": _mask_name(row["display_name"]),
            "age": _age_from_birth_year(row["birth_year"]),
        },
    }


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


def _request_client_ip(request: Request) -> str:
    """AWS ALB append-mode XFF의 마지막 주소를 사용하고 ASGI peer로 폴백한다."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidate = forwarded.rsplit(",", 1)[-1].strip()
        try:
            return str(ip_address(candidate))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


async def _take_simulation_rate_slot(
    conn, *, user_id: str, client_ip: str, pepper: str
) -> None:
    """공유 PostgreSQL에서 admin과 IP 각각 분당 실 TX 상한을 원자적으로 소비한다."""
    admin_key = hmac.new(pepper.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    ip_key = hmac.new(pepper.encode(), client_ip.encode(), hashlib.sha256).hexdigest()
    async with conn.cursor() as cur:
        await cur.execute(
            """with pruned as (
                   delete from public.fm_settlement_simulation_limits
                    where window_start < clock_timestamp() - interval '1 day'
               ), requested(scope, key_hash) as (
                   values ('admin', %s), ('ip', %s)
               ), consumed as (
                   insert into public.fm_settlement_simulation_limits
                       (scope, key_hash, window_start, request_count)
                   select scope, key_hash, date_trunc('minute', clock_timestamp()), 1
                     from requested
                   on conflict (scope, key_hash, window_start) do update
                      set request_count = fm_settlement_simulation_limits.request_count + 1
                    where fm_settlement_simulation_limits.request_count < %s
                   returning 1
               )
               select count(*)::int as accepted from consumed""",
            (admin_key, ip_key, _SIMULATION_RATE_LIMIT_PER_MINUTE),
        )
        accepted = (await cur.fetchone())["accepted"]
    if accepted != 2:
        raise _err("rate_limited", "잠시 후 다시 시도해주세요.", status=429)


async def _run_chain_call_until_done(call, **kwargs):
    """요청 취소 시에도 signer lock을 chain thread가 끝날 때까지 유지한다."""
    task = asyncio.create_task(asyncio.to_thread(call, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def _find_settlement(conn, payment_key: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_SETTLEMENT_COLS} from fm_settlements where payment_id = %s",
            (payment_key,),
        )
        return await cur.fetchone()


def _chain_result(chain, stored: dict, tx_hash: str | None = None) -> dict:
    return {
        "tx_hash": tx_hash, "block": stored["block"], "chain_id": chain.chain_id,
        "model_ref": stored["model_ref"], "model_amount": stored["model_amount"],
        "platform_amount": stored["platform_amount"], "ops_amount": stored["ops_amount"],
        "total": stored["total"],
    }


async def _mirror_settlement(conn, intent: dict, result: dict) -> dict | None:
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
                intent["payment_id"], intent.get("job_id"), intent.get("license_id"),
                intent.get("credit_ledger_id"), result["model_ref"], int(result["total"]),
                result["model_amount"], result["platform_amount"], result["ops_amount"],
                result["tx_hash"], str(result["chain_id"]), result["block"],
            ),
        )
        row = await cur.fetchone()
    return row or await _find_settlement(conn, intent["payment_id"])


async def _reconcile_settlement_intents(conn, chain) -> None:
    """이전 task가 broadcast 중 죽었다면 먼저 체인 결과를 미러하거나 timeout까지 fence한다."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select payment_id, license_id::text, job_id::text, credit_ledger_id::text,
                      model_id, total_amount, attempted_at
                 from fm_settlement_signer_intents
                where status = 'broadcasting'
                order by attempted_at nulls first"""
        )
        intents = await cur.fetchall()

    for intent in intents:
        if await _find_settlement(conn, intent["payment_id"]):
            status = "confirmed"
        else:
            attempted_at = intent.get("attempted_at") or datetime.now(timezone.utc)
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=timezone.utc)
            remaining = max(
                float(getattr(chain, "confirm_timeout", 90.0))
                - (datetime.now(timezone.utc) - attempted_at).total_seconds(),
                0.0,
            )
            stored = await _run_chain_call_until_done(
                chain.wait_for_settlement,
                payment_key=intent["payment_id"],
                timeout=remaining,
            )
            if not stored or not stored.get("exists"):
                # RPC 오류를 미기록으로 오판해 pending nonce를 재사용하지 않도록 마지막 조회는 fail-closed.
                stored = await _run_chain_call_until_done(
                    chain.get_settlement, payment_key=intent["payment_id"]
                )
            if stored and stored.get("exists"):
                result = _chain_result(chain, stored)
            else:
                # exists=false는 mempool에 같은 nonce의 pending TX가 없다는 증거가 아니다.
                # 다른 payment를 보내지 말고 동일 payment만 안전하게 재전송한다.
                try:
                    result = await _run_chain_call_until_done(
                        chain.record_settlement,
                        payment_key=intent["payment_id"],
                        model_uuid=intent["model_id"],
                        total=int(intent["total_amount"]),
                    )
                except Exception:
                    stored = await _run_chain_call_until_done(
                        chain.wait_for_settlement, payment_key=intent["payment_id"]
                    )
                    if not stored or not stored.get("exists"):
                        stored = await _run_chain_call_until_done(
                            chain.get_settlement, payment_key=intent["payment_id"]
                        )
                    if not stored or not stored.get("exists"):
                        raise RuntimeError(
                            f"settlement intent unresolved: {intent['payment_id']}"
                        )
                    result = _chain_result(chain, stored)
            await _mirror_settlement(conn, intent, result)
            status = "confirmed"
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_settlement_signer_intents set status = %s where payment_id = %s",
                (status, intent["payment_id"]),
            )
    await conn.commit()


async def record_license_settlement(
    app,
    *,
    payment_key: str,
    license_id: str,
    model_id: str,
    total: int,
    job_id: str | None = None,
    credit_ledger_id: str | None = None,
    first_attempt=None,
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
        existing = await _find_settlement(conn, payment_key)
        if not existing:
            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """insert into fm_settlement_signer_intents
                            (payment_id, license_id, job_id, credit_ledger_id,
                             model_id, total_amount)
                            values (%s, %s, %s, %s, %s, %s)
                            on conflict (payment_id) do nothing
                            returning payment_id""",
                        (
                            payment_key, license_id, job_id, credit_ledger_id,
                            model_id, int(total),
                        ),
                    )
                    inserted = await cur.fetchone()
                if inserted and first_attempt:
                    await first_attempt(conn)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
    if existing:
        return existing

    # Session advisory lock은 commit 뒤에도 유지된다. 미획득자는 연결을 즉시 반납해 작은 pool을
    # 고갈시키지 않고, owner만 durable broadcasting intent→RPC→mirror 구간에 연결 하나를 쓴다.
    while True:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select pg_try_advisory_lock(%s) as locked",
                    (_SETTLEMENT_SIGNER_LOCK_ID,),
                )
                locked = (await cur.fetchone())["locked"]
            if not locked:
                continue_after_release = True
            else:
                continue_after_release = False
                try:
                    await _reconcile_settlement_intents(conn, chain)
                    existing = await _find_settlement(conn, payment_key)
                    if existing:
                        return existing

                    async with conn.cursor() as cur:
                        await cur.execute(
                            """update fm_settlement_signer_intents
                                  set status = 'broadcasting', attempted_at = clock_timestamp()
                                where payment_id = %s""",
                            (payment_key,),
                        )
                    await conn.commit()  # crash fence가 RPC 전 반드시 durable 해야 한다.

                    try:
                        result = await _run_chain_call_until_done(
                            chain.record_settlement,
                            payment_key=payment_key,
                            model_uuid=model_id,
                            total=int(total),
                        )
                    except Exception:
                        try:
                            stored = await _run_chain_call_until_done(
                                chain.wait_for_settlement, payment_key=payment_key
                            )
                            if not stored or not stored.get("exists"):
                                stored = await _run_chain_call_until_done(
                                    chain.get_settlement, payment_key=payment_key
                                )
                        except Exception:
                            logger.exception(
                                "settlement_record_failed", extra={"payment_key": payment_key}
                            )
                            return None
                        if not stored or not stored.get("exists"):
                            logger.error(
                                "settlement_record_unresolved", extra={"payment_key": payment_key}
                            )
                            return None
                        result = _chain_result(chain, stored)

                    intent = {
                        "payment_id": payment_key, "license_id": license_id, "job_id": job_id,
                        "credit_ledger_id": credit_ledger_id,
                    }
                    row = await _mirror_settlement(conn, intent, result)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update fm_settlement_signer_intents set status = %s "
                            "where payment_id = %s",
                            ("confirmed", payment_key),
                        )
                    await conn.commit()
                    logger.info(
                        "settlement_recorded",
                        extra={
                            "payment_key": payment_key, "tx_hash": result["tx_hash"],
                            "total": total,
                        },
                    )
                    return row
                except Exception:
                    logger.exception(
                        "settlement_recovery_failed", extra={"payment_key": payment_key}
                    )
                    return None
                finally:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "select pg_advisory_unlock(%s) as unlocked",
                            (_SETTLEMENT_SIGNER_LOCK_ID,),
                        )
                    await conn.commit()
        if continue_after_release:
            await asyncio.sleep(_SETTLEMENT_LOCK_RETRY_SECONDS)


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
        429: {"model": ErrorResponse, "description": "실 TX 분당 요청 한도 초과"},
    },
    tags=["FaceMarket"],
    summary="정산 시뮬레이션 (데모/부하 — 실 TX)",
)
async def simulate_settlement(
    request: Request,
    body: SimulateRequest,
    user_id: str = Depends(require_user),
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
):
    """라이선스 1건 사용을 온체인에 실제 기록(장면④·KPI '시뮬' 집계용). 실 상세페이지 잡과 분리.

    관리자 본인의 active 라이선스만 허용한다. payment_id 는 Idempotency-Key에 결정적으로 묶어
    재시도가 실 TX를 중복 생성하지 않는다. 실거래(워커 훅)와 분리해 집계한다.
    """
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        if not await repo.is_admin(conn, user_id):
            raise _err("forbidden", "관리자만 가능해요.", status=403)
        chain = getattr(request.app.state, "fm_chain", None)
        if chain is None:
            raise _err("chain_unavailable", "체인이 설정되지 않았습니다.", status=404)
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

    key = idempotency_key.strip()
    if not key:
        raise _err("idempotency_key_required", "Idempotency-Key가 필요합니다.")
    digest = hashlib.sha256(f"{user_id}:{key}".encode()).hexdigest()
    payment_key = f"sim:{body.license_id}:{digest}"

    async def _first_attempt_rate_slot(conn):
        await _take_simulation_rate_slot(
            conn,
            user_id=user_id,
            client_ip=_request_client_ip(request),
            pepper=request.app.state.settings.fm_ci_pepper,
        )

    row = await record_license_settlement(
        request.app,
        payment_key=payment_key,
        license_id=lic["id"],
        model_id=lic["model_id"],
        total=int(lic["unit_price"]),
        first_attempt=_first_attempt_rate_slot,
    )
    if row is None:
        raise _err("settlement_failed", "온체인 정산 기록에 실패했습니다.", status=502)
    return row


# ============================================================================
# FaceLicense VC 발급 (OpenDID 커스터디얼 홀더 :8100 배선).
# 모델/라이선스 활성화 전 동기 발급. 실패 응답·claims·외부 body는 로그/응답에 싣지 않는다.
# ============================================================================

_HOLDER_TIMEOUT = 180.0


@dataclass(frozen=True, slots=True)
class FaceVcIssueResult:
    vc_id: str
    user_did: str | None


class FaceVcIssueError(RuntimeError):
    def __init__(self, code: str, *, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def build_face_vc_claims(*, allowed, forbidden, unit_price, valid_until, digest) -> dict:
    valid_str = valid_until.date().isoformat() if hasattr(valid_until, "date") else str(valid_until)
    return {
        "allowedUse": ", ".join(allowed),
        "forbiddenUse": ", ".join(forbidden),
        "unitPrice": int(unit_price),
        "licenseValidUntil": valid_str,
        "faceImageDigest": digest,
    }


async def issue_face_vc(app, *, license_id, model_id, allowed, forbidden,
                        unit_price, valid_until, digest) -> FaceVcIssueResult:
    base = getattr(app.state.settings, "opendid_holder_url", None)
    secret = getattr(app.state.settings, "opendid_holder_hmac_secret", None)
    if not base or not base.strip() or not secret or not secret.strip():
        raise FaceVcIssueError("holder_unavailable", status_code=503)

    async def checked_post(client, path, payload, accepted):
        response = await holder_client.post(
            client,
            base_url=base,
            secret=secret,
            path=path,
            payload=payload,
        )
        if response.status_code not in accepted:
            status = 503 if response.status_code >= 500 else 502
            raise FaceVcIssueError("vc_issue_delayed", status_code=status)
        return response

    try:
        async with httpx.AsyncClient(timeout=_HOLDER_TIMEOUT) as client:
            await checked_post(
                client, f"/holder/models/{model_id}/wallet", {}, {201, 409}
            )
            register = await checked_post(
                client, f"/holder/models/{model_id}/register-did", {}, {200}
            )
            register_body = register.json()
            if not isinstance(register_body, Mapping):
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
            user_did = register_body.get("userDid")
            if user_did is not None and (
                not isinstance(user_did, str) or not user_did.strip()
            ):
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
            if register_body.get("flowAComplete") is not True and not user_did:
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
            issue = await checked_post(
                client,
                f"/holder/models/{model_id}/issue-vc",
                {
                    "plan": "facelicense",
                    "idempotencyKey": f"fm-license:{license_id}",
                    "claims": build_face_vc_claims(
                        allowed=allowed, forbidden=forbidden, unit_price=unit_price,
                        valid_until=valid_until, digest=digest,
                    ),
                },
                {200},
            )
            issue_body = issue.json()
            if not isinstance(issue_body, Mapping):
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
            vc_id = issue_body.get("vcId")
            if not isinstance(vc_id, str) or not vc_id.strip():
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
            issue_user_did = issue_body.get("userDid")
            if issue_user_did is not None and (
                not isinstance(issue_user_did, str) or not issue_user_did.strip()
            ):
                raise FaceVcIssueError("vc_issue_delayed", status_code=502)
    except FaceVcIssueError:
        raise
    except httpx.TransportError as exc:
        raise FaceVcIssueError("vc_issue_delayed", status_code=503) from exc
    except Exception as exc:
        raise FaceVcIssueError("vc_issue_delayed", status_code=502) from exc
    return FaceVcIssueResult(vc_id=vc_id.strip(), user_did=issue_user_did or user_did)


# ============================================================================
# FM-30 verify-before-use 게이트 + 라이선스 해지 + 잡 정산 영수증.
# 상세페이지 생성(routes.generate_detail_page) 진입 시 셀러가 고른 모델의 얼굴 라이선스를
# 서버측에서 해석·검증한다. facemarket off면 게이트 미진입 → 기존 셀러 플로우 무영향.
# ============================================================================

_HOLDER_VERIFY_TIMEOUT = 5.0  # 게이트는 셀러 요청 블로킹 경로 — 홀더 지연이 생성 지연되지 않게 짧게.

async def resolve_project_license(conn, project: dict, analysis: dict) -> dict | None:
    """Resolve the selected model's current license; historical project locks are inert."""
    selected_model_id = (analysis or {}).get("selectedModelId") or (
        analysis or {}
    ).get("selected_model_id")
    return await resolve_model_license(conn, selected_model_id)


async def resolve_model_license(
    conn,
    model_id: str | None,
    *,
    license_id: str | None = None,
    for_update: bool = False,
) -> dict | None:
    """Load a model's current runtime evidence, optionally pinned to one license."""
    if not model_id:
        return None
    try:
        uuid.UUID(str(model_id))
    except (ValueError, TypeError):
        return None

    lock = " for update of m, l, e" if for_update and license_id is not None else ""
    join = "join" if lock else "left join"
    license_join = "l.model_id = m.id and l.enrollment_id = m.current_enrollment_id"
    where = "m.id = %s"
    params: tuple[str, ...] = (str(model_id),)
    if license_id is not None:
        license_join = "l.model_id = m.id"
        where += " and l.id = %s"
        params = (str(model_id), str(license_id))
    async with conn.cursor() as cur:
        await cur.execute(
            f"""select l.id::text as id, m.id::text as model_id,
                       m.display_name as _model_name_raw, l.status,
                       l.license_valid_until, l.unit_price, l.vc_id,
                       l.allowed_use, l.forbidden_use,
                       m.status as model_status, m.assets_status,
                       m.current_enrollment_id::text as current_enrollment_id,
                       l.enrollment_id::text as license_enrollment_id,
                       e.status as enrollment_status, e.match_policy_version,
                       exists (
                           select 1 from fm_model_assets a
                            where a.model_id = m.id and a.view = 'face_front'
                              and nullif(btrim(a.r2_key), '') is not null
                              and a.bucket = 'face' and a.mime like 'image/%%'
                       ) as has_face_front,
                       exists (
                           select 1 from fm_model_assets a
                            where a.model_id = m.id and a.view = 'grid_sedcard'
                              and nullif(btrim(a.r2_key), '') is not null
                              and a.bucket = 'face' and a.mime like 'image/%%'
                       ) as has_grid_sedcard,
                       nullif(btrim(e.match_policy_version), '') is not null
                       and exists (
                           select 1 from fm_model_assets a
                            where a.model_id = m.id and a.view = 'face_front'
                              and nullif(btrim(a.r2_key), '') is not null
                              and a.bucket = 'face' and a.mime like 'image/%%'
                              and a.source_enrollment_id = m.current_enrollment_id
                              and a.evidence_version = e.match_policy_version
                       ) and exists (
                           select 1 from fm_model_assets a
                            where a.model_id = m.id and a.view = 'grid_sedcard'
                              and nullif(btrim(a.r2_key), '') is not null
                              and a.bucket = 'face' and a.mime like 'image/%%'
                              and a.source_enrollment_id = m.current_enrollment_id
                              and a.evidence_version = e.match_policy_version
                       ) as assets_current_evidence
                  from fm_models m
                  {join} fm_biometric_enrollments e
                    on e.id = m.current_enrollment_id and e.model_id = m.id
                  {join} fm_licenses l on {license_join}
                 where {where}
                 limit 1{lock}""",
            params,
        )
        row = await cur.fetchone()
    if row is None:
        return None
    result = dict(row)
    raw_name = result.pop("_model_name_raw", result.pop("model_name", ""))
    result["model_name"] = _mask_name(raw_name or "")
    return result


def _is_expired(license_row: dict) -> bool:
    """`license_valid_until` 이 이미 지났는가. naive 값은 UTC 로 간주(DB timestamptz 관례).

    `verify_license`(게이트, 예외)와 `verify_license_public`(QR, bool)이 만료를 **같은 코드로**
    판정하게 하는 단일 소스 — 갈리면 QR 이 유효하다는 라이선스를 게이트가 막는 모순이 난다.
    """
    valid_until = license_row.get("license_valid_until")
    if valid_until is None:
        return False
    vu = valid_until
    if getattr(vu, "tzinfo", None) is None:
        vu = vu.replace(tzinfo=timezone.utc)
    return vu <= datetime.now(timezone.utc)


def verify_license_local(
    app,
    license_row: dict | None,
    *,
    model_id: str | None,
    brand_use_category: str | None,
) -> None:
    """Local-only runtime gate. No Holder/network; safe while DB locks are held."""
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return

    if (
        license_row is None
        or str(license_row.get("model_id") or "") != str(model_id)
        or license_row.get("model_status") != "verified"
    ):
        raise _err("model_unavailable", "사용할 수 없는 모델입니다.", status=409)

    status = license_row.get("status")
    if status == "revoked":
        raise _err(
            "license_revoked",
            "이 모델의 얼굴 라이선스가 해지되어 사용할 수 없습니다.",
            status=409,
        )
    if status != "active":
        raise _err("license_inactive", "활성화된 얼굴 라이선스가 아닙니다.", status=409)

    if _is_expired(license_row):
        raise _err(
            "license_expired",
            "얼굴 라이선스 사용 기간이 만료되었습니다.",
            status=409,
        )

    category = (
        brand_use_category.strip()
        if isinstance(brand_use_category, str)
        else ""
    )
    fixed_categories = {
        *ALLOWED_BRAND_USE_CATEGORIES,
        *FORBIDDEN_BRAND_USE_CATEGORIES,
    }
    if not category or category not in fixed_categories:
        raise _err(
            "brand_use_category_required",
            "브랜드 사용 카테고리를 확인해 주세요.",
            status=409,
        )
    forbidden = license_row.get("forbidden_use")
    if (
        not isinstance(forbidden, list)
        or any(
            not isinstance(value, str)
            or value not in FORBIDDEN_BRAND_USE_CATEGORIES
            for value in forbidden
        )
        or category in forbidden
    ):
        raise _err(
            "license_use_forbidden",
            "이 라이선스에서 금지된 사용 카테고리입니다.",
            status=409,
        )
    allowed = license_row.get("allowed_use")
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(
            not isinstance(value, str)
            or value not in ALLOWED_BRAND_USE_CATEGORIES
            for value in allowed
        )
        or category not in allowed
    ):
        raise _err(
            "license_use_not_allowed",
            "이 라이선스에서 허용된 사용 카테고리가 아닙니다.",
            status=409,
        )
    enrollment_id = str(license_row.get("current_enrollment_id") or "")
    if (
        not enrollment_id
        or license_row.get("enrollment_status") != "passed"
        or not str(license_row.get("match_policy_version") or "").strip()
        or str(license_row.get("license_enrollment_id") or "") != enrollment_id
    ):
        raise _err(
            "model_enrollment_unavailable",
            "현재 모델 등록을 사용할 수 없습니다.",
            status=409,
        )
    if (
        license_row.get("assets_status") != "ready"
        or license_row.get("has_face_front") is not True
        or license_row.get("has_grid_sedcard") is not True
        or license_row.get("assets_current_evidence") is not True
    ):
        raise _err(
            "model_assets_unavailable",
            "현재 모델 자산을 사용할 수 없습니다.",
            status=409,
        )
    if bool(getattr(app.state.settings, "fm_vc_required", False)) and not license_row.get("vc_id"):
        raise _err(
            "license_unverified",
            "라이선스 자격 증명(VC)이 준비되지 않았습니다.",
            status=409,
        )


async def verify_license(
    app,
    license_row: dict | None,
    *,
    model_id: str | None,
    brand_use_category: str | None,
) -> None:
    """얼굴 라이선스 사용 자격 검증. 실패 시 409 {code, message}(KR) 발생.

    검사 순서(계약):
      1. status == 'revoked'   → 409 license_revoked
      1'. status != 'active'   → 409 license_inactive (suspended 등)
      2. license_valid_until <= now → 409 license_expired
      3. mandatory VC 누락/invalid/revoked → 409, Holder 장애/계약 오류 → 503
    optional 개발 모드는 Holder 설정 또는 VC가 없을 때만 로컬 검사로 끝낸다.
    """
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return

    required = bool(getattr(app.state.settings, "fm_vc_required", False))
    base = getattr(app.state.settings, "opendid_holder_url", None)
    secret = getattr(app.state.settings, "opendid_holder_hmac_secret", None)
    verify_license_local(
        app,
        license_row,
        model_id=model_id,
        brand_use_category=brand_use_category,
    )
    vc_id = license_row.get("vc_id")
    if required and (
        not base or not base.strip() or not secret or not secret.strip()
    ):
        raise _err(
            "holder_unavailable",
            "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다.",
            status=503,
        )
    if (
        not base
        or not base.strip()
        or not secret
        or not secret.strip()
        or not vc_id
    ):
        return
    try:
        async with httpx.AsyncClient(timeout=_HOLDER_VERIFY_TIMEOUT) as client:
            response = await holder_client.post(
                client,
                base_url=base,
                secret=secret,
                path="/holder/vc/verify",
                payload={"vcId": vc_id},
            )
    except Exception:
        logger.warning("holder_vc_verify_unreachable")
        raise _err(
            "holder_unavailable",
            "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다.",
            status=503,
        )
    if response.status_code != 200:
        raise _err(
            "holder_unavailable",
            "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다.",
            status=503,
        )
    try:
        verify_result = response.json()
    except Exception:
        raise _err(
            "holder_unavailable",
            "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다.",
            status=503,
        )
    if not isinstance(verify_result, Mapping):
        raise _err(
            "holder_unavailable",
            "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다.",
            status=503,
        )
    if (
        verify_result.get("verified") is not True
        or verify_result.get("status") != "valid"
    ):
        raise _err(
            "license_unverified",
            "온체인에서 라이선스 자격 증명(VC)이 확인되지 않았습니다.",
            status=409,
        )


async def set_project_license(conn, project_id: str, license_id: str) -> None:
    """검증 통과한 라이선스를 프로젝트에 잠근다. 워커 정산 훅이 이 값을 읽는다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "update projects set facemarket_license_id = %s where id = %s",
            (str(license_id), str(project_id)),
        )


@router.post(
    "/licenses/{license_id}/revoke",
    response_model=LicenseCard,
    responses={
        **_FM_RESPONSES,
        404: {"model": ErrorResponse, "description": "라이선스 없음/비소유"},
    },
    tags=["FaceMarket"],
    summary="얼굴 라이선스 해지 (모델 본인)",
)
async def revoke_license(
    request: Request, license_id: str, user_id: str = Depends(require_user)
):
    """검증 모델 본인이 자신의 얼굴 라이선스를 해지한다(장면⑤).

    - **Bearer Token**: 필수 (라이선스 소유 모델 본인 — fm_models.user_id 조인 스코프)
    - **효과**: `status='revoked'` → 이후 얼굴 게이트(404)·verify 게이트(409) 모두 차단.
      VC가 있으면 같은 트랜잭션에 내구성 폐기 잡을 적재한다.
    - **에지 케이스**: `404 not_found`(비존재·비소유)
    """
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """select l.id::text as id, l.model_id::text as model_id, l.vc_id, l.status
                   from fm_licenses l join fm_models m on m.id = l.model_id
                   where l.id = %s and m.user_id = %s
                   for update of l""",
                (license_id, user_id),
            )
            lic = await cur.fetchone()
        if not lic:
            raise _err("not_found", "라이선스를 찾을 수 없습니다.", status=404)
        async with conn.cursor() as cur:
            await cur.execute(
                f"""update fm_licenses set status = 'revoked'
                    where id = %s returning {_LICENSE_CARD_COLS}""",
                (license_id,),
            )
            row = await cur.fetchone()
        if lic.get("vc_id"):
            await enqueue_vc_revocation(
                conn,
                license_id=lic["id"],
                model_id=lic["model_id"],
                vc_id=lic["vc_id"],
            )
        await conn.commit()
    return row


@router.get(
    "/jobs/{job_id}/settlement",
    responses={
        401: {"model": ErrorResponse, "description": "인증 실패"},
        404: {"model": ErrorResponse, "description": "정산 미기록/비소유 잡"},
    },
    tags=["FaceMarket"],
    summary="상세페이지 잡 정산 영수증",
)
async def get_job_settlement(
    request: Request, job_id: str, user_id: str = Depends(require_user)
):
    """상세페이지 생성 잡의 얼굴 라이선스 정산 영수증(장면⑤ 영수증 UI).

    `payment_id = f"job:{job_id}"` 정산 미러 + 라이선스 vc_id 를 한 번에 반환.
    잡 소유자(셀러) 스코프 — 남의 잡은 404. 정산 미기록(체인 미설정/실패)이면 404.
    """
    payment_id = f"job:{job_id}"
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select st.payment_id, st.tx_hash, st.chain_id, st.total_amount,
                          st.model_amount, st.platform_amount, st.ops_amount,
                          st.chain_status, l.vc_id
                   from fm_settlements st
                   join jobs j on j.id = st.job_id
                   left join fm_licenses l on l.id = st.license_id
                   where st.payment_id = %s and j.user_id = %s""",
                (payment_id, user_id),
            )
            row = await cur.fetchone()
    if not row:
        raise _err("not_found", "정산 내역을 찾을 수 없습니다.", status=404)
    # 나머지 FM API 와 동일 camelCase — 영수증 UI 가 그대로 소비.
    return {
        "paymentId": row["payment_id"],
        "txHash": row["tx_hash"],
        "chainId": row["chain_id"],
        "totalAmount": row["total_amount"],
        "modelAmount": row["model_amount"],
        "platformAmount": row["platform_amount"],
        "opsAmount": row["ops_amount"],
        "vcId": row["vc_id"],
        "chainStatus": row["chain_status"],
    }
