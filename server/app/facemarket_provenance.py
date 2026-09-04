"""FaceMarket 배포본 공증 — 층① 배포 원장 + 층② C2PA 서명 진입점.

브라우저가 캔버스로 만든 배포본을 R2 로 직접 올리고(ALB 우회), 서버는 그 바이트를 읽어
해시·원장·서명만 한다. 렌더는 하지 않는다 — editorExport.js 가 화면 그대로를 뜬 픽셀이
정본이고, 서버가 그걸 재현할 방법은 없다(설계 결정 #2).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from psycopg.types.json import Json

from .auth import require_user
from .facemarket import (
    CamelModel,
    ErrorResponse,
    PublicVerifyModel,
    _age_from_birth_year,
    _err,
    _is_expired,
    _mask_name,
    get_conn,
    resolve_model_license,
    verify_license_local,
)
from .services import c2pa_signer

logger = logging.getLogger("facemarket.provenance")

router = APIRouter(prefix="/v1/facemarket/publications", tags=["FaceMarket"])

_UPLOAD_TTL = 300           # presigned PUT 유효 5분
_DOWNLOAD_TTL = 600         # 서명본 GET 10분
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 긴 PNG 상한. 넘으면 presign 거부
_KINDS = {"long_png", "block_png", "zip"}
_MIME = {"long_png": "image/png", "block_png": "image/png", "zip": "application/zip"}


class TokenInvalid(Exception):
    """업로드 토큰이 위조·만료됐거나 다른 시크릿으로 만들어졌다."""


def make_upload_token(
    secret: str, *, seller_id: str, key: str, project_id: str, kind: str, expires_at: float
) -> str:
    """서명된 단명 업로드 토큰.

    임의 R2 키를 서명 대상으로 미는 것을 막고, presign 이 이미 검증한 project_id·kind 를
    sign 까지 나른다 — 키 문자열에서 역산하면 키 포맷 변경이 조용한 버그가 된다.
    """
    payload = json.dumps(
        {"s": seller_id, "k": key, "p": project_id, "t": kind, "e": int(expires_at)},
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def parse_upload_token(secret: str, token: str) -> dict:
    try:
        body, mac = str(token).split(".", 1)
    except ValueError:
        raise TokenInvalid("malformed")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise TokenInvalid("bad_signature")
    padded = body + "=" * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        raise TokenInvalid("bad_payload")
    if int(data.get("e", 0)) < time.time():
        raise TokenInvalid("expired")
    if data.get("t") not in _KINDS:
        raise TokenInvalid("bad_kind")
    return {
        "seller_id": data["s"], "key": data["k"],
        "project_id": data["p"], "kind": data["t"],
    }


def sign_bytes(signer, data: bytes, mime: str, manifest: dict) -> tuple[bytes, str]:
    """(바이트, c2pa_status). 서명 실패는 원본을 그대로 돌려준다.

    생성은 이미 끝났고 크레딧도 차감됐다. 도장이 안 찍혔다고 결과물을 인질로 잡지 않는다
    — 기존 정산 훅의 best-effort 원칙과 같다(설계 §6.2).
    """
    if signer is None:
        return data, "skipped"
    try:
        return signer.sign(data, mime, manifest), "signed"
    except Exception:
        logger.exception("c2pa_sign_failed")
        return data, "failed"


class PresignRequest(CamelModel):
    project_id: str
    kind: str
    byte_size: int


class PresignResult(CamelModel):
    upload_token: str
    upload_url: str


class SignRequest(CamelModel):
    upload_token: str


class SignResult(CamelModel):
    publication_id: str
    download_url: str
    verify_url: str
    c2pa_status: str
    chain_status: str


async def _resolve_project_license(conn, *, user_id: str, project_id: str) -> dict:
    """이 프로젝트가 실제로 소비한 REAL 라이선스. 없으면 404.

    verify_license_local 은 model_status 뿐 아니라 current_enrollment_id·enrollment_status·
    match_policy_version·assets_status·has_face_front·has_grid_sedcard·assets_current_evidence
    까지 요구한다(facemarket.py:2169-2191) — fm_output_records/fm_licenses/fm_models 만 얕게
    조인해서는 이 필드들을 채울 수 없다. 대신 editor_image_job.py:804 가 쓰는 것과 같은
    resolve_model_license() 를 그대로 재사용해 "지금 이 모델·라이선스가 verify_license_local
    을 통과할 수 있는 완전한 상태인가"를 판정한다. 이 함수는 fm_output_records 에서 "어떤
    모델·라이선스를 실제로 소비했는가"만 알아내고, 그 평가는 resolve_model_license 에 위임한다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """select r.license_ref::text as license_ref, r.model_id::text as model_id,
                      array_agg(distinct r.asset_id) filter (where r.asset_id is not null)
                        as asset_ids
                 from fm_output_records r
                 join jobs j on j.id = r.job_id
                where j.project_id = %s and r.seller_id = %s
                group by r.license_ref, r.model_id
                order by max(r.created_at) desc
                limit 1""",
            (project_id, user_id),
        )
        usage = await cur.fetchone()
    if usage is None:
        raise _err("not_found", "출처를 기록할 라이선스 사용 내역이 없습니다.", status=404)

    evidence = await resolve_model_license(
        conn, usage["model_id"], license_id=usage["license_ref"]
    )
    if evidence is None:
        raise _err("model_unavailable", "사용할 수 없는 모델입니다.", status=409)

    lic = dict(evidence)
    lic["license_ref"] = usage["license_ref"]
    lic["license_id"] = evidence.get("id")
    lic["asset_ids"] = usage["asset_ids"] or []
    return lic


@router.post(
    "/presign",
    response_model=PresignResult,
    responses={
        400: {"model": ErrorResponse, "description": "잘못된 요청"},
        401: {"model": ErrorResponse, "description": "인증 실패"},
        404: {"model": ErrorResponse, "description": "라이선스 사용 내역 없음"},
    },
    summary="배포본 업로드 URL 발급",
)
async def presign(
    request: Request, body: PresignRequest, user_id: str = Depends(require_user)
):
    if body.kind not in _KINDS:
        raise _err("invalid_kind", "지원하지 않는 형식입니다.")
    if body.byte_size <= 0 or body.byte_size > _MAX_UPLOAD_BYTES:
        raise _err("too_large", "파일이 너무 큽니다.")
    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=body.project_id)
    # verify_license_local 은 brand_use_category 도 게이트한다(라이선스가 허용하는 카테고리
    # 중 하나여야 함). 여기서는 특정 브랜드 카테고리로 "생성"하는 게 아니라 이미 끝난 생성이
    # 실제로 소비한 라이선스가 지금도 유효한지(활성·미만료·모델 verified)만 확인하면 되므로,
    # 그 라이선스 자신이 허용하는 카테고리 중 하나를 그대로 넘긴다 — None 을 넘기면 매번
    # brand_use_category_required 로 막힌다(모든 실호출 경로가 실제 카테고리 문자열을 넘기지,
    # None 을 넘기는 경로가 없다).
    allowed_use = lic.get("allowed_use") or []
    verify_license_local(
        request.app, lic, model_id=lic["model_id"],
        brand_use_category=allowed_use[0] if allowed_use else None,
    )

    key = f"publications/{user_id}/{uuid.uuid4()}/upload"
    secret = request.app.state.settings.fm_ci_pepper
    token = make_upload_token(
        secret, seller_id=user_id, key=key, project_id=body.project_id,
        kind=body.kind, expires_at=time.time() + _UPLOAD_TTL,
    )
    url = await asyncio.to_thread(
        request.app.state.r2.presigned_put, key, _MIME[body.kind], _UPLOAD_TTL
    )
    return {"uploadToken": token, "uploadUrl": url}


async def _upsert_publication(conn, *, seller_id, project_id, lic, kind, sha, size) -> dict:
    """(seller_id, image_sha256) 멱등. 이미 있으면 기존 행을 돌려준다 — 그 id 가 정본."""
    cols = ("id::text as id, c2pa_status, chain_status, r2_key, signed_sha256")
    async with conn.cursor() as cur:
        await cur.execute(
            f"""insert into fm_publication_records
                  (project_id, seller_id, license_id, license_ref, model_id,
                   kind, image_sha256, byte_size)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (seller_id, image_sha256) do nothing
                returning {cols}""",
            (project_id, seller_id, lic.get("license_id"), lic["license_ref"],
             lic["model_id"], kind, sha, size),
        )
        row = await cur.fetchone()
        if row is None:
            await cur.execute(
                f"select {cols} from fm_publication_records "
                "where seller_id = %s and image_sha256 = %s",
                (seller_id, sha),
            )
            row = await cur.fetchone()
    await conn.commit()
    return row


@router.post(
    "/sign",
    response_model=SignResult,
    responses={
        401: {"model": ErrorResponse, "description": "인증 실패"},
        403: {"model": ErrorResponse, "description": "업로드 토큰 무효"},
        404: {"model": ErrorResponse, "description": "업로드 객체 없음"},
    },
    summary="배포본 공증 — 해시·원장·C2PA 서명",
)
async def sign(request: Request, body: SignRequest, user_id: str = Depends(require_user)):
    s = request.app.state.settings
    try:
        parsed = parse_upload_token(s.fm_ci_pepper, body.upload_token)
    except TokenInvalid:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)
    if parsed["seller_id"] != user_id:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)

    r2 = request.app.state.r2
    key = parsed["key"]
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        raise _err("not_found", "업로드된 파일을 찾을 수 없습니다.", status=404)

    sha = hashlib.sha256(data).hexdigest()
    project_id = parsed["project_id"]
    kind = parsed["kind"]
    mime = _MIME[kind]
    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=project_id)
        row = await _upsert_publication(
            conn, seller_id=user_id, project_id=project_id, lic=lic,
            kind=kind, sha=sha, size=len(data),
        )

    publication_id = row["id"]
    verify_url = f"{s.public_web_origin}/verify/p/{publication_id}"

    if row["c2pa_status"] in ("signed", "skipped", "failed") and row["r2_key"]:
        signed_key = row["r2_key"]           # 멱등 — 이미 처리된 배포본이다
        c2pa_status = row["c2pa_status"]
    elif kind == "zip":
        # zip 아카이브에는 C2PA 를 못 박는다. 1차 범위에서는 원장·앵커만 태우고 서명은 생략한다
        # (설계 §6.1). 원본을 그대로 보관하고 c2pa_status='skipped'.
        signed_key = f"publications/{user_id}/{publication_id}/signed.zip"
        await asyncio.to_thread(r2.put_bytes, signed_key, data, mime)
        c2pa_status = "skipped"
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_records
                          set r2_key = %s, signed_sha256 = %s, c2pa_status = 'skipped'
                        where id = %s""",
                    (signed_key, sha, publication_id),
                )
                await cur.execute(
                    """insert into fm_publication_anchor_jobs (publication_id)
                       values (%s) on conflict (publication_id) do nothing""",
                    (publication_id,),
                )
            await conn.commit()
        await asyncio.to_thread(r2.delete, key)
    else:
        manifest = c2pa_signer.build_manifest(
            model_id=lic["model_id"],
            license_id=lic["license_ref"],
            vc_id=lic.get("vc_id"),
            publication_id=publication_id,
            verify_url=verify_url,
            allowed_use=lic.get("allowed_use") or [],
            forbidden_use=lic.get("forbidden_use") or [],
            license_valid_until=str(lic.get("license_valid_until") or ""),
            source_asset_ids=[str(a) for a in (lic.get("asset_ids") or [])],
            app_version=getattr(s, "app_version", "0"),
        )
        signer = getattr(request.app.state, "fm_c2pa_signer", None)
        signed, c2pa_status = await asyncio.to_thread(
            sign_bytes, signer, data, mime, manifest
        )
        signed_key = f"publications/{user_id}/{publication_id}/signed.png"
        await asyncio.to_thread(r2.put_bytes, signed_key, signed, mime)
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_records
                          set r2_key = %s, signed_sha256 = %s, c2pa_status = %s,
                              c2pa_manifest = %s
                        where id = %s""",
                    (signed_key, hashlib.sha256(signed).hexdigest(), c2pa_status,
                     Json(manifest), publication_id),
                )
                await cur.execute(
                    """insert into fm_publication_anchor_jobs (publication_id)
                       values (%s) on conflict (publication_id) do nothing""",
                    (publication_id,),
                )
            await conn.commit()
        await asyncio.to_thread(r2.delete, key)   # 임시 업로드본 정리

    download_url = await asyncio.to_thread(r2.preview_url, signed_key, _DOWNLOAD_TTL)
    return {
        "publicationId": publication_id, "downloadUrl": download_url,
        "verifyUrl": verify_url, "c2paStatus": c2pa_status,
        "chainStatus": row["chain_status"],
    }


# ============================================================================
# 공개 검증 (무인증) — C2PA 매니페스트의 verifyUrl 이 여기를 가리킨다.
#
# 🔴 하드룰: facemarket.py:1249 와 동일. 무인증이라 한 번 나가면 회수 불가다.
#   절대 미노출 — 얼굴·face_image_*·CI·ci_hash·생년월일 원문·실명·user_id·model_id·
#   seller_id·내부 R2 키·전체 image_sha256·source_asset_ids.
#   3중 방어: ① SELECT 화이트리스트 ② response_model 이 선언 밖 필드 탈락
#            ③ 신원은 파생값만(마스킹 이름·만 나이)
#   필드 추가 요청이 오면 이 주석을 먼저 읽을 것. 확장은 계약 변경이다.
# ============================================================================


class PublicationChain(CamelModel):
    status: str
    tx_hash: str | None = None
    chain_id: str | None = None
    block: int | None = None


class PublicationVerifyResult(CamelModel):
    """공개 검증 응답 화이트리스트. **이 필드가 전부** — 확장 금지."""

    valid: bool
    status: str                 # 'active' | 'revoked' | 'expired'
    published_at: datetime
    image_hash_prefix: str      # sha256 앞 12자. 전체는 안 싣는다
    kind: str
    allowed_use: list[str]
    forbidden_use: list[str]
    license_valid_until: datetime | None = None
    chain: PublicationChain | None = None
    model: PublicVerifyModel


@router.get(
    "/verify/{publication_id}",
    response_model=PublicationVerifyResult,
    responses={404: {"model": ErrorResponse, "description": "없음/잘못된 id"}},
    summary="배포본 공개 검증 (무인증)",
)
async def verify_publication(request: Request, publication_id: str, response: Response):
    """C2PA 매니페스트의 verifyUrl 종착지. **인증 없음**(누구나 파일 출처를 확인한다)."""
    try:
        pub_uuid = uuid.UUID(str(publication_id))
    except (ValueError, TypeError):
        raise _err("not_found", "기록을 찾을 수 없습니다.", status=404)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 방어 ① — 화이트리스트 SELECT. r2_key·seller_id·source_asset_ids·signed_sha256 미조회.
            await cur.execute(
                """select p.kind, p.image_sha256, p.created_at, p.revoked_at,
                          p.chain_status, p.tx_hash, p.chain_id, p.recorded_block,
                          l.status as license_status, l.allowed_use, l.forbidden_use,
                          l.license_valid_until, m.display_name,
                          (select v.fields->>'birthYear' from fm_identity_verifications v
                            where v.model_id = m.id
                            order by v.verified_at desc limit 1) as birth_year
                     from fm_publication_records p
                     left join fm_licenses l on l.id = p.license_id
                     left join fm_models m on m.id = p.model_id
                    where p.id = %s""",
                (str(pub_uuid),),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "기록을 찾을 수 없습니다.", status=404)

    if row["revoked_at"] is not None:
        status = "revoked"
    elif row["license_status"] == "revoked":
        status = "revoked"
    elif row["license_status"] is None:
        status = "revoked"        # 라이선스가 사라졌다 = 더 이상 권한을 확인할 수 없다
    elif _is_expired(row):
        status = "expired"
    else:
        status = row["license_status"]

    response.headers["Cache-Control"] = "no-store"   # 철회가 즉시 반영돼야 한다
    chain = None
    if row["chain_status"]:
        chain = {
            "status": row["chain_status"], "txHash": row["tx_hash"],
            "chainId": row["chain_id"], "block": row["recorded_block"],
        }
    return {
        "valid": status == "active",
        "status": status,
        "publishedAt": row["created_at"],
        "imageHashPrefix": (row["image_sha256"] or "")[:12],
        "kind": row["kind"],
        "allowedUse": row["allowed_use"] or [],
        "forbiddenUse": row["forbidden_use"] or [],
        "licenseValidUntil": row["license_valid_until"],
        "chain": chain,
        "model": {
            "nameMasked": _mask_name(row["display_name"] or ""),
            "age": _age_from_birth_year(row["birth_year"]),
        },
    }
