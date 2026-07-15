"""개인화(사용자 본인 얼굴·신체) — 경로-독립 라우터 (docs/personalization/api-spec.md §1~3·§5).

`PERSONALIZATION_ENABLED` 게이트: off(프로드 기본)면 main.py 가 이 라우터를 아예 등록하지 않는다
→ 생체정보 처리 코드가 프로드에 배포되지 않는다(§1.1). off = 전 라우트 일반 404.

도메인 요약:
  · 동의(consent)         — append-only 이력, 항목별 granted/withdrawn, 필수(service_use·cross_border).
  · 얼굴 사진(face-photos) — multipart 직접 수신 + 동기 QC(Gemini 비전). 통과분만 비공개 R2 저장 +
                            게이트 스트림. 원본·digest·r2_key 응답/로그/payload 미노출(§1.4).
  · 신체(profile/body)    — height/weight 범위검증. 민감정보 준함(로그 금지).
  · 상태(status)          — canGenerate + blockers 체크리스트(온보딩 단일 소스).
  · 철회(:withdraw)       — status→purging + 비동기 personalization_purge 잡(캐스케이드 파기).
  · 생성(generations)     — verify-before-use 게이트 후 personalization_generation 잡 큐잉(워커가 실행).

PII 하드 룰(§1.4 — 위반 금지):
  · 얼굴 원본·임베딩·해시(digest)·r2_key = 응답/로그/에러/job payload 절대 미노출. 화이트리스트만.
  · cross_border_transfer 동의 없이는 얼굴 바이트가 어떤 외부 API(Gemini 등 = 미국)로도 안 나감
    — 업로드 라우트가 QC 호출 전에 필수 동의를 코드로 게이트한다.
  · purging 중 모든 쓰기 = 409 purge_in_progress.

워커 계약(B 에이전트 공유):
  · 생성 잡: kind='personalization_generation', project_id=None,
             payload={profileId, productImageAssetIds, options, generationId}.
             (얼굴 바이트/게이트URL payload 금지 — 워커가 profileId 로 서버측 로드.)
  · 파기 잡: kind='personalization_purge', payload={profileId}.
"""

import asyncio
import hashlib
import logging
import uuid

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from . import cx_identity, repo
from .auth import require_user
from .db import get_conn
from .models import CamelModel, ErrorResponse
from .personalization_qc import FaceQcUnavailable, evaluate_face_qc, qc_reason_message
from .r2 import ext_for_mime, sha256_sri

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/personalization", tags=["Personalization"])

# ── 도메인 상수 ──────────────────────────────────────────────
ANGLES = ("front", "side", "angle45")
ALL_CONSENT_TYPES = ("service_use", "training_use", "cross_border_transfer")
REQUIRED_CONSENTS = ("service_use", "cross_border_transfer")
MAX_FACE_BYTES = 15 * 1024 * 1024  # 15MB — routes.MAX_UPLOAD_BYTES / facemarket MAX_FACE_BYTES 미러
_ALLOWED_FACE_MIME = {"image/png", "image/jpeg", "image/webp"}  # 얼굴은 png/jpg/webp 만(§3.2)

# 동의 문서 현행 버전(법무 확정값 자리). 제출 docVersion 이 불일치하면 400 stale_consent_doc.
CONSENT_DOC_VERSION = "2026-10-v1"
RETENTION_DAYS = 365  # 보관기간 고지값(법무 확정값 자리 — §3.1)
NOTICE_URIS = {  # 보관기간·제3자 제공·국외이전 고지 문서(법무 확정 URI 자리)
    "retention": "/legal/personalization/retention",
    "thirdParty": "/legal/personalization/third-party",
    "crossBorder": "/legal/personalization/cross-border",
}

# 개인화 생성 단가(TBD — 스파이크 T0-2). config 필드 없으면 이 기본값. env/설정으로 덮어씀.
_DEFAULT_GENERATION_COST = 3

# 프로필 화이트리스트 컬럼 — r2_key/image_digest 등 내부 식별자는 절대 비노출(§1.4).
_PROFILE_COLS = (
    "id::text as id, status, height_cm, weight_kg, body_type, body_type_custom, "
    "gender, age_range, skin_tone, hair, clothing_size, created_at, updated_at, purged_at"
)


# ── 요청 모델(도메인 검증은 핸들러에서 400, 타입 위반만 422 자동) ──
class IdentityVerifyBody(CamelModel):
    """CX 표준인증창 성공 콜백 token 만. 원문 신원은 서버가 CX 에서 직접 받는다(클라 신뢰 금지)."""

    token: str


class ConsentItem(CamelModel):
    type: str
    doc_version: str | None = None


class ConsentSubmitBody(CamelModel):
    items: list[ConsentItem] = []


class BodyProfileBody(CamelModel):
    """PUT /profile/body — 전체 교체. 범위·enum 검증은 핸들러에서 400 invalid_body_profile."""

    height_cm: float
    weight_kg: float
    body_type: str
    body_type_custom: str | None = None
    gender: str | None = None
    age_range: str | None = None
    skin_tone: str | None = None
    hair: str | None = None
    clothing_size: str | None = None


class GenerationBody(CamelModel):
    product_image_asset_ids: list[str] = []
    project_id: str | None = None
    options: dict = {}


_P_RESPONSES = {
    401: {"model": ErrorResponse, "description": "인증 실패"},
    403: {"model": ErrorResponse, "description": "전제조건 미충족(동의·미성년)"},
    404: {"model": ErrorResponse, "description": "리소스 없음/타인 소유 은닉/파기됨"},
    409: {"model": ErrorResponse, "description": "상태 충돌(파기 진행 중 등)"},
}


# ── 공통 헬퍼 ────────────────────────────────────────────────
def _err(code: str, message: str, status: int = 400, **extra) -> HTTPException:
    """main.py HTTPException 핸들러가 {error:{code,message,...}} 봉투로 재포장."""
    detail = {"code": code, "message": message}
    detail.update(extra)
    return HTTPException(status_code=status, detail=detail)


def _json(payload: dict, status: int = 200) -> JSONResponse:
    """datetime/Decimal 안전 직렬화(jsonable_encoder) + 상태코드 지정."""
    return JSONResponse(status_code=status, content=jsonable_encoder(payload))


def _wake_dispatcher(request: Request) -> None:
    """잡 생성 직후 디스패처 즉시 기상(routes._wake_dispatcher 미러 — 유휴 폴링 대기 스킵)."""
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()


def _r2_face(request: Request):
    """얼굴 전용 비공개 R2(app.state.r2_face). 미설정이면 503(공개 버킷 폴백 금지 — §1.4)."""
    r2 = getattr(request.app.state, "r2_face", None)
    if r2 is None:
        raise _err("storage_unavailable", "얼굴 저장소가 설정되지 않았습니다.", status=503)
    return r2


def _face_key(profile_id: str, angle: str, ext: str) -> str:
    """개인화 얼굴 슬롯의 비공개 R2 키. 서버에서만 유도(클라 신뢰 금지). 게이트 라우트만 스트림."""
    return f"personalization/profiles/{profile_id}/faces/{angle}.{ext}"


