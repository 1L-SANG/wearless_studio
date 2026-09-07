"""FaceMarket 테스트컷 관리자 전송과 모델 본인 확인 게이트.

원본 테스트컷은 얼굴 전용 비공개 R2에 남고, 모델이 고른 컷의 1024px WebP 축소본만
일반 자산 버킷으로 승격한다. 모든 응답은 내부 R2 키 대신 인증 스트림 URI를 내보낸다.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field

from . import admin_guard, facemarket_notify, repo
from .auth import require_user
from .db import get_conn
from .models import CamelModel
from .personalization import CONSENT_DOC_VERSION
from .r2 import (
    IMMUTABLE_CACHE,
    ext_for_mime,
    model_catalog_cover_key,
    model_test_cut_key,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket model test cuts"])

MAX_TEST_CUTS = 6
MAX_TEST_CUT_BYTES = 25 * 1024 * 1024
ALLOWED_TEST_CUT_MIME = {"image/png", "image/jpeg", "image/webp"}


class TestCutView(CamelModel):
    id: str
    mime: str
    sort: int
    approved: bool | None = None
    created_at: datetime
    image_uri: str


class AdminModelCard(CamelModel):
    id: str
    display_name: str
    status: str
    enrollment_status: str | None = None
    test_cut_count: int = 0
    redo_requested: bool = False
    confirm_requested_at: datetime | None = None
    redo_count: int = 0
    ready_to_send: bool = False
    test_cuts: list[TestCutView] = Field(default_factory=list)


class SendTestCutsResult(CamelModel):
    status: str
    confirm_requested_at: datetime
    email_sent: bool


class ModelTestCutsView(CamelModel):
    model_id: str
    status: str
    redo_count: int
    confirm_requested_at: datetime | None = None
    confirmed_at: datetime | None = None
    cuts: list[TestCutView] = Field(default_factory=list)


class ConfirmTestCutBody(CamelModel):
    approved_cut_id: str


class ConfirmTestCutResult(CamelModel):
    status: str
    confirmed_at: datetime
    cover_image_url: str


class RedoTestCutsBody(CamelModel):
    reason: str | None = Field(default=None, max_length=1000)


class RedoTestCutsResult(CamelModel):
    status: str
    redo_count: int


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _canonical_id(value: str, *, noun: str = "모델") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        raise _err("not_found", f"{noun}을 찾을 수 없습니다.", status=404)


def _r2_face(request: Request):
    client = getattr(request.app.state, "r2_face", None)
    if client is None:
        raise _err("storage_unavailable", "얼굴 저장소를 사용할 수 없습니다.", status=503)
    return client


def _r2_public(request: Request):
    client = getattr(request.app.state, "r2", None)
    if client is None:
        raise _err("storage_unavailable", "이미지 저장소를 사용할 수 없습니다.", status=503)
    return client


#: 콘솔(facemarket_admin.py)과 같은 가드를 쓴다 — 403 응답 형태와 감사 기록 규칙이
#: 갈리면 운영자가 "왜 여기만 다르지"를 겪는다.
_require_admin = admin_guard.require_admin


def _cut_view(row: dict, *, model_side: bool = False) -> dict:
    cut_id = str(row["id"])
    if model_side:
        uri = f"/v1/facemarket/model/test-cuts/{cut_id}/image"
    else:
        uri = (
            f"/v1/facemarket/admin/models/{row.get('model_id') or ''}"
            f"/test-cuts/{cut_id}/image"
        )
    return {
        "id": cut_id,
        "mime": row["mime"],
        "sort": row["sort"],
        "approved": row.get("approved"),
        "created_at": row["created_at"],
        "image_uri": uri,
    }


async def _load_admin_model(conn, model_id: str, *, for_update: bool = False) -> dict | None:
    lock = " for update of m" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select m.id::text as id, m.status, m.redo_count,
                      e.status as enrollment_status,
                      coalesce(u.email, a.contact_email) as contact_email,
                      exists (
                        select 1 from fm_licenses l
                         where l.model_id = m.id
                           and l.enrollment_id = m.current_enrollment_id
                           and l.status = 'active'
                           and nullif(btrim(l.vc_id), '') is not null
                      ) as has_active_license
                 from fm_models m
                 left join fm_biometric_enrollments e on e.id = m.current_enrollment_id
                 left join fm_model_applications a on a.id = e.application_id
                 left join auth.users u on u.id = m.user_id
                where m.id = %s""" + lock,
            (model_id,),
        )
        return await cur.fetchone()


