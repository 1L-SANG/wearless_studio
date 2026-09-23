"""FaceMarket 테스트컷 관리자 전송과 모델 본인 확인 게이트.

원본 테스트컷은 얼굴 전용 비공개 R2에 남고, 모델이 고른 컷의 1024px WebP 축소본만
일반 자산 버킷으로 승격한다. 모든 응답은 내부 R2 키 대신 인증 스트림 URI를 내보낸다.
"""

import asyncio
import logging
import uuid
from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field

from . import admin_guard, facemarket_notify, repo
from .agents.face_identity import SKIN_FINISH_CODES, SKIN_FINISH_DEFAULT
from .auth import require_user
from .db import get_conn
from .facemarket import _assert_account_open, _cover_serving_url, _model_id_or_404
from .facemarket_catalog_access import CatalogRole, catalog_access
from .facemarket_sponsorship import SponsorshipFields, sponsorship_view
from .models import CamelModel
from .personalization import CONSENT_DOC_VERSION
from .r2 import (
    IMMUTABLE_CACHE,
    ext_for_mime,
    model_catalog_cover_key,
    model_test_cut_key,
)
from .services import test_cut_build
from .workers import test_cut_build_reconciler

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket model test cuts"])

#: 한 모델의 테스트컷 전량 = 보정 3종 × (확대샷 2 + 전신 2) = 12장(2026-09-16 대표 결정).
#: 서버가 **고정 기준 원본 4장에 얼굴만 세 번 다시 그려** 만든다(services/test_cut_build.py).
MAX_TEST_CUTS = 12
#: 상한은 **(보정, 종류) 조합마다** 2장이다. 종류별 총합은 그 곱(보정 3종 × 2 = 6)이다.
MAX_TEST_CUTS_PER_KIND = 2
TEST_CUT_KIND_LIMITS = {"closeup": MAX_TEST_CUTS_PER_KIND * 3, "fullbody": MAX_TEST_CUTS_PER_KIND * 3}
#: 관리자가 한 보정을 골라 보낼 때 그 묶음에 있어야 하는 장수 — 등록자는 이 4장만 본다.
CUTS_PER_FINISH = {"closeup": MAX_TEST_CUTS_PER_KIND, "fullbody": MAX_TEST_CUTS_PER_KIND}
TEST_CUT_KINDS = {"closeup", "fullbody"}
MAX_TEST_CUT_BYTES = 25 * 1024 * 1024
ALLOWED_TEST_CUT_MIME = {"image/png", "image/jpeg", "image/webp"}


class TestCutView(CamelModel):
    id: str
    mime: str
    sort: int
    kind: str
    approved: bool | None = None
    created_at: datetime
    #: 이 컷을 만든 보정 prod|texture|soft50. 옛 컷은 null — 화면·승인이 그걸 견뎌야 한다.
    skin_finish: str | None = None
    image_uri: str


class TestCutBuildView(CamelModel):
    """테스트컷 자동 생성 한 건의 상태. 관리자 화면의 대기/생성 중/부분 완료/완료 표시."""

    id: str
    status: str
    requested: int = 0
    produced: int = 0
    error: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


class AdminModelCard(CamelModel):
    id: str
    display_name: str
    status: str
    enrollment_status: str | None = None
    test_cut_count: int = 0
    closeup_count: int = 0
    fullbody_count: int = 0
    cuts_complete: bool = False
    redo_requested: bool = False
    confirm_requested_at: datetime | None = None
    confirmed_at: datetime | None = None
    redo_count: int = 0
    ready_to_send: bool = False
    #: 보정별 구성 {prod: {closeup: 2, fullbody: 2}, ...}. 화면이 묶음마다 보낼 수 있는지 판단한다.
    finish_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    #: 지금 이 모델에 설정된 보정(= 보낸 보정). 아직 안 보냈으면 기본값 prod.
    skin_finish: str | None = None
    #: 자동 생성 진행. 없으면 null(옛 모델·마이그 미적용).
    build: TestCutBuildView | None = None
    #: 서버가 계산한 "지금 보내도 되는 상태". 화면은 이 값만 본다(_is_sendable 참고).
    sendable: bool = False
    test_cuts: list[TestCutView] = Field(default_factory=list)


class SendTestCutsBody(CamelModel):
    #: 등록자에게 보낼 보정 1종. 이 보정의 4장만 등록자에게 보인다.
    skin_finish: str


class SendTestCutsResult(CamelModel):
    status: str
    confirm_requested_at: datetime
    email_sent: bool
    skin_finish: str


class ProfileLicenseView(CamelModel):
    allowed_use: list[str] = Field(default_factory=list)
    forbidden_use: list[str] = Field(default_factory=list)
    unit_price: int
    valid_until: datetime | None
    valid_days: int | None


class ModelProfileView(SponsorshipFields):
    display_name: str
    gender: str | None = None
    #: "20대 초반"처럼 구간만. 생년월일·정확한 나이는 절대 내보내지 않는다(명세 §3.4).
    age_band: str | None = None
    height_cm: int | None = None
    height_bucket: str | None = None
    body_type: str | None = None
    license: ProfileLicenseView


