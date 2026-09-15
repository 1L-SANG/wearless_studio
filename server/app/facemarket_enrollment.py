"""Fail-closed FaceMarket biometric enrollment and quarantine lifecycle."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from . import cx_identity, facemarket_id_document, facemarket_id_mask_verify, repo
from .agents.face_qc import QcFailed, load_face_qc, weight_paths
from .auth import require_user
from .facemarket_applications import MAX_IDENTITY_MISMATCH, _dispatch_decision_email
from .config import Settings
from .db import get_conn
from .models import CamelModel
from .facemarket_photos import (
    ASSET_SOURCE_SLOTS, LEGACY_SLOT_ALIASES, PHOTO_SLOTS, PHOTO_SLOTS_V2, REFSET_SLOTS,
    canonical_photo_slot, photo_slot_candidates, resolve_photo_rows,
)
from .facemarket_photo_normalize import (
    NORMALIZED_MIME,
    NormalizeFailed,
    normalize_png,
    sniff_image_mime,
)
from .facemarket_photo_check import (
    PhotoCheckUnavailable, check_enrollment_photo, judge_refset, reject_message,
)
from .personalization_qc import FaceQcUnavailable, evaluate_face_qc, qc_reason_message
from .r2 import (
    enrollment_id_document_key, enrollment_normalized_key, enrollment_quarantine_key,
    ext_for_mime, normalized_sibling_key, sha256_sri,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket biometric enrollment"])

# 새 등록이 **기록**하는 동의 문서 버전. 동의 화면 문구가 실제로 바뀌는 배포에서만 올린다.
# 2026-09-v1 은 등록 위저드의 동의·안내 공개본이 함께 나가면서 올렸다(#285/#287).
# 2026-09-v2 는 수집 항목이 "얼굴 8·상반신 5·전신 5" → "얼굴 16장"으로 바뀌면서 올렸다
# (#298). 같은 버전 문자열에 다른 본문을 게시하면 누가 어느 본문에 동의했는지 증명할 수 없다.
# 2026-09-v3 은 수집 항목에 옆모습 2장·뒷모습 1장이 더해지면서 올렸다(16장 → 18장).
BIOMETRIC_CONSENT_VERSION = "2026-09-v3"
# ⚠️ **판정에는 이 목록을 쓴다(단일 상수를 바인딩하지 마라).**
# 옛 버전에 동의하고 이미 passed 인 등록은 그 문자열을 그대로 들고 있고 백필 마이그레이션은
# 없다. 카탈로그 자격(`facemarket.py` `_CURRENT_CARD_ELIGIBILITY`)·cutover legacy 스코프가
# 단일 상수를 바인딩하던 시절에는, 이 상수를 올리는 순간 **라이브 카탈로그가 비고** 기존
# 모델이 cutover 파기 대상으로 분류됐다. 그래서 그 자리들은 전부 `= any(%s)` 로 바꿨다.
# 새 버전을 추가할 때 옛 버전을 지우면 그 순간 같은 사고가 난다.
ACCEPTED_BIOMETRIC_CONSENT_VERSIONS: tuple[str, ...] = ("2026-09-v1", "2026-09-v2", "2026-09-v3")
# 국외 이전은 동의가 아니라 고지다(개인정보 보호법 제28조의8 제1항 제3호, 처리위탁·보관은 처리방침 공개로 갈음).
# 화면에 보여 준 안내 문서 버전만 기록한다. 옛 클라이언트가 overseasConsent 를 보내면 그 버전을 그대로 쓴다.
# 이 안내 본문도 #298 에서 이전 항목이 바뀌었다("얼굴·전신 사진" → "얼굴 사진") — 게시본이
# 바뀌었으면 기록되는 버전도 같이 올린다. 이 값은 기록·표시 전용이라 자격 판정에 쓰이지 않는다.
# 2026-09-v3: 이전 항목이 "얼굴 사진" → "등록 사진(얼굴·옆모습·뒷모습)" 으로 바뀌었다.
OVERSEAS_NOTICE_VERSION = "2026-09-v3"
# 동의문 텍스트를 바꾸면 버전을 올린다. 프론트(Vercel)·백엔드(CI) 배포 시점이 어긋나는
# 동안 stale_consent_version 400 으로 등록이 막히지 않게, 직전 버전도 함께 수락한다.
ACCEPTED_CONSENT_VERSIONS = ("2026-09-v3", "2026-09-v2", "2026-09-v1", "2026-08-v2", "2026-08-v1")

#: 이 동의 버전으로 시작한 등록은 **18칸**을 채워야 한다. 그 앞 버전은 그때 받은 16칸으로 완료다.
#: ★ 칸이 늘었다고 이미 통과한 등록을 미완료로 되돌리면 그 모델이 카탈로그에서 사라진다
#:   (운영 05caa497 은 v1·18칸 이름으로 passed 다). 그래서 **모르는 버전은 16칸**으로 본다.
CONSENT_VERSIONS_WITH_ANGLES: frozenset[str] = frozenset({"2026-09-v3"})


def required_slots_for_consent(consent_version: str | None) -> tuple[str, ...]:
    """그 동의 본문에 적힌 칸만 요구한다 — 동의서와 검사가 갈리면 둘 다 거짓이 된다."""
    return (PHOTO_SLOTS if str(consent_version or "") in CONSENT_VERSIONS_WITH_ANGLES
            else PHOTO_SLOTS_V2)
ENROLLMENT_TTL = timedelta(hours=24)
# 관리자 육안 심사 기한. 일반 등록의 24h TTL 로 자동 만료시키면 심사가 밀렸을 때 정상
# 지원자가 자동 탈락하므로 review_pending 은 그 스윕에서 뺐는데, **신분증 촬영본은 업로드
# 7일 뒤 배치 스윕이 DB 와 무관하게 지운다** — 그 둘이 합쳐지면 7일 뒤엔 심사가 불가능해진
# 행이 그 사용자의 단일 활성 등록 슬롯을 영구히 점유한다(최종리뷰 I3). 7일보다 짧은 전용
# 기한을 둬서 증거가 살아 있는 동안 심사가 끝나게 하고, 넘기면 실패로 닫고 통지한다.
REVIEW_DEADLINE_DAYS = 5
_PHOTO_FENCE_NAMESPACE = 0x464D5048
_MODEL_ASSET_FENCE_NAMESPACE = 0x464D4D41
LEGACY_ANGLES = ("front", "angle45", "side")
REQUIRED_SLOT_COUNT = len(PHOTO_SLOTS)
# 업로드가 받아 주는 이름 = 정식 16칸 + 그 16칸으로 **올려 줄 수 있는** 옛 이름뿐이다
# (face01/face03/face05 · front/angle45/side). 새 스펙에 자리가 없는 옛 이름(face02·torso*·full* …)은
# 여기서 invalid_slot 으로 막힌다 — 이미 올라간 행은 남아 있고(파기가 쓸어 담는다) 완료 판정에서만 빠진다.
ACCEPTED_PHOTO_SLOTS = PHOTO_SLOTS + tuple(LEGACY_SLOT_ALIASES)
# 등록 사진은 **원본 그대로** 받는다(프런트가 다시 인코딩하지 않는다) — 등록 사진이 곧
# LoRA 학습셋이라, 셀러 상품 사진용 축소 규칙(4000px·JPEG 0.85)이 학습 화질을 깎고 있었다.
# 실측(아이폰 48MP, 8064×6048): HEIC 8.6MB · JPEG 최고화질 26.6MB. 25MB 상한으로는 JPEG
# 원본이 막힌다. 40MB = 그 위로 한 뼘.
MAX_FACE_BYTES = 40 * 1024 * 1024
MAX_FACE_MB = MAX_FACE_BYTES // (1024 * 1024)
# 아이폰이 주는 HEIC/HEIF 를 그대로 받는다. content-type 이 비거나 octet-stream 으로 와도
# 매직바이트(ftyp 브랜드)로 판정한다 — 확장자는 .HEIC/.heif/.hif 로 제각각이다.
ALLOWED_FACE_MIME = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
#: 얼굴 전용 확장자 맵. r2.MIME_EXT(셀러 presigned 업로드 화이트리스트)에는 **더하지 않는다** —
#: 그쪽은 상품 사진 경로라 HEIC 를 받을 이유가 없고, 다운스트림(Gemini)도 못 읽는다.
FACE_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                 "image/heic": "heic", "image/heif": "heif"}
#: 브라우저가 "모르겠다" 고 말하는 값. 이걸 거절하면 아이폰 HEIC 가 통째로 막힌다 — 실제
#: 형식은 매직바이트로 판정한다(sniff_image_mime).
GENERIC_MIME = {"", "application/octet-stream", "binary/octet-stream"}
#: 대표이미지(cover)는 브라우저가 그대로 <img> 로 그린다 — HEIC 를 받으면 빈 칸이 된다.
#: 등록 사진과 달리 정규화본을 만들지 않으므로 여기선 HEIC 를 받지 않는다.
ALLOWED_COVER_MIME = {"image/png", "image/jpeg", "image/webp"}
START_LIVENESS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "rekognition:StartFaceLivenessSession",
            "Resource": "*",
            "Condition": {
                "StringEquals": {"aws:RequestedRegion": "us-east-1"}
            },
        }
    ],
}
AWS_LIVENESS_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"mode": "standard", "max_attempts": 3},
)


class BiometricProviderError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EnrollmentMappedError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EnrollmentExpiredError(RuntimeError):
    pass


@dataclass(slots=True)
class LivenessResult:
    reference_image: bytearray
    confidence: float
    provider_version: str = "aws-rekognition-face-liveness"


@dataclass(frozen=True, slots=True)
class EnrollmentDecision:
    passed: bool
    retryable: bool
    reason: str | None
    status: str
    model_id: str | None = None


RETRYABLE_REASONS = {
    "liveness_retry",
    "liveness_unavailable",
    "qc_unavailable",
    "id_portrait_unavailable",
}
TERMINAL_REASONS = {
    "minor_blocked",
    "liveness_failed",
    "face_match_failed",
    "identity_replay",
    "identity_recovery_required",
}


def create_liveness_session(rekognition, *, client_request_token: str) -> str:
    response = rekognition.create_face_liveness_session(
        ClientRequestToken=client_request_token,
        Settings={"AuditImagesLimit": 0},
    )
    session_id = response.get("SessionId")
    try:
        return str(uuid.UUID(str(session_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise BiometricProviderError("liveness_unavailable") from exc


def get_liveness_result(
    rekognition, *, session_id: str, minimum_confidence: float
) -> LivenessResult:
    try:
        response = rekognition.get_face_liveness_session_results(SessionId=session_id)
    except Exception as exc:
        raise BiometricProviderError("liveness_unavailable") from exc
    if response.get("Status") != "SUCCEEDED":
        raise BiometricProviderError("liveness_retry")
    reference = (response.get("ReferenceImage") or {}).get("Bytes")
    if not reference:
        raise BiometricProviderError("liveness_retry")
    try:
        confidence = float(response.get("Confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise BiometricProviderError("liveness_failed") from exc
    if not math.isfinite(confidence) or confidence < minimum_confidence:
        raise BiometricProviderError("liveness_failed")
    return LivenessResult(bytearray(reference), confidence)


def assume_liveness_browser_credentials(
    sts, *, role_arn: str, session_name: str
) -> dict:
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=900,
        Policy=json.dumps(START_LIVENESS_POLICY, separators=(",", ":")),
    )
    credentials = response["Credentials"]
    return {
        "accessKeyId": credentials["AccessKeyId"],
        "secretAccessKey": credentials["SecretAccessKey"],
        "sessionToken": credentials["SessionToken"],
        "expiration": credentials["Expiration"],
    }


class BiometricConsent(CamelModel):
    accepted: bool
    document_version: str


class CreateEnrollmentBody(CamelModel):
    device_id: str
    biometric_consent: BiometricConsent
    # 'mid' = OACX 모바일 신분증(기존), 'simple_auth' = 간편인증 + 신분증 촬영.
    identity_method: str = "mid"
    terms_consent: BiometricConsent | None = None
    overseas_consent: BiometricConsent | None = None


class LivenessSessionBody(CamelModel):
    nonce: str


class CompleteEnrollmentBody(CamelModel):
    # 라이브니스 off 면 세션이 없으므로 optional. 실제 요구는 라우트가 flag 로 강제한다.
    session_id: str | None = None
    # Legacy-only face-match input. Ignored unless FM_FACE_MATCH_ENABLED=true.
    id_photo_hex: str | None = None
    # Task3: 신분증(CI) 검증은 앞단 /identity 가 전담한다 — /complete 는 SFace 매칭만 하고
    # OACX token 을 더 이상 받지 않는다(저장된 identity_* 증거를 읽어 모델을 바인딩).
    # D1: OACX RESULT-step 신분증 초상(`data.dlphotoimage`, HEX JPEG) — 프론트가 위젯 콜백에서
    # 그대로 릴레이한다. 스키마 레벨에서는 optional(구버전 클라·계약 모드 무관하게 요청 자체는
    # 받아준다) — 실제 요구 여부는 process_enrollment_completion 이 fail-closed 로 강제한다.


class IdentityVerifyBody(CamelModel):
    token: str


class ReshootSlotView(CamelModel):
    """다시 찍어야 할 칸 하나 — 관리자가 고르고, 등록 화면이 그대로 읽는다."""
    slot: str
    reason: str = ""


class EnrollmentPhotoView(CamelModel):
    angle: str
    slot: str
    qc_status: str
    uploaded_at: datetime


class EnrollmentLicenseTerms(CamelModel):
    allowed_use: list[str]
    valid_days: int | None = None


class EnrollmentView(CamelModel):
    id: str
    model_id: str | None = None
    status: str
    photos: list[EnrollmentPhotoView] = []
    required_angles: list[str] = list(PHOTO_SLOTS)
    passed: bool | None = None
    retryable: bool | None = None
    reason: str | None = None
    expires_at: datetime
    height_bucket: str | None = None
    body_type: str | None = None
    gender: str | None = None
    identity_method: str = "mid"
    review_status: str | None = None
    photo_count: int = 0
    consent_document_version: str | None = None
    terms_consent_version: str | None = None
    overseas_consent_version: str | None = None
    license_id: str | None = None
    license_terms: EnrollmentLicenseTerms | None = None
    photo_revision: int = 0
    #: 관리자의 학습 전 사진 확인. pending | approved | reshoot_requested.
    photo_review_status: str = "pending"
    #: 다시 찍어야 할 칸 — [{slot, reason}]. 관리자가 고른 칸만, 사유 그대로.
    reshoot_slots: list[ReshootSlotView] = []


class PhysiqueBody(CamelModel):
    height_bucket: str | None = None
    body_type: str | None = None


def refset_agreement(settings: Settings, photo_items) -> dict:
    """기준 3장이 서로 같은 사람·같은 조건으로 찍혔는가. **기록만 하고 아무것도 막지 않는다.**

    v6_refset_check.py 와 같은 규칙(중앙값 0.80 · 최저쌍 0.70)이지만, 등록이 이 촬영으로
    처음 들어오는 중이라 그 문턱이 실사용자 분포에 맞는지 아직 모른다. 차단 여부는 첫 실데이터를
    보고 정한다 — 그때 되짚을 수 있게 숫자를 남긴다.

    `fm_face_match_enabled`(본인확인 매칭)와 무관하게 돈다. 가중치가 없거나 한 쌍도 못 재면
    상태만 남기고 넘어간다 — 완료를 막는 경로가 절대 되면 안 된다.
    """
    refs = [buffer for slot, buffer in photo_items
            if canonical_photo_slot(slot) in REFSET_SLOTS]
    if len(refs) < 2:
        return {"status": "absent", "photos": len(refs)}
    try:
        qc = load_face_qc(settings, required=True)
    except Exception as exc:  # noqa: BLE001 — 가중치 부재 등. 기록만 남기고 완료는 그대로 간다
        return {"status": "unavailable", "photos": len(refs), "error": type(exc).__name__}
    scores: list[float] = []
    unscored = 0
    for first in range(len(refs)):
        for second in range(first + 1, len(refs)):
            try:
                score = qc.one_to_one_similarity(refs[first], refs[second])
            except Exception:  # noqa: BLE001 — 한 쌍이 안 나와도 나머지로 본다
                unscored += 1
                continue
            if score is None:
                unscored += 1
            else:
                scores.append(float(score))
    summary = judge_refset(scores)
    summary["photos"] = len(refs)
    if unscored:
        summary["unscored"] = unscored
    return summary


def _err(code: str, message: str, status: int = 400, **extra) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **extra},
    )


def _required_photo_slots(settings: Settings, consent_version: str | None = None) -> tuple[str, ...]:
    """이 등록이 채워야 하는 칸.

    설정(FM_PHOTO_SLOTS·FM_REQUIRED_SLOT_COUNT)이 바깥 테두리이고, **동의 버전**이 그 안에서
    실제 요구를 정한다 — 옛 동의로 시작한 등록에 뒤늦게 칸을 더 요구하지 않는다.
    """
    slots = tuple(settings.fm_photo_slots)
    if (
        not slots
        or len(set(slots)) != len(slots)
        or any(slot not in PHOTO_SLOTS for slot in slots)
        or not 1 <= settings.fm_required_slot_count <= len(slots)
    ):
        raise RuntimeError("invalid FaceMarket photo slot settings")
    required = slots[: settings.fm_required_slot_count]
    wanted = set(required_slots_for_consent(consent_version))
    required = tuple(slot for slot in required if slot in wanted)
    if not all(slot in required for slot in ASSET_SOURCE_SLOTS):
        raise RuntimeError("required FaceMarket slots must include asset source slots")
    return required


async def _read_registration_photos(conn, enrollment_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select angle, qc_status, storage_state from fm_biometric_enrollment_photos "
            "where enrollment_id = %s", (enrollment_id,),
        )
        return await cur.fetchall()


def _ready_photo_rows(rows: list[dict], required_slots, *, allow_approved=True) -> list[dict]:
    states = {"quarantine", "approved"} if allow_approved else {"quarantine"}
    return [row for row in resolve_photo_rows(rows, required_slots)
            if row.get("qc_status") == "passed" and row.get("storage_state") in states]


async def _assert_account_open(conn, user_id: str) -> None:
    if await repo.user_account_purge_closed(conn, user_id):
        raise _err("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", status=404)


def _canonical_enrollment_id(enrollment_id: str) -> str:
    try:
        return str(uuid.UUID(str(enrollment_id)))
    except (AttributeError, TypeError, ValueError):
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "얼굴 저장소를 사용할 수 없습니다.", status=503)
    return client


def _wake_dispatcher(request: Request) -> None:
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is not None:
        dispatcher.wake()


def match_threshold_for_angle(settings: Settings, angle: str) -> float | None:
    """각도별 매칭 임계. 측면만 따로 둔다.

    정면 얼굴 인식기(YuNet 검출 + SFace)는 측면에서 유사도가 구조적으로 낮게 나온다.
    prod 실측(2026-09-01): 같은 사람 사진인데 front 0.1806 / angle45 0.2605 / side 0.14825 —
    측면만 공통 임계 0.15 에 0.0017 모자라 등록 전체가 face_match_failed 로 날아갔다(3회 반복,
    매칭이 결정적이라 같은 사진은 늘 같은 점수였다). 측면 전용 임계가 없으면 기존 값을 쓴다.
    """
    if angle == "side" and settings.fm_side_live_threshold is not None:
        return settings.fm_side_live_threshold
    return settings.fm_retouched_live_threshold


def review_required(settings: Settings, method: str) -> bool:
    """완료 시 관리자 심사(`review_pending`)로 멈춰야 하는지.

    "off" 는 아무도 심사 안 함(오늘의 mid-only 기본), "all" 은 인증 수단과 무관하게 전부
    심사(신중한 롤아웃용 — mid 도 걸린다. mid 의 매칭 자체는 여전히 enforce 라 임계 미달은
    이 함수까지 오지 못하고 먼저 face_match_failed 로 실패한다), 기본값 "simple_auth_only" 는
    간편인증만 심사한다(위조 가능한 앵커라 기계가 진위를 못 가리므로).
    """
    mode = settings.fm_enrollment_review
    if mode == "off":
        return False
    if mode == "all":
        return True
    return method == "simple_auth"


async def bind_model_and_enqueue_asset_build(
    cur,
    *,
    user_id: str,
    enrollment_id: str,
    row: dict,
    match_snapshot: dict,
    method: str,
    identity_contract_version: str | None,
    liveness_provider_version: str,
    match_policy_version: str,
) -> str:
    """모델 바인딩(생성/재사용) + 신원증거 기록 + 자산빌드 잡 큐잉.

    Task8: 관리자 승인 후 재개(`facemarket_admin_review.approve_enrollment`)가
    `process_enrollment_completion` 의 이 tail 을 그대로 재사용한다 — 심사가 필요 없던
    성공 경로와 심사 승인 후 재개 경로가 SQL 문 하나까지 동일해야 두 경로가 갈라져
    드리프트하는 일이 없다. 호출자가 이미 `where id = %s and status = 'processing' for
    update` 로 행을 잠근 뒤 불러야 한다(둘 다 이 전제를 지킨다).

    `liveness_provider_version` 은 정상 경로에선 `liveness.provider_version`(또는
    "disabled"), 재개 경로에선 원래 라이브니스 프레임이 이미 사라졌으므로 항상
    "disabled_resume" 을 넘긴다(리뷰 대상은 늘 `fm_liveness_enabled=False` 조합이라
    실질적 정보 손실은 없다 — Task7 참조).
    """
    ci_hash = row["identity_ci_hash"]
    identity_tx_digest = row["identity_tx_digest"]
    identity_name_masked = row["identity_name_masked"]
    identity_birth_year = row["identity_birth_year"]

    await cur.execute(
        "select id::text as id, user_id::text as user_id from fm_models where ci_hash = %s for update",
        (ci_hash,),
    )
    model = await cur.fetchone()
    if model and model["user_id"] != user_id:
        raise EnrollmentMappedError("identity_recovery_required")
    # #285(사진 재검증): 이미 모델이 있는 등록이 사진만 다시 올린 경우다 — 그때는 같은
    # 모델로만 재바인딩할 수 있다(다른 모델로 갈아타면 남의 얼굴이 승격된다).
    revalidating_photos = row.get("photo_revision", 0) > 0
    if revalidating_photos and (not model or str(model["id"]) != str(row["model_id"])):
        raise EnrollmentMappedError("identity_recovery_required")
    if model:
        model_id = model["id"]
    elif row.get("model_id"):
        model_id = row["model_id"]
        await cur.execute(
            """
            update fm_models
            set ci_hash = %s, display_name = %s, user_id = %s
            where id = %s
            """,
            (ci_hash, identity_name_masked, user_id, model_id),
        )
    else:
        await cur.execute(
            """
            insert into fm_models (user_id, display_name, status, ci_hash)
            values (%s, %s, 'pending', %s)
            returning id::text as id
            """,
            (user_id, identity_name_masked, ci_hash),
        )
        model_id = (await cur.fetchone())["id"]
    try:
        # 재검증 회차는 같은 cx_tx_id 를 다시 넣으면 유니크 위반이라 건너뛴다(#285).
        if not revalidating_photos:
            await cur.execute(
                """
                insert into fm_identity_verifications
                    (model_id, cx_tx_id, cx_tx_id_format, fields)
                values (%s, %s, 'sha256-v1', %s)
                """,
                (
                    model_id,
                    identity_tx_digest,
                    Json({
                        "nameMasked": identity_name_masked,
                        "birthYear": identity_birth_year,
                        "biometric": True,
                    }),
                ),
            )
    except UniqueViolation:
        raise EnrollmentMappedError("identity_replay")
    await cur.execute(
        """
        update fm_models
        set assets_status = 'building', current_enrollment_id = %s
        where id = %s
        """,
        (enrollment_id, model_id),
    )
    # Task4: 등록 중 올린 대표이미지가 있으면 바인딩 시 모델 커버로 승격한다.
    # cover_image_url 은 기존 관례상 별도 URL 변환 없이 그대로 읽히므로(facemarket.py
    # _MODEL_CARD_COLS 참조) R2 키를 그대로 저장한다 — 노출 URL화는 범위 밖.
    if row.get("profile_image_r2_key"):
        await cur.execute(
            "update fm_models set cover_image_url = %s where id = %s",
            (row["profile_image_r2_key"], model_id),
        )
    # Task5: 등록 중 입력받은 키·체형(height_bucket·body_type)이 있으면 바인딩 시
    # 모델로 승격한다. gender는 identity(OACX)에서 설정되지만, CX가 성별을 안 주면
    # NULL로 남으므로 — 모델이 고른 키 구간 접두사(m_/f_)에서 유도해 채운다(coalesce).
    if row.get("height_bucket") or row.get("body_type"):
        from .facemarket_physique import bucket_gender

        await cur.execute(
            "update fm_models set height_bucket = coalesce(%s, height_bucket), "
            "body_type = coalesce(%s, body_type), "
            "gender = coalesce(gender, %s) where id = %s",
            (
                row.get("height_bucket"),
                row.get("body_type"),
                bucket_gender(row.get("height_bucket")),
                model_id,
            ),
        )
    await cur.execute(
        """
        update fm_biometric_enrollments
        set model_id = %s, status = 'asset_building', decision = 'passed',
            reason = null, completed_at = now(), oacx_tx_digest = %s,
            match_policy_version = %s,
            provider_versions = provider_versions || %s::jsonb
        where id = %s
        """,
        (
            model_id,
            identity_tx_digest,
            match_policy_version,
            Json({
                "faceLiveness": liveness_provider_version,
                "oacx": identity_contract_version,
                "faceMatch": "sface-one-to-one",
            }),
            enrollment_id,
        ),
    )
    if method == "simple_auth":
        # 심사가 필요 없는 간편인증 성공 경로(예: fm_enrollment_review=off)도
        # advisory 점수를 감사 기록으로 남긴다 — mid 경로는 이 문장을 안 타서
        # 기존 회귀 스위트가 고정해 둔 asset_building UPDATE 파라미터 수는
        # 그대로다.
        await cur.execute(
            "update fm_biometric_enrollments set match_scores = %s where id = %s",
            (Json(match_snapshot), enrollment_id),
        )
    await cur.execute(
        """
        insert into jobs (user_id, project_id, kind, status, payload, credits_reserved, metadata)
        values (%s, null, 'fm_model_asset_build', 'pending', %s, 0, '{}'::jsonb)
        """,
        (
            user_id,
            Json({"modelId": model_id, "enrollmentId": enrollment_id,
                  "photoRevision": row.get("photo_revision", 0)}),
        ),
    )
    return model_id


def _prewarm_opendid(request: Request) -> None:
    """VC 발급이 사실상 확정된 지점에서 holder(opendid)를 미리 깨운다.

    holder 는 scale-to-zero 라 콜드부트가 ~2분(4 JVM)이다. 발급 버튼에서 처음 깨우면
    사용자가 그 2분을 그대로 기다린다(#201 의 재시도 진행표시가 버텨줄 뿐이다). 라이브니스
    시작·등록 완료는 발급까지 몇 분 남은 가장 이른 확실한 신호라, 여기서 켜 두면 부팅이
    등록 뒷단계에 가려진다. 실패·중복은 무해 — reconciler 가 60초 안에 덮고, 오토스케일이
    off 면 prewarm_soon 이 즉시 return 한다."""
    scaler = getattr(request.app.state, "opendid_autoscaler", None)
    if scaler is not None:
        with contextlib.suppress(Exception):   # 훅은 등록 응답을 절대 막지 않는다
            scaler.prewarm_soon()


def _readable_photo(row: dict) -> tuple[str, str]:
    """이 사진을 **읽을 때** 쓸 (R2 키, MIME).

    원본은 사용자가 올린 그대로라 HEIC 일 수 있다 — 브라우저도 cv2 도 못 읽는다. 읽기는
    언제나 정규화본(EXIF 적용 무손실 PNG)으로 한다. 정규화본이 없는 옛 행(2026-09-15 이전)만
    원본으로 물러난다.
    """
    normalized = row.get("normalized_r2_key")
    if normalized:
        return normalized, NORMALIZED_MIME
    return row["r2_key"], row.get("mime_type") or "image/jpeg"


async def _load_owned_enrollment(conn, enrollment_id: str, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at,
                   e.liveness_session_digest, e.height_bucket, e.body_type,
                   e.identity_method, e.review_status,
                   e.consent_version, e.terms_consent_version,
                   e.overseas_consent_version, e.photo_revision,
                   coalesce(e.photo_review_status, 'pending') as photo_review_status,
                   e.reshoot_slots, m.gender as model_gender,
                   l.id::text as license_id, l.allowed_use as license_allowed_use,
                   l.license_valid_until
            from fm_biometric_enrollments e
            left join fm_models m on m.id = e.model_id
            left join fm_licenses l on l.enrollment_id = e.id
            where e.id = %s and e.user_id = %s
            """,
            (enrollment_id, user_id),
        )
        return await cur.fetchone()