async def _load_owned_model(conn, user_id: str, *, for_update: bool = False) -> dict | None:
    lock = " for update" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, status, redo_count, confirm_requested_at, confirmed_at
                 from fm_models
                where user_id = %s
                order by created_at desc limit 1""" + lock,
            (user_id,),
        )
        return await cur.fetchone()


async def _load_cuts(conn, model_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, mime, sort, approved, created_at
                 from fm_model_test_cuts
                where model_id = %s order by sort, created_at""",
            (model_id,),
        )
        return await cur.fetchall()


async def _load_cut_for_admin(
    conn, model_id: str, cut_id: str, *, for_update: bool = False
) -> dict | None:
    lock = " for update" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, r2_key, mime, approved
                 from fm_model_test_cuts
                where id = %s and model_id = %s""" + lock,
            (cut_id, model_id),
        )
        return await cur.fetchone()


async def _load_owned_cut(conn, cut_id: str, user_id: str, *, for_update: bool = False) -> dict | None:
    lock = " for update of m, c" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select c.id::text as id, c.r2_key, c.mime, c.sort, c.approved, c.created_at,
                      m.id::text as model_id, m.status as model_status, m.redo_count
                 from fm_model_test_cuts c
                 join fm_models m on m.id = c.model_id
                where c.id = %s and m.user_id = %s""" + lock,
            (cut_id, user_id),
        )
        return await cur.fetchone()


