"""Mall connections, reusable defaults and Cafe24 registration requests."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, StrictInt, field_validator
from starlette.responses import JSONResponse, RedirectResponse

from . import repo
from .auth import require_user
from .config import is_allowed_mall, registration_enabled
from .db import get_conn
from .detail_publications import _assert_account_open, publication_files
from .routes import _wake_dispatcher
from .services.cafe24_client import (
    ERROR_COPY as CAFE24_ERROR_COPY,
    Cafe24Client, ConnectionChanged, ReconnectRequired, SignatureInvalid,
    build_authorize_url, classify_error as classify_cafe24_error, derive_state_key,
    make_signed, normalize_mall_id, parse_signed,
)
from .services.cafe24_tokens import call_with_token
from .services.zigzag_client import ERROR_COPY, ZigzagClient, classify_error


router = APIRouter(prefix="/v1/malls", tags=["Mall connections"])
project_router = APIRouter(prefix="/v1/projects", tags=["Mall registrations"])
_INSTALL_COOKIE = "wl_cafe24_install"
_INSTALL_PATH = "/v1/malls/cafe24"
_CATEGORY_CACHE = {}


def _error(code, message, status=400):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class ConnectionKeys(BaseModel):
    access_key: SecretStr = Field(alias="accessKey", min_length=1, max_length=256)
    secret_key: SecretStr = Field(alias="secretKey", min_length=1, max_length=256)

    @field_validator("access_key", "secret_key", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Cafe24Install(BaseModel):
    mallId: str = Field(max_length=2048)
    returnTo: str | None = Field(default=None, max_length=2048)


class Cafe24Registration(BaseModel):
    projectId: uuid.UUID
    publicationId: uuid.UUID
    requestKey: uuid.UUID
    productName: str = Field(min_length=1, max_length=100)
    price: StrictInt = Field(ge=1, le=2147483647)
    supplyPrice: StrictInt | None = Field(default=None, ge=0)
    display: Literal["T", "F"] = "T"
    selling: Literal["T", "F"] = "T"
    taxType: Literal["A", "B"] = "A"
    categoryNo: StrictInt | None = Field(default=None, ge=1)
    thumbnailAssetId: uuid.UUID | None = None


class ShippingPatch(BaseModel):
    feeType: Literal["FREE", "CHARGED", "CONDITIONAL_FREE"] | None = None
    baseFee: StrictInt | None = Field(default=None, ge=0)
    jejuFee: StrictInt | None = Field(default=None, ge=0)
    isolatedFee: StrictInt | None = Field(default=None, ge=0)
    conditionalAmount: StrictInt | None = Field(default=None, ge=0)


class ReturnsPatch(BaseModel):
    partialFee: StrictInt | None = Field(default=None, ge=0)
    totalFee: StrictInt | None = Field(default=None, ge=0)
    exchangeFee: StrictInt | None = Field(default=None, ge=0)


class AddressesPatch(BaseModel):
    shippingAddressId: str | None = Field(default=None, max_length=256)
    returnAddressId: str | None = Field(default=None, max_length=256)


class DisclosurePatch(BaseModel):
    country: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=200)
    asManager: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30, pattern=r"^[0-9-]*$")


class DefaultsPatch(BaseModel):
    shipping: ShippingPatch | None = None
    returns: ReturnsPatch | None = None
    addresses: AddressesPatch | None = None
    disclosure: DisclosurePatch | None = None
    taxType: Literal["TAX", "FREE"] | None = None
    productOrigin: Literal["domestic", "imported"] | None = None


def _require_kek(request):
    kek = request.app.state.settings.mall_keys_kek
    if not kek:
        raise _error("mall_connect_unconfigured", "쇼핑몰 연결이 아직 준비되지 않았어요", 503)
    return kek


def _client(request, access_key, secret_key):
    settings = request.app.state.settings
    return ZigzagClient(settings.zigzag_openapi_url, access_key, secret_key,
                        algorithm=settings.zigzag_hmac_algorithm)


def _connection_result(row, mall):
    result = {"mall": mall, "status": "connected" if row else "none",
            "shopName": row.get("shop_name") if row else None,
            "keyHint": row.get("key_hint") if row else None,
            "permissions": row.get("permissions", []) if row else [],
            "connectedAt": row.get("connected_at") if row else None,
            "lastRegisteredAt": row.get("last_registered_at") if row else None}
    if mall == "cafe24":
        result.pop("keyHint")
        if row and (not row.get("refresh_expires_at")
                    or row["refresh_expires_at"] <= datetime.now(timezone.utc)):
            result["status"] = "reconnect_required"
        result["mallId"] = row.get("shop_id") if row else None
        result["tokenExpiresAt"] = row.get("token_expires_at") if row else None
    return result


def _defaults_result(row):
    row = row or {}
    return {**{name: row.get(name) or {} for name in ("shipping", "returns", "addresses", "disclosure")},
            "taxType": row.get("tax_type", "TAX"), "productOrigin": row.get("product_origin", "domestic"),
            "completedItems": row.get("completed_items", []), "total": 4}


@router.get("")
async def list_malls(request: Request, response: Response, user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        row = await repo.get_mall_connection(conn, user_id=user_id, mall="zigzag")
        defaults = await repo.get_mall_defaults(conn, user_id=user_id, mall="zigzag")
        cafe24 = await repo.get_mall_connection(conn, user_id=user_id, mall="cafe24")
    cafe24_item = _connection_result(cafe24, "cafe24")
    if not request.app.state.settings.cafe24_ready:
        cafe24_item["status"] = "unavailable"
    return {"items": [{**_connection_result(row, "zigzag"), "defaults": _defaults_result(defaults)},
                      cafe24_item, {"mall": "ably", "status": "unavailable"}, {"mall": "smartstore", "status": "unavailable"}]}


async def _verify_and_save(request, body, user_id, *, rotate=False):
    kek = _require_kek(request)
    # Commit the attempt before the external request so failed verification also consumes a slot.
    async with get_conn(request) as conn:
        allowed = await repo.claim_mall_connection_attempt(conn, user_id=user_id)
        previous = await repo.get_mall_connection(conn, user_id=user_id, mall="zigzag") if rotate and allowed else None
        await conn.commit()
    if not allowed:
        raise _error("rate_limited", "한 시간에 10번까지 확인할 수 있어요. 잠시 뒤 다시 시도해 주세요", 429)
    if rotate and not previous:
        raise _error("connection_changed", "연결 정보가 바뀌었어요. 현재 연결을 확인한 뒤 다시 시도해 주세요", 409)
    access_key, secret_key = body.access_key.get_secret_value(), body.secret_key.get_secret_value()
    try:
        shop = await _client(request, access_key, secret_key).shop()
    except Exception as exc:
        code = classify_error(exc)
        raise _error(code, ERROR_COPY[code]) from None
    async with get_conn(request) as conn:
        hint = access_key[-8:] if len(access_key) > 8 else None
        try:
            row = await repo.upsert_mall_connection(conn, user_id=user_id, mall="zigzag", shop_id=shop["shop_id"],
                shop_name=shop["shop_name"], key_hint=hint, access_key=access_key, secret_key=secret_key,
                permissions=["상품 조회와 등록"], kek=kek,
                expected_connection_id=previous["id"] if rotate else None)
        except repo.MallAccountClosed:
            raise _error("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", 404) from None
        except repo.MallShopMismatch:
            raise _error("shop_mismatch", "다른 스토어의 인증키예요. 스토어를 바꾸려면 연결을 해제한 뒤 다시 연결해 주세요") from None
        if row is None:
            raise _error("connection_changed", "연결 정보가 바뀌었어요. 현재 연결을 확인한 뒤 다시 시도해 주세요", 409)
    return _connection_result(row, "zigzag")


def _require_cafe24(request):
    settings = request.app.state.settings
    if not settings.cafe24_ready:
        raise _error("cafe24_unconfigured", "카페24 연결이 아직 준비되지 않았어요", 503)
    return settings


def _return_path(value):
    return value if value and value.startswith("/") and not value.startswith("//") else None


def _install_result(request, code, *, return_to=None, clear_cookie=True):
    query = {"mall": "cafe24", "result": code}
    if path := _return_path(return_to):
        query["returnTo"] = path
    response = RedirectResponse(request.app.state.settings.public_web_origin.rstrip("/")
        + "/settings/malls?" + urlencode(query), status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    if clear_cookie:
        response.delete_cookie(_INSTALL_COOKIE, path=_INSTALL_PATH, secure=True, httponly=True, samesite="lax")
    return response


def _install_cookie_hash(request, attempt_id):
    try:
        if str(uuid.UUID(attempt_id)) != attempt_id:
            return None
        cookie_attempt_id, secret = request.cookies.get(_INSTALL_COOKIE, "").split(".", 1)
        if cookie_attempt_id == attempt_id and secret:
            return hashlib.sha256(secret.encode()).digest()
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _cafe24_client(settings, mall_id):
    return Cafe24Client(mall_id, client_id=settings.cafe24_client_id,
        client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)


def _external_code(job_id):
    return "wl" + job_id.replace("-", "")[:16]


def derive_outcome(job):
    metadata, result = job.get("metadata") or {}, job.get("result") or {}
    if metadata.get("productNo"):
        return "created"
    if metadata.get("rejectedAt"):
        return "failed"
    if (metadata.get("create") or {}).get("sentAt"):
        return "duplicate_suspected" if result.get("outcome") == "duplicate_suspected" else "unknown"
    return "failed"


def registration_view(job):
    metadata, result = job.get("metadata") or {}, job.get("result") or {}
    outcome = derive_outcome(job)
    warnings = list(result.get("warnings") or metadata.get("warnings") or [])
    if outcome == "created" and job["status"] == "error":
        warnings.append("마무리 확인이 늦어졌어요. 상품은 만들어졌어요")
    return {"jobId": job["id"], "status": job["status"], "progress": job.get("progress", 0),
            "outcome": outcome, "productNo": metadata.get("productNo"),
            "productUrl": result.get("productUrl"),
            "imageCount": result.get("imageCount", len((metadata.get("images") or {}).get("urls") or [])),
            "thumbnail": result.get("thumbnail"), "code": result.get("code"),
            "errorMessage": job.get("error_message") if outcome == "failed" else None,
            "externalCode": metadata.get("externalCode") or result.get("externalCode") or _external_code(job["id"]),
            "verifiable": outcome in ("unknown", "duplicate_suspected"),
            "warnings": warnings,
            "createdAt": job.get("created_at"), "finishedAt": job.get("finished_at")}


def confirmed_input(payload):
    return {key: value for key, value in payload.items() if key not in ("requestKey", "connectionId")}


async def _registration_connection(conn, settings, user_id):
    row = await repo.get_mall_connection(conn, user_id=user_id, mall="cafe24")
    if not row:
        raise _error("not_connected", "먼저 카페24를 연결해 주세요", 404)
    if not row.get("refresh_expires_at") or row["refresh_expires_at"] <= datetime.now(timezone.utc):
        raise _error("reconnect_required", CAFE24_ERROR_COPY["reconnect_required"], 409)
    if not is_allowed_mall(settings, row["shop_id"]):
        raise _error("cafe24_not_allowed", CAFE24_ERROR_COPY["cafe24_not_allowed"], 409)
    return row


@router.post("/cafe24/registrations", status_code=202)
async def create_cafe24_registration(request: Request, response: Response, body: Cafe24Registration,
                                     user_id: str = Depends(require_user)):
    settings = request.app.state.settings
    if not registration_enabled(settings):
        raise _error("cafe24_unconfigured", "카페24 연결이 아직 준비되지 않았어요", 503)
    response.headers["Cache-Control"] = "no-store"
    project_id, publication_id = str(body.projectId), str(body.publicationId)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _error("project_not_found", "프로젝트를 찾을 수 없어요.", 404)
        if await repo.project_uses_real_model(conn, user_id=user_id, project_id=project_id):
            raise _error("real_model_not_allowed", "실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요.", 403)
        connection = await _registration_connection(conn, settings, user_id)
        publication = await repo.get_detail_publication(conn, user_id=user_id, publication_id=publication_id)
        # get_detail_publication takes a row lock; release it before validating JSON and creating the job.
        await conn.commit()
        if not publication or publication["status"] != "ready" or publication["project_id"] != project_id:
            raise _error("publication_not_found", "게시한 상세페이지를 찾을 수 없어요.", 404)
        try:
            publication_files(publication)
        except ValueError:
            raise _error("invalid_files", "이미지 조각 정보를 다시 확인해 주세요.") from None
        if body.thumbnailAssetId:
            asset = await repo.get_project_asset_for_user(conn, user_id, project_id, str(body.thumbnailAssetId))
            if not asset or not (asset.get("mime_type") or "").startswith("image/"):
                raise _error("asset_not_found", "대표 이미지를 찾을 수 없어요.", 404)
        allowed = await repo.claim_mall_registration_attempt(conn, user_id=user_id)
        await conn.commit()
        if not allowed:
            raise _error("rate_limited", "한 시간에 10번까지 등록할 수 있어요. 잠시 뒤 다시 시도해 주세요", 429)
        payload = body.model_dump(mode="json", exclude={"projectId"})
        payload.update(mall="cafe24", connectionId=connection["id"], mallId=connection["shop_id"],
                       width=publication["width"], supplyPrice=body.price if body.supplyPrice is None else body.supplyPrice)
        key = f"cafe24-register:{user_id}:{body.requestKey}"
        row, created = await repo.create_job(conn, user_id=user_id, project_id=project_id, kind="mall_register",
            payload=payload, idempotency_key=key, credits_reserved=0, metadata={"mall": "cafe24", "stage": "queued"})
        if created:
            code = _external_code(row["id"])
            await repo.set_job_metadata_patch(conn, job_id=row["id"], patch={"externalCode": code},
                                              user_id=user_id, expect_status=("pending",))
            await conn.commit()
            _wake_dispatcher(request)
            return {"jobId": row["id"], "status": "queued", "externalCode": code}
        existing = await repo.get_job_request_identity(conn, user_id=user_id, job_id=row["id"])
        if existing and existing["idempotency_key"] == key:
            if existing["project_id"] != project_id or confirmed_input(existing["payload"]) != confirmed_input(payload):
                raise _error("idempotency_key_reused", "같은 요청 번호에 다른 입력을 사용할 수 없어요. 입력을 확인해 주세요", 409)
            response.status_code = 200
            return registration_view(row)
        raise HTTPException(status_code=409, detail={"code": "registration_in_progress",
            "message": "이 프로젝트의 상품을 등록하고 있어요", "jobId": row["id"]})


@router.get("/cafe24/registrations/{job_id}")
async def get_cafe24_registration(request: Request, response: Response, job_id: str,
                                  user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise _error("registration_not_found", "등록 내역을 찾을 수 없어요.", 404) from None
    async with get_conn(request) as conn:
        row = await repo.get_job(conn, user_id, job_id)
    if not row or row["kind"] != "mall_register":
        raise _error("registration_not_found", "등록 내역을 찾을 수 없어요.", 404)
    return registration_view(row)


async def _verify_connection(conn, user_id, payload):
    connection = await repo.get_mall_connection(conn, user_id=user_id, mall="cafe24")
    message = "연결이 바뀌어 확인할 수 없어요. 카페24 관리자에서 상품 목록을 확인해 주세요"
    if (not connection or connection["id"] != payload["connectionId"]
            or connection["shop_id"] != payload["mallId"]):
        raise _error("connection_changed", message, 409)
    if not connection.get("refresh_expires_at") or connection["refresh_expires_at"] <= datetime.now(timezone.utc):
        raise _error("reconnect_required", message, 409)


@router.post("/cafe24/registrations/{job_id}/verify")
async def verify_cafe24_registration(request: Request, response: Response, job_id: str,
                                     user_id: str = Depends(require_user)):
    settings = _require_cafe24(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise _error("registration_not_found", "등록 내역을 찾을 수 없어요.", 404) from None
    async with get_conn(request) as conn:
        job = await repo.get_job(conn, user_id, job_id)
        if not job or job["kind"] != "mall_register":
            raise _error("registration_not_found", "등록 내역을 찾을 수 없어요.", 404)
        if job["status"] not in ("done", "error") or not registration_view(job)["verifiable"]:
            raise _error("not_verifiable", "등록 여부를 다시 확인할 수 없는 상태예요", 409)
        await _assert_account_open(conn, user_id)
        payload = job["payload"]
        await _verify_connection(conn, user_id, payload)
        key = f"mall-verify:{user_id}"
        async with conn.cursor() as cur:
            await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            await cur.execute("""select coalesce(sum(request_count), 0) as count from client_event_rate_limits
                where scope = 'user' and key = %s and window_start > clock_timestamp() - interval '1 minute'""", (key,))
            if (await cur.fetchone())["count"] >= 6:
                raise _error("rate_limited", "1분에 6번까지 확인할 수 있어요. 잠시 뒤 다시 시도해 주세요", 429)
            await cur.execute("""insert into client_event_rate_limits (scope, key, window_start, request_count)
                values ('user', %s, clock_timestamp(), 1)""", (key,))
        await conn.commit()
    external_code = registration_view(job)["externalCode"]
    client = _cafe24_client(settings, payload["mallId"])
    try:
        products = await call_with_token(request.app, user_id=user_id, connection_id=payload["connectionId"],
            fn=lambda token: client.find_products_by_custom_code(token, external_code))
    except Exception as exc:
        code = classify_cafe24_error(exc)
        if isinstance(exc, (ConnectionChanged, ReconnectRequired)):
            raise _error(code, "연결이 바뀌어 확인할 수 없어요. 카페24 관리자에서 상품 목록을 확인해 주세요", 409) from None
        raise _error(code, CAFE24_ERROR_COPY[code], 429 if code == "rate_limited" else 400) from None
    async with get_conn(request) as conn:
        # Serialize disconnect and concurrent verifies only while saving, never across the remote lookup.
        async with conn.cursor() as cur:
            await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mall:{user_id}:cafe24",))
            await cur.execute("select id from jobs where id = %s and user_id = %s for update", (job_id, user_id))
        job = await repo.get_job(conn, user_id, job_id)
        if not job or job["kind"] != "mall_register":
            raise _error("registration_not_found", "등록 내역을 찾을 수 없어요.", 404)
        await _verify_connection(conn, user_id, payload)
        if job["status"] not in ("done", "error"):
            raise _error("not_verifiable", "등록 여부를 다시 확인할 수 없는 상태예요", 409)
        if derive_outcome(job) == "created":
            return registration_view(job)
        if not registration_view(job)["verifiable"]:
            raise _error("not_verifiable", "등록 여부를 다시 확인할 수 없는 상태예요", 409)
        checked_at = datetime.now(timezone.utc).isoformat()
        if not products:
            return {"found": False, "checkedAt": checked_at,
                    "message": "아직 확인되지 않았어요. 잠시 뒤 다시 확인하거나 카페24 관리자 상품 목록을 봐 주세요"}
        warnings = list((job.get("result") or {}).get("warnings") or (job.get("metadata") or {}).get("warnings") or [])
        if len(products) == 1:
            product_no = products[0]
            if not await repo.set_job_metadata_patch(conn, job_id=job_id, patch={"productNo": product_no},
                                                     user_id=user_id, expect_status=("done", "error")):
                raise _error("not_verifiable", "등록 여부를 다시 확인할 수 없는 상태예요", 409)
            warnings.append("대표 이미지는 카페24 관리자에서 넣어 주세요")
            patch = dict(outcome="created", productNo=product_no, thumbnail="skipped", code=None,
                productUrl=f'https://{payload["mallId"]}.cafe24.com/product/detail.html?product_no={product_no}',
                verifiedAt=checked_at, warnings=warnings)
        else:
            message = "같은 코드의 상품이 여러 개예요. 카페24 관리자에서 확인해 주세요"
            if message not in warnings:
                warnings.append(message)
            patch = dict(outcome="duplicate_suspected", warnings=warnings)
        if not await repo.set_job_result_patch(conn, job_id=job_id, patch=patch,
                                               user_id=user_id, expect_status=("done", "error")):
            raise _error("not_verifiable", "등록 여부를 다시 확인할 수 없는 상태예요", 409)
        job = await repo.get_job(conn, user_id, job_id)
        await conn.commit()
    return registration_view(job)


@project_router.get("/{project_id}/mall-registrations")
async def list_mall_registrations(request: Request, response: Response, project_id: str,
                                  user_id: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise _error("project_not_found", "프로젝트를 찾을 수 없어요.", 404)
        rows = await repo.list_project_jobs_by_kind(conn, user_id=user_id, project_id=project_id,
                                                   kind="mall_register", limit=5)
    return {"items": [registration_view(row) for row in rows]}


@router.get("/cafe24/categories")
async def list_cafe24_categories(request: Request, response: Response, user_id: str = Depends(require_user)):
    settings = _require_cafe24(request)
    response.headers["Cache-Control"] = "no-store"
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        connection = await _registration_connection(conn, settings, user_id)
    now = monotonic()
    for key, (expires, _) in list(_CATEGORY_CACHE.items()):
        if expires <= now:
            del _CATEGORY_CACHE[key]
    key = (user_id, connection["id"])
    if key in _CATEGORY_CACHE:
        return _CATEGORY_CACHE[key][1]
    client = _cafe24_client(settings, connection["shop_id"])
    items = []
    try:
        for offset in (0, 100, 200):
            rows = await call_with_token(request.app, user_id=user_id, connection_id=connection["id"],
                                        fn=lambda token: client.categories(token, limit=100, offset=offset))
            items.extend({"categoryNo": row["category_no"], "depth": row["category_depth"],
                          "name": row["category_name"], "fullName": row.get("full_category_name") or []}
                         for row in rows[:100] if row.get("use_display") != "F")
            if len(rows) < 100:
                break
    except Exception as exc:
        code = classify_cafe24_error(exc)
        raise _error(code, CAFE24_ERROR_COPY[code],
                     409 if isinstance(exc, (ConnectionChanged, ReconnectRequired)) else 400) from None
    _CATEGORY_CACHE[key] = (monotonic() + 600, items)
    return items


@router.delete("/cafe24", status_code=204)
async def disconnect_cafe24(request: Request, user_id: str = Depends(require_user)):
    settings = request.app.state.settings
    refresh_token = None
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mall:{user_id}:cafe24",))
        row = await repo.get_mall_connection(conn, user_id=user_id, mall="cafe24")
        if row and settings.mall_keys_kek:
            tokens = await repo.get_cafe24_tokens(conn, user_id=user_id, connection_id=row["id"], kek=settings.mall_keys_kek)
            refresh_token = tokens["refresh_token"] if tokens else None
        await repo.revoke_mall_connection(conn, user_id=user_id, mall="cafe24")
        await repo.void_install_attempts(conn, user_id=user_id, mall="cafe24")
        await conn.commit()
    if refresh_token:
        succeeded = False
        try:
            await _cafe24_client(settings, row["shop_id"]).revoke(refresh_token, "refresh_token")
            succeeded = True
        except Exception:
            pass
        logging.getLogger(__name__).info("Cafe24 revoke succeeded=%s", succeeded)
    return Response(status_code=204)


@router.post("/cafe24/install")
async def install_cafe24(request: Request, body: Cafe24Install, user_id: str = Depends(require_user)):
    settings = _require_cafe24(request)
    async with get_conn(request) as conn:
        allowed = await repo.claim_mall_connection_attempt(conn, user_id=user_id)
        await conn.commit()
    if not allowed:
        raise _error("rate_limited", "한 시간에 10번까지 확인할 수 있어요. 잠시 뒤 다시 시도해 주세요", 429)
    mall_id = normalize_mall_id(body.mallId)
    if mall_id is None:
        raise _error("invalid_mall_id", "몰 아이디를 확인해 주세요")
    if not is_allowed_mall(settings, mall_id):
        raise _error("cafe24_not_allowed", "지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요", 403)
    secret = secrets.token_urlsafe(32)
    async with get_conn(request) as conn:
        if await repo.user_account_purge_closed(conn, user_id):
            raise _error("account_closed", "계정 삭제가 완료되어 사용할 수 없습니다.", 404)
        await repo.purge_old_install_attempts(conn, user_id=user_id)
        generation = await repo.mall_connection_generation(conn, user_id=user_id, mall="cafe24")
        attempt_id = await repo.create_install_attempt(conn, user_id=user_id, mall="cafe24", mall_id=mall_id,
            expected_generation=generation, return_to=_return_path(body.returnTo),
            cookie_hash=hashlib.sha256(secret.encode()).digest(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        await conn.commit()
    response = JSONResponse({"startUrl": f"{settings.cafe24_api_origin}{_INSTALL_PATH}/install/{attempt_id}/start"},
                            headers={"Cache-Control": "no-store"})
    response.set_cookie(_INSTALL_COOKIE, f"{attempt_id}.{secret}", max_age=600, path=_INSTALL_PATH,
                        secure=True, httponly=True, samesite="lax")
    return response


@router.get("/cafe24/install/{attempt_id}/start")
async def start_cafe24_install(request: Request, attempt_id: str):
    settings = request.app.state.settings
    cookie_hash = _install_cookie_hash(request, attempt_id)
    if not settings.cafe24_ready or cookie_hash is None:
        return _install_result(request, "state_invalid", clear_cookie=False)
    async with get_conn(request) as conn:
        attempt = await repo.start_install_attempt(conn, attempt_id=attempt_id, cookie_hash=cookie_hash)
        await conn.commit()
    if not attempt:
        return _install_result(request, "state_invalid", clear_cookie=False)
    if not is_allowed_mall(settings, attempt["mall_id"]):
        return _install_result(request, "not_allowed", clear_cookie=False)
    try:
        state = make_signed(derive_state_key(settings.cafe24_client_secret),
            {"a": attempt_id, "e": attempt["expires_at"].timestamp()}, now=datetime.now(timezone.utc).timestamp())
    except SignatureInvalid:
        return _install_result(request, "state_invalid", clear_cookie=False)
    return RedirectResponse(build_authorize_url(attempt["mall_id"], client_id=settings.cafe24_client_id,
        redirect_uri=settings.cafe24_redirect_uri, scopes=settings.cafe24_scopes, state=state), status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def _revoke_cafe24_token(client, tokens):
    try:
        await client.revoke(tokens["access_token"], "access_token")
    except Exception:
        pass


@router.get("/cafe24/callback")
async def cafe24_callback(request: Request):
    settings = request.app.state.settings
    if not settings.cafe24_ready:
        return _install_result(request, "state_invalid")
    try:
        state = parse_signed(derive_state_key(settings.cafe24_client_secret),
            request.query_params.get("state", ""), now=datetime.now(timezone.utc).timestamp())
        if set(state) != {"a", "e"}:
            raise SignatureInvalid()
    except SignatureInvalid:
        return _install_result(request, "state_invalid")
    cookie_hash = _install_cookie_hash(request, state["a"])
    if cookie_hash is None:
        return _install_result(request, "state_invalid")
    try:
        async with get_conn(request) as conn:
            attempt = await repo.consume_install_attempt(conn, attempt_id=state["a"], cookie_hash=cookie_hash)
            # Release the one-use gate durably before any token request.
            await conn.commit()
    except Exception:
        return _install_result(request, "token_exchange_failed")
    if not attempt:
        return _install_result(request, "state_invalid")
    if request.query_params.get("error") == "access_denied":
        return _install_result(request, "cancelled")
    code = request.query_params.get("code")
    if not code or request.query_params.get("error"):
        return _install_result(request, "token_exchange_failed")
    client = _cafe24_client(settings, attempt["mall_id"])
    try:
        tokens = await client.exchange_code(code, settings.cafe24_redirect_uri)
    except Exception:
        return _install_result(request, "token_exchange_failed")
    received_at = datetime.now(timezone.utc)
    scopes = tokens.get("scopes")
    failure = None
    if tokens.get("mall_id") != attempt["mall_id"]:
        failure = "mall_mismatch"
    elif not is_allowed_mall(settings, tokens["mall_id"]):
        failure = "not_allowed"
    elif (not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes)
            or not set(settings.cafe24_scopes.split()).issubset(scopes)):
        failure = "missing_permission"
    if failure:
        await _revoke_cafe24_token(client, tokens)
        return _install_result(request, failure)
    shop_name = attempt["mall_id"]
    try:
        shop_name = (await client.store(tokens["access_token"])).get("shop_name") or shop_name
    except Exception:
        pass
    permissions = ["상품 조회와 등록", "쇼핑몰 정보 조회"]
    if "mall.read_category" in scopes:
        permissions.append("상품 분류 조회")
    try:
        async with get_conn(request) as conn:
            row = await repo.save_cafe24_connection(conn, attempt_id=state["a"], user_id=attempt["user_id"],
                mall_id=tokens["mall_id"], shop_name=shop_name, access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"], token_expires_at=received_at + timedelta(hours=2, seconds=-60),
                refresh_expires_at=received_at + timedelta(days=14, hours=-1), scopes=scopes,
                permissions=permissions, kek=settings.mall_keys_kek)
            await conn.commit()
        failure = None if row else "connection_changed"
    except repo.MallAccountClosed:
        failure = "account_closed"
    except Exception:
        failure = "token_exchange_failed"
    if failure:
        await _revoke_cafe24_token(client, tokens)
        return _install_result(request, failure)
    return _install_result(request, "connected", return_to=attempt["return_to"])


@router.post("/zigzag/connect")
async def connect_zigzag(request: Request, body: ConnectionKeys, user_id: str = Depends(require_user)):
    return await _verify_and_save(request, body, user_id)


@router.post("/zigzag/rotate")
async def rotate_zigzag(request: Request, body: ConnectionKeys, user_id: str = Depends(require_user)):
    return await _verify_and_save(request, body, user_id, rotate=True)


@router.delete("/zigzag", status_code=204)
async def disconnect_zigzag(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        await repo.revoke_mall_connection(conn, user_id=user_id, mall="zigzag")
    return Response(status_code=204)


@router.get("/zigzag/addresses")
async def list_zigzag_addresses(request: Request, user_id: str = Depends(require_user)):
    kek = _require_kek(request)
    async with get_conn(request) as conn:
        keys = await repo.get_mall_connection_keys(conn, user_id=user_id, mall="zigzag", kek=kek)
    if not keys:
        raise _error("not_connected", "먼저 지그재그를 연결해 주세요", 404)
    try:
        return await _client(request, *keys).shipping_addresses()
    except Exception as exc:
        code = classify_error(exc)
        raise _error(code, ERROR_COPY[code]) from None


@router.put("/zigzag/defaults")
async def save_zigzag_defaults(request: Request, body: DefaultsPatch, user_id: str = Depends(require_user)):
    payload = body.model_dump(exclude_unset=True)
    payload = {key: value for key, value in payload.items() if value is not None}
    async with get_conn(request) as conn:
        row = await repo.upsert_mall_defaults(conn, user_id=user_id, mall="zigzag", payload=payload)
    return _defaults_result(row)
