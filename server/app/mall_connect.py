"""Mall connections and reusable defaults. Product creation belongs to the next stage."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, StrictInt, field_validator

from . import repo
from .auth import require_user
from .db import get_conn
from .services.zigzag_client import ERROR_COPY, ZigzagClient, classify_error


router = APIRouter(prefix="/v1/malls", tags=["Mall connections"])


def _error(code, message, status=400):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class ConnectionKeys(BaseModel):
    access_key: SecretStr = Field(alias="accessKey", min_length=1, max_length=256)
    secret_key: SecretStr = Field(alias="secretKey", min_length=1, max_length=256)

    @field_validator("access_key", "secret_key", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


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


def _connection_result(row):
    return {"mall": "zigzag", "status": "connected" if row else "none",
            "shopName": row.get("shop_name") if row else None,
            "keyHint": row.get("key_hint") if row else None,
            "permissions": row.get("permissions", []) if row else [],
            "connectedAt": row.get("connected_at") if row else None,
            "lastRegisteredAt": row.get("last_registered_at") if row else None}


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
    return {"items": [{**_connection_result(row), "defaults": _defaults_result(defaults)},
                      {"mall": "ably", "status": "unavailable"}, {"mall": "smartstore", "status": "unavailable"}]}


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
        except repo.MallShopMismatch:
            raise _error("shop_mismatch", "다른 스토어의 인증키예요. 스토어를 바꾸려면 연결을 해제한 뒤 다시 연결해 주세요") from None
        if row is None:
            raise _error("connection_changed", "연결 정보가 바뀌었어요. 현재 연결을 확인한 뒤 다시 시도해 주세요", 409)
    return _connection_result(row)


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