@router.get(
    "/admin/models/{model_id}/test-cuts",
    response_model=AdminModelCard,
    summary="모델 1건의 테스트컷 상태(콘솔 모델 상세에서 호출)",
)
async def admin_model_test_cuts(
    model_id: str, request: Request, user_id: str = Depends(require_user)
):
    """콘솔의 `GET /admin/models` 목록·`/{model_id}` 상세와 경로가 겹치지 않게 하위
    리소스로 둔다. 목록에 테스트컷 컬럼을 얹지 않는 이유는 목록이 200행까지 오는데
    컷 이미지 메타를 전부 조인하면 목록 응답이 무거워지기 때문이다."""
    mid = _canonical_id(model_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """select m.id::text as id, m.display_name, m.status,
                          e.status as enrollment_status,
                          count(c.id)::integer as test_cut_count,
                          (m.status = 'pending' and m.redo_count > 0) as redo_requested,
                          m.confirm_requested_at, m.redo_count,
                          (e.status = 'passed' and exists (
                            select 1 from fm_licenses l
                             where l.model_id = m.id
                               and l.enrollment_id = m.current_enrollment_id
                               and l.status = 'active'
                               and nullif(btrim(l.vc_id), '') is not null
                          )) as ready_to_send,
                          coalesce(
                            jsonb_agg(jsonb_build_object(
                              'id', c.id::text, 'mime', c.mime, 'sort', c.sort,
                              'approved', c.approved, 'created_at', c.created_at
                            ) order by c.sort) filter (where c.id is not null),
                            '[]'::jsonb
                          ) as test_cuts
                     from fm_models m
                     left join fm_biometric_enrollments e on e.id = m.current_enrollment_id
                     left join fm_model_test_cuts c on c.model_id = m.id
                    where m.id = %s
                    group by m.id, e.status""",
                (mid,),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("model_not_found", "모델을 찾을 수 없어요.", status=404)
    data = dict(row)
    data["test_cuts"] = [
        _cut_view({**cut, "model_id": row["id"]})
        for cut in (row.get("test_cuts") or [])
    ]
    return data


@router.post(
    "/admin/models/{model_id}/test-cuts",
    response_model=list[TestCutView],
    status_code=201,
)
async def admin_upload_test_cuts(
    request: Request,
    model_id: str,
    images: list[UploadFile] = File(...),
    user_id: str = Depends(require_user),
):
    model_id = _canonical_id(model_id)
    if not 1 <= len(images) <= MAX_TEST_CUTS:
        raise _err("invalid_cut_count", "테스트컷은 한 번에 1~6장 올려 주세요.")

    # multipart 파싱은 프레임워크가 맡지만 실제 파일 바이트는 관리자 확인 뒤에만 읽는다.
    # 비관리자가 큰 업로드를 반복해 애플리케이션 메모리를 쓰는 경로를 닫는다.
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        if await _load_admin_model(conn, model_id) is None:
            raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
        existing_sorts = await _load_cut_sorts(conn, model_id)
    if len(existing_sorts) + len(images) > MAX_TEST_CUTS:
        raise _err("cut_limit", "모델당 테스트컷은 6장까지 올릴 수 있습니다.", status=409)

    prepared = []
    for image in images:
        mime = (image.content_type or "").lower()
        if mime not in ALLOWED_TEST_CUT_MIME:
            raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
        data = await image.read(MAX_TEST_CUT_BYTES + 1)
        if not data:
            raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
        if len(data) > MAX_TEST_CUT_BYTES:
            raise _err("file_too_large", "이미지는 25MB 이하만 가능합니다.", status=413)
        await asyncio.to_thread(_validate_image, data)
        cut_id = str(uuid.uuid4())
        prepared.append({
            "id": cut_id,
            "mime": mime,
            "data": data,
            "key": model_test_cut_key(model_id, cut_id, ext_for_mime(mime)),
        })

    r2 = _r2_face(request)
    stored_keys = []
    try:
        for item in prepared:
            await asyncio.to_thread(r2.put_bytes, item["key"], item["data"], item["mime"])
            stored_keys.append(item["key"])

        async with get_conn(request) as conn:
            await _require_admin(conn, user_id)
            if await _load_admin_model(conn, model_id, for_update=True) is None:
                raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
            used = set(await _load_cut_sorts(conn, model_id))
            if len(used) + len(prepared) > MAX_TEST_CUTS:
                raise _err("cut_limit", "모델당 테스트컷은 6장까지 올릴 수 있습니다.", status=409)
            available = [value for value in range(MAX_TEST_CUTS) if value not in used]
            rows = []
            async with conn.cursor() as cur:
                for item, sort in zip(prepared, available[:len(prepared)], strict=True):
                    await cur.execute(
                        """insert into fm_model_test_cuts (id, model_id, r2_key, mime, sort)
                           values (%s, %s, %s, %s, %s)
                           returning id::text as id, mime, sort, approved, created_at""",
                        (item["id"], model_id, item["key"], item["mime"], sort),
                    )
                    rows.append(await cur.fetchone())
            await conn.commit()
    except Exception:
        for key in stored_keys:
            try:
                await asyncio.to_thread(r2.delete, key)
            except Exception:
                logger.warning("test cut upload cleanup failed", exc_info=True)
        raise
    return [_cut_view({**row, "model_id": model_id}) for row in rows]


async def _load_cut_sorts(conn, model_id: str) -> list[int]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select sort from fm_model_test_cuts where model_id = %s order by sort",
            (model_id,),
        )
        return [row["sort"] for row in await cur.fetchall()]


@router.delete("/admin/models/{model_id}/test-cuts/{cut_id}", status_code=204)
async def admin_delete_test_cut(
    request: Request,
    model_id: str,
    cut_id: str,
    user_id: str = Depends(require_user),
):
    model_id = _canonical_id(model_id)
    cut_id = _canonical_id(cut_id, noun="테스트컷")
    r2 = _r2_face(request)
    backup = None
    deleted = False
    try:
        async with get_conn(request) as conn:
            await _require_admin(conn, user_id)
            if await _load_admin_model(conn, model_id, for_update=True) is None:
                raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
            cut = await _load_cut_for_admin(conn, model_id, cut_id, for_update=True)
            if cut is None:
                raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
            if cut.get("approved") is True:
                raise _err(
                    "approved_cut_locked",
                    "모델이 승인한 원본 테스트컷은 삭제할 수 없습니다.",
                    status=409,
                )
            try:
                backup = await asyncio.to_thread(r2.get_bytes, cut["r2_key"])
            except Exception:
                raise _err(
                    "storage_unavailable",
                    "테스트컷 원본을 확인하지 못해 삭제를 중단했습니다. 잠시 후 다시 시도해 주세요.",
                    status=503,
                )
            await asyncio.to_thread(r2.delete, cut["r2_key"])
            deleted = True
            async with conn.cursor() as cur:
                await cur.execute(
                    "delete from fm_model_test_cuts where id = %s and model_id = %s",
                    (cut_id, model_id),
                )
            await conn.commit()
    except Exception:
        if deleted and backup is not None:
            try:
                await asyncio.to_thread(r2.put_bytes, cut["r2_key"], backup, cut["mime"])
            except Exception:
                logger.warning("test cut delete compensation failed", exc_info=True)
        raise
    return Response(status_code=204)