async def _load_fm_identity(conn, user_id: str) -> tuple[bool, dict | None]:
    """FaceMarket 본인확인 조회 → `(인증 기록 존재 여부, 연령 파생 결과|None)`.

    사용자는 FaceMarket(`/model/register`)과 개인화에서 **같은 CX 표준인증창을 두 번** 겪지
    않아야 한다(통합 지시). FaceMarket 이 이미 실명 확인을 마쳤다면 그 결과에서 연령만 파생한다.
    두 값을 함께 돌려주는 이유 — "인증 없음"과 "인증은 있으나 연령 파생 불가"는 사용자에게
    전혀 다른 상태이고(전자는 조치 가능, 후자는 종결), 이를 구분하려고 카탈로그·조인을 두 번
    돌던 중복을 없앤다.

    · `fm_identity_verifications.fields.birthYear` = 연도만 보관 → `is_adult_from_birth` 의
      **보수 판정**(연도차 >= min_age+1)이 적용된다. 경계 연도는 미성년 취급 = 안전측.
    · **ci_hash 는 읽지 않는다.** 신원-계정 바인딩(1신원 N계정 탐지·미성년 판정 고착)은 파기
      의무와 상충해 법무 확인 보류 중인 결정이다(api-spec §3.0 '알려진 한계'). 여기서 ci_hash 를
      쓰기 시작하면 그 결정을 뒷문으로 확정하게 되므로 birthYear 만 본다.
    · fm_ 테이블이 없는 배포(개인화 단독)에서도 죽지 않아야 한다. 존재하지 않는 테이블을 그냥
      조회하면 psycopg 가 트랜잭션 전체를 abort 시켜 이후 쿼리까지 깨지므로, `to_regclass` 로
      **선확인**한다(에러를 내지 않는 카탈로그 조회).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select to_regclass('public.fm_identity_verifications') is not null "
            "and to_regclass('public.fm_models') is not null as present"
        )
        row = await cur.fetchone()
        if not row or not row["present"]:
            return (False, None)
        await cur.execute(
            "select v.fields->>'birthYear' as birth_year, v.verified_at "
            "from fm_identity_verifications v "
            "join fm_models m on m.id = v.model_id "
            "where m.user_id = %s order by v.verified_at desc limit 1",
            (user_id,),
        )
        fm = await cur.fetchone()
    if fm is None:
        return (False, None)
    if not fm["birth_year"]:
        return (True, None)  # 인증은 있으나 birthYear 부재 → 연령 파생 불가
    try:
        is_adult = cx_identity.is_adult_from_birth(fm["birth_year"])
    except cx_identity.CxIdentityError:
        return (True, None)  # 파싱 불가도 동일 — 인증은 있으나 연령 미상
    return (True, {"is_adult": is_adult, "verified_at": fm["verified_at"]})


# 연령 게이트 상태 — 게이트(403)와 /status(blocker)가 **같은 판정**을 쓰게 하는 단일 소스.
# 둘이 갈리면 같은 사용자 상태에 다른 코드가 나가고, 종결 상태에 "재인증하세요"가 나가면
# 무한 왕복이 재현된다.
_AGE_OK, _AGE_MINOR, _AGE_NONE, _AGE_UNAVAILABLE = "ok", "minor", "none", "age_unavailable"


async def _age_state(conn, user_id: str) -> tuple[str, dict | None]:
    """연령 게이트 단일 판정 → `(state, row|None)`.

    state:
      · `ok` — 성인 확인됨
      · `minor` — 미성년 확정(종결)
      · `none` — 본인확인 기록 자체가 없음(조치 가능: `/model/register`)
      · `age_unavailable` — 인증은 했으나 연령 파생 불가(종결 — 재인증해도 같은 결과)

    소스 우선순위: ① 개인화 자체 인증 행 → ② 없으면 FaceMarket 인증에서 birthYear 파생.
    개인화 저장은 **is_adult 불리언뿐** — 생년월일·CI·이름 미보관(최소수집, cx_identity.py).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select is_adult, verified_at from personalization_identity_verifications "
            "where user_id = %s order by verified_at desc limit 1",
            (user_id,),
        )
        own = await cur.fetchone()
    if own is not None:
        return (_AGE_OK if own["is_adult"] else _AGE_MINOR, own)
    fm_present, fm_row = await _load_fm_identity(conn, user_id)
    if fm_row is not None:
        return (_AGE_OK if fm_row["is_adult"] else _AGE_MINOR, fm_row)
    return (_AGE_UNAVAILABLE if fm_present else _AGE_NONE, None)


_AGE_BLOCKER_CODE = {
    _AGE_MINOR: "minor_blocked",
    _AGE_NONE: "identity_verification_required",
    _AGE_UNAVAILABLE: "identity_age_unavailable",
}


def _age_blocker(state: str) -> dict | None:
    """`_age_state` 결과 → blocker 1건(없으면 None). `_readiness`·`get_status` 공용.

    **fail-closed** — `ok` 가 아닌 모든 state 는 blocker 를 낸다. 미지 state 를 통과로 처리하면
    (`.get()` → None → blocker 없음) 연령 게이트가 조용히 열린다.
    """
    if state == _AGE_OK:
        return None
    return {"code": _AGE_BLOCKER_CODE.get(state, "identity_verification_required"), "detail": {}}


_AGE_ERROR_MESSAGE = {
    _AGE_MINOR: "만 19세 미만은 이용할 수 없어요.",
    _AGE_NONE: "본인확인이 필요해요. 성인 인증을 먼저 완료해 주세요.",
    _AGE_UNAVAILABLE: (
        "본인확인은 완료됐지만 성인 여부를 확인할 수 있는 정보를 받지 못했어요. "
        "고객센터로 문의해 주세요."
    ),
}


def _assert_age_eligible(state: str) -> None:
    """연령 게이트(§1.4·§3.1) — 동의 제출·얼굴 업로드·생성·보정 공용 **단일 훅**.

    `state` 는 `_age_state` 결과 — 호출자가 자신의 conn 컨텍스트에서 판정해 넘긴다(게이트 순서를
    호출부가 스펙대로 제어할 수 있게 판정/차단을 분리). 에러코드는 `/status` 의 blocker 코드와
    **같은 소스**(`_AGE_BLOCKER_CODE`)에서 나온다 — 갈리면 종결 상태(`age_unavailable`)에
    "재인증하세요"(`identity_verification_required`)가 나가 무한 왕복이 재현된다.
    """
    if state == _AGE_OK:
        return
    # fail-closed — 미지 state 는 통과시키지 않는다(`.get()` 이 None 을 내면 게이트가 조용히 열림).
    raise _err(
        _AGE_BLOCKER_CODE.get(state, "identity_verification_required"),
        _AGE_ERROR_MESSAGE.get(state, _AGE_ERROR_MESSAGE[_AGE_NONE]),
        status=403,
    )


async def _load_profile(conn, user_id: str, *, for_update: bool = False):
    """활성 프로필(status<>'purged') 1행. 없으면 None. 화이트리스트 컬럼만(§1.1 user 1:1)."""
    lock = " for update" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {_PROFILE_COLS} from personalization_profiles "
            f"where user_id = %s and status <> 'purged'{lock}",
            (user_id,),
        )
        return await cur.fetchone()