def reshoot_slot_views(raw) -> list[ReshootSlotView]:
    """jsonb 컬럼 → 뷰. 모양이 깨진 항목은 버린다(화면이 죽는 것보다 낫다)."""
    out: list[ReshootSlotView] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "")
        if slot not in PHOTO_SLOTS:
            continue
        out.append(ReshootSlotView(slot=slot, reason=str(item.get("reason") or "")))
    return out


def requested_reshoot_slots(row: dict | None) -> set[str]:
    """지금 다시 받아도 되는 칸. 관리자가 재촬영을 요청한 칸 **그것뿐**이다."""
    if not row or row.get("photo_review_status") != "reshoot_requested":
        return set()
    return {view.slot for view in reshoot_slot_views(row.get("reshoot_slots"))}


async def _consume_reshoot_slot(cur, enrollment_id: str, user_id: str, slot: str) -> None:
    """다시 찍은 칸을 요청 목록에서 뺀다. 남은 칸이 없으면 '확인 대기'로 돌아간다.

    타임스탬프로 "다시 찍었는지" 를 추정하지 않는다 — 목록에서 빼는 게 유일한 진실이다.
    photo_reviewed_at/by 는 그대로 둔다(누가 언제 재촬영을 요청했는지는 기록으로 남긴다).
    """
    await cur.execute(
        """
        update fm_biometric_enrollments
           set reshoot_slots = coalesce((
                   select jsonb_agg(item)
                     from jsonb_array_elements(reshoot_slots) item
                    where item->>'slot' <> %s
               ), '[]'::jsonb)
         where id = %s and user_id = %s and photo_review_status = 'reshoot_requested'
        returning jsonb_array_length(reshoot_slots) as remaining
        """,
        (slot, enrollment_id, user_id),
    )
    row = await cur.fetchone()
    if row is None or (row.get("remaining") or 0) > 0:
        return
    await cur.execute(
        """
        update fm_biometric_enrollments
           set photo_review_status = 'pending', reshoot_slots = null
         where id = %s and user_id = %s and photo_review_status = 'reshoot_requested'
        """,
        (enrollment_id, user_id),
    )


def _validate_photo_mutation_enrollment(row: dict | None, slot: str | None = None) -> dict:
    if row is None:
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
    if row["status"] == "photos_pending":
        return row
    if row["status"] == "liveness_pending" and not row.get(
        "liveness_session_digest"
    ):
        return row
    # 재촬영 요청 — 등록은 이미 끝났고(passed) 모델도 있다. 등록 상태는 **건드리지 않고**
    # 관리자가 고른 칸만 새 사진으로 갈아 끼운다. 여기서 slot 을 안 보면 "한 칸 다시 찍어
    # 주세요" 가 "사진 전부 다시 받습니다" 가 된다.
    if slot is not None and slot in requested_reshoot_slots(row):
        return row
    raise _err(
        "invalid_enrollment_state",
        "현재 등록 단계에서는 사진을 변경할 수 없습니다.",
        status=409,
    )


async def _lock_photo_mutation_enrollment(
    conn, enrollment_id: str, user_id: str, slot: str | None = None
) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.status, e.liveness_session_digest,
                   coalesce(e.photo_review_status, 'pending') as photo_review_status,
                   e.reshoot_slots
            from fm_biometric_enrollments e
            where e.id = %s and e.user_id = %s
            for update
            """,
            (enrollment_id, user_id),
        )
        row = await cur.fetchone()
    return _validate_photo_mutation_enrollment(row, slot)


def _is_r2_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    return isinstance(error, dict) and str(error.get("Code")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


async def _try_photo_fence(conn, enrollment_id: str) -> bool:
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
            (_PHOTO_FENCE_NAMESPACE, enrollment_id),
        )
        return bool((await cur.fetchone())["locked"])


async def _reject_cutover_closed(conn) -> None:
    await repo.lock_facemarket_writer_boundary(conn)
    if await repo.facemarket_writer_boundary_closed(conn):
        raise _err(
            "facemarket_cutover_in_progress",
            "실물 모델 보안 전환 중이라 잠시 후 다시 시도해 주세요.",
            status=409,
        )


async def _unlock_photo_fence_once(conn, enrollment_id: str) -> None:
    await conn.rollback()
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_unlock(%s, hashtext(%s)) as unlocked",
            (_PHOTO_FENCE_NAMESPACE, enrollment_id),
        )
        if not (await cur.fetchone())["unlocked"]:
            raise RuntimeError("photo fence was not owned by this connection")
    await conn.rollback()


async def _unlock_photo_fence(conn, enrollment_id: str) -> None:
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    task = asyncio.create_task(_unlock_photo_fence_once(conn, enrollment_id))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            await conn.close()
        raise
    except Exception:
        await conn.close()
        raise


async def _run_r2_call_until_done(call, *args):
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def _drain_photo_cleanup_locked(
    conn,
    r2,
    *,
    enrollment_id: str,
    angle: str | None = None,
    key: str | None = None,
    reason: str | None = None,
) -> tuple[int, int]:
    clauses = ["c.enrollment_id = %s", "c.not_before <= now()"]
    params: list[str] = [enrollment_id]
    if angle is not None:
        clauses.append("c.angle = %s")
        params.append(angle)
    if key is not None:
        clauses.append("c.r2_key = %s")
        params.append(key)
    if reason is not None:
        clauses.append("c.reason = %s")
        params.append(reason)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                select c.angle, c.r2_key, c.reason, p.storage_state as current_state,
                       a.view as current_asset_view
                from fm_biometric_enrollment_photo_cleanup c
                left join fm_biometric_enrollment_photos p
                  on p.enrollment_id = c.enrollment_id
                 and p.angle = c.angle and p.r2_key = c.r2_key
                left join fm_model_assets a
                  on a.source_enrollment_id = c.enrollment_id
                 and a.r2_key = c.r2_key
                where {' and '.join(clauses)}
                order by c.created_at
                """,
                tuple(params),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        await conn.rollback()
        logger.warning(
            "facemarket_enrollment_photo_cleanup_load_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        return 0, 1

    deleted_count = 0
    failed_count = 0
    for row in rows:
        if row.get("current_state") in {"quarantine", "approved"} or row.get("current_asset_view"):
            delete_object = False
        else:
            delete_object = True
            try:
                if row["reason"] == "upload_orphan":
                    if (
                        await _run_r2_call_until_done(r2.head, row["r2_key"])
                        is None
                    ):
                        continue
                # 정규화본은 원본 키에서 계산되는 형제다 — 같이 지운다. 없으면 R2 delete 는
                # 무해한 no-op 이라(S3 의미론) 옛 행에도 안전하다.
                for target in (row["r2_key"], normalized_sibling_key(row["r2_key"])):
                    if target is None:
                        continue
                    try:
                        await _run_r2_call_until_done(r2.delete, target)
                    except Exception as exc:
                        if not _is_r2_not_found(exc):
                            raise
                if row["reason"] == "upload_orphan" and (
                    await _run_r2_call_until_done(r2.head, row["r2_key"])
                    is not None
                ):
                    raise RuntimeError("R2 object remained after delete")
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    "facemarket_enrollment_photo_cleanup_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "angle": row["angle"],
                        "error_type": type(exc).__name__,
                    },
                )
                continue
        try:
            async with conn.cursor() as cur:
                if delete_object:
                    await cur.execute(
                        """
                        delete from fm_biometric_enrollment_photos
                        where enrollment_id = %s and angle = %s and r2_key = %s
                          and storage_state = 'delete_pending'
                        """,
                        (enrollment_id, row["angle"], row["r2_key"]),
                    )
                await cur.execute(
                    """
                    delete from fm_biometric_enrollment_photo_cleanup
                    where enrollment_id = %s and r2_key = %s
                    """,
                    (enrollment_id, row["r2_key"]),
                )
            await conn.commit()
        except Exception as exc:
            await conn.rollback()
            failed_count += 1
            logger.warning(
                "facemarket_enrollment_photo_cleanup_commit_failed",
                extra={
                    "enrollment_id": enrollment_id,
                    "angle": row["angle"],
                    "error_type": type(exc).__name__,
                },
            )
            continue
        deleted_count += int(delete_object)
    return deleted_count, failed_count