class ModelTestCutsView(CamelModel):
    model_id: str
    status: str
    redo_count: int
    confirm_requested_at: datetime | None = None
    confirmed_at: datetime | None = None
    cuts: list[TestCutView] = Field(default_factory=list)
    profile: ModelProfileView | None = None


class ConfirmTestCutBody(CamelModel):
    closeup_cut_id: str
    fullbody_cut_id: str


class ConfirmTestCutResult(CamelModel):
    status: str
    confirmed_at: datetime
    closeup_image_url: str
    fullbody_image_url: str


class PublicModelItem(ModelProfileView):
    id: str
    closeup_image_url: str
    fullbody_image_url: str
    confirmed_at: datetime


class PublicModelsResult(CamelModel):
    items: list[PublicModelItem] = Field(default_factory=list)


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
        "kind": row["kind"],
        "approved": row.get("approved"),
        "created_at": row["created_at"],
        # 옛 컷은 None — 화면이 이름표 없이 보여 준다(승인도 그대로 돈다).
        "skin_finish": row.get("skin_finish_code"),
        "image_uri": uri,
    }


_MODEL_PROFILE_SELECT = """
select m.id::text as id, m.display_name, m.gender,
       a.height_cm, a.birthdate, m.height_bucket, m.body_type,
       l.allowed_use, l.forbidden_use, l.unit_price, l.license_valid_until,
       round(extract(epoch from (l.license_valid_until - l.created_at)) / 86400.0)::integer
         as license_valid_days,
       m.cover_image_url, m.fullbody_image_url, m.confirmed_at,
       m.sponsorship_enabled, m.instagram_handle, m.instagram_followers,
       m.instagram_followers_reported_at, m.size_top, m.size_bottom_waist,
       m.sponsorship_profile_consent_at
  from fm_models m
  join fm_biometric_enrollments e
    on e.id = m.current_enrollment_id and e.model_id = m.id
  -- 지원서는 left join — 플랫폼 대행 온보딩(enrollment.application_id null, fm_models.user_id null 허용)
  -- 모델은 지원서가 없다. inner join 이면 그 모델이 프로필·공개 목록에서 통째로 빠진다.
  -- 키(height_cm)는 그때 null 이고, 화면은 키 구간·체형으로 대신한다(명세 §3.4).
  left join fm_model_applications a on a.id = e.application_id
  join fm_licenses l
    on l.model_id = m.id and l.enrollment_id = m.current_enrollment_id
"""


async def _load_model_profiles(
    conn, *, model_id: str | None = None, public_only: bool = False,
    for_update: bool = False,
) -> list[dict]:
    """현재 enrollment의 활성 라이선스가 붙은 공개 프로필 원천만 읽는다.

    모델 확인 화면과 무인증 공개 목록이 같은 SQL/shape를 사용해야 미리보기와 실제
    카드가 어긋나지 않는다. 공개 목록 조건만 이 헬퍼의 ``public_only`` 분기로 추가한다.
    """
    clauses = [
        "l.status = 'active'",
        "nullif(btrim(l.vc_id), '') is not null",
        "(l.license_valid_until is null or l.license_valid_until > now())",
    ]
    params: tuple[str, ...] = ()
    if model_id is not None:
        clauses.insert(0, "m.id = %s")
        params = (model_id,)
    if public_only:
        # LIKE 패턴의 % 는 반드시 파라미터로 넘긴다. SQL 문자열에 직접 박으면 psycopg 가
        # 그 % 를 자리표시자로 읽어 execute 단계에서 ProgrammingError 를 던진다(운영 500).
        catalog_prefix = "facemarket/catalog/models/%"
        clauses.extend(
            [
                "m.status = 'verified'",
                "m.confirmed_at is not null",
                "m.cover_image_url like %s",
                "m.fullbody_image_url like %s",
            ]
        )
        params = params + (catalog_prefix, catalog_prefix)
    suffix = "order by m.confirmed_at desc limit 200" if public_only else "limit 1"
    if for_update:
        suffix += " for update of m, l"
    async with conn.cursor() as cur:
        await cur.execute(
            _MODEL_PROFILE_SELECT + " where " + " and ".join(clauses) + " " + suffix,
            params,
        )
        return await cur.fetchall()


def _today() -> date:
    """테스트가 날짜를 고정할 수 있게 한 겹 뗀다."""
    return date.today()