async def _ensure_profile(conn, user_id: str):
    """활성 프로필 로드(FOR UPDATE), 없으면 draft 생성. (row, created) 반환.

    동시 첫-제출 레이스는 personalization_profiles_active_user_idx(user_id where status<>'purged')
    가 막고, UniqueViolation 시 savepoint 롤백 후 재조회로 합류한다."""
    row = await _load_profile(conn, user_id, for_update=True)
    if row is not None:
        return row, False
    async with conn.cursor() as cur:
        await cur.execute("savepoint ensure_profile")
        try:
            await cur.execute(
                f"insert into personalization_profiles (user_id) values (%s) returning {_PROFILE_COLS}",
                (user_id,),
            )
            row = await cur.fetchone()
            await cur.execute("release savepoint ensure_profile")
            return row, True
        except UniqueViolation:
            await cur.execute("rollback to savepoint ensure_profile")
    row = await _load_profile(conn, user_id, for_update=True)
    return row, False


async def _audit(conn, user_id: str, profile_id: str | None, event_type: str, detail: dict) -> None:
    """감사로그 append — detail 은 비-PII(사유코드·카운트·타입 enum 만, §1.4·§5)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into personalization_audit_log (user_id, profile_id, event_type, detail) "
            "values (%s, %s, %s, %s)",
            (user_id, profile_id, event_type, Json(detail)),
        )


async def _consent_status_map(conn, user_id: str) -> dict:
    """유형별 현재 동의 상태(append-only 이력의 유형별 최신 행). status: none|granted|withdrawn."""
    m = {
        t: {"status": "none", "docVersion": None, "grantedAt": None, "withdrawnAt": None}
        for t in ALL_CONSENT_TYPES
    }
    async with conn.cursor() as cur:
        await cur.execute(
            "select consent_type, action, doc_version, created_at "
            "from personalization_consents where user_id = %s order by created_at",
            (user_id,),
        )
        rows = await cur.fetchall()
    for r in rows:  # 오름차순 → 마지막 처리 행이 최신 상태
        e = m.get(r["consent_type"])
        if e is None:
            continue
        e["status"] = r["action"]
        if r["doc_version"]:
            e["docVersion"] = r["doc_version"]
        if r["action"] == "granted":
            e["grantedAt"] = r["created_at"]
        else:
            e["withdrawnAt"] = r["created_at"]
    return m


async def _slot_angles(conn, profile_id: str) -> dict:
    """프로필의 얼굴 슬롯 {angle: uploaded_at}. r2_key/digest 는 로드하지 않는다(불필요·PII)."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select angle, uploaded_at from personalization_face_photos where profile_id = %s",
            (profile_id,),
        )
        return {r["angle"]: r["uploaded_at"] for r in await cur.fetchall()}


async def _readiness(conn, user_id: str, profile: dict) -> tuple[bool, list[dict]]:
    """READY 파생조건(§2) → (canGenerate, blockers). blocker code enum(§3.4)."""
    slots = await _slot_angles(conn, profile["id"])
    missing_angles = [a for a in ANGLES if a not in slots]
    cmap = await _consent_status_map(conn, user_id)
    missing_consents = [t for t in REQUIRED_CONSENTS if cmap[t]["status"] != "granted"]
    body_missing = profile["height_cm"] is None or profile["weight_kg"] is None
    age_state, _ = await _age_state(conn, user_id)

    blockers: list[dict] = []
    # 연령 게이트를 blockers 최상단에 — 온보딩 첫 단계(본인확인)가 미완이면 그것부터 안내.
    age_blocker = _age_blocker(age_state)
    if age_blocker:
        blockers.append(age_blocker)
    if missing_angles:
        blockers.append({"code": "photos_incomplete", "detail": {"missingAngles": missing_angles}})
    if missing_consents:
        blockers.append({"code": "consent_missing", "detail": {"types": missing_consents}})
    if body_missing:
        blockers.append({"code": "body_profile_missing", "detail": {}})
    return (not blockers, blockers)


async def _active_purge_job_id(conn, user_id: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id from jobs where user_id = %s and kind = 'personalization_purge' "
            "and status in ('pending', 'running') order by created_at desc limit 1",
            (user_id,),
        )
        row = await cur.fetchone()
    return row["id"] if row else None


async def _start_purge(conn, user_id: str, profile: dict) -> str:
    """프로필 status→purging + personalization_purge 잡 생성. 멱등(이미 purging 이면 기존 잡 id).

    호출자는 profile 을 FOR UPDATE 로 잠근 뒤 호출(동시 요청 직렬화)하고, 반환 후 commit + wake.
    payload 는 {profileId} 만 — PII 금지(워커가 profileId 로 서버측 로드).
    """
    if profile["status"] == "purging":
        existing = await _active_purge_job_id(conn, user_id)
        if existing:
            return existing
    # MAJOR-D: 전체 파기 시 현재 granted 인 동의를 전부 withdrawn 으로 기록(append-only).
    # 이렇게 하지 않으면 파기 후에도 동의가 user_id 스코프로 granted 잔존 → 재온보딩 시
    # 과거 동의로 얼굴 업로드 게이트가 통과되어 재동의 없이 생체정보가 재수집된다.
    consents = await _consent_status_map(conn, user_id)
    granted_types = [t for t, e in consents.items() if e["status"] == "granted"]
    async with conn.cursor() as cur:
        await cur.execute(
            "update personalization_profiles set status = 'purging', withdrawn_at = now() "
            "where id = %s and status <> 'purged'",
            (profile["id"],),
        )
        for ctype in granted_types:
            await cur.execute(
                "insert into personalization_consents "
                "(user_id, profile_id, consent_type, action, doc_version) "
                "values (%s, %s, %s, 'withdrawn', %s)",
                (user_id, profile["id"], ctype, consents[ctype]["docVersion"]),
            )
    job, _created = await repo.create_job(
        conn,
        user_id=user_id,
        project_id=None,
        kind="personalization_purge",
        payload={"profileId": profile["id"]},
        idempotency_key=None,
        credits_reserved=0,
        metadata={},
    )
    await _audit(conn, user_id, profile["id"], "purge_started", {})
    return job["id"]


# ── 뷰 빌더(화이트리스트 shape) ──────────────────────────────
def _consents_view(cmap: dict) -> list[dict]:
    return [
        {
            "type": t,
            "required": t in REQUIRED_CONSENTS,
            "status": cmap[t]["status"],
            "docVersion": cmap[t]["docVersion"],
            "grantedAt": cmap[t]["grantedAt"],
            "withdrawnAt": cmap[t]["withdrawnAt"],
        }
        for t in ALL_CONSENT_TYPES
    ]


def _photos_view(slots: dict) -> tuple[list[dict], bool]:
    photos = [
        {
            "angle": a,
            "qcStatus": "passed" if a in slots else "none",
            "qcReasons": [],
            "imageUri": f"/v1/personalization/face-photos/{a}/file" if a in slots else None,
            "uploadedAt": slots.get(a),
        }
        for a in ANGLES
    ]
    return photos, all(a in slots for a in ANGLES)


