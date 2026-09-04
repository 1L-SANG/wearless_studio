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
from pydantic import Field

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


def _token_secret(settings) -> str:
    """업로드 토큰 HMAC 시크릿.

    fm_ci_pepper 는 생체 CI(주민등록번호 계열) 해시 전용 시크릿이다 — 이 시스템이 들고
    있는 가장 민감한 PII 를 보호한다. 재사용하면 그 시크릿의 blast radius 가 훨씬 더
    노출된 경로(모든 배포 액션, 공격자 영향 입력에 가까운 presign/sign 왕복)로 번진다.
    HMAC-SHA256 은 관측된 MAC 에서 키가 새지 않으니 오늘 당장의 익스플로잇은 없지만,
    "TokenInvalid 를 디버그 로그로 찍자"는 아주 흔한 후속 변경 하나가 이 시크릿을,
    fm_ci_pepper 를 재사용했다면 CI 해시 전체까지 함께 새게 만든다(리뷰 I1, 2026-09-04).

    미설정이면 **폐쇄 실패**(503) 한다 — fm_ci_pepper 로 조용히 되돌아가는 건 I1 이
    끊으려는 바로 그 결합을 다시 붙이는 것이라 선택하지 않았다. 이 라우트는
    fm_provenance_enabled 뒤에 있는 옵트인 기능이라, 503 은 "이 기능은 아직 설정이 끝나지
    않았다"는 정확한 신호이지 이미 쓰고 있던 사용자에게 가는 피해가 아니다.
    """
    secret = settings.fm_provenance_token_secret
    if not secret:
        raise _err(
            "provenance_unconfigured",
            "배포본 공증 기능이 아직 설정되지 않았습니다.",
            status=503,
        )
    return secret


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
    # CamelModel 의 alias_generator(pydantic to_camel)가 "c2pa_status" 를 "c2PaStatus" 로
    # 잘못 변환한다("2" 뒤 경계를 토큰 분리로 오인) — 실측: to_camel("c2pa_status") ==
    # "c2PaStatus". 라우트는 브리핑·프론트(Task 8)가 합의한 "c2paStatus" 를 돌려주므로,
    # 둘이 어긋나 모든 성공 응답이 ResponseValidationError 500 이 났다(리뷰 I5 라우트
    # 테스트가 처음 잡음). alias 를 명시로 고정해 자동생성기를 우회한다.
    c2pa_status: str = Field(alias="c2paStatus")
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


def _verify_license_or_raise(app, lic: dict) -> None:
    """verify_license_local 호출 단일 지점 — presign·sign 양쪽이 동일하게 통과해야
    한다(리뷰 I2: sign 이 이 게이트를 빠뜨리면 presign→sign 사이 _UPLOAD_TTL(5분) 창에서
    라이선스가 철회돼도 그 스테일 상태가 회수 불가능한 파일 안에 그대로 박힌다).

    brand_use_category 는 라이선스 자신이 허용하는 카테고리 중 하나를 그대로 넘긴다 —
    여기서는 특정 브랜드 카테고리로 "생성" 중이 아니라 이미 끝난 생성이 실제로 소비한
    라이선스가 지금도 유효한지(활성·미만료·모델 verified·enrollment passed·자산 ready)만
    확인하면 되기 때문이다. None 을 넘기면 verify_license_local 이 매번
    brand_use_category_required 로 막는다 — 실호출 경로 중 None 을 넘기는 경로는 없다.
    """
    allowed_use = lic.get("allowed_use") or []
    verify_license_local(
        app, lic, model_id=lic["model_id"],
        brand_use_category=allowed_use[0] if allowed_use else None,
    )


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
    s = request.app.state.settings
    # 설정 게이트(미설정 시 503)를 DB 조회보다 먼저 확인한다 — 이 기능이 아예 설정 안
    # 됐으면 라이선스 사용 내역을 찾겠다고 DB 를 굳이 건드릴 이유가 없다.
    secret = _token_secret(s)
    if body.kind not in _KINDS:
        raise _err("invalid_kind", "지원하지 않는 형식입니다.")
    if body.byte_size <= 0 or body.byte_size > _MAX_UPLOAD_BYTES:
        raise _err("too_large", "파일이 너무 큽니다.")
    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=body.project_id)
    _verify_license_or_raise(request.app, lic)

    key = f"publications/{user_id}/{uuid.uuid4()}/upload"
    token = make_upload_token(
        secret, seller_id=user_id, key=key, project_id=body.project_id,
        kind=body.kind, expires_at=time.time() + _UPLOAD_TTL,
    )
    url = await asyncio.to_thread(
        request.app.state.r2.presigned_put, key, _MIME[body.kind], _UPLOAD_TTL
    )
    return {"uploadToken": token, "uploadUrl": url}