def _age_band(birthdate) -> str | None:
    """생년월일 → "20대 초반" 같은 구간. 셀러가 타깃 연령과 맞춰 보는 용도라 이 정도면 충분하고,
    정확한 나이는 개인정보라 내보내지 않는다. 0~3 초반, 4~6 중반, 7~9 후반."""
    if birthdate is None:
        return None
    if isinstance(birthdate, datetime):
        birthdate = birthdate.date()
    today = _today()
    age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
    if age < 20:
        return "10대"
    decade = (age // 10) * 10
    pos = age % 10
    part = "초반" if pos <= 3 else ("중반" if pos <= 6 else "후반")
    return f"{decade}대 {part}"


def _profile_view(row: dict, *, owner: bool = False, details: bool = True) -> dict:
    return {
        **sponsorship_view(row, owner=owner, details=details),
        "display_name": row["display_name"],
        "gender": row.get("gender"),
        "age_band": _age_band(row.get("birthdate")),
        "height_cm": row.get("height_cm"),
        "height_bucket": row.get("height_bucket"),
        "body_type": row.get("body_type"),
        "license": {
            "allowed_use": list(row.get("allowed_use") or []),
            "forbidden_use": [],
            "unit_price": row["unit_price"],
            "valid_until": row["license_valid_until"],
            "valid_days": row["license_valid_days"],
        },
    }


async def _load_admin_model(conn, model_id: str, *, for_update: bool = False) -> dict | None:
    lock = " for update of m" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select m.id::text as id, m.status, m.redo_count, m.fullbody_image_url,
                      e.status as enrollment_status,
                      coalesce(a.contact_email, u.email) as contact_email,
                      exists (
                        select 1 from fm_licenses l
                         where l.model_id = m.id
                           and l.enrollment_id = m.current_enrollment_id
                           and l.status = 'active'
                           and nullif(btrim(l.vc_id), '') is not null
                           and (l.license_valid_until is null or l.license_valid_until > now())
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
            """select id::text as id, status, redo_count, confirm_requested_at, confirmed_at,
                      skin_finish_code
                 from fm_models
                where user_id = %s
                order by created_at desc limit 1""" + lock,
            (user_id,),
        )
        return await cur.fetchone()


async def _load_cuts(conn, model_id: str, *, skin_finish: str | None = None) -> list[dict]:
    """skin_finish 를 주면 **그 보정 묶음만**. 등록자는 관리자가 보낸 한 묶음만 본다.

    옛 컷(보정 없음)은 coalesce 로 같이 잡는다 — 손으로 올린 4장짜리 모델이 화면에서 빈 채로
    남으면 그 사람은 확정을 못 한다.
    """
    where, params = "model_id = %s", [model_id]
    if skin_finish is not None:
        where += " and coalesce(skin_finish_code, %s) = %s"
        params += [skin_finish, skin_finish]
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, mime, sort, kind, approved, created_at, skin_finish_code
                 from fm_model_test_cuts
                where """ + where + " order by sort, created_at",
            tuple(params),
        )
        return await cur.fetchall()


async def _load_cut_for_admin(
    conn, model_id: str, cut_id: str, *, for_update: bool = False
) -> dict | None:
    lock = " for update" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, r2_key, mime, kind, approved, skin_finish_code
                 from fm_model_test_cuts
                where id = %s and model_id = %s""" + lock,
            (cut_id, model_id),
        )
        return await cur.fetchone()