def _body_view(profile: dict) -> dict | None:
    if profile["height_cm"] is None and profile["weight_kg"] is None and profile["body_type"] is None:
        return None
    return {
        "heightCm": float(profile["height_cm"]) if profile["height_cm"] is not None else None,
        "weightKg": float(profile["weight_kg"]) if profile["weight_kg"] is not None else None,
        "bodyType": profile["body_type"],
        "bodyTypeCustom": profile["body_type_custom"],
        "gender": profile["gender"],
        "ageRange": profile["age_range"],
        "skinTone": profile["skin_tone"],
        "hair": profile["hair"],
        "clothingSize": profile["clothing_size"],
    }


# ============================================================================
# 연령 게이트(T2-1) — 본인확인(CX 표준인증창) 기반 성인 인증
# ============================================================================
@router.post(
    "/identity:verify",
    responses={**_P_RESPONSES, 400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="본인확인(성인 인증) — CX 표준인증창 token 검증",
)
async def identity_verify(
    request: Request,
    body: IdentityVerifyBody = Body(...),
    user_id: str = Depends(require_user),
):
    """CX 표준인증창 성공 token 으로 서버가 본인확인을 완료하고 **성인 여부만** 기록한다.

    개인화는 '성인 본인 동의' 전제(정책 게이트)라 미성년은 차단한다 — 이 라우트가 유일한 연령
    소스이며, 동의·업로드·생성 3게이트가 여기 기록을 참조한다.

    - **입력**: `{ token }` — 위젯 콜백 token 만. 원문 신원(CI·생년월일)은 서버가 CX 에서 받는다.
    - **저장**: `is_adult` 불리언 + cx_tx_id 만. **생년월일·CI·이름 미보관**(최소수집, cx_identity.py).
    - **리플레이 차단**: cx_tx_id UNIQUE — 같은 인증 토큰 재사용은 409(FaceMarket 선례).
    - **에지**: 400 `token_required`/`cx_verify_failed`/`birth_unavailable` · 409 `identity_replay`.
    """
    settings = request.app.state.settings
    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")

    trans = birth = None
    try:
        trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
        birth = cx_identity.dig(trans, "birth", "birthdate")
        if not birth:
            raise cx_identity.CxIdentityError("birth_missing")
        # 원문 birth 는 여기서만 존재 → 불리언으로 환원하고 아래 finally 에서 언바인드.
        is_adult = cx_identity.is_adult_from_birth(birth)
    except cx_identity.CxIdentityError as e:
        # 사유코드만 관측 — 원문·birth 미포함(§1.4).
        logger.warning("personalization_identity_verify_failed", extra={"reason": str(e)[:40]})
        raise _err("cx_verify_failed", "본인확인에 실패했어요. 다시 시도해 주세요.")
    finally:
        # 예외 전파 중에도 프레임 로컬을 반드시 언바인드한다. except 절 뒤에 두면 raise 로 이탈해
        # 실행되지 않고, HTTPException 의 __context__→__traceback__ 이 이 프레임을 잡아 CI·이름·
        # 생년월일이 든 trans 전체가 도달 가능해진다(프레임 로컬 캡처형 에러 트래커 = 외부 유출).
        del trans, birth

    # 원본 CX token 은 CX 에서 CI·생년월일을 재조회할 수 있는 **라이브 capability** 라 저장하지
    # 않는다(저장 시 "CI·생년월일 미저장" 최소수집 불변식이 무효화됨). sha256 해시만 보관 —
    # 리플레이 차단(UNIQUE) 의미론은 그대로이고 원문 재취득 경로는 사라진다.
    cx_tx_hash = hashlib.sha256(token.encode()).hexdigest()

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "insert into personalization_identity_verifications "
                    "(user_id, cx_tx_hash, is_adult) values (%s, %s, %s)",
                    (user_id, cx_tx_hash, is_adult),
                )
            except UniqueViolation:
                raise _err("identity_replay", "이미 처리된 인증입니다.", status=409)
        await conn.commit()

    # 미성년도 인증 자체는 기록(재시도 루프 방지) 후 차단 — 게이트와 동일 사유코드로 응답.
    if not is_adult:
        raise _err("minor_blocked", "만 19세 미만은 이용할 수 없어요.", status=403)
    return {"verified": True, "isAdult": True}


# ============================================================================
# §3.1 동의(Consent)
# ============================================================================
@router.get("/consents", responses={**_P_RESPONSES}, summary="동의 상태 조회")
async def get_consents(request: Request, user_id: str = Depends(require_user)):
    """유형별 현재 동의 상태 + 보관기간·고지 URI. 프로필 유무와 무관(이력 기준)."""
    async with get_conn(request) as conn:
        cmap = await _consent_status_map(conn, user_id)
    return {
        "consents": _consents_view(cmap),
        "retentionDays": RETENTION_DAYS,
        "noticeUris": NOTICE_URIS,
    }


@router.post("/consents", responses={**_P_RESPONSES, 400: {"model": ErrorResponse}}, summary="동의 제출(항목별)")
async def submit_consents(
    request: Request, body: ConsentSubmitBody, user_id: str = Depends(require_user)
):
    """항목별 granted 기록(프로필 없으면 생성→draft). 이미 granted 는 멱등 no-op.

    - 400 `invalid_consent_type` / 400 `stale_consent_doc` · 403 `minor_blocked` · 409 `purge_in_progress`.
    """
    if not body.items:
        raise _err("invalid_consent_type", "제출할 동의 항목이 없습니다.")
    for item in body.items:  # 도메인 검증(동의 기록 전에 전부 통과해야 함)
        if item.type not in ALL_CONSENT_TYPES:
            raise _err("invalid_consent_type", "알 수 없는 동의 유형입니다.")
        if (item.doc_version or None) != CONSENT_DOC_VERSION:
            raise _err("stale_consent_doc", "동의 문서 버전이 최신이 아니에요. 새로고침 후 다시 시도해 주세요.")

    async with get_conn(request) as conn:
        # 연령 게이트 — 프로필 생성·동의 기록 **이전**에 검사(미성년이면 어떤 동의도 기록 안 함).
        # raise 시 이 tx 는 커밋되지 않으므로 _ensure_profile 의 행 생성도 영속되지 않는다.
        _assert_age_eligible((await _age_state(conn, user_id))[0])
        profile, _created = await _ensure_profile(conn, user_id)
        if profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 변경할 수 없어요.", status=409)
        cmap = await _consent_status_map(conn, user_id)
        for item in body.items:
            if cmap[item.type]["status"] == "granted":
                continue  # 멱등 no-op
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into personalization_consents "
                    "(user_id, profile_id, consent_type, action, doc_version) "
                    "values (%s, %s, %s, 'granted', %s)",
                    (user_id, profile["id"], item.type, CONSENT_DOC_VERSION),
                )
            await _audit(conn, user_id, profile["id"], "consent_granted",
                         {"type": item.type, "docVersion": CONSENT_DOC_VERSION})
        await _sync_status_row(conn, user_id, profile)
        cmap = await _consent_status_map(conn, user_id)
        await conn.commit()
    return {"consents": _consents_view(cmap), "retentionDays": RETENTION_DAYS, "noticeUris": NOTICE_URIS}