@router.post("/admin/models/{model_id}/send-test-cuts", response_model=SendTestCutsResult)
async def admin_send_test_cuts(
    request: Request,
    model_id: str,
    user_id: str = Depends(require_user),
):
    model_id = _canonical_id(model_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        model = await _load_admin_model(conn, model_id, for_update=True)
        if model is None:
            raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
        if model["status"] not in {"pending", "awaiting_confirm", "reverification_required"}:
            raise _err("model_not_sendable", "테스트컷을 보낼 수 없는 모델 상태입니다.", status=409)
        if model.get("enrollment_status") != "passed" or model.get("has_active_license") is not True:
            raise _err(
                "model_registration_incomplete",
                "등록과 VC 발급이 끝난 모델에게만 테스트컷을 보낼 수 있습니다.",
                status=409,
            )
        async with conn.cursor() as cur:
            await cur.execute(
                "select exists(select 1 from fm_model_test_cuts where model_id = %s) as has_cuts",
                (model_id,),
            )
            if not (await cur.fetchone())["has_cuts"]:
                raise _err("test_cuts_required", "먼저 테스트컷을 1장 이상 올려 주세요.", status=409)
            await cur.execute(
                """update fm_models set status = 'awaiting_confirm', confirm_requested_at = now()
                    where id = %s returning confirm_requested_at""",
                (model_id,),
            )
            updated = await cur.fetchone()
        await conn.commit()

    email_sent = False
    settings = request.app.state.settings
    if settings.resend_api_key and model.get("contact_email"):
        try:
            email_sent, _message_id, _error = await facemarket_notify.send_application_email(
                settings,
                to=model["contact_email"],
                email_type="test_cuts_ready",
            )
        except Exception:
            logger.warning("test cut email dispatch failed", exc_info=True)
    return {
        "status": "awaiting_confirm",
        "confirm_requested_at": updated["confirm_requested_at"],
        "email_sent": email_sent,
    }


@router.get("/admin/models/{model_id}/test-cuts/{cut_id}/image")
async def admin_test_cut_image(
    request: Request,
    model_id: str,
    cut_id: str,
    user_id: str = Depends(require_user),
):
    model_id = _canonical_id(model_id)
    cut_id = _canonical_id(cut_id, noun="테스트컷")
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id)
        cut = await _load_cut_for_admin(conn, model_id, cut_id)
    if cut is None:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    return await _stream_private_cut(request, cut)


