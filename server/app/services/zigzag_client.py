"""Server-only KakaoStyle Open API queries. Never log credentials or upstream errors."""

import asyncio
import hashlib
import hmac
import re
import time

import httpx


ERROR_COPY = {
    "invalid_key": "인증키가 맞지 않아요. 파트너센터에서 다시 복사해 주세요",
    "missing_permission": "상품 등록 권한이 없는 키예요. 상품과 주문 API 권한을 켜 주세요",
    "unreachable": "지그재그에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요",
    "unknown": "연결을 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요",
}


def _message_kind(message):
    lowered = message.lower()
    if any(word in lowered for word in ("권한", "permission", "scope")):
        return "missing_permission"
    if any(word in lowered for word in ("인증", "authentication", "unauthorized", "invalid_key", "access key", "signature")):
        return "invalid_key"
    return "unknown"


class ZigzagApiError(Exception):
    def __init__(self, code, message):
        # Upstream messages may echo secrets. Retain only their classification.
        self.code = _message_kind(f"{code} {message}")
        self.message = ERROR_COPY[self.code]
        super().__init__(self.message)


def classify_error(exc) -> str:
    if isinstance(exc, ZigzagApiError):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return "invalid_key"
    if isinstance(exc, (httpx.TransportError, TimeoutError)):
        return "unreachable"
    return "unknown"


def build_authorization(access_key: str, secret_key: str, query: str, *, signed_date_ms: int,
                        algorithm: str = "sha1") -> str:
    if algorithm not in ("sha1", "sha256"):
        raise ValueError("Unsupported HMAC algorithm")
    normalized = re.sub(r"\s+", " ", query)
    message = f"{signed_date_ms}.{normalized}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), message, getattr(hashlib, algorithm)).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={signed_date_ms}, signature={signature}")


class ZigzagClient:
    def __init__(self, base_url, access_key, secret_key, *, algorithm, timeout=8.0, transport=None):
        self.base_url = base_url
        self._access_key = access_key
        self._secret_key = secret_key
        self.algorithm = algorithm
        self.timeout = timeout
        self.transport = transport

    async def query(self, graphql: str, variables: dict | None = None) -> dict:
        return await asyncio.wait_for(self._query(graphql, variables), timeout=self.timeout)

    async def _query(self, graphql: str, variables: dict | None = None) -> dict:
        graphql = re.sub(r"\s+", " ", graphql)
        authorization = build_authorization(self._access_key, self._secret_key, graphql,
                                            signed_date_ms=int(time.time() * 1000), algorithm=self.algorithm)
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(self.base_url, json={"query": graphql, "variables": variables or {}},
                                         headers={"Authorization": authorization})
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            errors = payload["errors"]
            raise ZigzagApiError(" ".join(str((e.get("extensions") or {}).get("code", "")) for e in errors),
                                 " ".join(str(e.get("message", "")) for e in errors)) from None
        if not isinstance(payload.get("data"), dict):
            raise ZigzagApiError("unknown", "")
        return payload["data"]

    async def shop(self):
        data = await self.query("""query { shop { shop_id shop_name
            allowed_brand_list { brand_id brand_name }
            site_country_list { site site_name country_code country_name } } }""")
        shop = data.get("shop")
        if not shop or not shop.get("shop_id") or not shop.get("shop_name"):
            raise ZigzagApiError("invalid_key", "")
        return {"shop_id": shop["shop_id"], "shop_name": shop["shop_name"],
                "allowed_brands": shop.get("allowed_brand_list", []), "sites": shop.get("site_country_list", [])}

    async def shipping_addresses(self):
        data = await self.query("""query { shop_shipping_address_list(limit_count: 50) {
            item_list { id name address address_detail postcode is_default } } }""")
        return [{"id": row["id"], "name": row["name"],
                 "address": " ".join(filter(None, [row["address"], row.get("address_detail")])),
                 "postcode": row["postcode"], "is_default": row["is_default"]}
                for row in data["shop_shipping_address_list"]["item_list"]]