async def _load_owned_cut(
    conn, cut_id: str, user_id: str, *, for_update: bool = False
) -> dict | None:
    lock = " for update of m, c" if for_update else ""
    async with conn.cursor() as cur:
        await cur.execute(
            """select c.id::text as id, c.r2_key, c.mime, c.sort, c.kind,
                      c.approved, c.created_at, c.skin_finish_code,
                      m.id::text as model_id, m.status as model_status, m.redo_count,
                      m.display_name
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
        await _require_admin(conn, user_id, request)
        async with conn.cursor() as cur:
            await cur.execute(
                """select m.id::text as id, m.display_name, m.status,
                          e.status as enrollment_status,
                          count(c.id)::integer as test_cut_count,
                          count(c.id) filter (where c.kind = 'closeup')::integer
                            as closeup_count,
                          count(c.id) filter (where c.kind = 'fullbody')::integer
                            as fullbody_count,
                          (count(c.id) filter (where c.kind = 'closeup') = %s
                           and count(c.id) filter (where c.kind = 'fullbody') = %s)
                            as cuts_complete,
                          m.skin_finish_code as skin_finish,
                          (m.status = 'pending' and m.redo_count > 0) as redo_requested,
                          m.confirm_requested_at, m.confirmed_at, m.redo_count,
                          (e.status = 'passed' and exists (
                            select 1 from fm_licenses l
                             where l.model_id = m.id
                               and l.enrollment_id = m.current_enrollment_id
                               and l.status = 'active'
                               and nullif(btrim(l.vc_id), '') is not null
                               and (l.license_valid_until is null or l.license_valid_until > now())
                          )) as ready_to_send,
                          (m.status in ('pending', 'awaiting_confirm', 'reverification_required')
                           or (m.status = 'verified' and m.fullbody_image_url is null))
                            as sendable,
                          coalesce(
                            jsonb_agg(jsonb_build_object(
                              'id', c.id::text, 'mime', c.mime, 'sort', c.sort,
                              'kind', c.kind, 'approved', c.approved,
                              'skin_finish_code', c.skin_finish_code,
                              'created_at', c.created_at
                            ) order by c.sort) filter (where c.id is not null),
                            '[]'::jsonb
                          ) as test_cuts
                     from fm_models m
                     left join fm_biometric_enrollments e on e.id = m.current_enrollment_id
                     left join fm_model_test_cuts c on c.model_id = m.id
                    where m.id = %s
                    group by m.id, e.status""",
                (TEST_CUT_KIND_LIMITS["closeup"], TEST_CUT_KIND_LIMITS["fullbody"], mid),
            )
            row = await cur.fetchone()
        build = await test_cut_build_reconciler.latest_build(conn, mid)
    if row is None:
        raise _err("model_not_found", "모델을 찾을 수 없어요.", status=404)
    data = dict(row)
    cuts = row.get("test_cuts") or []
    data["test_cuts"] = [_cut_view({**cut, "model_id": row["id"]}) for cut in cuts]
    # 보정별 구성은 **파이썬에서** 센다. SQL 로 묶으려면 보정 목록을 cross join 해야 하는데,
    # 그러면 같은 질의의 count(c.id) 들이 보정 수만큼 부풀어 cuts_complete 가 거짓이 된다.
    data["finish_counts"] = finish_counts(cuts)
    data["build"] = dict(build) if build else None
    return data


def finish_counts(cuts) -> dict[str, dict[str, int]]:
    """보정별 {closeup, fullbody} 장수. 보정 없는 옛 컷은 기본값(prod) 묶음으로 센다."""
    out = {code: {"closeup": 0, "fullbody": 0} for code in SKIN_FINISH_CODES}
    for cut in cuts:
        code = cut.get("skin_finish_code") or SKIN_FINISH_DEFAULT
        kind = cut.get("kind")
        if code in out and kind in out[code]:
            out[code][kind] += 1
    return out


@router.post(
    "/admin/models/{model_id}/test-cuts",
    response_model=list[TestCutView],
    status_code=201,
)
async def admin_upload_test_cuts(
    request: Request,
    model_id: str,
    images: list[UploadFile] = File(...),
    kind: str | None = Form(default=None),
    skin_finish: str | None = Form(default=None),
    user_id: str = Depends(require_user),
):
    model_id = _canonical_id(model_id)
    kind = kind or ""
    if kind not in TEST_CUT_KINDS:
        raise _err("invalid_kind", "테스트컷 종류를 올바르게 선택해 주세요.")
    if not 1 <= len(images) <= MAX_TEST_CUTS_PER_KIND:
        raise _err("invalid_cut_count",
                   f"테스트컷은 한 번에 1~{MAX_TEST_CUTS_PER_KIND}장 올려 주세요.")

    # multipart 파싱은 프레임워크가 맡지만 실제 파일 바이트는 관리자 확인 뒤에만 읽는다.
    # 비관리자가 큰 업로드를 반복해 애플리케이션 메모리를 쓰는 경로를 닫는다.
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        if await _load_admin_model(conn, model_id) is None:
            raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
        existing_slots = await _load_cut_slots(conn, model_id)
    # 보정 판정은 **관리자 확인 뒤**다 — 비관리자에게는 요청 모양을 따져 줄 이유가 없고,
    # 그 순서가 뒤집히면 403 이어야 할 응답이 400 으로 나간다.
    finish = _parse_skin_finish(skin_finish)
    if finish is None:
        # 손으로 올리는 경로에도 보정이 있어야 한다 — 없으면 어느 묶음인지 알 수 없어
        # 전송에서 영원히 빠진다(2026-09-16 대표 지시: 업로드 API 는 보정 필수).
        raise _err("skin_finish_required", _FINISH_HELP)
    _ensure_cut_capacity(existing_slots, kind, len(images), finish)

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
            await _require_admin(conn, user_id, request)
            if await _load_admin_model(conn, model_id, for_update=True) is None:
                raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
            slots = await _load_cut_slots(conn, model_id)
            _ensure_cut_capacity(slots, kind, len(prepared), finish)
            used = {slot["sort"] for slot in slots}
            available = [value for value in range(MAX_TEST_CUTS) if value not in used]
            rows = []
            async with conn.cursor() as cur:
                for item, sort in zip(prepared, available[:len(prepared)], strict=True):
                    await cur.execute(
                        """insert into fm_model_test_cuts
                             (id, model_id, r2_key, mime, kind, sort, skin_finish_code)
                           values (%s, %s, %s, %s, %s, %s, %s)
                           returning id::text as id, mime, sort, kind, approved, created_at,
                                     skin_finish_code""",
                        (item["id"], model_id, item["key"], item["mime"], kind, sort, finish),
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


#: 관리자 화면에 보이는 보정 이름 — 셋을 나란히 놓고 하나를 고른다.
SKIN_FINISH_LABEL = {
    "prod": "A 매끈하게(지금 기본)",
    "texture": "B 결 살리기",
    "soft50": "C 중간",
}
_FINISH_HELP = "보정은 prod, texture, soft50 중 하나예요."


def _parse_skin_finish(value) -> str | None:
    """폼·본문 값 → 보정 코드. 빈 값은 None(옛 컷과 같다), 모르는 값은 400.

    조용히 기본값(prod)으로 바꾸지 않는다 — "결 살리기"로 만들려던 컷이 "매끈하게" 로
    기록되면 관리자가 고른 것과 다른 값이 그 사람의 설정이 된다.
    """
    # FastAPI 를 거치지 않는 직접 호출은 Form(default=None) 센티널을 그대로 넘긴다 —
    # 그걸 "값이 있다" 로 읽으면 없는 오류가 생긴다. 실제 문자열만 본다.
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "":
        return None
    if text not in SKIN_FINISH_CODES:
        raise _err("invalid_skin_finish", _FINISH_HELP)
    return text


async def _load_cut_slots(conn, model_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select sort, kind, skin_finish_code from fm_model_test_cuts "
            "where model_id = %s order by sort",
            (model_id,),
        )
        return await cur.fetchall()


def _ensure_cut_capacity(slots: list[dict], kind: str, incoming: int,
                         skin_finish: str | None = None) -> None:
    """상한은 **(보정, 종류) 조합마다** 2장이다.

    종류 총합만 보면 한 보정에 6장이 몰려도 통과하는데, 그러면 그 보정 묶음만 전송 가능해지고
    나머지 둘은 영영 못 보낸다 — 관리자가 셋을 비교할 수 없게 된다.
    """
    code = skin_finish or SKIN_FINISH_DEFAULT
    label = "확대샷" if kind == "closeup" else "전신샷"
    pair_count = sum(
        slot.get("kind") == kind
        and (slot.get("skin_finish_code") or SKIN_FINISH_DEFAULT) == code
        for slot in slots
    )
    if pair_count + incoming > MAX_TEST_CUTS_PER_KIND:
        raise _err(
            "cut_limit",
            f"{SKIN_FINISH_LABEL.get(code, code)} {label}은 "
            f"{MAX_TEST_CUTS_PER_KIND}장까지 올릴 수 있어요.",
            status=409,
        )
    if len(slots) + incoming > MAX_TEST_CUTS:
        raise _err("cut_limit", f"테스트컷은 {MAX_TEST_CUTS}장까지예요.", status=409)


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
            await _require_admin(conn, user_id, request)
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


SENDABLE_STATUSES = {"pending", "awaiting_confirm", "reverification_required"}


def _is_sendable(model: dict) -> bool:
    """검증 전 상태이거나, 전신샷 없이 확정된 옛(1장 선택) 모델이면 보낼 수 있다.

    옛 모델은 전신샷이 없어 공개 목록(§3.3)에 없으므로 다시 awaiting_confirm 으로 돌려도
    잃는 게 없다. 두 장으로 이미 공개된 모델은 막는다 — 재전송하면 status 가 바뀌어 셀러
    카탈로그에서도 사라지기 때문(2026-09-07 Codex 검증 P1)."""
    if model["status"] in SENDABLE_STATUSES:
        return True
    return model["status"] == "verified" and not model.get("fullbody_image_url")


def _not_sendable_message(model: dict) -> str:
    if model["status"] == "verified":
        return "이미 공개된 모델이에요. 지금은 테스트컷을 다시 보낼 수 없어요."
    return "테스트컷을 보낼 수 없는 모델 상태입니다."


@router.post("/admin/models/{model_id}/send-test-cuts", response_model=SendTestCutsResult)
async def admin_send_test_cuts(
    request: Request,
    model_id: str,
    body: SendTestCutsBody,
    user_id: str = Depends(require_user),
):
    """고른 보정 **한 묶음(4장)** 만 등록자에게 보낸다.

    등록자는 보정을 고르지 않는다(2026-09-16 대표 결정) — 셋을 비교하는 건 관리자 일이고,
    등록자는 받은 4장에서 확대샷 1 + 전신 1 을 고를 뿐이다.
    """
    model_id = _canonical_id(model_id)
    finish = _parse_skin_finish(body.skin_finish)
    if finish is None:
        raise _err("invalid_skin_finish", _FINISH_HELP)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        model = await _load_admin_model(conn, model_id, for_update=True)
        if model is None:
            raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
        if not _is_sendable(model):
            raise _err("model_not_sendable", _not_sendable_message(model), status=409)
        if model.get("enrollment_status") != "passed" or model.get("has_active_license") is not True:
            raise _err(
                "model_registration_incomplete",
                "등록과 VC 발급이 끝난 모델에게만 테스트컷을 보낼 수 있습니다.",
                status=409,
            )
        async with conn.cursor() as cur:
            await cur.execute(
                """select count(*) filter (where kind = 'closeup')::integer as closeup_count,
                          count(*) filter (where kind = 'fullbody')::integer as fullbody_count
                     from fm_model_test_cuts
                    where model_id = %s and coalesce(skin_finish_code, %s) = %s""",
                (model_id, finish, finish),
            )
            counts = await cur.fetchone()
            if (
                counts["closeup_count"] != CUTS_PER_FINISH["closeup"]
                or counts["fullbody_count"] != CUTS_PER_FINISH["fullbody"]
            ):
                # 고른 보정 묶음이 4장이 아니면 보내지 않는다 — 반쪽 묶음을 보내면 등록자가
                # 확대샷이나 전신샷 중 하나를 못 골라 확정 자체가 막힌다.
                raise _err(
                    "test_cuts_incomplete",
                    f"{SKIN_FINISH_LABEL.get(finish, finish)} 묶음은 확대샷 "
                    f"{CUTS_PER_FINISH['closeup']}장과 전신샷 "
                    f"{CUTS_PER_FINISH['fullbody']}장이 다 있어야 보낼 수 있어요.",
                    status=409,
                )
            # ★ 보낸 보정이 곧 그 사람의 설정이다 — **같은 트랜잭션**에서 적는다. 따로 쓰면
            #   "보내긴 했는데 보정은 예전 값" 인 중간 상태가 생기고, 등록자가 확정하는 순간
            #   고르지도 않은 얼굴이 그 사람의 상품이 된다.
            await cur.execute(
                """update fm_models set status = 'awaiting_confirm', confirm_requested_at = now(),
                          skin_finish_code = %s
                    where id = %s returning confirm_requested_at, skin_finish_code""",
                (finish, model_id),
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
        "skin_finish": updated["skin_finish_code"],
    }


@router.post(
    "/admin/models/{model_id}/test-cuts/build",
    response_model=TestCutBuildView,
    status_code=202,
    summary="테스트컷 12장 자동 생성(수동 실행 · 다시 생성)",
)
async def admin_build_test_cuts(
    request: Request,
    model_id: str,
    user_id: str = Depends(require_user),
):
    """큐에 한 건 넣는다. 실제 생성은 reconciler 가 한다 — 한 건이 얼굴 파드를 몇 분 쓴다.

    평소에는 학습이 끝나 ready LoRA 행이 붙는 순간 저절로 들어간다(services/model_lora.register).
    이 엔드포인트는 그 자동 경로가 안 돌았거나(플래그 off 시절) 다시 만들고 싶을 때의 손잡이다.
    **승인된 컷은 지우지 않는다** — 다시 생성은 승인 전 컷만 갈아 끼운다(test_cut_build.clear_cuts).
    """
    mid = _canonical_id(model_id)
    async with get_conn(request) as conn:
        await _require_admin(conn, user_id, request)
        if await _load_admin_model(conn, mid) is None:
            raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id from fm_model_loras "
                "where model_id = %s and status = 'ready' limit 1",
                (mid,),
            )
            lora = await cur.fetchone()
        if lora is None:
            # 학습이 안 끝났으면 만들 얼굴이 없다. 조용히 큐에 넣으면 20분 뒤 실패로만 보인다.
            raise _err(
                "lora_not_ready",
                "학습이 끝난 LoRA 가 없어요. 학습이 끝난 뒤에 생성할 수 있어요.",
                status=409,
            )
        build_id = await test_cut_build.enqueue(conn, mid, lora_id=lora["id"])
        await conn.commit()
        if build_id is None:
            raise _err("build_in_progress", "이미 생성 중이거나 대기 중이에요.", status=409)
        build = await test_cut_build_reconciler.latest_build(conn, mid)
    return dict(build) if build else {"id": build_id, "status": "queued"}


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
        await _require_admin(conn, user_id, request)
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
        # ★ 보낸 보정 묶음만. 나머지 8장은 등록자에게 보이지 않는다 — 고르는 건 관리자 일이다.
        cuts = await _load_cuts(conn, model["id"], skin_finish=model.get("skin_finish_code")
                                or SKIN_FINISH_DEFAULT)
        profiles = await _load_model_profiles(conn, model_id=model["id"])
    return {
        "model_id": model["id"],
        "status": model["status"],
        "redo_count": model["redo_count"],
        "confirm_requested_at": model.get("confirm_requested_at"),
        "confirmed_at": model.get("confirmed_at"),
        "cuts": [_cut_view(row, model_side=True) for row in cuts],
        "profile": _profile_view(profiles[0], owner=True) if profiles else None,
    }


class CatalogAccessResult(CamelModel):
    allowed: bool
    role: CatalogRole | None = None


@router.get("/catalog-access", response_model=CatalogAccessResult)
async def get_catalog_access(
    request: Request, response: Response, user_id: str = Depends(require_user),
):
    """이 계정이 모델 리스트를 볼 수 있는지. 화면(/models)이 목록과 안내 중 무엇을 그릴지 정한다.

    볼 수 있는 계정 = 관리자, 셀러 약관 동의 계정, 2차 등록(라이선스 발급)까지 마친 모델.
    정의와 근거는 facemarket_catalog_access 모듈 설명에 있다.
    """
    async with get_conn(request) as conn:
        access = await catalog_access(conn, user_id)
    response.headers["Cache-Control"] = "no-store"
    return {"allowed": access["allowed"], "role": access["role"]}


@router.get("/public/models", response_model=PublicModelsResult)
async def public_models(
    request: Request, response: Response, user_id: str = Depends(require_user),
):
    """모델 리스트. 등록된 셀러와 2차 등록까지 마친 모델만 본다(2026-09-23 오너 결정,
    facemarket_catalog_access 참고).
    비로그인은 401, 로그인했지만 자격이 없으면 403 이다. 주소의 public 은 역사적 이름이다.

    협찬 상세(계정·팔로워·사이즈)는 그중 **셀러**에게만 실어요(E-2b 동의 범위).
    셀러 = 셀러 약관 동의 기록(seller_consents)이 있는 계정. 모델 계정에게는 배지만 가요.
    """
    async with get_conn(request) as conn:
        access = await catalog_access(conn, user_id)
        if not access["allowed"]:
            raise _err("members_only", "모델 리스트는 등록된 셀러와 모델만 볼 수 있어요.", status=403)
        rows = await _load_model_profiles(conn, public_only=True)
    seller = access["seller"]
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                **_profile_view(row, details=seller),
                "closeup_image_url": _cover_serving_url(
                    request, row["cover_image_url"]
                ),
                "fullbody_image_url": _cover_serving_url(
                    request, row["fullbody_image_url"]
                ),
                "confirmed_at": row["confirmed_at"],
            }
        )
    response.headers["Cache-Control"] = "no-store"
    return {"items": items}


class SponsorshipInterestResult(CamelModel):
    interested: bool


async def _sponsorship_target(conn, model_id: str) -> None:
    rows = await _load_model_profiles(conn, model_id=model_id, public_only=True, for_update=True)
    if not rows or not rows[0].get("sponsorship_enabled"):
        raise _err("not_found", "협찬을 받는 공개 모델을 찾을 수 없습니다.", status=404)


@router.get("/models/{model_id}/sponsorship-interest", response_model=SponsorshipInterestResult)
async def get_sponsorship_interest(
    model_id: str, request: Request, response: Response,
    user_id: str = Depends(require_user),
):
    model_id = _model_id_or_404(model_id)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _sponsorship_target(conn, model_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """select exists (select 1 from fm_sponsorship_interest
                    where seller_user_id = %s and model_id = %s) as interested""",
                (user_id, model_id),
            )
            result = await cur.fetchone()
    response.headers["Cache-Control"] = "no-store, private"
    return result


@router.post("/models/{model_id}/sponsorship-interest", response_model=SponsorshipInterestResult)
async def create_sponsorship_interest(
    model_id: str, request: Request, response: Response,
    user_id: str = Depends(require_user),
):
    model_id = _model_id_or_404(model_id)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _sponsorship_target(conn, model_id)
        async with conn.cursor() as cur:
            await cur.execute(
                """insert into fm_sponsorship_interest (seller_user_id, model_id)
                    values (%s, %s) on conflict (seller_user_id, model_id) do nothing""",
                (user_id, model_id),
            )
        await conn.commit()
    response.headers["Cache-Control"] = "no-store, private"
    return {"interested": True}


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


async def _enable_ready_lora(cur, model_id: str) -> str | None:
    """모델 승인 = 얼굴 참조 자산 사용 시작. 붙여만 둔 최신 LoRA 를 켠다.

    seed_model_lora 는 승인 전 모델에 status='ready', enabled=false 로 행만 붙인다
    (본인이 테스트컷을 확인하기 전에 착용컷에 쓰이면 안 된다). 켜는 시점은 여기 한 곳이다.
    partial unique index(fm_model_loras_one_enabled_uidx)가 모델당 켜진 행 하나만 허용하므로
    **먼저 끄고** 켠다. 붙은 행이 없으면 아무것도 안 하고 None 을 준다(=verified 만).
    """
    await cur.execute(
        """select id::text as id from fm_model_loras
            where model_id = %s and status = 'ready'
            order by version desc
            limit 1
            for update""",
        (model_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    await cur.execute(
        "update fm_model_loras set enabled = false where model_id = %s and id <> %s and enabled",
        (model_id, row["id"]),
    )
    await cur.execute(
        "update fm_model_loras set enabled = true where id = %s and not enabled",
        (row["id"],),
    )
    return row["id"]


@router.post("/model/test-cuts/confirm", response_model=ConfirmTestCutResult)
async def confirm_test_cut(
    request: Request,
    body: ConfirmTestCutBody,
    user_id: str = Depends(require_user),
):
    closeup_cut_id = _canonical_id(body.closeup_cut_id, noun="테스트컷")
    fullbody_cut_id = _canonical_id(body.fullbody_cut_id, noun="테스트컷")
    async with get_conn(request) as conn:
        closeup_cut = await _load_owned_cut(conn, closeup_cut_id, user_id)
        fullbody_cut = await _load_owned_cut(conn, fullbody_cut_id, user_id)
    _validate_confirmation_cuts(closeup_cut, fullbody_cut)

    try:
        face_r2 = _r2_face(request)
        closeup_original, fullbody_original = await asyncio.gather(
            asyncio.to_thread(face_r2.get_bytes, closeup_cut["r2_key"]),
            asyncio.to_thread(face_r2.get_bytes, fullbody_cut["r2_key"]),
        )
    except Exception:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    closeup_image, fullbody_image = await asyncio.gather(
        asyncio.to_thread(_resize_cover, closeup_original),
        asyncio.to_thread(_resize_cover, fullbody_original),
    )
    public_r2 = _r2_public(request)
    closeup_key = _new_confirmation_cover_key(
        closeup_cut["model_id"], closeup_cut_id
    )
    fullbody_key = _new_confirmation_cover_key(
        closeup_cut["model_id"], fullbody_cut_id
    )
    stored_keys: list[str] = []
    committed = False
    enabled_lora: str | None = None
    try:
        for key, image in (
            (closeup_key, closeup_image),
            (fullbody_key, fullbody_image),
        ):
            await asyncio.to_thread(
                public_r2.put_bytes,
                key,
                image,
                "image/webp",
                IMMUTABLE_CACHE,
            )
            stored_keys.append(key)

        async with get_conn(request) as conn:
            locked_closeup = await _load_owned_cut(
                conn, closeup_cut_id, user_id, for_update=True
            )
            locked_fullbody = await _load_owned_cut(
                conn, fullbody_cut_id, user_id, for_update=True
            )
            _validate_confirmation_cuts(locked_closeup, locked_fullbody)
            await _ensure_active_license(conn, locked_closeup["model_id"])
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_model_test_cuts
                          set approved = (id in (%s, %s))
                        where model_id = %s""",
                    (closeup_cut_id, fullbody_cut_id, locked_closeup["model_id"]),
                )
                # 보정은 **전송 때** 이미 정해졌다(admin_send_test_cuts) — 여기서 건드리지
                # 않는다. 등록자는 받은 4장에서 1+1 을 고를 뿐이고, 그 넷은 전부 같은 보정이다.
                await cur.execute(
                    """update fm_models set status = 'verified', confirmed_at = now(),
                              confirm_consent_version = %s, cover_image_url = %s,
                              fullbody_image_url = %s
                        where id = %s and user_id = %s and status = 'awaiting_confirm'
                        returning confirmed_at, skin_finish_code""",
                    (
                        CONSENT_DOC_VERSION,
                        closeup_key,
                        fullbody_key,
                        locked_closeup["model_id"],
                        user_id,
                    ),
                )
                updated = await cur.fetchone()
                if updated is not None:
                    # verified 로 바꾸는 **같은 트랜잭션**에서 켠다 — 승인은 됐는데 얼굴 자산은
                    # 꺼져 있는 중간 상태가 생기면 "얼굴이 안 바뀐다" 로만 나타난다.
                    enabled_lora = await _enable_ready_lora(cur, locked_closeup["model_id"])
            if updated is None:
                raise _err("not_awaiting_confirm", "확인 상태가 변경되었습니다.", status=409)
            await conn.commit()
            committed = True
            if enabled_lora:
                logger.info("model lora enabled on confirm model=%s lora=%s",
                            locked_closeup["model_id"], enabled_lora)
    finally:
        if not committed:
            for key in stored_keys:
                try:
                    await asyncio.to_thread(public_r2.delete, key)
                except Exception:
                    logger.warning("uncommitted model cover cleanup failed", exc_info=True)

    settings = request.app.state.settings
    admin_base = settings.fm_application_public_base.replace(
        "facemarket.", "admin."
    ).rstrip("/")
    try:
        await facemarket_notify.notify_slack_model_confirmed(
            settings,
            display_name=locked_closeup["display_name"],
            admin_link=f"{admin_base}/models",
        )
    except Exception:
        logger.warning("model confirmation slack dispatch failed", exc_info=True)
    return {
        "status": "verified",
        "confirmed_at": updated["confirmed_at"],
        "closeup_image_url": public_r2.public_url(closeup_key),
        "fullbody_image_url": public_r2.public_url(fullbody_key),
    }