async def _drain_photo_cleanup(
    app,
    *,
    enrollment_id: str,
    angle: str | None = None,
    key: str | None = None,
    reason: str | None = None,
) -> tuple[int, int]:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return 0, 1
    try:
        enrollment_id = _canonical_enrollment_id(enrollment_id)
        async with pool.connection() as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                return 0, 0
            try:
                return await _drain_photo_cleanup_locked(
                    conn,
                    r2,
                    enrollment_id=enrollment_id,
                    angle=angle,
                    key=key,
                    reason=reason,
                )
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_photo_cleanup_load_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        return 0, 1


async def _drain_model_asset_cleanup(
    app, *, limit: int = 100, model_id: str | None = None
) -> int:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return 0
    limit = max(1, min(int(limit), 100))
    clauses = ["c.not_before <= now()"]
    params: list[object] = []
    if model_id is not None:
        clauses.append("c.model_id = %s")
        params.append(model_id)
    params.append(limit)
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    select c.model_id::text as model_id, c.r2_key
                    from fm_model_asset_cleanup c
                    where {' and '.join(clauses)}
                    order by c.created_at
                    for update skip locked
                    limit %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                resolved = 0
                for row in rows:
                    await cur.execute(
                        "select pg_try_advisory_xact_lock(%s, hashtext(%s)) as locked",
                        (_MODEL_ASSET_FENCE_NAMESPACE, row["model_id"].lower()),
                    )
                    if not (await cur.fetchone())["locked"]:
                        continue
                    await cur.execute(
                        "select 1 from fm_model_assets where model_id=%s and r2_key=%s limit 1",
                        (row["model_id"], row["r2_key"]),
                    )
                    if await cur.fetchone() is None:
                        try:
                            await _run_r2_call_until_done(r2.delete, row["r2_key"])
                        except Exception as exc:
                            if not _is_r2_not_found(exc):
                                await cur.execute(
                                    """
                                    update fm_model_asset_cleanup
                                    set not_before = now() + interval '5 minutes'
                                    where model_id=%s and r2_key=%s
                                    """,
                                    (row["model_id"], row["r2_key"]),
                                )
                                logger.warning(
                                    "facemarket_model_asset_cleanup_failed",
                                    extra={
                                        "model_id": row["model_id"],
                                        "error_type": type(exc).__name__,
                                    },
                                )
                                continue
                    await cur.execute(
                        "delete from fm_model_asset_cleanup where model_id=%s and r2_key=%s",
                        (row["model_id"], row["r2_key"]),
                    )
                    resolved += 1
            await conn.commit()
            return resolved
    except Exception as exc:
        logger.warning(
            "facemarket_model_asset_cleanup_sweep_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0


async def _load_current_enrollment(conn, user_id: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select e.id::text as id, e.model_id::text as model_id, e.status,
                   e.decision, e.reason, e.cooldown_until, e.expires_at,
                   e.height_bucket, e.body_type, e.identity_method, e.review_status,
                   e.consent_version, e.terms_consent_version, e.overseas_consent_version,
                   e.photo_revision, m.gender as model_gender, l.id::text as license_id,
                   l.allowed_use as license_allowed_use, l.license_valid_until
            from fm_biometric_enrollments e
            left join fm_models m on m.id = e.model_id
            left join fm_licenses l on l.enrollment_id = e.id
            where e.user_id = %s and e.status in (
                'id_capture_pending', 'identity_pending', 'photos_pending',
                'review_pending', 'liveness_pending', 'processing',
                'asset_building', 'license_pending', 'vc_pending'
            )
            order by e.created_at desc limit 1
            """,
            (user_id,),
        )
        return await cur.fetchone()


async def _enrollment_view(conn, row: dict, settings: Settings) -> EnrollmentView:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select p.angle, p.qc_status, p.uploaded_at, p.storage_state
            from fm_biometric_enrollment_photos p
            where p.enrollment_id = %s
            order by case p.angle when 'front' then 1 when 'angle45' then 2 else 3 end
            """,
            (row["id"],),
        )
        photos = await cur.fetchall()
        photos = [
            {**photo, "slot": canonical_photo_slot(photo["angle"])}
            for photo in _ready_photo_rows(photos, settings.fm_photo_slots)
        ]
    decision = row.get("decision")
    cooldown_until = row.get("cooldown_until")
    retryable = None
    if decision == "failed":
        retryable = cooldown_until is None or cooldown_until <= datetime.now(timezone.utc)
    license_terms = None
    if row.get("license_id"):
        valid_until = row.get("license_valid_until")
        valid_days = (
            None
            if valid_until is None
            else max(1, math.ceil((valid_until - datetime.now(timezone.utc)).total_seconds() / 86400))
        )
        license_terms = EnrollmentLicenseTerms(
            allowed_use=list(row.get("license_allowed_use") or []),
            valid_days=valid_days,
        )
    return EnrollmentView(
        id=str(row["id"]),
        model_id=str(row["model_id"]) if row.get("model_id") else None,
        status=row["status"],
        photos=photos,
        required_angles=list(_required_photo_slots(settings, row.get("consent_version"))),
        passed=True if decision == "passed" else False if decision == "failed" else None,
        retryable=retryable,
        reason=row.get("reason"),
        expires_at=row["expires_at"],
        height_bucket=row.get("height_bucket"),
        body_type=row.get("body_type"),
        gender=row.get("model_gender"),
        photo_review_status=row.get("photo_review_status") or "pending",
        reshoot_slots=reshoot_slot_views(row.get("reshoot_slots")),
        identity_method=row.get("identity_method") or "mid",
        review_status=row.get("review_status"),
        photo_count=len(photos),
        consent_document_version=row.get("consent_version"),
        terms_consent_version=row.get("terms_consent_version"),
        overseas_consent_version=row.get("overseas_consent_version"),
        license_id=str(row["license_id"]) if row.get("license_id") else None,
        license_terms=license_terms,
        photo_revision=row.get("photo_revision", 0),
    )


@router.get("/config")
async def facemarket_config(request: Request):
    """등록 위저드 런타임 설정 — 프론트가 라이브니스 단계를 렌더할지 판정한다(서버 authoritative).

    인증 불필요(민감정보 없음, boolean 플래그 하나). livenessRequired=false 면 프론트는
    라이브니스 세션/위젯을 건너뛰고 사진 → 완료로 직행한다.
    """
    settings: Settings = request.app.state.settings
    from .facemarket_payout import PAYOUT_BANKS
    # /config 는 **새로 시작하는** 등록이 본다 — 지금 게시된 동의 본문의 칸을 준다.
    required_slots = _required_photo_slots(settings, BIOMETRIC_CONSENT_VERSION)
    return {
        "photoSlots": list(settings.fm_photo_slots),
        "payoutBanks": [{"code": code, "name": name} for code, name in PAYOUT_BANKS.items()],
        "requiredSlotCount": len(required_slots),
        "faceMatchEnabled": settings.fm_face_match_enabled,
        "livenessRequired": settings.fm_liveness_enabled,
        # 지원서 게이트 on 이면 프론트는 신규 진입을 /model/apply 로 보낸다.
        "applicationRequired": settings.fm_application_required,
        "consentDocumentVersion": BIOMETRIC_CONSENT_VERSION,
    }


@router.post("/enrollments", response_model=EnrollmentView, status_code=201)
async def create_enrollment(
    request: Request,
    body: CreateEnrollmentBody,
    user_id: str = Depends(require_user),
):
    settings: Settings = request.app.state.settings
    device_id = body.device_id.strip()
    consent = body.biometric_consent
    if len(device_id) < 32:
        raise _err("invalid_device", "기기 식별자를 확인할 수 없습니다.")
    if not consent.accepted:
        raise _err("biometric_consent_required", "생체정보 처리 동의가 필요합니다.")
    if consent.document_version not in ACCEPTED_CONSENT_VERSIONS:
        raise _err("stale_consent_version", "최신 생체정보 처리 동의를 확인해 주세요.")
    if consent.document_version == BIOMETRIC_CONSENT_VERSION:
        additional = (body.terms_consent,)
        if any(
            item is None
            or not item.accepted
            or item.document_version != BIOMETRIC_CONSENT_VERSION
            for item in additional
        ):
            raise _err("consent_required", "필수 동의를 모두 확인해 주세요.")
    overseas_version = (
        body.overseas_consent.document_version if body.overseas_consent else OVERSEAS_NOTICE_VERSION
    )
    # Task5: 인증 수단 분기. 'mid' = OACX 모바일 신분증(기존), 'simple_auth' = 간편인증 +
    # 신분증 촬영. 플래그에 없는 수단은 서버가 막는다 — 프론트가 낡아도 서버가 진실이다.
    # /id-document 라우트와 같은 에러 코드·상태코드를 쓴다(두 진입점이 합의한다).
    method = (body.identity_method or "mid").strip()
    if method not in settings.fm_identity_methods:
        raise _err(
            "identity_method_unavailable",
            "지금은 이 방식으로 등록할 수 없어요.",
            status=409,
        )
    # Task6: 순서 뒤집기 — 두 경로 모두 identity_pending 에서 시작한다. simple_auth 는
    # 본인확인을 마친 뒤(verify_enrollment_identity)에야 id_capture_pending(신분증 촬영)
    # 으로 넘어간다. 촬영 시점에 이미 이름·생년월일·CI 를 인증사가 검증해 뒀어야, 카드가
    # "정보를 읽는 대상"이 아니라 "그 사람 것인가"를 확인하는 물증이 된다.
    initial_status = "identity_pending"
    device_digest = hashlib.sha256(device_id.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + ENROLLMENT_TTL
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select count(*) filter (
                         where status = 'failed' and completed_at >= now() - interval '3 minutes'
                       ) as recent_failures,
                       max(cooldown_until) as cooldown_until
                from fm_biometric_enrollments
                where user_id = %s or device_digest = %s
                """,
                (user_id, device_digest),
            )
            rate = await cur.fetchone()
            if rate["recent_failures"] >= 5 or (
                rate["cooldown_until"] is not None and rate["cooldown_until"] > now
            ):
                raise _err(
                    "liveness_cooldown",
                    "잠시 후 생체 인증을 다시 시도해 주세요.",
                    status=429,
                )
            await cur.execute(
                """
                select id::text as id, status from fm_models
                where user_id = %s order by created_at desc limit 1 for update
                """,
                (user_id,),
            )
            model = await cur.fetchone()
            model_id = model["id"] if model else None
            if model and model["status"] == "awaiting_confirm":
                raise _err(
                    "model_confirmation_required",
                    "도착한 테스트컷을 먼저 확인해 주세요.",
                    status=409,
                )
            # 지원서 게이트(E1/E5/E6): 플래그 on 이면 신규 등록은 승인 지원서가 있어야 한다.
            # 면제 = 이미 등록 흐름에 들어온 모델 보유자(pending/verified/
            # reverification_required). suspended 는 우회 불가. 승인 지원서로 진입하는
            # 경우 enrollment 에 application_id 를 박아 대조·strike 대상을 고정한다(E5).
            application_id = None
            if settings.fm_application_required:
                # pending 도 면제다. fm_models 행은 **신분증 인증 성공 시점**에 status='pending'
                # 으로 먼저 생기므로(아래 _upsert_model), 게이트 도입 전에 신분증까지 마치고 사진
                # 단계에서 이탈한 사람은 모델 행은 있는데 활성 enrollment 도 지원서도 없다.
                # 이들을 막으면 등록도 못 하고 지원서 화면도 안 뜨는 막다른 골목에 갇힌다.
                # suspended 는 뺀다 — 정지된 모델이 심사 없이 재등록하는 우회로가 되면 안 된다.
                legacy_exempt = model is not None and model["status"] in (
                    "pending",
                    "verified",
                    "reverification_required",
                )
                if not legacy_exempt:
                    await cur.execute(
                        "select id::text as id from fm_model_applications "
                        "where user_id = %s and status = 'approved' "
                        "order by reviewed_at desc nulls last limit 1",
                        (user_id,),
                    )
                    approved = await cur.fetchone()
                    if approved is None:
                        raise _err(
                            "application_required",
                            "모델 지원서 승인 후 등록을 시작할 수 있어요.",
                            status=403,
                        )
                    application_id = approved["id"]
            if model and model["status"] == "verified":
                await cur.execute(
                    """
                    update fm_models
                    set status = 'reverification_required', assets_status = 'none',
                        current_enrollment_id = null
                    where id = %s
                    """,
                    (model_id,),
                )
                await cur.execute(
                    """
                    update fm_licenses set status = 'reverification_required'
                    where model_id = %s and status = 'active'
                    """,
                    (model_id,),
                )
            # mid 는 identity_method/status 컬럼을 안 건드린다 — DB 기본값('mid'/
            # 'identity_pending')이 그대로 적용되어 SQL 텍스트가 오늘의 mid 경로와 컬럼
            # 목록까지 동일하다(기존 mid 경로 불변이 최우선 순위). simple_auth 만 두 컬럼을
            # 리터럴로 명시한다(값이 화이트리스트를 통과한 코드 상수라 바인드 파라미터일
            # 필요가 없다). 동의 버전 두 컬럼(terms/overseas)은 #285 가 더한 것으로 두 분기
            # 모두 같은 자리에 싣는다 — 바인드 파라미터 개수가 갈라지면 안 된다.
            # on conflict 의 where 절은 fm_biometric_active_per_user 부분 유니크 인덱스
            # (Task1 마이그레이션)의 arbiter 추론 대상이다 — 인덱스 predicate 와 정확히
            # 맞춘다(순서까지). 짧은 7-state 부분집합도 PG 추론상 동작은 하지만("암시"
            # 관계로 arbiter 는 잡힌다 — 로컬 Postgres 로 실측 확인함), 사람이 눈으로 diff
            # 하기 쉽게, 그리고 이후 드리프트를 막기 위해 인덱스와 텍스트를 일치시킨다.
            if method == "simple_auth":
                insert_sql = f"""
                    insert into fm_biometric_enrollments
                        (user_id, model_id, device_digest, consent_version, expires_at,
                         application_id, terms_consent_version, overseas_consent_version,
                         identity_method, status)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, '{method}', '{initial_status}')
                    on conflict (user_id) where status in (
                        'id_capture_pending', 'identity_pending', 'photos_pending',
                        'review_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending'
                    ) do nothing
                    returning id::text as id
                    """
            else:
                insert_sql = """
                    insert into fm_biometric_enrollments
                        (user_id, model_id, device_digest, consent_version, expires_at, application_id,
                         terms_consent_version, overseas_consent_version)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (user_id) where status in (
                        'id_capture_pending', 'identity_pending', 'photos_pending',
                        'review_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending'
                    ) do nothing
                    returning id::text as id
                    """
            await cur.execute(
                insert_sql,
                (user_id, model_id, device_digest, consent.document_version, expires_at,
                 application_id,
                 body.terms_consent.document_version if body.terms_consent else None,
                 overseas_version),
            )
            inserted = await cur.fetchone()
            if inserted:
                enrollment_id = inserted["id"]
            else:
                # 활성 상태 집합은 fm_biometric_active_per_user 인덱스(Task1 마이그레이션)와
                # 맞춘다 — id_capture_pending/review_pending 을 빠뜨리면 그 상태로 활성인
                # simple_auth 등록의 재조회가 여기서 None 을 내 500 으로 죽는다.
                await cur.execute(
                    """
                    select id::text as id from fm_biometric_enrollments
                    where user_id = %s and status in (
                        'id_capture_pending', 'identity_pending', 'photos_pending',
                        'review_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending'
                    ) order by created_at desc limit 1
                    """,
                    (user_id,),
                )
                enrollment_id = (await cur.fetchone())["id"]
            if consent.document_version == BIOMETRIC_CONSENT_VERSION:
                await cur.execute(
                    "update fm_biometric_enrollments set consent_version = %s, "
                    "terms_consent_version = %s, overseas_consent_version = %s "
                    "where id = %s and user_id = %s",
                    (
                        BIOMETRIC_CONSENT_VERSION,
                        body.terms_consent.document_version,
                        overseas_version,
                        enrollment_id,
                        user_id,
                    ),
                )
                await cur.execute(
                    "insert into fm_enrollment_consent_events "
                    "(enrollment_id, user_id, biometric_version, terms_version, overseas_version) "
                    "values (%s, %s, %s, %s, %s)",
                    (
                        enrollment_id,
                        user_id,
                        BIOMETRIC_CONSENT_VERSION,
                        body.terms_consent.document_version,
                        overseas_version,
                    ),
                )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row, settings)


@router.post("/enrollments/{enrollment_id}/identity", response_model=EnrollmentView)
async def verify_enrollment_identity(
    request: Request,
    enrollment_id: str,
    body: IdentityVerifyBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")
    settings: Settings = request.app.state.settings
    token_digest = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"
    # 계약은 클라가 아니라 이 등록에 저장된 identity_method 로 고른다 — 클라가 토큰에
    # 태울 파서를 스스로 고르지 못하게 한다. fetch_trans(제공자 네트워크 호출) 앞에서,
    # 락 없이 가볍게 읽는다: 아래 소유·상태 검사(for update)는 fetch_trans *뒤에* 있어서
    # 거기 얹으면 네트워크 왕복 동안 행 잠금을 쥐게 된다. 여기서 못 찾아도 에러 내지
    # 않는다 — 소유권의 단일 진실은 아래 for update 조회이고, 이건 계약 선택용 힌트일
    # 뿐이다(찾지 못하면 mid 로 진행하다 아래에서 정식으로 404 난다 — 오늘과 동일한
    # 순서: 존재하지 않는 enrollment 도 지금처럼 fetch_trans 를 먼저 태운다).
    # 이 때문에 /identity 는 커넥션 풀 체크아웃을 한 번이 아니라 두 번 순차로 쓴다(이
    # 블록에서 하나를 반납하고, fetch_trans 이후 아래에서 새로 하나를 연다) — 겹쳐 쥐지는
    # 않으므로 기본 풀 크기에서는 무해하지만, 풀 크기 재산정 때 다시 발견하지 않도록
    # 남겨 둔다.
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select identity_method from fm_biometric_enrollments "
                "where id = %s and user_id = %s",
                (enrollment_id, user_id),
            )
            method_row = await cur.fetchone()
    # NULL(마이그레이션 이전 행) 도 'mid' 로 취급한다.
    method = (method_row or {}).get("identity_method") or "mid"
    try:
        contract = cx_identity.get_oacx_biometric_contract(settings, method=method)
    except cx_identity.OacxBiometricError as exc:
        raise _err(exc.reason, "본인확인을 지금 진행할 수 없어요.")
    try:
        # CI·이름·생년월일은 trans/{token}(서버발 조회)에서만 온다 — 서버검증 완료.
        trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
        if method == "simple_auth":
            evidence = cx_identity.parse_simple_auth_evidence(trans, contract=contract)
        else:
            evidence = cx_identity.parse_oacx_biometric_evidence(trans, contract=contract)
    except cx_identity.OacxBiometricError as exc:
        raise _err(exc.reason, "본인확인에 실패했어요. 다시 시도해 주세요.")
    except cx_identity.CxIdentityError:
        raise _err("id_portrait_unavailable", "신분증 확인에 실패했어요. 다시 시도해 주세요.")
    try:
        # 원시 CI 는 HMAC(ci_hash)만 장기저장하고 raw 는 즉시 폐기한다.
        ci_hash = hmac.new(
            settings.fm_ci_pepper.encode(), evidence.ci, hashlib.sha256
        ).hexdigest()
    finally:
        cx_identity.wipe_bytearray(evidence.ci)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
        async with conn.cursor() as cur:
            # 소유·상태 검사(identity_pending 만 허용)
            await cur.execute(
                "select status, application_id::text as application_id "
                "from fm_biometric_enrollments "
                "where id = %s and user_id = %s for update",
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if row["status"] != "identity_pending":
                raise _err(
                    "invalid_enrollment_state",
                    "이미 본인확인이 완료됐거나 진행할 수 없는 상태입니다.",
                    status=409,
                )
            # replay(토큰 재사용) 차단
            await cur.execute(
                """
                select exists(
                  select 1 from fm_identity_verifications
                  where cx_tx_id = %s and cx_tx_id_format = 'sha256-v1'
                  union all
                  select 1 from fm_biometric_enrollments where identity_tx_digest = %s
                ) as replayed
                """,
                (token_digest, token_digest),
            )
            if (await cur.fetchone())["replayed"]:
                await conn.commit()
                raise _err("identity_replay", "이미 사용된 인증입니다. 새로 시작해 주세요.")
            # 교차유저 CI 충돌(다른 유저 모델이면 소유권 확인 필요)
            await cur.execute(
                "select user_id::text as user_id from fm_models where ci_hash = %s",
                (ci_hash,),
            )
            owner = await cur.fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise _err("identity_recovery_required", "기존 모델 소유권 확인이 필요해요.")
            # 지원서 대조(E13): enrollment 에 승인 지원서가 연결돼 있으면 이름·생년월일을 대조한다.
            # 불일치는 지원서에 누적(E2, enrollment 재생성과 무관), 3회면 지원서 거절 + enrollment
            # 종료를 한 트랜잭션으로(E7). 실패 시도도 token 을 소비해(E8) 같은 token 재전송을 막는다.
            #
            # 플래그(fm_application_required)도 함께 본다. 안 보면 롤백이 반쪽이 된다 — 플래그가
            # 켜져 있던 동안 만들어진 enrollment 는 application_id 가 박혀 있어서, 운영자가 문제를
            # 보고 플래그를 false 로 되돌린 뒤에도 그 사용자는 대조에 걸려 422 를 받고 3회면
            # 지원서 자동 거절 + 등록 취소 + 거절 메일까지 나간다. '끄면 구 경로'가 되어야 한다.
            if settings.fm_application_required and row["application_id"]:
                await cur.execute(
                    "select applicant_name, birthdate, identity_mismatch_count, contact_email "
                    "from fm_model_applications where id = %s for update",
                    (row["application_id"],),
                )
                approw = await cur.fetchone()
                if approw is not None:
                    claim = cx_identity.compare_identity_claim(
                        trans,
                        contract=contract,
                        expected_name=approw["applicant_name"],
                        expected_birthdate=approw["birthdate"],
                    )
                    if not claim.matched:
                        new_count = approw["identity_mismatch_count"] + 1
                        # 실패 token 소비(attempt ledger, E8): 같은 token 재전송은 replay 로 차단.
                        await cur.execute(
                            "update fm_biometric_enrollments set identity_tx_digest = %s "
                            "where id = %s and user_id = %s",
                            (token_digest, enrollment_id, user_id),
                        )
                        if new_count >= MAX_IDENTITY_MISMATCH:
                            await cur.execute(
                                "update fm_model_applications set status = 'rejected', "
                                "identity_mismatch_count = %s, reject_reason = '정보 불일치', "
                                "terminated_at = now() where id = %s",
                                (new_count, row["application_id"]),
                            )
                            await cur.execute(
                                "update fm_biometric_enrollments set status = 'cancelled', "
                                "reason = 'identity_claim_mismatch', completed_at = now() "
                                "where id = %s and user_id = %s",
                                (enrollment_id, user_id),
                            )
                            await conn.commit()
                            # 자동 거절 메일(스펙 7·10) — 상태는 이미 커밋됨, 발송 실패는 뱃지+재발송.
                            await _dispatch_decision_email(
                                request,
                                application_id=row["application_id"],
                                to=approw["contact_email"],
                                email_type="auto_rejected",
                                reject_reason="정보 불일치",
                            )
                            raise _err(
                                "identity_claim_rejected",
                                "지원서 정보와 신분증이 3회 일치하지 않아 지원이 거절됐어요. "
                                "지원서를 다시 작성해 주세요.",
                                status=409,
                            )
                        await cur.execute(
                            "update fm_model_applications set identity_mismatch_count = %s "
                            "where id = %s",
                            (new_count, row["application_id"]),
                        )
                        await conn.commit()
                        raise _err(
                            "identity_claim_mismatch",
                            "지원서 정보와 신분증이 일치하지 않아요. "
                            f"{MAX_IDENTITY_MISMATCH - new_count}회 더 시도할 수 있어요.",
                            status=422,
                        )
            # 증거 저장 + 상태 전이. Task6: 다음 상태는 인증 수단으로 가른다 — simple_auth
            # 는 아직 사람 얼굴 앵커가 없어(초상이 안 온다) 신분증 촬영(id_capture_pending)
            # 으로, mid 는 이미 초상이 있어 곧장 사진 촬영(photos_pending)으로 간다.
            # mid 분기의 SQL 텍스트는 오늘과 완전히 동일하게 유지한다(리터럴
            # "'photos_pending'") — 바인드 파라미터로 통합하면 문자열이 갈라져,
            # test_facemarket_biometric_enrollment.py 의 FakeCursor 가 이 UPDATE 를
            # 리터럴로 식별하는 mid 회귀 스위트 전체가 조용히 다른 분기로 샌다.
            next_status = "id_capture_pending" if method == "simple_auth" else "photos_pending"
            await cur.execute(
                f"""
                update fm_biometric_enrollments
                set status = '{next_status}',
                    identity_ci_hash = %s, identity_name_masked = %s,
                    identity_birth_year = %s, identity_tx_digest = %s,
                    identity_contract_version = %s
                where id = %s and user_id = %s and status = 'identity_pending'
                """,
                (ci_hash, evidence.name_masked, evidence.birth[:4], token_digest,
                 evidence.contract_version, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row, request.app.state.settings)


@router.get("/enrollments/current", response_model=EnrollmentView)
async def get_current_enrollment(
    request: Request,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        row = await _load_current_enrollment(conn, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        return await _enrollment_view(conn, row, request.app.state.settings)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentView)
async def get_enrollment(
    request: Request,
    enrollment_id: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with get_conn(request) as conn:
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        return await _enrollment_view(conn, row, request.app.state.settings)


@router.post(
    "/enrollments/{enrollment_id}/photos",
    response_model=EnrollmentPhotoView,
    status_code=201,
)
async def upload_enrollment_photo(
    request: Request,
    enrollment_id: str,
    angle: str | None = Form(None),
    photo: UploadFile = File(...),
    user_id: str = Depends(require_user),
    slot: str | None = Form(None),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    slot = slot if isinstance(slot, str) and slot else angle
    if slot not in ACCEPTED_PHOTO_SLOTS:
        code = "invalid_angle" if angle is not None else "invalid_slot"
        raise _err(code, "사진 슬롯을 확인해 주세요.")
    requested_slot = slot
    slot = LEGACY_SLOT_ALIASES.get(slot, slot)
    if slot not in request.app.state.settings.fm_photo_slots:
        raise _err("invalid_slot", "사진 슬롯을 확인해 주세요.")
    angle = slot
    # content-type 은 믿지 **않지만**, 확실히 아닌 것은 바이트를 읽기 전에 막는다. iOS 는
    # HEIC 의 type 을 비우거나 octet-stream 으로 주므로 그 둘만 통과시키고 아래에서 매직바이트로
    # 판정한다. 이 앞당긴 검사가 없으면 PDF 업로드가 400 이 아니라 계정·단계 게이트의 403/409 를
    # 받는다 — 요청 모양이 틀린 것과 권한이 없는 것을 클라이언트가 구분하지 못한다.
    declared = (photo.content_type or "").lower()
    if declared and declared not in GENERIC_MIME and declared not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "HEIC, PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
    r2 = _r2_face(request)
    data = await photo.read()
    # 확장자·content-type 은 믿지 않는다 — 실제 형식은 매직바이트가 정한다.
    mime = sniff_image_mime(data, declared)
    if mime not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "HEIC, PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    new_key = None
    normalized_key = None
    try:
        if not data:
            raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
        if len(data) > MAX_FACE_BYTES:
            raise _err("file_too_large", f"이미지는 {MAX_FACE_MB}MB 이하만 가능합니다.", status=413)
        # 정규화본을 **먼저** 만든다. 검사도 열람도 학습도 전부 이걸 읽는다 — HEIC 는 cv2 가
        # 아예 못 읽고, JPEG 는 EXIF orientation 을 PIL 과 cv2 가 다르게 다룬다.
        # to_thread 필수: 48MP 한 장이 이 자리에서 수 초를 쓴다(2026-08-26 루프 동결 사고).
        try:
            normalized, normalized_size = await asyncio.to_thread(
                normalize_png, data, request.app.state.settings.fm_normalized_max_edge)
        except NormalizeFailed as exc:
            logger.info(
                "facemarket_enrollment_photo_unreadable",
                extra={"enrollment_id": enrollment_id, "angle": angle, "reason": str(exc)},
            )
            raise _err("photo_framing", reject_message("unreadable"), reasons=["unreadable"])
        # 촬영 스펙 검사 — 등록 사진이 곧 학습셋이라 **항상** 본다(fm_face_match_enabled 와 무관).
        # 여기서 막지 않으면 사용자는 촬영 자리를 떠난 뒤에야 못 쓰는 사진임을 알게 된다.
        try:
            shot_reason, shot = await asyncio.to_thread(
                check_enrollment_photo, normalized, angle,
                model_dir=request.app.state.settings.fm_face_qc_dir,
            )
        except PhotoCheckUnavailable:
            raise _err(
                "qc_unavailable",
                "사진 검사를 지금 수행할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                status=503,
            )
        if shot_reason:
            logger.info(
                "facemarket_enrollment_photo_rejected",
                extra={"enrollment_id": enrollment_id, "angle": angle,
                       "reason": shot_reason, **shot},
            )
            raise _err("photo_framing", reject_message(shot_reason), reasons=[shot_reason])
        qc = None
        if request.app.state.settings.fm_face_match_enabled:
            try:
                qc = await evaluate_face_qc(
                    request.app.state.settings,
                    image_bytes=normalized,
                    mime=NORMALIZED_MIME,
                    angle=angle,
                )
            except FaceQcUnavailable:
                raise _err(
                    "qc_unavailable",
                    "얼굴 검사를 지금 수행할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                    status=503,
                )
        if qc is not None and not qc.passed:
            # 차단 사유만 노출(angle_mismatch advisory 는 여기 도달 못하지만, 혼재 시에도
            # 거절 카피에 각도 안내가 섞이지 않게 blocking_reasons 로 한정한다).
            raise _err(
                "face_quality",
                qc_reason_message(qc.blocking_reasons),
                reasons=qc.blocking_reasons,
            )
        # 원본의 확장자는 실제 형식을 따른다(HEIC 는 .heic). 정규화본은 같은 버전을 쓰는
        # 형제 키다 — 한 업로드가 만든 두 객체가 짝이라는 게 키에서 보여야 파기·정리가 쉽다.
        version = uuid.uuid4().hex
        ext = FACE_MIME_EXT[mime]
        new_key = enrollment_quarantine_key(enrollment_id, angle, ext, version=version)
        normalized_key = enrollment_normalized_key(enrollment_id, angle, version=version)
        old_key = None
        intent_committed = False
        try:
            async with get_conn(request) as conn:
                await _assert_account_open(conn, user_id)
                row = await _load_owned_enrollment(conn, enrollment_id, user_id)
                _validate_photo_mutation_enrollment(row, angle)
                await _reject_cutover_closed(conn)
                await conn.commit()
            await _drain_photo_cleanup(
                request.app,
                enrollment_id=enrollment_id,
                angle=angle,
                reason="upload_orphan",
            )
            async with get_conn(request) as conn:
                if not await _try_photo_fence(conn, enrollment_id):
                    raise _err(
                        "photo_cleanup_pending",
                        "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                        status=409,
                    )
                try:
                    await _assert_account_open(conn, user_id)
                    await _reject_cutover_closed(conn)
                    locked = await _lock_photo_mutation_enrollment(
                        conn, enrollment_id, user_id, angle)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select r2_key, storage_state
                            from fm_biometric_enrollment_photos
                            where enrollment_id = %s and angle = %s
                            """,
                            (enrollment_id, angle),
                        )
                        old = await cur.fetchone()
                        if old and old["storage_state"] == "delete_pending":
                            raise _err(
                                "photo_cleanup_pending",
                                "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                                status=409,
                            )
                        # 원본 키 **하나만** 예약한다. 정규화본은 이 키에서 계산되는
                        # 형제(normalized_sibling_key)라, 이 한 줄이 정리되면 둘 다 정리된다 —
                        # 둘을 따로 추적하면 반쪽만 남는 경우가 생긴다.
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photo_cleanup
                                (enrollment_id, angle, r2_key, reason)
                            values (%s, %s, %s, 'upload_orphan')
                            on conflict (enrollment_id, r2_key) do nothing
                            """,
                            (enrollment_id, angle, new_key),
                        )
                    await conn.commit()
                    intent_committed = True
                    try:
                        await _run_r2_call_until_done(r2.put_bytes, new_key, data, mime)
                        await _run_r2_call_until_done(
                            r2.put_bytes, normalized_key, normalized, NORMALIZED_MIME)
                    except Exception as exc:
                        logger.warning(
                            "facemarket_enrollment_photo_store_failed",
                            extra={
                                "enrollment_id": enrollment_id,
                                "angle": angle,
                                "error_type": type(exc).__name__,
                            },
                        )
                        raise _err(
                            "storage_unavailable",
                            "얼굴 저장소를 사용할 수 없습니다.",
                            status=503,
                        )

                    await _reject_cutover_closed(conn)
                    locked = await _lock_photo_mutation_enrollment(
                        conn, enrollment_id, user_id, angle)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select r2_key, storage_state
                            from fm_biometric_enrollment_photos
                            where enrollment_id = %s and angle = %s
                            """,
                            (enrollment_id, angle),
                        )
                        old = await cur.fetchone()
                        if old and old["storage_state"] == "delete_pending":
                            raise _err(
                                "photo_cleanup_pending",
                                "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                                status=409,
                            )
                        old_key = old["r2_key"] if old else None
                        # 이전 정규화본도 같이 회수한다 — 안 하면 갈아 끼울 때마다 옛 PNG 가
                        # 한 장씩 쌓인다(48MP 면 한 장 51MB).
                        if old_key and old_key != new_key:
                            await cur.execute(
                                """
                                insert into fm_biometric_enrollment_photo_cleanup
                                    (enrollment_id, angle, r2_key, reason)
                                values (%s, %s, %s, 'superseded')
                                on conflict (enrollment_id, r2_key) do update
                                set reason = 'superseded'
                                """,
                                (enrollment_id, angle, old_key),
                            )
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photos
                                (enrollment_id, angle, r2_key, image_digest, mime_type, byte_size,
                                 normalized_r2_key, normalized_byte_size,
                                 normalized_width, normalized_height)
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            on conflict (enrollment_id, angle) do update set
                                r2_key = excluded.r2_key,
                                image_digest = excluded.image_digest,
                                mime_type = excluded.mime_type,
                                byte_size = excluded.byte_size,
                                normalized_r2_key = excluded.normalized_r2_key,
                                normalized_byte_size = excluded.normalized_byte_size,
                                normalized_width = excluded.normalized_width,
                                normalized_height = excluded.normalized_height,
                                qc_status = 'passed',
                                storage_state = 'quarantine',
                                uploaded_at = now(),
                                approved_at = null
                            returning uploaded_at
                            """,
                            (
                                enrollment_id,
                                angle,
                                new_key,
                                # 무결성 해시는 **원본** 바이트로 남긴다 — 증서(VC)가 가리키는
                                # 것이 사용자가 올린 그 파일이어야 한다.
                                sha256_sri(data),
                                mime,
                                len(data),
                                normalized_key,
                                len(normalized),
                                normalized_size[0],
                                normalized_size[1],
                            ),
                        )
                        uploaded_at = (await cur.fetchone())["uploaded_at"]
                        await cur.execute(
                            """
                            delete from fm_biometric_enrollment_photo_cleanup
                            where enrollment_id = %s and r2_key = %s
                            """,
                            (enrollment_id, new_key),
                        )
                        # 동의 버전은 위에서 이미 읽은 등록 행(row)에서 가져온다 — 쿼리를 늘리지 않는다.
                        required_slots = _required_photo_slots(
                            request.app.state.settings, row.get("consent_version"))
                        uploaded = _ready_photo_rows(
                            await _read_registration_photos(conn, enrollment_id), required_slots)
                        if len(uploaded) == len(required_slots):
                            await cur.execute(
                                """
                                update fm_biometric_enrollments set status = 'liveness_pending'
                                where id = %s and user_id = %s and status = 'photos_pending'
                                """,
                                (enrollment_id, user_id),
                            )
                        # 재촬영 요청으로 들어온 칸이면 그 칸을 목록에서 뺀다 — 요청한 칸을
                        # 다 채우면 저절로 '확인 대기'로 돌아가 관리자 큐에 다시 뜬다.
                        if angle in requested_reshoot_slots(locked):
                            await _consume_reshoot_slot(cur, enrollment_id, user_id, angle)
                    await conn.commit()
                    intent_committed = False
                finally:
                    await _unlock_photo_fence(conn, enrollment_id)
        except Exception as db_error:
            if intent_committed:
                await _drain_photo_cleanup(
                    request.app, enrollment_id=enrollment_id, key=new_key
                )
            if isinstance(db_error, HTTPException):
                raise
            logger.warning(
                "facemarket_enrollment_photo_metadata_store_failed",
                extra={
                    "enrollment_id": enrollment_id,
                    "angle": angle,
                    "error_type": type(db_error).__name__,
                },
            )
            raise _err(
                "db_unavailable",
                "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
                status=503,
            )

        await _drain_photo_cleanup(
            request.app,
            enrollment_id=enrollment_id,
            angle=angle,
            reason="superseded",
        )
        # 사진을 올리기 시작했다 = 발급까지 아직 몇 분 남았다. 여기서 깨우는 게 가장 이르다.
        # prod 실측(2026-09-01): 사진 3장에 3분 26초가 걸렸고, 그동안 홀더(~2분 부팅)를 띄우면
        # 발급 시점엔 이미 따뜻하다. 라이브니스 훅은 발급까지 1분도 안 남아 부팅을 못 가렸다.
        _prewarm_opendid(request)
        return EnrollmentPhotoView(
            angle=requested_slot,
            slot=angle,
            qc_status="passed",
            uploaded_at=uploaded_at,
        )
    finally:
        data = b""


@router.post(
    "/enrollments/{enrollment_id}/id-document",
    response_model=EnrollmentView,
    status_code=201,
)
async def upload_id_document(
    request: Request,
    enrollment_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(..., alias="documentType"),
    masked_confirmed: bool = Form(..., alias="maskedConfirmed"),
    user_id: str = Depends(require_user),
):
    """간편인증(simple_auth) 경로 전용 — 사용자가 촬영한 신분증 업로드.

    OACX 초상(dlphotoimage)이 없는 이 경로에서는 신분증 사진 속 얼굴이 SFace 앵커를
    대신한다. 저장 대상은 마스킹 전체본(관리자 육안 심사용)이고, crop_id_face 는 얼굴이
    검출 가능한지 확인하는 게이트로만 쓴다 — 크롭 자체는 여기서 저장하지 않는다.
    """
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    settings: Settings = request.app.state.settings
    if "simple_auth" not in settings.fm_identity_methods:
        raise _err(
            "identity_method_unavailable", "지금은 이 방식으로 등록할 수 없어요.", status=409
        )
    if document_type not in facemarket_id_document.ID_DOCUMENT_TYPES:
        raise _err("invalid_document_type", "신분증 종류를 확인해 주세요.")
    if not masked_confirmed:
        raise _err("masking_required", "주민등록번호 뒷자리를 가린 뒤 올려 주세요.")
    mime = (file.content_type or "").lower()
    if mime not in facemarket_id_document.ALLOWED_ID_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
    data = await file.read()
    try:
        if not data:
            raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
        if len(data) > facemarket_id_document.MAX_ID_BYTES:
            raise _err("file_too_large", "이미지는 12MB 이하만 가능합니다.", status=413)
        # 얼굴이 안 잡히면 심사할 대상이 없다 — 저장하지 말고 재촬영을 요구한다.
        # crop_id_face 는 settings 가 keyword-only 라 to_thread 에도 키워드로 넘긴다.
        try:
            await asyncio.to_thread(
                facemarket_id_document.crop_id_face, data, settings=settings
            )
        except facemarket_id_document.IdDocumentError as exc:
            raise _err(exc.reason, "신분증 얼굴이 보이게 다시 찍어 주세요.")
        except QcFailed:
            raise _err(
                "qc_unavailable",
                "얼굴 검사를 지금 수행할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                status=503,
            )
        # 신분증 마스킹 기하 검증(shadow). 얼굴 게이트를 통과했고 아직 아무것도 저장하지
        # 않은 시점 — off 면 검사 자체를 건너뛴다. cv2.imdecode 는 이벤트 루프를 얼릴 수
        # 있는 동기 작업이라(2026-08-26 ALB 37초 장애 선례) crop_id_face 와 같은 방식으로
        # to_thread 에 위임한다. 판정은 enforce 여부와 무관하게 항상 로그로 남긴다 —
        # 임계 캘리브 근거가 그 로그뿐이다(off 는 예외 — 검사 자체를 안 하니 남길 것도 없다).
        #
        # mask_mode 는 이 판정을 그대로 영속화한다(Task9) — 클라이언트가 선언하는 값이
        # 아니다: 클라는 어느 경로를 탔는지 거짓말할 수 있지만 서버의 기하 판정은 그럴 수
        # 없다. off 라 판정 자체가 없으면 None(NULL) 을 그대로 둔다 — 'auto'는 검사 안
        # 한 걸 통과로, 'manual'은 통과 못 한 걸로 거짓 기록하는 셈이라 둘 다 안 된다.
        mask_mode: str | None = None
        if settings.fm_id_mask_verify != "off":
            mask_applied, mask_metrics = await asyncio.to_thread(
                facemarket_id_mask_verify.mask_is_applied, data
            )
            logger.info(
                "facemarket_id_mask_verify_verdict",
                extra={
                    "enrollment_id": enrollment_id,
                    "fm_id_mask_verify": settings.fm_id_mask_verify,
                    "mask_applied": mask_applied,
                    **mask_metrics,
                },
            )
            mask_mode = "auto" if mask_applied else "manual"
            if settings.fm_id_mask_verify == "enforce" and not mask_applied:
                raise _err(
                    "id_mask_not_applied",
                    "주민등록번호가 가려졌는지 확인해 주세요.",
                    status=422,
                )
        # 업로드 시도마다 새 키를 쓴다. 고정 키를 쓰면 동시/재시도 제출이 같은 객체를
        # 공유해, 늦게 실패한 요청의 rowcount==0 정리(delete)가 먼저 커밋된 요청의
        # 객체를 지워 버린다(리뷰 finding) — enrollment_quarantine_key 와 같은 이유다.
        key = enrollment_id_document_key(enrollment_id, "jpg", version=uuid.uuid4().hex)
        r2 = _r2_face(request)
        async with get_conn(request) as conn:
            await _assert_account_open(conn, user_id)
            try:
                await asyncio.to_thread(r2.put_bytes, key, data, mime)
            except Exception as exc:
                logger.warning(
                    "facemarket_enrollment_id_document_store_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "error_type": type(exc).__name__,
                    },
                )
                raise _err(
                    "storage_unavailable",
                    "얼굴 저장소를 사용할 수 없습니다.",
                    status=503,
                )
            async with conn.cursor() as cur:
                # Task6: 순서 뒤집기 이후 이 라우트에 오는 시점엔 본인확인이 이미 끝나
                # 있다(이름·생년월일·CI 확보됨) — 이 카드 사진은 이제 "정보를 읽는 대상"이
                # 아니라 "방금 인증된 그 사람 것인가"를 확인하는 물증이라, 성공하면 곧장
                # 사진 촬영(photos_pending)으로 넘어간다.
                #
                # `identity_ci_hash is not null` 은 불변조건이다 — 이 단계에 왔다면 본인확인은
                # 이미 끝나 있어야 한다. 새 순서로 만들어진 행은 전부 이 조건을 만족하므로
                # 정상 흐름에서는 이 가드가 절대 발동하지 않는다(no-op). 발동한다면 그건 순서
                # 뒤집기 배포 이전에 id_capture_pending 에서 멈춰 있던 행뿐이다 — 그런 행은
                # (구버전 순서에서) 본인확인을 아직 거치지 않았으므로 CI 교차계정 충돌 검사도
                # 안 거쳤고, 이대로 photos_pending 으로 보내면 identity_ci_hash 가 NULL 인 채로
                # 나중에 fm_identity_verifications 삽입이 NOT NULL 제약 위반으로 죽는다.
                await cur.execute(
                    """
                    update fm_biometric_enrollments
                    set status = 'photos_pending',
                        id_document_r2_key = %s, id_document_type = %s,
                        id_document_uploaded_at = now(), id_document_purged_at = null,
                        mask_mode = %s
                    where id = %s and user_id = %s and status = 'id_capture_pending'
                      and identity_ci_hash is not null
                    """,
                    (key, document_type, mask_mode, enrollment_id, user_id),
                )
                if cur.rowcount == 0:
                    # 이미 지나간 단계이거나 남의 등록이거나(오늘과 동일), 위 불변조건이
                    # 깨진 구버전 행(신규)이거나 — 어느 쪽이든 방금 올린 객체를 되돌린다.
                    await asyncio.to_thread(r2.delete, key)
                    await cur.execute(
                        "select status, identity_ci_hash from fm_biometric_enrollments "
                        "where id = %s and user_id = %s",
                        (enrollment_id, user_id),
                    )
                    guard_row = await cur.fetchone()
                    if (
                        guard_row is not None
                        and guard_row["status"] == "id_capture_pending"
                        and guard_row["identity_ci_hash"] is None
                    ):
                        raise _err(
                            "identity_not_verified",
                            "본인확인을 다시 진행한 뒤 신분증을 올려 주세요.",
                            status=409,
                        )
                    raise _err(
                        "invalid_enrollment_state",
                        "신분증을 올릴 수 있는 단계가 아니에요.",
                        status=409,
                    )
            await conn.commit()
            row = await _load_owned_enrollment(conn, enrollment_id, user_id)
            return await _enrollment_view(conn, row, settings)
    finally:
        data = b""


@router.post(
    "/enrollments/{enrollment_id}/profile-image",
    response_model=EnrollmentView,
    status_code=201,
)
async def upload_profile_image(
    request: Request,
    enrollment_id: str,
    image: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    # 대표이미지(cover)는 표시용일 뿐 컷 파이프라인·상태머신을 게이팅하지 않는다 —
    # SFace/QC 없음, 상태 전이 없음. 바인딩 시 fm_models.cover_image_url 로 승격만 한다.
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    mime = (image.content_type or "").lower()
    if mime not in ALLOWED_COVER_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    data = await image.read()
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
    if len(data) > MAX_FACE_BYTES:
        raise _err("file_too_large", f"이미지는 {MAX_FACE_MB}MB 이하만 가능합니다.", status=413)
    r2 = _r2_face(request)
    ext = ext_for_mime(mime)
    key = f"private/fm-profile/{enrollment_id}.{ext}"
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        r2.put_bytes(key, data, mime)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set profile_image_r2_key = %s "
                "where id = %s and user_id = %s",
                (key, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row, request.app.state.settings)


@router.post("/enrollments/{enrollment_id}/physique", response_model=EnrollmentView)
async def set_physique(
    request: Request,
    enrollment_id: str,
    body: PhysiqueBody,
    user_id: str = Depends(require_user),
):
    # 체형·키(physique)는 표시·프롬프트 문구용 메타데이터일 뿐 컷 파이프라인·상태머신을
    # 게이팅하지 않는다 — 검증은 값 자체(enum·성별 일치)만 본다(app.facemarket_physique 단일소스).
    from .facemarket_physique import PhysiqueError, validate_physique

    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        try:
            validate_physique(
                height_bucket=body.height_bucket,
                body_type=body.body_type,
                gender=row.get("model_gender"),
            )
        except PhysiqueError as e:
            raise _err(e.code, e.message, status=400)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set height_bucket = %s, body_type = %s "
                "where id = %s and user_id = %s",
                (body.height_bucket, body.body_type, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row, request.app.state.settings)


@router.post("/enrollments/{enrollment_id}/liveness-session", status_code=201)
async def start_enrollment_liveness(
    request: Request,
    enrollment_id: str,
    body: LivenessSessionBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    settings: Settings = request.app.state.settings
    if not settings.fm_liveness_enabled:
        # 라이브니스 off — 세션을 만들 필요가 없다(stale 프론트 방어). 프론트는 /config 로
        # livenessRequired=false 를 보고 이 호출을 건너뛰어야 한다.
        raise _err("liveness_disabled", "라이브 인증이 필요하지 않습니다.", status=409)
    nonce = body.nonce.strip()
    nonce_bytes = nonce.encode()
    if not 32 <= len(nonce_bytes) <= 512:
        raise _err("invalid_nonce", "인증 세션을 시작할 수 없습니다.")
    nonce_digest = hashlib.sha256(nonce_bytes).hexdigest()

    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.status, e.cooldown_until, e.liveness_nonce_digest,
                       e.liveness_session_digest, e.consent_version
                from fm_biometric_enrollments e
                where e.id = %s and e.user_id = %s
                for update
                """,
                (enrollment_id, user_id),
            )
            enrollment = await cur.fetchone()
            if enrollment is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            # status 만 검사한다 — 이미 세션을 한 번 발급받았어도(liveness_session_digest 존재)
            # 라이브니스 에러/취소 후 재시도를 허용해 신분증·사진 재입력 없이 라이브 인증만 다시
            # 진행하게 한다. 새 nonce 라 아래 nonce 재사용 검사는 여전히 새 세션을 강제한다.
            if enrollment["status"] != "liveness_pending":
                raise _err(
                    "invalid_enrollment_state",
                    "현재 등록 단계에서는 인증 세션을 시작할 수 없습니다.",
                    status=409,
                )
            cooldown_until = enrollment.get("cooldown_until")
            if cooldown_until is not None and cooldown_until > datetime.now(timezone.utc):
                raise _err(
                    "liveness_cooldown",
                    "잠시 후 생체 인증을 다시 시도해 주세요.",
                    status=429,
                )
            if enrollment.get("liveness_nonce_digest") == nonce_digest:
                raise _err(
                    "nonce_replayed",
                    "새 인증 세션으로 다시 시도해 주세요.",
                    status=409,
                )
            await cur.execute(
                """
                select exists(
                    select 1 from fm_biometric_enrollments
                    where liveness_nonce_digest = %s
                ) as replayed
                """,
                (nonce_digest,),
            )
            if (await cur.fetchone())["replayed"]:
                raise _err(
                    "nonce_replayed",
                    "새 인증 세션으로 다시 시도해 주세요.",
                    status=409,
                )
            required_slots = _required_photo_slots(settings, enrollment.get("consent_version"))
            if len(_ready_photo_rows(await _read_registration_photos(conn, enrollment_id), required_slots)) != len(required_slots):
                raise _err(
                    "photos_required",
                    "정면, 45도, 측면 사진을 모두 등록해 주세요.",
                    status=409,
                )
            await cur.execute(
                """
                update fm_biometric_enrollments
                set liveness_nonce_digest = %s
                where id = %s and user_id = %s and status = 'liveness_pending'
                """,
                (nonce_digest, enrollment_id, user_id),
            )

        try:
            session_id = await asyncio.to_thread(
                create_liveness_session,
                request.app.state.fm_rekognition,
                client_request_token=nonce_digest,
            )
            credentials = await asyncio.to_thread(
                assume_liveness_browser_credentials,
                request.app.state.fm_sts,
                role_arn=request.app.state.settings.fm_liveness_browser_role_arn,
                session_name=f"fm-live-{enrollment_id.replace('-', '')[:12]}",
            )
        except Exception as exc:
            await conn.commit()
            logger.warning(
                "facemarket_liveness_provider_unavailable",
                extra={"provider": "aws_liveness", "error_type": type(exc).__name__},
            )
            raise _err(
                "liveness_unavailable",
                "생체 인증을 지금 시작할 수 없습니다. "
                "잠시 후 다시 시도해 주세요.",
                status=503,
            )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set liveness_session_digest = %s,
                    provider_versions = provider_versions
                      || jsonb_build_object('faceLiveness', 'aws-rekognition-us-east-1')
                where id = %s and user_id = %s and status = 'liveness_pending'
                  and liveness_nonce_digest = %s
                """,
                (
                    hashlib.sha256(session_id.encode()).hexdigest(),
                    enrollment_id,
                    user_id,
                    nonce_digest,
                ),
            )
        await conn.commit()

    # 라이브 인증에 들어왔다 = 몇 분 뒤 VC 발급이다. holder 콜드부트를 지금 시작해 둔다.
    _prewarm_opendid(request)
    return {
        "sessionId": session_id,
        "region": "us-east-1",
        "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
        "credentials": credentials,
    }


@router.post("/enrollments/{enrollment_id}/reopen-photos", response_model=EnrollmentView)
async def reopen_enrollment_photos(
    request: Request, enrollment_id: str, user_id: str = Depends(require_user),
):
    """Reopen only unsigned photos, preserving previously verified identity.

    License creation locks this same enrollment row before checking/inserting a
    license. Do not lock a license here: issuance finalization locks it first.
    """
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, model_id::text as model_id, status, photo_revision, "
                "consent_version from fm_biometric_enrollments where id = %s and user_id = %s "
                "for update",
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            retry = row["photo_revision"] > 0 and row["status"] in {"photos_pending", "liveness_pending"}
            if row["status"] != "license_pending" and not retry:
                raise _err("invalid_enrollment_state", "현재 등록 단계에서는 사진을 고칠 수 없어요.", status=409)
            await cur.execute(
                "select exists(select 1 from fm_licenses where enrollment_id = %s) as has_license",
                (enrollment_id,),
            )
            if (await cur.fetchone())["has_license"]:
                raise _err("license_already_started", "증서 발급을 시작해서 사진을 고칠 수 없어요.", status=409)
            if not retry:
                required = _required_photo_slots(settings, row.get("consent_version"))
                ready = _ready_photo_rows(await _read_registration_photos(conn, enrollment_id), required)
                status = "liveness_pending" if len(ready) == len(required) else "photos_pending"
                await cur.execute(
                    "update fm_biometric_enrollments set status = %s, photo_revision = photo_revision + 1, "
                    "decision = null, reason = null, completed_at = null, expires_at = %s, "
                    "liveness_session_digest = null, liveness_nonce_digest = null "
                    "where id = %s and user_id = %s",
                    (status, datetime.now(timezone.utc) + ENROLLMENT_TTL, enrollment_id, user_id),
                )
                await cur.execute(
                    "update fm_models set assets_status = 'none', assets_source_hash = null "
                    "where id = %s and user_id = %s and current_enrollment_id = %s",
                    (row["model_id"], user_id, enrollment_id),
                )
        await conn.commit()
        return await _enrollment_view(conn, await _load_owned_enrollment(conn, enrollment_id, user_id), settings)


@router.get("/enrollments/{enrollment_id}/photos/{slot}")
async def get_enrollment_photo(
    request: Request,
    enrollment_id: str,
    slot: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    if slot not in ACCEPTED_PHOTO_SLOTS:
        raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        if await _load_owned_enrollment(conn, enrollment_id, user_id) is None:
            raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
        photo = None
        async with conn.cursor() as cur:
            for candidate in photo_slot_candidates(slot):
                await cur.execute(
                    "select r2_key, normalized_r2_key, mime_type, storage_state "
                    "from fm_biometric_enrollment_photos "
                    "where enrollment_id = %s and angle = %s", (enrollment_id, candidate),
                )
                photo = await cur.fetchone()
                if photo is not None:
                    break
    if photo is None or photo["storage_state"] not in {"quarantine", "approved"}:
        raise _err("not_found", "사진을 찾을 수 없습니다.", status=404)
    # 정규화본을 준다 — 원본이 HEIC 면 브라우저가 못 그려서 미리보기가 빈 칸이 된다.
    # 정규화본이 없는 옛 행은 원본으로 물러난다(그때는 전부 JPEG 였다).
    key, mime = _readable_photo(photo)
    try:
        data = await asyncio.to_thread(_r2_face(request).get_bytes, key)
    except Exception:
        raise _err("storage_unavailable", "사진을 불러올 수 없습니다.", status=503)
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, no-store"},
    )


@router.delete("/enrollments/{enrollment_id}/photos/{angle}", status_code=204)
async def delete_enrollment_photo(
    request: Request,
    enrollment_id: str,
    angle: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    if angle not in ACCEPTED_PHOTO_SLOTS:
        raise _err("invalid_slot", "사진 슬롯을 확인해 주세요.")
    candidates = photo_slot_candidates(angle)
    r2 = _r2_face(request)
    try:
        async with get_conn(request) as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                raise _err(
                    "photo_cleanup_pending",
                    "이전 사진 정리를 마친 뒤 다시 시도해 주세요.",
                    status=409,
                )
            try:
                await _assert_account_open(conn, user_id)
                await _lock_photo_mutation_enrollment(conn, enrollment_id, user_id)
                async with conn.cursor() as cur:
                  for angle in candidates:
                    await cur.execute(
                        """
                        select r2_key, storage_state
                        from fm_biometric_enrollment_photos
                        where enrollment_id = %s and angle = %s
                        """,
                        (enrollment_id, angle),
                    )
                    photo = await cur.fetchone()
                    if photo is not None:
                        if photo["storage_state"] not in {
                            "quarantine",
                            "delete_pending",
                            "approved",
                        }:
                            raise _err(
                                "invalid_enrollment_state",
                                "현재 등록 단계에서는 사진을 변경할 수 없습니다.",
                                status=409,
                            )
                        await cur.execute(
                            """
                            update fm_biometric_enrollment_photos
                            set storage_state = 'delete_pending'
                            where enrollment_id = %s and angle = %s and r2_key = %s
                            """,
                            (enrollment_id, angle, photo["r2_key"]),
                        )
                        await cur.execute(
                            """
                            insert into fm_biometric_enrollment_photo_cleanup
                                (enrollment_id, angle, r2_key, reason)
                            values (%s, %s, %s, 'delete')
                            on conflict (enrollment_id, r2_key) do update
                            set reason = 'delete'
                            """,
                            (enrollment_id, angle, photo["r2_key"]),
                        )
                        await cur.execute(
                            """
                            update fm_biometric_enrollments set status = 'photos_pending'
                            where id = %s and user_id = %s
                              and status = 'liveness_pending'
                              and liveness_session_digest is null
                            """,
                            (enrollment_id, user_id),
                        )
                await conn.commit()
                failed_count = 0
                for angle in candidates:
                    _, failures = await _drain_photo_cleanup_locked(
                        conn, r2, enrollment_id=enrollment_id, angle=angle,
                    )
                    failed_count += failures
                if failed_count:
                    raise _err(
                        "storage_unavailable",
                        "얼굴 저장소를 사용할 수 없습니다.",
                        status=503,
                    )
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_photo_delete_prepare_failed",
            extra={
                "enrollment_id": enrollment_id,
                "angle": angle,
                "error_type": type(exc).__name__,
            },
        )
        raise _err(
            "db_unavailable",
            "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
            status=503,
        )
    return Response(status_code=204)


async def cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool:
    pool = getattr(app.state, "pool", None)
    r2 = getattr(app.state, "r2_face", None)
    if pool is None or r2 is None:
        return False

    try:
        enrollment_id = _canonical_enrollment_id(enrollment_id)
        async with pool.connection() as conn:
            if not await _try_photo_fence(conn, enrollment_id):
                return False
            try:
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select e.status, p.angle, p.r2_key, p.storage_state,
                                   e.model_id::text as model_id,
                                   not exists (select 1 from fm_licenses l where l.enrollment_id = e.id) as unsigned
                            from fm_biometric_enrollments e
                            left join fm_biometric_enrollment_photos p
                              on p.enrollment_id = e.id
                             and (p.storage_state in ('quarantine', 'delete_pending')
                                  or (p.storage_state = 'approved' and not exists (
                                      select 1 from fm_licenses l where l.enrollment_id = e.id)))
                            where e.id = %s
                              and e.status in ('failed', 'cancelled', 'expired')
                            for update of e
                            """,
                            (enrollment_id,),
                        )
                        rows = await cur.fetchall()
                        if not rows:
                            return False
                        model_id = rows[0].get("model_id")
                        if model_id and rows[0]["unsigned"]:
                            # A cancelled worker can still be writing. Its session fence
                            # must be free before detaching references or certifying deletion.
                            await cur.execute(
                                "select pg_try_advisory_xact_lock(%s, hashtext(%s)) as locked",
                                (_MODEL_ASSET_FENCE_NAMESPACE, model_id.lower()),
                            )
                            if not (await cur.fetchone())["locked"]:
                                return False
                            await cur.execute(
                                "select view, r2_key from fm_model_assets where source_enrollment_id = %s",
                                (enrollment_id,),
                            )
                            for asset in await cur.fetchall():
                                await cur.execute(
                                    "insert into fm_biometric_enrollment_photo_cleanup "
                                    "(enrollment_id, angle, r2_key, reason) values (%s, %s, %s, 'delete') "
                                    "on conflict (enrollment_id, r2_key) do update set reason = 'delete'",
                                    (enrollment_id, "face01", asset["r2_key"]),
                                )
                            await cur.execute(
                                "delete from fm_model_assets where source_enrollment_id = %s",
                                (enrollment_id,),
                            )
                            await cur.execute(
                                "update fm_models set assets_status = 'none', assets_source_hash = null "
                                "where id = %s and current_enrollment_id = %s "
                                "and status in ('pending', 'reverification_required')",
                                (model_id, enrollment_id),
                            )
                        for row in rows:
                            if row.get("r2_key") is None:
                                continue
                            await cur.execute(
                                """
                                update fm_biometric_enrollment_photos
                                set storage_state = 'delete_pending'
                                where enrollment_id = %s and angle = %s and r2_key = %s
                                """,
                                (enrollment_id, row["angle"], row["r2_key"]),
                            )
                            await cur.execute(
                                """
                                insert into fm_biometric_enrollment_photo_cleanup
                                    (enrollment_id, angle, r2_key, reason)
                                values (%s, %s, %s, 'delete')
                                on conflict (enrollment_id, r2_key) do update
                                set reason = 'delete'
                                """,
                                (enrollment_id, row["angle"], row["r2_key"]),
                            )
                    await conn.commit()
                except Exception as exc:
                    await conn.rollback()
                    logger.warning(
                        "facemarket_enrollment_cleanup_prepare_failed",
                        extra={
                            "enrollment_id": enrollment_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return False

                # Task9: 신분증 촬영본 파기 안전망 3겹 중 2번째 겹 — 취소(cancel_enrollment)·
                # 실패(_fail_enrollment)·만료(EnrollmentExpiredError 즉시 호출 + 이 함수를
                # 재방문하는 sweep_terminal_enrollments 의 candidates 재조회)가 전부 이
                # 함수를 거치므로 여기 한 곳에 걸면 세 경로를 동시에 커버한다(1번째 겹은
                # admin approve/reject 의 즉시 파기, 3번째 겹은 dispatcher.py 의 7일 스윕).
                # mid 경로는
                # id_document_r2_key 가 애초에 null 이라 delete 를 안 타는 무해한 no-op —
                # id_document_purged_at 은 API 응답(EnrollmentView)에 노출되지 않는 내부
                # 감사 컬럼이라 mid 의 관측 가능한 photo cleanup·expiry 동작은 그대로다.
                # 실패해도 사진 정리(remaining 카운트)는 계속 진행한다 — 7일 배치 스윕이 상한.
                try:
                    await facemarket_id_document.purge_id_document(r2, conn, enrollment_id)
                    await conn.commit()
                except Exception as exc:
                    await conn.rollback()
                    logger.warning(
                        "facemarket_enrollment_id_document_purge_failed",
                        extra={
                            "enrollment_id": enrollment_id,
                            "error_type": type(exc).__name__,
                        },
                    )

                deleted_count, failed_count = await _drain_photo_cleanup_locked(
                    conn,
                    r2,
                    enrollment_id=enrollment_id,
                )
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            select (
                                select count(*) from fm_biometric_enrollment_photos p
                                where p.enrollment_id = %s
                                  and (p.storage_state in ('quarantine', 'delete_pending')
                                       or (p.storage_state = 'approved' and not exists (
                                           select 1 from fm_licenses l where l.enrollment_id = p.enrollment_id)))
                            ) + (
                                select count(*) from fm_biometric_enrollment_photo_cleanup
                                where enrollment_id = %s
                            ) as remaining
                            """,
                            (enrollment_id, enrollment_id),
                        )
                        remaining = (await cur.fetchone())["remaining"]
                        await cur.execute(
                            """
                            update fm_biometric_enrollments
                            set raw_deletion_evidence = coalesce(raw_deletion_evidence, '{}'::jsonb)
                                || jsonb_build_object(
                                    'quarantineDeleted', %s,
                                    'quarantineDeletedCount',
                                        coalesce((raw_deletion_evidence->>'quarantineDeletedCount')::int, 0) + %s,
                                    'quarantineDeleteFailedCount',
                                        coalesce((raw_deletion_evidence->>'quarantineDeleteFailedCount')::int, 0) + %s,
                                    'quarantineCleanupAt', now()
                                )
                            where id = %s and status in ('failed', 'cancelled', 'expired')
                            """,
                            (remaining == 0, deleted_count, failed_count, enrollment_id),
                        )
                    await conn.commit()
                except Exception as exc:
                    await conn.rollback()
                    logger.warning(
                        "facemarket_enrollment_cleanup_evidence_failed",
                        extra={
                            "enrollment_id": enrollment_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return False
                return remaining == 0
            finally:
                await _unlock_photo_fence(conn, enrollment_id)
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cleanup_prepare_failed",
            extra={
                "enrollment_id": enrollment_id,
                "error_type": type(exc).__name__,
            },
        )
        return False


class _AppRequest:
    """`app` 만 들고 `request` 처럼 구는 얇은 대역.

    `_dispatch_decision_email`/`get_conn` 은 `request.app.state` 밖을 보지 않는다. 스윕은
    요청 컨텍스트가 없으므로 그 한 가지만 채워 같은 메일 경로(발송 원장 포함)를 그대로
    재사용한다 — 통지 경로를 두 벌 만들면 한쪽만 고쳐지는 날이 온다."""

    __slots__ = ("app",)

    def __init__(self, app):
        self.app = app


async def notify_enrollment_decision(
    app, *, enrollment_id: str, email_type: str, reject_reason: str | None = None
) -> bool:
    """등록 심사 결과를 지원서 연락처로 메일 통지한다(best-effort).

    심사 대기 화면이 "결과는 메일로 알려 드려요" 라고 약속하는데 실제로는 아무것도 보내지
    않았다 — 그 화면은 폴링도 하지 않으므로 사용자에게 **어떤 통지 경로도 없었다**
    (최종리뷰 I2). 연락처는 지원서에만 있으므로 지원서가 없으면(fm_application_required
    off) 보낼 곳이 없다 — 조용히 건너뛰되 그 사실을 로그로 남긴다.

    반환값: 실제로 발송을 시도했으면 True. 결정 자체는 이미 커밋됐으므로 절대 예외를
    올리지 않는다(지원서 승인·거절 메일과 같은 규율).
    """
    try:
        request = _AppRequest(app)
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select a.id::text as application_id, a.contact_email as contact_email
                    from fm_biometric_enrollments e
                    join fm_model_applications a on a.id = e.application_id
                    where e.id = %s
                    """,
                    (enrollment_id,),
                )
                row = await cur.fetchone()
        if not row or not row.get("contact_email"):
            logger.info(
                "enrollment_decision_email_skipped enrollment=%s type=%s reason=no_contact",
                enrollment_id, email_type,
            )
            return False
        await _dispatch_decision_email(
            request,
            application_id=row["application_id"],
            to=row["contact_email"],
            email_type=email_type,
            reject_reason=reject_reason,
        )
        return True
    except Exception:
        logger.warning(
            "enrollment_decision_email_failed enrollment=%s type=%s",
            enrollment_id, email_type, exc_info=True,
        )
        return False


async def sweep_terminal_enrollments(app, *, limit: int = 100) -> int:
    pool = getattr(app.state, "pool", None)
    if pool is None:
        return 0
    limit = max(1, min(int(limit), 100))
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    with due as (
                        select id from fm_biometric_enrollments
                        where expires_at <= now()
                          -- Task5: id_capture_pending(신분증 촬영 대기)은 나머지와 같은 본인인증
                          -- 진행 단계라 같은 24h TTL 로 만료시킨다. review_pending 은 일부러
                          -- 뺀다 — 그건 사람 심사원을 기다리는 단계라, 같은 24h 로 자동만료시키면
                          -- 심사가 밀린 정상 지원자가 자동 탈락한다. 대신 심사 액션(승인/반려,
                          -- Task7/8 소관)이 명시적으로 빠져나가게 한다 — review_pending 이 무기한
                          -- 방치될 위험은 심사팀 SLA/전용 타임아웃으로 다뤄야 할 별개 과제다.
                          and status in (
                            'id_capture_pending', 'identity_pending', 'photos_pending',
                            'liveness_pending', 'processing'
                          )
                        order by expires_at
                        for update skip locked
                        limit %s
                    )
                    update fm_biometric_enrollments e
                    set status='expired', decision='failed',
                        reason='enrollment_expired', completed_at=now()
                    from due where e.id=due.id
                    returning e.id::text as id
                    """,
                    (limit,),
                )
                await cur.fetchall()
            await conn.commit()

        # 심사 기한(REVIEW_DEADLINE_DAYS) 초과분을 닫는다. 위 만료 스윕이 review_pending 을
        # 일부러 빼 두는 건 "심사가 밀렸다고 정상 지원자를 24시간 만에 탈락시키지 않는다"는
        # 뜻이지 "영원히 기다린다"는 뜻이 아니다 — 신분증 촬영본은 7일이면 배치 스윕이
        # 지우므로 그 뒤엔 심사 자체가 불가능하고, 행은 사용자의 단일 활성 슬롯을 계속
        # 점유한다(최종리뷰 I3). review_status 도 비워 관리자 대기 큐에서 내린다.
        # 아래 정리 쿼리가 같은 tick 에 이 행들을 집어 사진·신분증을 파기한다.
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    with due as (
                        select id from fm_biometric_enrollments
                        where status = 'review_pending' and review_status = 'pending'
                          and created_at <= now() - interval '{REVIEW_DEADLINE_DAYS} days'
                        order by created_at
                        for update skip locked
                        limit %s
                    )
                    update fm_biometric_enrollments e
                    set status='failed', decision='failed', reason='review_timeout',
                        review_status=null, completed_at=now()
                    from due where e.id=due.id
                    returning e.id::text as id
                    """,
                    (limit,),
                )
                timed_out = await cur.fetchall()
            await conn.commit()
        for row in timed_out:
            logger.warning("enrollment_review_timed_out enrollment=%s", row["id"])
            await notify_enrollment_decision(
                app, enrollment_id=row["id"], email_type="enrollment_review_timeout"
            )

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.status in ('failed', 'cancelled', 'expired')
                      and coalesce(
                            (e.raw_deletion_evidence->>'quarantineDeleted')::boolean,
                            false
                          ) is not true
                    order by e.completed_at nulls first, e.created_at
                    for update skip locked
                    limit %s
                    """,
                    (limit,),
                )
                candidates = await cur.fetchall()
            await conn.commit()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.status = 'license_pending'
                      and exists (
                          select 1
                          from fm_biometric_enrollment_photo_cleanup c
                          where c.enrollment_id = e.id
                            and c.not_before <= now()
                      )
                    order by e.id
                    for update skip locked
                    limit %s
                    """,
                    (limit,),
                )
                cleanup_candidates = await cur.fetchall()
            await conn.commit()
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cleanup_sweep_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0

    cleaned = 0
    for row in candidates:
        cleaned += int(
            await cleanup_terminal_enrollment(app, enrollment_id=row["id"])
        )
    for row in cleanup_candidates:
        deleted, failed = await _drain_photo_cleanup(
            app, enrollment_id=row["id"], reason="delete"
        )
        cleaned += int(deleted > 0 and failed == 0)
    cleaned += await _drain_model_asset_cleanup(app, limit=limit)
    return cleaned


