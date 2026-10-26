"""Mall connections and reusable defaults. Product creation belongs to the next stage."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, StrictInt, field_validator
from starlette.responses import JSONResponse, RedirectResponse

from . import repo
from .auth import require_user
from .config import is_allowed_mall
from .db import get_conn
from .services.cafe24_client import (
    Cafe24Client, SignatureInvalid, build_authorize_url, derive_state_key,
    make_signed, normalize_mall_id, parse_signed,
)
from .services.zigzag_client import ERROR_COPY, ZigzagClient, classify_error


router = APIRouter(prefix="/v1/malls", tags=["Mall connections"])
_INSTALL_COOKIE = "wl_cafe24_install"
_INSTALL_PATH = "/v1/malls/cafe24"


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