async def _ensure_active_license(conn, model_id: str) -> None:
    """확정 순간에 현재 enrollment 의 라이선스가 살아 있어야 한다.

    전송 뒤 모델이 라이선스를 해지했거나(ModelLicense 화면) 만료됐으면 verified 로 바꿔도
    공개 목록 조건(§3.3)을 못 넘어 "모델 리스트에 올라갔어요"가 거짓이 된다. 잠금 안에서
    확인해 상태 전이와 공개 조건을 한 묶음으로 만든다(2026-09-07 Codex 검증 P1)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select exists (
                     select 1 from fm_licenses l
                       join fm_models m on m.id = l.model_id
                      where l.model_id = %s
                        and l.enrollment_id = m.current_enrollment_id
                        and l.status = 'active'
                        and nullif(btrim(l.vc_id), '') is not null
                        and (l.license_valid_until is null or l.license_valid_until > now())
                   ) as license_ok""",
            (model_id,),
        )
        row = await cur.fetchone()
    if not row or not row.get("license_ok"):
        raise _err(
            "license_inactive",
            "라이선스가 활성 상태가 아니에요. 라이선스를 다시 발급한 뒤 확정해 주세요.",
            status=409,
        )


def _validate_confirmation_cuts(closeup_cut: dict | None, fullbody_cut: dict | None) -> None:
    if closeup_cut is None or fullbody_cut is None:
        raise _err("not_found", "테스트컷을 찾을 수 없습니다.", status=404)
    if (
        closeup_cut.get("kind") != "closeup"
        or fullbody_cut.get("kind") != "fullbody"
        or closeup_cut["model_id"] != fullbody_cut["model_id"]
    ):
        raise _err(
            "kind_mismatch",
            "확대샷과 전신샷을 각각 한 장씩 선택해 주세요.",
        )
    if (
        closeup_cut["model_status"] != "awaiting_confirm"
        or fullbody_cut["model_status"] != "awaiting_confirm"
    ):
        raise _err(
            "not_awaiting_confirm",
            "확인 대기 중인 테스트컷이 아닙니다.",
            status=409,
        )


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