@router.post(
    "/consents/{consent_type}:withdraw",
    responses={**_P_RESPONSES, 400: {"model": ErrorResponse}},
    summary="동의 철회",
)
async def withdraw_consent(
    request: Request, consent_type: str, user_id: str = Depends(require_user)
):
    """유형별 철회. training=학습만 중단(200) / service·cross_border=전체 캐스케이드 파기(202+purgeJobId).

    멱등: 이미 withdrawn 이면 현재 상태 그대로 200. 파기 미동반=200, 파기 잡 동반=202.
    """
    if consent_type not in ALL_CONSENT_TYPES:
        raise _err("invalid_consent_type", "알 수 없는 동의 유형입니다.")

    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id, for_update=True)
        if profile is None:
            raise _err("not_found", "개인화 프로필을 찾을 수 없습니다.", status=404)
        if profile["status"] == "purging" and consent_type == "training_use":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 변경할 수 없어요.", status=409)

        cmap = await _consent_status_map(conn, user_id)
        already_withdrawn = cmap[consent_type]["status"] == "withdrawn"

        purge_job_id: str | None = None
        if consent_type in REQUIRED_CONSENTS:
            # service_use·cross_border_transfer 철회 → 전체 캐스케이드 파기(§3.5 동일 경로).
            if not already_withdrawn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "insert into personalization_consents "
                        "(user_id, profile_id, consent_type, action) values (%s, %s, %s, 'withdrawn')",
                        (user_id, profile["id"], consent_type),
                    )
                await _audit(conn, user_id, profile["id"], "consent_withdrawn", {"type": consent_type})
            # 파기는 멱등(_start_purge 가 이미 purging 이면 기존 잡 반환).
            purge_job_id = await _start_purge(conn, user_id, profile)
            await conn.commit()
            _wake_dispatcher(request)
            withdrawn_at = await _latest_consent_ts(conn, user_id, consent_type, "withdrawn")
            return _json(
                {"type": consent_type, "status": "withdrawn",
                 "withdrawnAt": withdrawn_at, "purgeJobId": purge_job_id},
                status=202,
            )

        # training_use 철회 → 학습 활용만 중단(서비스 유지). 학습 사본은 계약상 아직 미생성
        # (§6 "확정 전 학습 사본 생성 금지")이라 파기 대상 0 → 파기 잡 없음. 이후 학습 파이프라인은
        # 최신 동의(withdrawn)를 읽어 사용하지 않는다.
        if not already_withdrawn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into personalization_consents "
                    "(user_id, profile_id, consent_type, action) values (%s, %s, %s, 'withdrawn')",
                    (user_id, profile["id"], consent_type),
                )
            await _audit(conn, user_id, profile["id"], "consent_withdrawn", {"type": consent_type})
        await conn.commit()
        withdrawn_at = await _latest_consent_ts(conn, user_id, consent_type, "withdrawn")
    return _json(
        {"type": consent_type, "status": "withdrawn", "withdrawnAt": withdrawn_at, "purgeJobId": None},
        status=200,
    )


async def _latest_consent_ts(conn, user_id: str, consent_type: str, action: str):
    async with conn.cursor() as cur:
        await cur.execute(
            "select created_at from personalization_consents "
            "where user_id = %s and consent_type = %s and action = %s "
            "order by created_at desc limit 1",
            (user_id, consent_type, action),
        )
        row = await cur.fetchone()
    return row["created_at"] if row else None