def _decision_body(decision: EnrollmentDecision) -> dict:
    body = {
        "passed": decision.passed,
        "retryable": decision.retryable,
        "reason": decision.reason,
        "status": decision.status,
    }
    if decision.model_id is not None:
        body["modelId"] = decision.model_id
    return body


def _coerce_match_score(score) -> float | None:
    """score 를 유한한 float 로 정규화한다. 변환 불가·비유한이면 None(사실상 최저점)."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _is_below_threshold(score, threshold: float) -> bool:
    """미달 판정의 단일 정의 — `_assert_match`(경로 M, enforce)와 매칭 루프의 advisory
    기록(경로 S)이 같은 기준으로 "미달"을 판정하게 한다. 여기가 갈라지면 mid 의 enforce
    임계와 simple_auth 의 belowThreshold 기록이 조용히 어긋난다."""
    value = _coerce_match_score(score)
    return value is None or value < threshold


def _assert_match(score: float, threshold: float) -> None:
    if _is_below_threshold(score, threshold):
        raise EnrollmentMappedError("face_match_failed")


async def record_raw_release_evidence(
    request: Request, enrollment_id: str, **evidence: bool
) -> None:
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_biometric_enrollments
                set raw_deletion_evidence = coalesce(raw_deletion_evidence, '{}'::jsonb)
                  || jsonb_build_object(
                    'oacxPortraitReleased', %s,
                    'livenessReferenceReleased', %s,
                    'temporaryEmbeddingsReleased', %s
                  )
                where id = %s
                """,
                (
                    evidence.get("oacx_portrait_released", False),
                    evidence.get("liveness_reference_released", False),
                    evidence.get("temporary_embeddings_released", False),
                    enrollment_id,
                ),
            )
        await conn.commit()