@router.get("/model/test-cuts", response_model=ModelTestCutsView)
async def model_test_cuts(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        model = await _load_owned_model(conn, user_id)
        if model is None:
            raise _err("not_found", "내 모델을 찾을 수 없습니다.", status=404)
        cuts = await _load_cuts(conn, model["id"])
    return {
        "model_id": model["id"],
        "status": model["status"],
        "redo_count": model["redo_count"],
        "confirm_requested_at": model.get("confirm_requested_at"),
        "confirmed_at": model.get("confirmed_at"),
        "cuts": [_cut_view(row, model_side=True) for row in cuts],
    }


@router.get("/model/test-cuts/{cut_id}/image")
async def model_test_cut_image(
    request: Request,
    cut_id: str,
    user_id: str = Depends(require_user),
):
    cut_id = _canonical_id(cut_id, noun="테스트컷")
    async with get_conn(request) as conn:
        cut = await _load_owned_cut(conn, cut_id, user_id)
    if cut is None:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    return await _stream_private_cut(request, cut)


async def _stream_private_cut(request: Request, cut: dict) -> Response:
    try:
        data = await asyncio.to_thread(_r2_face(request).get_bytes, cut["r2_key"])
    except Exception:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    return Response(
        content=data,
        media_type=cut["mime"],
        headers={"Cache-Control": "private, no-store"},
    )


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _err("invalid_image", "테스트컷 이미지를 읽을 수 없습니다.", status=422) from exc


def _resize_cover(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _err("invalid_image", "테스트컷 이미지를 읽을 수 없습니다.", status=422) from exc
    width, height = image.size
    scale = 1024 / max(width, height)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    image = image.resize(target, Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, format="WEBP", quality=90, method=6)
    return out.getvalue()


def _new_confirmation_cover_key(model_id: str, cut_id: str) -> str:
    """확정 시도마다 다른 키를 써서 경합에서 진 요청이 승자 커버를 지우지 않게 한다."""
    return model_catalog_cover_key(model_id, f"{cut_id}-{uuid.uuid4().hex}")


@router.post("/model/test-cuts/confirm", response_model=ConfirmTestCutResult)
async def confirm_test_cut(
    request: Request,
    body: ConfirmTestCutBody,
    user_id: str = Depends(require_user),
):
    cut_id = _canonical_id(body.approved_cut_id, noun="테스트컷")
    async with get_conn(request) as conn:
        cut = await _load_owned_cut(conn, cut_id, user_id)
    if cut is None:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    if cut["model_status"] != "awaiting_confirm":
        raise _err("not_awaiting_confirm", "확인 대기 중인 테스트컷이 아닙니다.", status=409)

    try:
        original = await asyncio.to_thread(_r2_face(request).get_bytes, cut["r2_key"])
    except Exception:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    cover = await asyncio.to_thread(_resize_cover, original)
    public_r2 = _r2_public(request)
    cover_key = _new_confirmation_cover_key(cut["model_id"], cut_id)
    await asyncio.to_thread(
        public_r2.put_bytes,
        cover_key,
        cover,
        "image/webp",
        IMMUTABLE_CACHE,
    )
    cover_url = public_r2.public_url(cover_key)
    committed = False
    try:
        async with get_conn(request) as conn:
            locked = await _load_owned_cut(conn, cut_id, user_id, for_update=True)
            if locked is None:
                raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
            if locked["model_status"] != "awaiting_confirm":
                raise _err("not_awaiting_confirm", "확인 대기 중인 테스트컷이 아닙니다.", status=409)
            async with conn.cursor() as cur:
                await cur.execute(
                    "update fm_model_test_cuts set approved = (id = %s) where model_id = %s",
                    (cut_id, locked["model_id"]),
                )
                await cur.execute(
                    """update fm_models set status = 'verified', confirmed_at = now(),
                              confirm_consent_version = %s, cover_image_url = %s
                        where id = %s and user_id = %s and status = 'awaiting_confirm'
                        returning confirmed_at""",
                    (CONSENT_DOC_VERSION, cover_key, locked["model_id"], user_id),
                )
                updated = await cur.fetchone()
            if updated is None:
                raise _err("not_awaiting_confirm", "확인 상태가 변경되었습니다.", status=409)
            await conn.commit()
            committed = True
    finally:
        if not committed:
            try:
                await asyncio.to_thread(public_r2.delete, cover_key)
            except Exception:
                logger.warning("uncommitted model cover cleanup failed", exc_info=True)
    return {
        "status": "verified",
        "confirmed_at": updated["confirmed_at"],
        "cover_image_url": cover_url,
    }


@router.post("/model/test-cuts/redo", response_model=RedoTestCutsResult)
async def redo_test_cuts(
    request: Request,
    body: RedoTestCutsBody,
    user_id: str = Depends(require_user),
):
    reason = (body.reason or "").strip() or None
    async with get_conn(request) as conn:
        model = await _load_owned_model(conn, user_id, for_update=True)
        if model is None:
            raise _err("not_found", "내 모델을 찾을 수 없습니다.", status=404)
        if model["redo_count"] >= 1:
            raise _err("redo_limit", "재생성은 1회까지예요.", status=409)
        if model["status"] != "awaiting_confirm":
            raise _err("not_awaiting_confirm", "확인 대기 중일 때만 다시 요청할 수 있습니다.", status=409)
        async with conn.cursor() as cur:
            await cur.execute(
                """update fm_models set status = 'pending', redo_count = redo_count + 1,
                          redo_reason = %s, confirm_requested_at = null
                    where id = %s and user_id = %s and status = 'awaiting_confirm' and redo_count < 1
                    returning redo_count""",
                (reason, model["id"], user_id),
            )
            updated = await cur.fetchone()
        if updated is None:
            raise _err("redo_limit", "재생성은 1회까지예요.", status=409)
        await conn.commit()
    return {"status": "pending", "redo_count": updated["redo_count"]}