# ============================================================================
# §3.2 얼굴 사진(3장, 각도 슬롯)
# ============================================================================
@router.post(
    "/face-photos",
    status_code=201,
    responses={**_P_RESPONSES, 400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    summary="얼굴 슬롯 업로드(+동기 QC)",
)
async def upload_face_photo(
    request: Request,
    photo: UploadFile = File(..., description="얼굴 이미지(png/jpg/webp, ≤15MB)"),
    angle: str = Form(..., description="각도 슬롯: front|side|angle45"),
    user_id: str = Depends(require_user),
):
    """multipart 직접 수신 → 동기 QC(Gemini 비전) → **통과 시에만** 비공개 R2 put + 슬롯 upsert.

    전제조건: service_use + cross_border_transfer granted(미충족 403 consent_required),
    미성년 재검사(403 minor_blocked). 불합격 시 원본 즉시 파기(저장 0) + 400 face_quality.
    """
    if angle not in ANGLES:
        raise _err("invalid_angle", "각도는 front/side/angle45 중 하나여야 해요.")
    mime = (photo.content_type or "").lower()
    if mime not in _ALLOWED_FACE_MIME or not ext_for_mime(mime):
        raise _err("unsupported_type", "허용되지 않는 이미지 형식입니다. (png/jpg/webp)")

    # 1) 전제조건 게이트(얼굴 바이트를 외부 API로 보내기 전에 필수 동의를 코드로 확인 — §1.4).
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is not None and profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 업로드할 수 없어요.", status=409)
        cmap = await _consent_status_map(conn, user_id)
        age_state, _ = await _age_state(conn, user_id)
    if profile is None or any(cmap[t]["status"] != "granted" for t in REQUIRED_CONSENTS):
        raise _err("consent_required", "얼굴 업로드 전에 필수 동의(서비스 이용·국외이전)가 필요해요.", status=403)
    _assert_age_eligible(age_state)  # 동의 이후 인증이 소실·갱신돼도 업로드에서 재차단(§3.2 전제조건 ②)

    # 2) 바이트 수신 + 크기 검증(연결 점유 없이). 얼굴 바이트는 메모리에만 — 파일명 로그 금지.
    data = await photo.read()
    if not data:
        raise _err("unsupported_type", "빈 파일입니다.")
    if len(data) > MAX_FACE_BYTES:
        raise _err("file_too_large", "이미지는 15MB 이하만 가능합니다.", status=413)

    # 3) 동기 QC(외부 비전 API — cross_border 동의가 전제라 국외전송 정합). 실패=503 fail-safe.
    try:
        qc = await evaluate_face_qc(request.app.state.settings, image_bytes=data, mime=mime, angle=angle)
    except FaceQcUnavailable:
        raise _err("qc_unavailable", "얼굴 검사를 지금 수행할 수 없어요. 잠시 후 다시 시도해 주세요.", status=503)
    if not qc.passed:
        # 불합격 → 원본 즉시 파기(data 는 스코프 종료로 GC, 저장·로그 0). 사유코드만 반환/감사.
        async with get_conn(request) as conn:
            await _audit(conn, user_id, profile["id"], "qc_rejected",
                         {"angle": angle, "reasons": qc.reasons})
            await conn.commit()
        raise _err("face_quality", qc_reason_message(qc.reasons), reasons=qc.reasons)

    # 4) 통과 → 비공개 R2 put + 슬롯 upsert(구 객체 즉시 delete). digest 는 DB 내부용(응답 미노출).
    r2 = _r2_face(request)
    ext = ext_for_mime(mime)
    new_key = _face_key(profile["id"], angle, ext)
    digest = sha256_sri(data)
    await asyncio.to_thread(r2.put_bytes, new_key, data, mime)

    old_key: str | None = None
    try:
        async with get_conn(request) as conn:
            locked = await _load_profile(conn, user_id, for_update=True)
            if locked is None or locked["status"] == "purging":
                # QC 도중 철회가 시작됐을 수 있음 — 방금 올린 객체 회수 후 409.
                await asyncio.to_thread(r2.delete, new_key)
                raise _err("purge_in_progress", "파기가 진행 중이라 지금은 업로드할 수 없어요.", status=409)
            async with conn.cursor() as cur:
                await cur.execute(
                    "select r2_key from personalization_face_photos where profile_id = %s and angle = %s",
                    (locked["id"], angle),
                )
                ex = await cur.fetchone()
                old_key = ex["r2_key"] if ex else None
                await cur.execute(
                    "insert into personalization_face_photos "
                    "(profile_id, angle, r2_key, image_digest, mime_type, byte_size) "
                    "values (%s, %s, %s, %s, %s, %s) "
                    "on conflict (profile_id, angle) do update set "
                    "r2_key = excluded.r2_key, image_digest = excluded.image_digest, "
                    "mime_type = excluded.mime_type, byte_size = excluded.byte_size, uploaded_at = now() "
                    "returning uploaded_at",
                    (locked["id"], angle, new_key, digest, mime, len(data)),
                )
                uploaded_at = (await cur.fetchone())["uploaded_at"]
            await _audit(conn, user_id, locked["id"], "photo_uploaded", {"angle": angle})
            await _sync_status_row(conn, user_id, locked)
            await conn.commit()
    except HTTPException:
        raise
    except Exception:
        await asyncio.to_thread(r2.delete, new_key)  # DB 실패 → 고아 방지
        raise

    if old_key and old_key != new_key:  # 교체 시 구 객체 즉시 정리(고아 얼굴 금지 — §3.2)
        try:
            await asyncio.to_thread(r2.delete, old_key)
        except Exception:
            logger.warning("personalization_old_face_cleanup_failed", extra={"angle": angle})

    return _json(
        {"angle": angle, "qcStatus": "passed", "qcReasons": [],
         "imageUri": f"/v1/personalization/face-photos/{angle}/file",
         "byteSize": len(data), "uploadedAt": uploaded_at},
        status=201,
    )


@router.get("/face-photos", responses={**_P_RESPONSES}, summary="얼굴 슬롯 상태 목록")
async def list_face_photos(request: Request, user_id: str = Depends(require_user)):
    """3개 각도 슬롯 상태. 프로필 없음/purged 는 404(리소스 조회 은닉)."""
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is None:
            raise _err("not_found", "개인화 프로필을 찾을 수 없습니다.", status=404)
        slots = await _slot_angles(conn, profile["id"])
    photos, complete = _photos_view(slots)
    return {"photos": photos, "complete": complete}


@router.get(
    "/face-photos/{angle}/file",
    responses={401: {"description": "인증 실패"}, 404: {"description": "없음/타인/파기·파기중"}},
    summary="얼굴 바이트 게이트 스트림",
)
async def get_face_photo_file(
    request: Request, angle: str, user_id: str = Depends(require_user)
):
    """본인만·프로필 draft|ready 일 때만 바이트 스트림. 그 외 전부 404(존재 은닉).

    공개 URL 절대 미생성 — 인증 게이트로만 스트림, `Cache-Control: no-store, private`.
    프론트는 `<img src>` 대신 fetch+objectURL(FaceMarket get_license_face 계약).
    """
    if angle not in ANGLES:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    r2 = _r2_face(request)
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is None or profile["status"] not in ("draft", "ready"):
            raise _err("not_found", "찾을 수 없습니다.", status=404)
        async with conn.cursor() as cur:
            await cur.execute(
                "select r2_key, mime_type from personalization_face_photos "
                "where profile_id = %s and angle = %s",
                (profile["id"], angle),
            )
            row = await cur.fetchone()
    if not row or not row["r2_key"]:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    try:
        data = await asyncio.to_thread(r2.get_bytes, row["r2_key"])
    except Exception:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    return Response(
        content=data, media_type=row["mime_type"], headers={"Cache-Control": "no-store, private"}
    )


_RESULT_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
}


@router.get(
    "/generations/{generation_id}/results/{index}/file",
    responses={401: {"description": "인증 실패"}, 404: {"description": "없음/타인/파기·파기중"}},
    summary="개인화 생성 결과 바이트 게이트 스트림",
)
async def get_generation_result_file(
    request: Request, generation_id: str, index: int, user_id: str = Depends(require_user)
):
    """생성 산출물(얼굴 PII)을 본인만·프로필 draft|ready 일 때만 스트림. 그 외 404(존재 은닉).

    공유 assets 테이블·무인증 `/v1/assets/{id}/file` 경로를 쓰지 않는다(§4 하드룰, CRITICAL-B).
    산출물 R2 키는 personalization_generations.result_keys 에만 있고 어떤 응답에도 미노출 —
    여기서 인증 게이트로만 r2_face 에서 스트림, `Cache-Control: no-store, private`.
    """
    if index < 0:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    try:  # uuid 컬럼 직접 비교 전 형식 가드 — 쓰레기 입력은 500 아닌 404(refine_generation 선례)
        uuid.UUID(str(generation_id))
    except (ValueError, TypeError):
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    r2 = _r2_face(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select g.result_keys from personalization_generations g "
                "join personalization_profiles p on p.id = g.profile_id "
                "where g.id = %s and p.user_id = %s and p.status in ('draft', 'ready')",
                (generation_id, user_id),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    keys = row["result_keys"] or []
    if index >= len(keys):
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    key = keys[index]
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return Response(
        content=data,
        media_type=_RESULT_MIME.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "no-store, private"},
    )


@router.delete(
    "/face-photos/{angle}",
    status_code=204,
    responses={**_P_RESPONSES},
    summary="얼굴 슬롯 삭제",
)
async def delete_face_photo(
    request: Request, angle: str, user_id: str = Depends(require_user)
):
    """R2 delete + 행 hard delete(digest 잔존 금지). ready→draft 강등. 멱등(빈 슬롯도 204)."""
    if angle not in ANGLES:
        raise _err("not_found", "찾을 수 없습니다.", status=404)
    r2 = _r2_face(request)
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id, for_update=True)
        if profile is None:
            raise _err("not_found", "개인화 프로필을 찾을 수 없습니다.", status=404)
        if profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 삭제할 수 없어요.", status=409)
        async with conn.cursor() as cur:
            await cur.execute(
                "delete from personalization_face_photos where profile_id = %s and angle = %s "
                "returning r2_key",
                (profile["id"], angle),
            )
            deleted = await cur.fetchone()
        if deleted:
            await _audit(conn, user_id, profile["id"], "photo_deleted", {"angle": angle})
            await _sync_status_row(conn, user_id, profile)
        await conn.commit()
    if deleted and deleted["r2_key"]:  # 커밋 후 객체 정리(best-effort)
        try:
            await asyncio.to_thread(r2.delete, deleted["r2_key"])
        except Exception:
            logger.warning("personalization_face_delete_cleanup_failed", extra={"angle": angle})
    return Response(status_code=204)