async def _fail_enrollment(
    request: Request,
    *,
    enrollment_id: str,
    reason: str,
    retryable: bool,
    expected_status: str,
) -> EnrollmentDecision:
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            cooldown_until = None
            if not retryable:
                await cur.execute(
                    """
                    select count(*) as recent_failures
                    from fm_biometric_enrollments current
                    join fm_biometric_enrollments prior
                      on (prior.user_id = current.user_id
                          or prior.device_digest = current.device_digest)
                    where current.id = %s
                      and prior.status = 'failed'
                      and prior.completed_at >= now() - interval '3 minutes'
                      and prior.reason in ('minor_blocked', 'liveness_failed',
                                           'face_match_failed', 'identity_replay',
                                           'identity_recovery_required')
                    """,
                    (enrollment_id,),
                )
                recent = int((await cur.fetchone() or {}).get("recent_failures") or 0)
                if recent + 1 >= 5:
                    cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=45)
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'failed', decision = 'failed', reason = %s,
                    completed_at = coalesce(completed_at, now()),
                    cooldown_until = coalesce(%s, cooldown_until)
                where id = %s and status = %s
                returning status
                """,
                (reason, cooldown_until, enrollment_id, expected_status),
            )
            updated = await cur.fetchone()
            if updated is None:
                await cur.execute(
                    "select status from fm_biometric_enrollments where id = %s",
                    (enrollment_id,),
                )
                closed = await cur.fetchone()
                await conn.commit()
                return EnrollmentDecision(
                    False,
                    retryable,
                    reason,
                    (closed or {}).get("status") or "failed",
                )
        await conn.commit()
    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    return EnrollmentDecision(False, retryable, reason, "failed")


async def _initial_completion_checks(
    request: Request, *, enrollment_id: str, user_id: str, session_id: str | None
) -> tuple[dict, list[dict]]:
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.id::text as id, e.user_id::text as user_id,
                       e.model_id::text as model_id, e.status, e.cooldown_until,
                       e.expires_at, e.liveness_session_digest, e.device_digest,
                       e.identity_ci_hash, e.identity_name_masked, e.identity_birth_year,
                       e.identity_tx_digest, e.identity_contract_version,
                       e.profile_image_r2_key, e.height_bucket, e.body_type,
                       e.photo_revision,
                       -- 경로 분기(mid/simple_auth)와 간편인증 앵커의 출처. dict_row 라
                       -- 여기 없는 컬럼은 row 에 아예 없다 — process_enrollment_completion
                       -- 이 row.get("identity_method") 로 읽는 값이 항상 None 이 되어
                       -- 간편인증 완료 경로 전체가 mid 로 오폴백한다(최종리뷰 C1).
                       -- 컬럼 추가·삭제는 test_completion_select_projects_every_column_read
                       -- 가 잡는다(읽는 쪽 소스를 스캔해 이 select 목록과 대조).
                       e.identity_method, e.id_document_r2_key
                from fm_biometric_enrollments e
                where e.id = %s and e.user_id = %s
                for update
                """,
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if row["status"] != "liveness_pending":
                raise _err(
                    "invalid_enrollment_state",
                    "현재 등록 단계에서는 인증을 완료할 수 없습니다.",
                    status=409,
                )
            # Task3 방어선: 신분증 게이트(/identity)를 통과한 등록만 완료할 수 있다.
            # identity_tx_digest 가 없다면 앞단 CI 증거가 저장된 적이 없다는 뜻 → 완료 불가.
            if not row.get("identity_tx_digest"):
                raise _err(
                    "invalid_enrollment_state",
                    "본인확인을 먼저 완료해 주세요.",
                    status=409,
                )
            if row.get("photo_revision", 0) > 0:
                await cur.execute(
                    "select exists(select 1 from fm_identity_verifications "
                    "where model_id = %s and cx_tx_id = %s and cx_tx_id_format = 'sha256-v1') as identity_recorded",
                    (row["model_id"], row["identity_tx_digest"]),
                )
                if not (await cur.fetchone())["identity_recorded"]:
                    raise _err("identity_recovery_required", "기존 본인확인 기록을 확인할 수 없어요.", status=409)
            if row.get("cooldown_until") and row["cooldown_until"] > datetime.now(timezone.utc):
                raise _err("liveness_cooldown", "잠시 후 다시 시도해 주세요.", status=429)
            if row["expires_at"] <= datetime.now(timezone.utc):
                await cur.execute(
                    """
                    update fm_biometric_enrollments
                    set status = 'expired', decision = 'failed',
                        reason = 'enrollment_expired',
                        completed_at = coalesce(completed_at, now())
                    where id = %s
                    """,
                    (enrollment_id,),
                )
                await conn.commit()
                raise EnrollmentExpiredError
            if settings.fm_liveness_enabled and (
                row["liveness_session_digest"] != hashlib.sha256(session_id.encode()).hexdigest()
            ):
                await conn.commit()
                raise EnrollmentMappedError("liveness_retry")
            await cur.execute(
                """
                select angle, r2_key, mime_type, qc_status, storage_state
                from fm_biometric_enrollment_photos
                where enrollment_id = %s
                  and angle = any(%s)
                order by array_position(%s, angle)
                """,
                (
                    enrollment_id,
                    list(ACCEPTED_PHOTO_SLOTS),
                    list(ACCEPTED_PHOTO_SLOTS),
                ),
            )
            required_slots = _required_photo_slots(settings, row.get("consent_version"))
            photos = _ready_photo_rows(
                await cur.fetchall(), required_slots,
                allow_approved=row.get("photo_revision", 0) > 0,
            )
            if len(photos) != len(required_slots):
                raise _err("photos_required", "필수 사진을 모두 등록해 주세요.", status=409)
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'processing'
                where id = %s and user_id = %s and status = 'liveness_pending'
                """,
                (enrollment_id, user_id),
            )
        await conn.commit()
    return row, photos


async def process_enrollment_completion(
    request: Request,
    *,
    enrollment_id: str,
    user_id: str,
    session_id: str,
    id_photo_hex: str | None = None,
) -> EnrollmentDecision:
    settings = request.app.state.settings
    liveness = None
    portrait: bytearray | None = None
    id_document_buffer: bytearray | None = None
    photo_buffers: list[bytearray] = []
    processing_started = False
    try:
        row, photos = await _initial_completion_checks(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
        )
        processing_started = True
        if settings.fm_liveness_enabled:
            try:
                liveness = await asyncio.to_thread(
                    get_liveness_result,
                    request.app.state.fm_rekognition,
                    session_id=session_id,
                    minimum_confidence=settings.fm_liveness_confidence_threshold,
                )
            except BiometricProviderError as exc:
                raise EnrollmentMappedError(exc.reason) from None

        method = row.get("identity_method") or "mid"
        # 얼굴 매칭이 꺼져 있으면(main 기본값) 점수가 없다 — 심사 카드에 "매칭 안 함"을
        # 명시적으로 남긴다. 빈 dict 를 넘겨 놓고 나중에 KeyError 로 터지게 두지 않는다.
        match_snapshot: dict = {
            "policyVersion": settings.fm_match_policy_version,
            "anchor": "id_document_crop" if method == "simple_auth" else "oacx_portrait",
            "faceMatch": "disabled",
        }
        if settings.fm_face_match_enabled:
          try:
            if method == "simple_auth":
                # 경로 S(간편인증): OACX 는 초상(dlphotoimage)을 안 준다 — 앵커는 사용자가
                # 업로드한 마스킹 신분증 사진에서 얼굴만 잘라낸 바이트다. 이 앵커는 사용자
                # 본인이 촬영한 것이라 위조 가능 — 아래 매칭 루프는 advisory 로 돈다(enforce
                # 아님, WHY 는 매칭 루프 주석 참조).
                key = row.get("id_document_r2_key")
                if not key:
                    raise EnrollmentMappedError("id_portrait_unavailable")
                r2_document = _r2_face(request)
                id_document_buffer = bytearray(
                    await asyncio.to_thread(r2_document.get_bytes, key)
                )
                try:
                    portrait = await asyncio.to_thread(
                        facemarket_id_document.crop_id_face,
                        id_document_buffer,
                        settings=settings,
                    )
                except facemarket_id_document.IdDocumentError as exc:
                    raise EnrollmentMappedError(exc.reason) from None
                except QcFailed:
                    # (MINOR7) crop_id_face 내부 load_face_qc/detect_largest_face 가 인프라
                    # 문제로 실패하면(가중치 부재 등) IdDocumentError 가 아니라 맨 QcFailed 를
                    # 던진다 — 아래 일반 except Exception 이 삼키면 on-call 이
                    # id_portrait_unavailable(재촬영 문제)로 오인한다. 사용자 재시도
                    # 가능성(RETRYABLE_REASONS)은 둘 다 같지만 사유는 정확해야 한다.
                    raise EnrollmentMappedError("qc_unavailable") from None
            else:
                # Task3: CI·이름·생년월일 검증은 앞단 /identity 가 이미 마쳤다(저장 컬럼을 아래에서
                # 읽는다). 여기서는 SFace 매칭에 쓸 신분증 초상만 파싱한다 — trans 재조회 없음.
                contract = cx_identity.get_oacx_biometric_contract(settings)
                # 초상은 D1부터 trans 필드가 아니라 클라가 OACX RESULT-step(`data.dlphotoimage`)
                # 콜백에서 그대로 릴레이한 HEX 다 — cx_identity.parse_oacx_portrait_hex 의
                # 모듈 docstring 에 이 릴레이의 보안 경계(client-relayed, bounded)를 기록해 두었다.
                portrait = cx_identity.parse_oacx_portrait_hex(id_photo_hex, contract=contract)
          except EnrollmentMappedError:
              raise
          except cx_identity.OacxBiometricError as exc:
              raise EnrollmentMappedError(exc.reason) from None
          except Exception:
              raise EnrollmentMappedError("id_portrait_unavailable") from None

        r2 = _r2_face(request)
        photo_items: list[tuple[str, bytearray]] = []  # (angle, buffer)
        for photo in photos:
            try:
                buffer = bytearray(await asyncio.to_thread(r2.get_bytes, photo["r2_key"]))
            except Exception:
                raise EnrollmentMappedError("id_portrait_unavailable") from None
            photo_items.append((photo["angle"], buffer))
            photo_buffers.append(buffer)  # 아래 finally 에서 일괄 wipe

        # 기준 3장끼리의 합의도 — 기록만 한다(막지 않는다). 기록은 아래에서 행을 잠근 뒤에 쓴다.
        refset = await asyncio.to_thread(refset_agreement, settings, photo_items)
        logger.info("fm_refset_agreement enrollment=%s %s", enrollment_id, refset)

        if settings.fm_face_match_enabled:
          try:
            qc = load_face_qc(settings, required=True)
            # 매칭 앵커: 라이브니스 on 이면 라이브 프레임, off 면 신분증 초상.
            #  - on: 신분증 초상 ↔ 라이브(신원 앵커, 차단) + 업로드 사진 ↔ 라이브(스왑 방지).
            #  - off: 업로드 사진 ↔ 신분증 초상. 신분증 초상이 앵커 = OACX 모바일신분증(실시간 폰
            #    인증)으로 실명검증된 본인. id↔live 는 라이브 프레임이 없으니 생략(신분증이 곧 앵커).
            match_anchor = liveness.reference_image if settings.fm_liveness_enabled else portrait
            if settings.fm_liveness_enabled:
                # 신원 앵커: 신분증 초상 ↔ 라이브 프레임(둘 다 정면 → SFace 유효). 차단.
                id_live_score = qc.one_to_one_similarity(portrait, match_anchor)
                # score 는 float 이거나 None(검출 실패) — %s 로 로깅해 None 도 안전하게 찍고,
                # None 판정(fail-closed)은 _assert_match 가 face_match_failed 로 처리한다.
                logger.info(
                    "fm_match_id_live score=%s threshold=%.4f",
                    id_live_score, settings.fm_id_live_threshold,
                )
                _assert_match(id_live_score, settings.fm_id_live_threshold)
            # 업로드 사진 ↔ 앵커: 정면 얼굴 인식기(YuNet 검출 + SFace)는 측면·프로필을
            # 신뢰성 있게 다루지 못한다 — 옆모습은 검출(YuNet) 자체가 실패한다. 그래서 정면 얼굴이
            # 잡히는 사진만 매칭해 "모델 사진 = 검증된 본인"을 확인하고, 검출 불가한 각도
            # (45/측면)는 자산용 앵글 소스로만 취급해 건너뛴다.
            #
            # 경로 M(mid): 앵커가 정부 서명 VC 초상이다 — 임계 미달은 "본인이 아니다"라는
            # 신뢰할 수 있는 신호라 지금처럼 즉시 차단한다(enforce). 검출된 사진은 모두
            # 매칭돼야 하고 최소 1장은 매칭돼야 한다(스왑 방지). **불변**.
            # 경로 S(simple_auth): 앵커가 사용자가 손에 들고 촬영한 신분증이다 — 위조 가능해서
            # 점수가 높아도 진짜라는 보장이 안 되고, 촬영 각도·조명·코팅 반사 때문에 낮아도
            # 본인이 아니라는 보장이 안 된다. 기계가 어느 방향으로도 신뢰 판정을 못 내리므로
            # 점수는 기록만 하고(advisory) 사람이 심사한다. 예외: 세 각도 전부 얼굴 미검출이면
            # 심사할 근거 자체가 없으므로 그때는 막는다(재촬영 유도).
            advisory = method == "simple_auth"
            scores: dict[str, float] = {}
            below: list[str] = []
            skipped: list[str] = []
            matched_any = False
            for _angle, buffer in photo_items:
                try:
                    score = qc.one_to_one_similarity(buffer, match_anchor)
                except QcFailed as exc:
                    if exc.reason == "no_face_detected":
                        skipped.append(_angle)  # 정면 검출기가 못 잡는 각도 — 매칭 대상 아님
                        continue
                    # (MINOR4) reason 이 "no_face_detected" 가 아닌 QcFailed(예:
                    # embedding_invalid)는 advisory 라도 완료 전체를 중단시킨다 — 브리프의
                    # 차단 예외("세 각도 전부 미검출")보다 넓지만, mid 의 기존 동작과 같은
                    # 선이라 의도적으로 바꾸지 않는다. 다음 사람이 놓친 게 아니라 알고
                    # 있다는 것만 남긴다.
                    raise
                threshold = match_threshold_for_angle(settings, _angle)
                logger.info(
                    "fm_match_photo_anchor angle=%s score=%s threshold=%.4f advisory=%s",
                    _angle, score, threshold, advisory,
                )
                if _is_below_threshold(score, threshold):
                    below.append(_angle)
                    if not advisory:
                        _assert_match(score, threshold)
                else:
                    matched_any = True
                numeric_score = _coerce_match_score(score)
                if numeric_score is not None:
                    scores[_angle] = numeric_score
            if not advisory and not matched_any:
                raise EnrollmentMappedError("face_match_failed")
            if advisory and not scores:
                # 세 각도 전부 얼굴 미검출(혹은 무효 점수) = 심사할 근거가 없다.
                raise EnrollmentMappedError("face_match_failed")
            match_snapshot = {
                # raw 코사인을 그대로 저장한다. 백분율 변환은 표시층에서만 한다 — 임계
                # 재캘리브·사후 분석이 원본을 요구하고, 표시 형식이 바뀐다고 저장 값이
                # 흔들리면 안 된다.
                "policyVersion": settings.fm_match_policy_version,
                "anchor": "id_document_crop" if advisory else "oacx_portrait",
                # 실제로 매칭을 시도한 각도만 적는다 — 슬롯 구성이 3각도에서 18장으로
                # 바뀌었으므로(main) 상수 목록을 박아 두면 스냅샷이 거짓이 된다.
                "thresholds": {
                    angle: match_threshold_for_angle(settings, angle)
                    for angle, _ in photo_items
                },
                "scores": scores,
                "belowThreshold": below,
                "skipped": skipped,
                "computedAt": datetime.now(timezone.utc).isoformat(),
            }
          except EnrollmentMappedError:
              raise
          except QcFailed as exc:
              reason = exc.reason if exc.reason == "qc_unavailable" else "face_match_failed"
              raise EnrollmentMappedError(reason) from None
          except Exception:
              raise EnrollmentMappedError("qc_unavailable") from None

        match_required_review = review_required(settings, method)

        # Task3: 바인딩 증거(ci_hash 등)는 앞단 /identity 가 fm_biometric_enrollments 에 저장한
        # 값을 읽는다 — bind_model_and_enqueue_asset_build 가 row 에서 직접 꺼내 쓴다.
        identity_contract_version = row["identity_contract_version"]
        async with get_conn(request) as conn:
            await _assert_account_open(conn, user_id)
            await _reject_cutover_closed(conn)
            async with conn.cursor() as cur:
                # 라이브니스 off 면 세션 다이제스트가 없다(NULL) — 예측어를 빼야 잠금이 걸린다.
                if settings.fm_liveness_enabled:
                    _digest_predicate = "and e.liveness_session_digest = %s"
                    _lock_params = (
                        enrollment_id,
                        user_id,
                        hashlib.sha256(session_id.encode()).hexdigest(),
                    )
                else:
                    _digest_predicate = ""
                    _lock_params = (enrollment_id, user_id)
                await cur.execute(
                    f"""
                    select e.id::text as id
                    from fm_biometric_enrollments e
                    where e.id = %s and e.user_id = %s
                      and e.status = 'processing'
                      {_digest_predicate}
                    for update
                    """,
                    _lock_params,
                )
                if await cur.fetchone() is None:
                    raise _err(
                        "invalid_enrollment_state",
                        "현재 등록 단계에서는 인증을 완료할 수 없습니다.",
                        status=409,
                    )
                # 기준 3장 합의도를 남긴다. 통과/실패 어느 쪽으로도 쓰이지 않는다 — 문턱이
                # 실사용자 분포에 맞는지 보려는 기록이다(차단 여부는 그다음에 정한다).
                await cur.execute(
                    "update fm_biometric_enrollments "
                    "set provider_versions = provider_versions || %s::jsonb where id = %s",
                    (Json({"refset": refset}), enrollment_id),
                )
                if match_required_review:
                    # 심사 대기: 모델 바인딩·자산빌드를 시작하지 않는다 — 심사 안 된 얼굴이
                    # 생성 파이프라인에 들어가면 안 된다. 점수는 사람이 볼 정보로만 남긴다.
                    await cur.execute(
                        """
                        update fm_biometric_enrollments
                        set status = 'review_pending', review_status = 'pending',
                            match_scores = %s
                        where id = %s and status = 'processing'
                        """,
                        (Json(match_snapshot), enrollment_id),
                    )
                    await conn.commit()
                    return EnrollmentDecision(False, False, None, "review_pending")
                model_id = await bind_model_and_enqueue_asset_build(
                    cur,
                    user_id=user_id,
                    enrollment_id=enrollment_id,
                    row=row,
                    match_snapshot=match_snapshot,
                    method=method,
                    identity_contract_version=identity_contract_version,
                    liveness_provider_version=(
                        liveness.provider_version if liveness is not None else "disabled"
                    ),
                    match_policy_version=settings.fm_match_policy_version,
                )
            await conn.commit()
        return EnrollmentDecision(True, False, None, "asset_building", model_id)
    except EnrollmentExpiredError:
        await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
        return EnrollmentDecision(False, False, "enrollment_expired", "expired")
    except EnrollmentMappedError as exc:
        return await _fail_enrollment(
            request,
            enrollment_id=enrollment_id,
            reason=exc.reason,
            retryable=exc.reason in RETRYABLE_REASONS,
            expected_status="processing" if processing_started else "liveness_pending",
        )
    finally:
        # Task3: 원시 CI(evidence.ci)는 앞단 /identity 가 이미 폐기했다 — 여기서 다룰 게 없다.
        if portrait is not None:
            cx_identity.wipe_bytearray(portrait)
        if id_document_buffer is not None:
            cx_identity.wipe_bytearray(id_document_buffer)
        if liveness is not None:
            cx_identity.wipe_bytearray(liveness.reference_image)
        for buffer in photo_buffers:
            cx_identity.wipe_bytearray(buffer)
        photo_buffers.clear()
        release_task = asyncio.create_task(
            record_raw_release_evidence(
                request,
                enrollment_id,
                oacx_portrait_released=True,
                liveness_reference_released=True,
                temporary_embeddings_released=True,
            )
        )
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            await release_task
            raise


@router.post("/enrollments/{enrollment_id}/complete")
async def complete_enrollment(
    request: Request,
    enrollment_id: str,
    body: CompleteEnrollmentBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    settings: Settings = request.app.state.settings
    if settings.fm_liveness_enabled:
        try:
            session_id = str(uuid.UUID(str(body.session_id)))
        except (AttributeError, TypeError, ValueError):
            raise _err("invalid_liveness_session", "인증 세션을 확인할 수 없습니다.")
    else:
        session_id = None  # 라이브니스 off — 세션 없이 신분증 초상 앵커로 매칭
    try:
        decision = await process_enrollment_completion(
            request,
            enrollment_id=enrollment_id,
            user_id=user_id,
            session_id=session_id,
            id_photo_hex=body.id_photo_hex,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_complete_failed",
            extra={"enrollment_id": enrollment_id, "error_type": type(exc).__name__},
        )
        raise _err("enrollment_unavailable", "등록을 완료할 수 없습니다.", status=503)
    if decision.passed:
        _wake_dispatcher(request)
        _prewarm_opendid(request)   # 자산빌드(1~3분) 뒤가 발급 — 그 사이에 holder 를 띄운다
    return JSONResponse(
        status_code=202 if decision.passed else 200,
        content=_decision_body(decision),
    )


@router.post("/enrollments/{enrollment_id}/cancel", response_model=EnrollmentView)
async def cancel_enrollment(
    request: Request,
    enrollment_id: str,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    try:
        async with get_conn(request) as conn:
            await _assert_account_open(conn, user_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update fm_biometric_enrollments e
                    set status = 'cancelled', completed_at = coalesce(completed_at, now()),
                        -- 취소한 등록이 관리자 대기 큐에 영원히 남지 않게 한다(최종리뷰 I5).
                        -- 이미 승인/거절된 행의 결정 기록은 지우지 않는다 — 'pending' 일 때만 비운다.
                        review_status = case when e.review_status = 'pending'
                                             then null else e.review_status end
                    where e.id = %s and e.user_id = %s and e.status in (
                        'id_capture_pending', 'identity_pending', 'photos_pending',
                        'review_pending', 'liveness_pending', 'processing',
                        'asset_building', 'license_pending', 'vc_pending', 'cancelled'
                    )
                    returning e.id::text as id
                    """,
                    (enrollment_id, user_id),
                )
                if await cur.fetchone() is None:
                    raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            await conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "facemarket_enrollment_cancel_commit_failed",
            extra={
                "enrollment_id": enrollment_id,
                "error_type": type(exc).__name__,
            },
        )
        raise _err(
            "db_unavailable",
            "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
            status=503,
        )

    await cleanup_terminal_enrollment(request.app, enrollment_id=enrollment_id)
    async with get_conn(request) as conn:
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row, request.app.state.settings)