async def _upsert_publication(conn, *, seller_id, project_id, lic, kind, sha, size) -> dict:
    """(seller_id, image_sha256) 멱등. 이미 있으면 기존 행을 돌려준다 — 그 id 가 정본.

    source_asset_ids 를 INSERT 값에 싣는다(리뷰 I4) — 마이그레이션이 1급 컬럼으로 선언한
    걸 이전에는 빠뜨려서 모든 행에서 영구히 비어 있었다. zip 처럼 매니페스트가 아예 없는
    kind 에서는 이 컬럼이 "어떤 원본 컷들이 이 배포본을 만들었는가"의 유일한 기록이라
    분쟁·정산 조회가 c2pa_manifest jsonb 를 파헤치지 않아도 되게 한다. `on conflict ...
    do nothing` 이라 충돌 시 이 INSERT 문 자체가 통째로 스킵된다 — 기존 행의
    source_asset_ids 는 절대 건드리지 않는다(클로버링 없음)."""
    cols = ("id::text as id, c2pa_status, chain_status, r2_key, signed_sha256")
    asset_ids = list(lic.get("asset_ids") or [])
    async with conn.cursor() as cur:
        await cur.execute(
            f"""insert into fm_publication_records
                  (project_id, seller_id, license_id, license_ref, model_id,
                   kind, image_sha256, byte_size, source_asset_ids)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (seller_id, image_sha256) do nothing
                returning {cols}""",
            (project_id, seller_id, lic.get("license_id"), lic["license_ref"],
             lic["model_id"], kind, sha, size, asset_ids),
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
        400: {"model": ErrorResponse, "description": "잘못된 요청"},
        401: {"model": ErrorResponse, "description": "인증 실패"},
        403: {"model": ErrorResponse, "description": "업로드 토큰 무효"},
        404: {"model": ErrorResponse, "description": "업로드 객체 없음"},
        409: {"model": ErrorResponse, "description": "라이선스 사용 불가"},
        503: {"model": ErrorResponse, "description": "공증 기능 미설정"},
    },
    summary="배포본 공증 — 해시·원장·C2PA 서명",
)
async def sign(request: Request, body: SignRequest, user_id: str = Depends(require_user)):
    s = request.app.state.settings
    try:
        parsed = parse_upload_token(_token_secret(s), body.upload_token)
    except TokenInvalid:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)
    if parsed["seller_id"] != user_id:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)

    r2 = request.app.state.r2
    key = parsed["key"]

    # I3 — presign 에 실린 byte_size 는 클라이언트 자기신고값이라 강제력이 없다(presigned
    # PUT 은 ContentType 만 고정하지 ContentLength 는 고정하지 않는다). get_bytes 로 전체
    # 바이트를 메모리에 올리기 전에 HEAD 로 실제 크기를 먼저 본다 — 그래야 무권한이거나
    # 초과용량인 요청이 바이트 전송 비용을 물지 않는다. 라이선스 게이트(아래)도 같은 이유로
    # get_bytes 이전에 끝낸다.
    meta = await asyncio.to_thread(r2.head, key)
    if meta is None:
        raise _err("not_found", "업로드된 파일을 찾을 수 없습니다.", status=404)
    actual_size = meta.get("size")
    if actual_size is None or actual_size <= 0 or actual_size > _MAX_UPLOAD_BYTES:
        raise _err("too_large", "파일이 너무 큽니다.", status=400)

    project_id = parsed["project_id"]
    kind = parsed["kind"]
    mime = _MIME[kind]

    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=project_id)
    # I2 — presign 이 통과했더라도 업로드 대기 TTL(_UPLOAD_TTL=300초) 동안 라이선스가
    # 철회될 수 있다. 산출물(서명된 파일)은 여기, sign 에서 "만들어진다" — 이미 나간 바이트는
    # 회수할 수 없지만, 아직 안 만든 바이트를 만들지 않는 건 지금 우리가 통제할 수 있다.
    # 그래서 매니페스트를 조립하기 전에 다시 게이트를 통과해야 한다(리뷰 I2 판정) — 여기서
    # 막히면 스테일 licenseId/allowedUse/forbiddenUse 가 회수 불가 파일에 안 박힌다.
    _verify_license_or_raise(request.app, lic)

    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        raise _err("not_found", "업로드된 파일을 찾을 수 없습니다.", status=404)

    sha = hashlib.sha256(data).hexdigest()
    async with get_conn(request) as conn:
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