# ============================================================================
# §3.3 신체 프로필
# ============================================================================
_BODY_TYPES = {"slim", "normal", "muscular", "chubby", "custom"}
_GENDERS = {"female", "male", "other"}
_AGE_RANGES = {"20s", "30s", "40s", "50s_plus"}


@router.put(
    "/profile/body",
    responses={**_P_RESPONSES, 400: {"model": ErrorResponse}},
    summary="신체 프로필 저장(전체 교체)",
)
async def put_body_profile(
    request: Request, body: BodyProfileBody, user_id: str = Depends(require_user)
):
    """height/weight 필수 범위검증(100~230/30~200) + body_type enum. 400 invalid_body_profile.

    민감정보 준함 — 키·몸무게는 로그 금지(마스킹), 응답은 본인 조회만.
    """
    if not (100.0 <= body.height_cm <= 230.0):
        raise _err("invalid_body_profile", "키는 100~230cm 범위로 입력해 주세요.")
    if not (30.0 <= body.weight_kg <= 200.0):
        raise _err("invalid_body_profile", "몸무게는 30~200kg 범위로 입력해 주세요.")
    if body.body_type not in _BODY_TYPES:
        raise _err("invalid_body_profile", "체형 유형이 올바르지 않습니다.")
    custom = (body.body_type_custom or "").strip() or None
    if body.body_type == "custom":
        if not custom:
            raise _err("invalid_body_profile", "직접 입력 체형은 설명을 적어주세요.")
        if len(custom) > 30:
            raise _err("invalid_body_profile", "직접 입력 체형은 30자 이하로 적어주세요.")
    else:
        custom = None
    if body.gender is not None and body.gender not in _GENDERS:
        raise _err("invalid_body_profile", "성별 값이 올바르지 않습니다.")
    if body.age_range is not None and body.age_range not in _AGE_RANGES:
        raise _err("invalid_body_profile", "연령대 값이 올바르지 않습니다.")
    hair = (body.hair or None)
    if hair is not None and len(hair) > 40:
        raise _err("invalid_body_profile", "헤어 설명은 40자 이하로 적어주세요.")
    clothing = (body.clothing_size or None)
    if clothing is not None and len(clothing) > 10:
        raise _err("invalid_body_profile", "의류 사이즈는 10자 이하로 적어주세요.")
    skin = (body.skin_tone or None)

    async with get_conn(request) as conn:
        profile, _created = await _ensure_profile(conn, user_id)
        if profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 저장할 수 없어요.", status=409)
        async with conn.cursor() as cur:
            await cur.execute(
                f"""update personalization_profiles set
                    height_cm = %s, weight_kg = %s, body_type = %s, body_type_custom = %s,
                    gender = %s, age_range = %s, skin_tone = %s, hair = %s, clothing_size = %s
                    where id = %s returning {_PROFILE_COLS}""",
                (body.height_cm, body.weight_kg, body.body_type, custom, body.gender,
                 body.age_range, skin, hair, clothing, profile["id"]),
            )
            profile = await cur.fetchone()
        status = await _sync_status_row(conn, user_id, profile)
        await conn.commit()
    return {"body": _body_view(profile), "profileStatus": status}


@router.get("/profile", responses={**_P_RESPONSES}, summary="프로필 종합 조회")
async def get_profile(request: Request, user_id: str = Depends(require_user)):
    """상태 + 신체 + 슬롯 요약 + 동의 요약. 없음/purged 는 404(리소스 조회 은닉)."""
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is None:
            raise _err("not_found", "개인화 프로필을 찾을 수 없습니다.", status=404)
        slots = await _slot_angles(conn, profile["id"])
        cmap = await _consent_status_map(conn, user_id)
    photos, _complete = _photos_view(slots)
    return {
        "status": profile["status"],
        "body": _body_view(profile),
        "photos": photos,
        "consents": _consents_view(cmap),
        "createdAt": profile["created_at"],
        "updatedAt": profile["updated_at"],
    }


# ============================================================================
# §3.4 상태 조회(생성 가능 여부 체크리스트)
# ============================================================================
@router.get("/status", responses={401: {"model": ErrorResponse}}, summary="생성 가능 여부 상태")
async def get_status(request: Request, user_id: str = Depends(require_user)):
    """온보딩 게이트 단일 소스. 프로필 없음/purged 는 404 아님 — 200 {status:none}."""
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is None:
            # 프로필 없음/파기됨 → 재시작 가능. 전 블로커 노출(온보딩 진입 화면 소비).
            age_state, _ = await _age_state(conn, user_id)
            blockers: list[dict] = []
            age_blocker = _age_blocker(age_state)
            if age_blocker:
                blockers.append(age_blocker)
            blockers += [
                {"code": "photos_incomplete", "detail": {"missingAngles": list(ANGLES)}},
                {"code": "consent_missing", "detail": {"types": list(REQUIRED_CONSENTS)}},
                {"code": "body_profile_missing", "detail": {}},
            ]
            return {
                "status": "none",
                "canGenerate": False,
                "blockers": blockers,
                "purgeJobId": None,
            }
        if profile["status"] == "purging":
            return {
                "status": "purging",
                "canGenerate": False,
                "blockers": [{"code": "purge_in_progress", "detail": {}}],
                "purgeJobId": await _active_purge_job_id(conn, user_id),
            }
        can_generate, blockers = await _readiness(conn, user_id, profile)
    return {
        "status": profile["status"],
        "canGenerate": can_generate,
        "blockers": blockers,
        "purgeJobId": None,
    }


# ============================================================================
# §3.5 전체 철회(캐스케이드 파기)
# ============================================================================
@router.post(":withdraw", responses={**_P_RESPONSES}, summary="전체 철회(프로필 파기)")
async def withdraw_all(request: Request, user_id: str = Depends(require_user)):
    """status→purging + personalization_purge 잡 생성 + 디스패처 기상. 멱등. 202.

    계정 삭제 훅도 내부적으로 이 경로를 호출한다. 진행 상태는 GET /v1/jobs/{id}(+SSE) 재사용.
    """
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id, for_update=True)
        if profile is None:
            raise _err("not_found", "개인화 프로필을 찾을 수 없습니다.", status=404)
        purge_job_id = await _start_purge(conn, user_id, profile)
        await conn.commit()
    _wake_dispatcher(request)
    return _json({"purgeJobId": purge_job_id, "status": "purging"}, status=202)


# ============================================================================
# §4 엔진-의존: 개인화 생성(라우트는 잡 큐잉만 — 실제 생성은 워커)
# ============================================================================
async def _validate_product_assets(conn, user_id: str, asset_ids: list[str]) -> None:
    """상품 이미지 asset 이 본인 소유·존재하는지 검증. 아니면 400."""
    ids: list[str] = []
    for a in asset_ids:
        try:
            uuid.UUID(str(a))
        except (ValueError, TypeError):
            raise _err("invalid_product_images", "상품 이미지 식별자가 올바르지 않습니다.")
        if a not in ids:
            ids.append(a)
    async with conn.cursor() as cur:
        await cur.execute(
            "select count(*) as c from assets "
            "where id = any(%s::uuid[]) and user_id = %s and deleted_at is null",
            (ids, user_id),
        )
        found = (await cur.fetchone())["c"]
    if found != len(ids):
        raise _err("invalid_product_images", "상품 이미지를 찾을 수 없거나 접근할 수 없습니다.")