def validate_biometric_settings(settings: Settings) -> None:
    if not settings.fm_biometric_enrollment_enabled:
        return
    if not settings.facemarket_enabled:
        raise RuntimeError("FACEMARKET_ENABLED is required for biometric enrollment")
    # 가장 넓은 요구(지금 동의 본문)로 검증한다 — 설정이 새 칸을 빠뜨리면 여기서 걸린다.
    _required_photo_slots(settings, BIOMETRIC_CONSENT_VERSION)
    if not settings.opendid_holder_url:
        raise RuntimeError("OPENDID_HOLDER_URL is required for biometric enrollment")
    # 라이브니스 관련 요구는 on 일 때만 — off 면 매칭 앵커가 신분증 초상이라 리전·브라우저 role·
    # liveness confidence·id_live 임계가 쓰이지 않는다.
    if settings.fm_liveness_enabled:
        if settings.fm_liveness_region != "us-east-1":
            raise RuntimeError("Face Liveness region must be us-east-1")
        if not settings.fm_liveness_browser_role_arn:
            raise RuntimeError("FM_LIVENESS_BROWSER_ROLE_ARN is required")
        if settings.fm_liveness_confidence_threshold is None:
            raise RuntimeError("FM_LIVENESS_CONFIDENCE_THRESHOLD is required")
    if not settings.fm_ci_pepper or not settings.fm_ci_pepper.strip():
        raise RuntimeError("FM_CI_PEPPER is required for biometric enrollment")
    # 촬영 스펙 검사(facemarket_photo_check)는 fm_face_match_enabled 와 무관하게 **항상** 돈다.
    # YuNet 가중치가 없으면 사진 업로드가 전부 503 이라 등록이 통째로 막힌다 — 그런 배포가
    # 조용히 나가지 않게 부팅에서 막는다(가중치는 Dockerfile 이 빌드 때 받는다).
    if not os.path.exists(weight_paths(settings)[0]):
        raise RuntimeError(
            "YuNet face detector weight file is required for biometric enrollment"
        )
    required = []
    bounded_thresholds = []
    if settings.fm_liveness_enabled:
        bounded_thresholds.append((settings.fm_liveness_confidence_threshold, 100.0))
    if settings.fm_face_match_enabled:
        if not settings.fm_face_qc_enabled:
            raise RuntimeError("FM_FACE_QC_ENABLED is required")
        det_path, rec_path = weight_paths(settings)
        if not (os.path.exists(det_path) and os.path.exists(rec_path)):
            raise RuntimeError(
                "SFace/YuNet face QC weight files are required for biometric enrollment"
            )
        required += [settings.fm_retouched_live_threshold, settings.fm_match_policy_version]
        bounded_thresholds.append((settings.fm_retouched_live_threshold, 1.0))
        if settings.fm_liveness_enabled:
            required.append(settings.fm_id_live_threshold)
            bounded_thresholds.append((settings.fm_id_live_threshold, 1.0))
    if any(value is None for value in required):
        raise RuntimeError("calibrated biometric thresholds and policy version are required")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 < value <= upper
        for value, upper in bounded_thresholds
    ):
        raise RuntimeError(
            "calibrated biometric thresholds must be finite and within provider domains"
        )
    if settings.fm_oacx_contract_mode == "dev-mock-v1" and settings.app_env != "dev":
        raise RuntimeError("verified OACX biometric contract is required outside dev")
    # D1: prod-dlphoto-v1 은 실 프로덕션 계약(cx_identity.PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT)
    # 이라 dev-mock-v1 처럼 app_env=='dev' 로 가둘 필요가 없다 — prod 에서도 유효해야 한다.
    if settings.fm_oacx_contract_mode not in ("dev-mock-v1", "prod-dlphoto-v1"):
        raise RuntimeError("verified OACX biometric contract is required")


def build_biometric_aws_clients(settings: Settings):
    rekognition = boto3.client(
        "rekognition", region_name="us-east-1", config=AWS_LIVENESS_CONFIG
    )
    sts = boto3.client("sts", region_name="us-east-1", config=AWS_LIVENESS_CONFIG)
    return rekognition, sts