@router.post(
    "/generations",
    responses={
        **_P_RESPONSES,
        400: {"model": ErrorResponse},
        402: {"model": ErrorResponse, "description": "크레딧 부족"},
    },
    summary="개인화 생성 시작(verify-before-use)",
)
async def start_generation(
    request: Request,
    body: GenerationBody,
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """READY 아니면 409 profile_not_ready(+blockers). 크레딧 402. 잡 큐잉만(워커가 실행).

    Idempotency-Key 스코프 = `personalization:{user_id}:{key}`(전역 UNIQUE 충돌 차단). 202 {jobId}.
    """
    if not body.product_image_asset_ids:
        raise _err("invalid_product_images", "상품 이미지를 1장 이상 선택해 주세요.")

    settings = request.app.state.settings
    cost = int(getattr(settings, "credit_cost_personalization_generation", _DEFAULT_GENERATION_COST))
    scoped_key = f"personalization:{user_id}:{idempotency_key}" if idempotency_key else None
    generation_id = str(uuid.uuid4())
    options = dict(body.options or {})
    if body.project_id:  # projectId 는 산출물 귀속 힌트 — options 에 실어 워커 계약 키(4개) 유지.
        options["projectId"] = body.project_id
    payload = {
        "profileId": None,  # 아래 profile 확정 후 채움
        "productImageAssetIds": list(body.product_image_asset_ids),
        "options": options,
        "generationId": generation_id,
    }

    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is not None and profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 생성할 수 없어요.", status=409)
        if profile is None:
            raise _err(
                "profile_not_ready", "개인화 준비가 완료되지 않았어요.", status=409,
                blockers=[
                    {"code": "photos_incomplete", "detail": {"missingAngles": list(ANGLES)}},
                    {"code": "consent_missing", "detail": {"types": list(REQUIRED_CONSENTS)}},
                    {"code": "body_profile_missing", "detail": {}},
                ],
            )
        payload["profileId"] = profile["id"]

        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=None, kind="personalization_generation",
            payload=payload, idempotency_key=scoped_key, credits_reserved=cost,
            metadata={"creditCostVersion": settings.credit_cost_version},
        )
        if created:  # 신규 잡만 verify-before-use 게이트 + 예약(합류는 기존 잡 그대로 반환)
            can_generate, blockers = await _readiness(conn, user_id, profile)
            if not can_generate:
                raise _err("profile_not_ready", "개인화 준비가 완료되지 않았어요.",
                           status=409, blockers=blockers)
            _assert_age_eligible((await _age_state(conn, user_id))[0])  # 연령 게이트 ③
            await _validate_product_assets(conn, user_id, body.product_image_asset_ids)
            if await repo.reserve_credits(conn, user_id, cost) is None:
                raise _err("insufficient_credits", "크레딧이 부족해요.", status=402)
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into personalization_generations (id, profile_id, job_id, options) "
                    "values (%s, %s, %s, %s)",
                    (generation_id, profile["id"], job["id"], Json(options)),
                )
            await _audit(conn, user_id, profile["id"], "generation_started",
                         {"generationId": generation_id})
        await conn.commit()
    _wake_dispatcher(request)
    gid = generation_id if created else (job.get("payload") or {}).get("generationId")
    return _json({"jobId": job["id"], "generationId": gid}, status=202)


@router.post(
    "/generations/{generation_id}:refine",
    responses={**_P_RESPONSES},
    summary="보정 요청(골격)",
)
async def refine_generation(
    request: Request,
    generation_id: str,
    body: dict = Body(default={}),
    user_id: str = Depends(require_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """완료 생성 결과에 대한 보정 재생성(핏·포즈·배경 등). 잡 큐잉만 — 실제 보정은 워커.

    골격(엔진-의존 TBD): 소유 generation 검증 + purging 가드 후 personalization_generation 잡 큐잉.
    """
    settings = request.app.state.settings
    cost = int(getattr(settings, "credit_cost_personalization_generation", _DEFAULT_GENERATION_COST))
    scoped_key = (
        f"personalization:refine:{user_id}:{idempotency_key}" if idempotency_key else None
    )
    try:
        uuid.UUID(str(generation_id))
    except (ValueError, TypeError):
        raise _err("not_found", "생성 결과를 찾을 수 없습니다.", status=404)

    new_generation_id = str(uuid.uuid4())
    async with get_conn(request) as conn:
        profile = await _load_profile(conn, user_id)
        if profile is None:
            raise _err("not_found", "생성 결과를 찾을 수 없습니다.", status=404)
        if profile["status"] == "purging":
            raise _err("purge_in_progress", "파기가 진행 중이라 지금은 보정할 수 없어요.", status=409)
        async with conn.cursor() as cur:  # 소유 generation(프로필 경유 스코프)
            await cur.execute(
                "select id::text as id from personalization_generations "
                "where id = %s and profile_id = %s",
                (generation_id, profile["id"]),
            )
            src = await cur.fetchone()
        if src is None:
            raise _err("not_found", "생성 결과를 찾을 수 없습니다.", status=404)

        payload = {
            "profileId": profile["id"],
            "productImageAssetIds": [],
            "options": {"refineOf": generation_id, "changes": body.get("changes")},
            "generationId": new_generation_id,
        }
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=None, kind="personalization_generation",
            payload=payload, idempotency_key=scoped_key, credits_reserved=cost,
            metadata={"creditCostVersion": settings.credit_cost_version, "refine": True},
        )
        if created:
            _assert_age_eligible((await _age_state(conn, user_id))[0])
            if await repo.reserve_credits(conn, user_id, cost) is None:
                raise _err("insufficient_credits", "크레딧이 부족해요.", status=402)
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into personalization_generations (id, profile_id, job_id, options) "
                    "values (%s, %s, %s, %s)",
                    (new_generation_id, profile["id"], job["id"],
                     Json({"refineOf": generation_id})),
                )
            await _audit(conn, user_id, profile["id"], "generation_started",
                         {"generationId": new_generation_id, "refineOf": generation_id})
        await conn.commit()
    _wake_dispatcher(request)
    gid = new_generation_id if created else (job.get("payload") or {}).get("generationId")
    return _json({"jobId": job["id"], "generationId": gid}, status=202)


# ── 상태 동기화(실제 계산) ──────────────────────────────────
async def _sync_status_row(conn, user_id: str, profile: dict) -> str:
    """draft↔ready 파생 상태 동기화(purging/purged 불변). 갱신된 status 반환."""
    if profile["status"] in ("purging", "purged"):
        return profile["status"]
    ready, _ = await _readiness(conn, user_id, profile)
    target = "ready" if ready else "draft"
    if target != profile["status"]:
        async with conn.cursor() as cur:
            await cur.execute(
                "update personalization_profiles set status = %s "
                "where id = %s and status in ('draft', 'ready')",
                (target, profile["id"]),
            )
        profile["status"] = target
    return target
