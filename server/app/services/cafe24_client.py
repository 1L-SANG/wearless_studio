"""Server-only Cafe24 API calls. Never retain credentials or upstream error bodies."""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import math
import re
from urllib.parse import urlencode
from urllib.parse import quote
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)


class Cafe24ImageSetting(TypedDict):
    width: int
    height: int


class Cafe24ProductState(TypedDict):
    productId: str
    display: str
    selling: str


class Cafe24Variant(TypedDict):
    variantCode: str
    options: list[dict]
    quantity: int | None
    additionalAmount: str | int | None
    useInventory: str | None
    display: str | None
    selling: str | None
    displaySoldout: str | None
    safetyInventory: int | None


class AdditionalImageResult(TypedDict):
    count: int


PRICE_BASIS_FIELD = "calculate_price_based_on"
PRICE_ROUNDING_UNIT_FIELD = "price_rounding_unit"
PRICE_ROUNDING_RULE_FIELD = "price_rounding_rule"
PRICE_BASES = frozenset({"S", "A", "P", "B"})

ERROR_COPY = {
    "registration_preparing": "이 쇼핑몰 등록은 준비 중이에요",
    "price_basis_changed": "쇼핑몰의 가격 기준이 바뀌었어요. 가격을 다시 확인해 주세요",
    "variant_mapping_mismatch": "쇼핑몰 옵션을 입력한 조합과 맞추지 못했어요. 관리자에서 확인해 주세요",
    "inventory_setup_incomplete": "상품은 만들어졌지만 재고 설정을 마치지 못했어요",
    "inventory_confirmation_pending": "카페24 재고 설정 반영을 확인 중이에요. 잠시 뒤 설정 이어 하기를 눌러 주세요",
    "additional_images_unknown": "추가 이미지 등록 여부를 관리자에서 확인해 주세요",
    "activation_unconfirmed": "카페24 진열과 판매 상태를 확인 중이에요. 잠시 뒤 설정 이어 하기를 눌러 주세요",
    "remote_product_changed": "관리자에서 상품이 바뀌어 설정을 이어 할 수 없어요",
    "reconnect_required": "카페24 연결이 끊겼어요. 쇼핑몰 연결에서 다시 설치해 주세요",
    "app_removed": "카페24에서 앱이 삭제됐어요. 다시 설치해 주세요",
    "missing_permission": "상품 등록 권한이 없어요. 앱을 다시 설치하며 권한을 허용해 주세요",
    "rate_limited": "카페24 호출이 잠시 막혔어요. 1분 뒤 다시 시도해 주세요",
    "invalid_request": "카페24가 입력을 받지 않았어요. 판매가와 상품명을 확인해 주세요",
    "price_setting_unavailable": "카페24 가격 설정을 확인하지 못했어요. 잠시 뒤 다시 등록해 주세요",
    "unreachable": "카페24에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요",
    "image_setting_unavailable": "카페24 이미지 설정을 확인하지 못했어요. 잠시 뒤 다시 확인해 주세요",
    "image_upload_mismatch": "상세 이미지를 올리지 못했어요. 잠시 뒤 다시 시도해 주세요",
    "connection_changed": "그 사이 카페24 연결이 바뀌었어요. 다시 등록해 주세요",
    "cafe24_not_allowed": "지금은 시범 운영 중이라 등록된 몰만 등록할 수 있어요",
    "cafe24_disabled": "카페24 등록이 잠시 꺼져 있어요",
    "account_closed": "계정 삭제가 완료되어 사용할 수 없습니다.",
    "cancelled": "등록을 취소했어요",
    "unknown": "카페24에서 오류가 났어요. 잠시 뒤 다시 시도해 주세요",
}


class Cafe24ApiError(Exception):
    code = "unknown"

    def __init__(self, code=None):
        self.code = code if code in ERROR_COPY else self.code
        self.message = ERROR_COPY[self.code]
        super().__init__(self.message)


class ConnectionChanged(Cafe24ApiError):
    code = "connection_changed"


class ReconnectRequired(Cafe24ApiError):
    code = "reconnect_required"


class AppRemoved(Cafe24ApiError):
    code = "app_removed"


class MissingPermission(Cafe24ApiError):
    code = "missing_permission"


class SignatureInvalid(Exception):
    def __init__(self):
        super().__init__("invalid_state_token")


def normalize_mall_id(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    value = re.sub(r"^https?://", "", raw.strip().lower())
    value = re.split(r"[/?#]", value, maxsplit=1)[0]
    value = re.sub(r"\.(cafe24|cafe24api)\.com$", "", value)
    return value if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,29}", value) else None


def _mall_id(raw) -> str:
    mall_id = normalize_mall_id(raw)
    if mall_id is None:
        raise ValueError("invalid_mall_id")
    return mall_id


def build_authorize_url(mall_id, *, client_id, redirect_uri, scopes, state) -> str:
    scope = " ".join(scopes.split() if isinstance(scopes, str) else scopes)
    query = urlencode({"response_type": "code", "client_id": client_id, "state": state,
                       "redirect_uri": redirect_uri, "scope": scope})
    return f"https://{_mall_id(mall_id)}.cafe24api.com/api/v2/oauth/authorize?{query}"


def derive_state_key(client_secret) -> bytes:
    return hmac.new(client_secret.encode(), b"wearless-cafe24-state", hashlib.sha256).digest()


def _check_expiry(payload, now):
    if (not isinstance(payload, dict) or type(payload.get("e")) not in (int, float)
            or not math.isfinite(payload["e"]) or payload["e"] <= now):
        raise SignatureInvalid()


def make_signed(key: bytes, payload: dict, *, now: float) -> str:
    _check_expiry(payload, now)
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def parse_signed(key: bytes, token: str, *, now: float) -> dict:
    try:
        body, mac = token.split(".", 1)
        expected = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac):
            raise SignatureInvalid()
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        _check_expiry(payload, now)
        return payload
    except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError):
        raise SignatureInvalid() from None


def _error_fields(response):
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    return error if isinstance(error, dict) else {"code": error}


def _app_removed(error):
    text = " ".join(value.lower() for key in ("code", "message", "more_info")
                    if isinstance(value := error.get(key), str))
    return (("app" in text and any(word in text for word in (
                "removed", "deleted", "uninstall", "not installed", "not_installed")))
            or ("앱" in text and "삭제" in text))


def classify_error(exc) -> str:
    if isinstance(exc, Cafe24ApiError):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "reconnect_required"
        if status == 403:
            return "app_removed" if _app_removed(_error_fields(exc.response)) else "missing_permission"
        if status == 429:
            return "rate_limited"
        if status in (400, 422):
            return "invalid_request"
    if isinstance(exc, (httpx.TransportError, TimeoutError)):
        return "unreachable"
    return "unknown"


def price_fields(setting, price) -> dict:
    if setting not in PRICE_BASES:
        raise ValueError(ERROR_COPY["price_setting_unavailable"])
    return {"price_excluding_tax" if setting == "B" else "price": price}


def build_description_html(urls, product_name, width) -> str:
    def escape(value):
        return html.escape(str(value) if value is not None else "", quote=True).replace("&#x27;", "&#39;")

    images = "\n".join(
        f'<img src="{escape(url)}" alt="{escape(product_name)} 상세 {i + 1}" '
        f'style="display:block;width:100%;max-width:{int(width)}px;margin:0 auto;">'
        for i, url in enumerate(urls))
    return f'<div style="text-align:center;">\n{images}\n</div>'


def _object(payload, key, *, code="unknown"):
    value = payload.get(key)
    if not isinstance(value, dict):
        raise Cafe24ApiError(code)
    return value


def _rows(payload, key, *, code="unknown"):
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise Cafe24ApiError(code)
    return value


def log_confirmation_failure(check, actual, expected):
    # Callers pass only field names and normalized numbers, flags or shape checks.
    differences = {key: dict(actual=actual.get(key), expected=value)
                   for key, value in expected.items() if actual.get(key) != value}
    logger.warning('cafe24_confirmation_failed check=%s fields=%s', check,
                   json.dumps(differences, sort_keys=True, separators=(',', ':')))


def _variant(row, inventory):
    return dict(variantCode=row['variant_code'], options=row.get('options') or [],
        quantity=inventory.get('quantity'), additionalAmount=row.get('additional_amount'),
        useInventory=inventory.get('use_inventory'), display=row.get('display'), selling=row.get('selling'),
        displaySoldout=inventory.get('display_soldout'), safetyInventory=inventory.get('safety_inventory'))


def _product_state(payload, product_no, *, check, code):
    row = payload.get('product')
    actual = {'product.is_object': isinstance(row, dict)}
    if isinstance(row, dict):
        actual.update({'product.product_no_matches': type(row.get('product_no')) is int and row['product_no'] == product_no,
            'product.display_valid': row.get('display') in ('T', 'F'),
            'product.selling_valid': row.get('selling') in ('T', 'F'), 'product.error': 'error' in row})
    expected = {'product.is_object': True, 'product.product_no_matches': True,
        'product.display_valid': True, 'product.selling_valid': True, 'product.error': False}
    if actual != expected:
        log_confirmation_failure(check, actual, expected)
        raise Cafe24ApiError(code)
    return dict(productId=str(product_no), display=row['display'], selling=row['selling'])


class Cafe24Client:
    def __init__(self, mall_id, *, client_id, client_secret, api_version, timeout=8.0, transport=None):
        self.base_url = f"https://{_mall_id(mall_id)}.cafe24api.com/api/v2"
        self._client_id = client_id
        self._client_secret = client_secret
        self.api_version = api_version
        self.timeout = timeout
        self.transport = transport
        self.before_request = None

    async def _request(self, method, path, *, access_token=None, **kwargs):
        # A guarded operation can paginate internally. Fence each HTTP request,
        # rather than only the first page. Token clients leave this hook unset.
        if self.before_request is not None:
            await self.before_request()
        try:
            return await asyncio.wait_for(self._send(method, path, access_token=access_token, **kwargs),
                                          timeout=self.timeout)
        except TimeoutError:
            raise TimeoutError(ERROR_COPY["unreachable"]) from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Preserve proof that no request was sent, without the request or secrets.
            raise type(exc)(ERROR_COPY["unreachable"]) from None
        except httpx.TransportError:
            raise httpx.TransportError(ERROR_COPY["unreachable"]) from None
        except httpx.RequestError:
            raise httpx.RequestError(ERROR_COPY["unknown"]) from None

    async def _send(self, method, path, *, access_token=None, **kwargs):
        if access_token is None:
            auth = httpx.BasicAuth(self._client_id, self._client_secret)
            headers = {}
        else:
            auth = None
            headers = {"Authorization": f"Bearer {access_token}", "X-Cafe24-Api-Version": self.api_version}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.request(method, self.base_url + path, auth=auth, headers=headers, **kwargs)
        if not response.is_success:
            error = _error_fields(response)
            if (path == "/oauth/token" and kwargs.get("data", {}).get("grant_type") == "refresh_token"
                    and response.status_code == 400 and error.get("code") == "invalid_grant"):
                raise ReconnectRequired()
            if response.status_code == 403:
                raise AppRemoved() if _app_removed(error) else MissingPermission()
            # Keep status and retry timing available without retaining the upstream body or credentials.
            safe_request = httpx.Request(method, response.request.url.copy_with(query=None))
            retry_after = response.headers.get("Retry-After", "")
            safe_headers = {"Retry-After": retry_after} if re.fullmatch(r"[0-9]+", retry_after) else {}
            safe_response = httpx.Response(response.status_code, request=safe_request, headers=safe_headers)
            status_error = httpx.HTTPStatusError("", request=safe_request, response=safe_response)
            raise httpx.HTTPStatusError(ERROR_COPY[classify_error(status_error)],
                                        request=safe_request, response=safe_response) from None
        if response.status_code == 204 or path == "/oauth/revoke":
            return {}
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise Cafe24ApiError() from None
        if not isinstance(payload, dict) or "error" in payload:
            raise Cafe24ApiError()
        if path == "/oauth/token" and not all(isinstance(payload.get(key), str) and payload[key]
                                              for key in ("access_token", "refresh_token")):
            raise Cafe24ApiError()
        return payload

    async def _write_response(self, path, *, access_token, body, code):
        try:
            return await self._request('PUT', path, access_token=access_token, json=body)
        except Cafe24ApiError as exc:
            if exc.code != 'unknown':
                raise
            log_confirmation_failure('response_shape', {'response.valid_object': False}, {'response.valid_object': True})
            raise Cafe24ApiError(code) from None

    async def exchange_code(self, code, redirect_uri) -> dict:
        return await self._request("POST", "/oauth/token", data={"grant_type": "authorization_code",
                                   "code": code, "redirect_uri": redirect_uri})

    async def refresh(self, refresh_token) -> dict:
        return await self._request("POST", "/oauth/token", data={"grant_type": "refresh_token",
                                   "refresh_token": refresh_token})

    async def revoke(self, token, hint) -> None:
        await self._request("POST", "/oauth/revoke", data={"token": token, "token_hint": hint})

    async def store(self, access_token) -> dict:
        return _object(await self._request("GET", "/admin/store", access_token=access_token), "store")

    async def product_setting(self, access_token) -> str:
        payload = await self._request("GET", "/admin/products/setting", access_token=access_token)
        settings = _object(payload, "product", code="price_setting_unavailable")
        basis = settings.get(PRICE_BASIS_FIELD)
        if not isinstance(basis, str) or basis not in PRICE_BASES:
            raise Cafe24ApiError("price_setting_unavailable")
        return basis

    async def upload_description_images(self, access_token, images: list[bytes]) -> list[str]:
        if len(images) > 20:
            raise ValueError("At most 20 description images per batch")
        if not images:
            return []
        payload = await self._request("POST", "/admin/products/images", access_token=access_token,
            json={"requests": [{"image": base64.b64encode(image).decode()} for image in images]})
        rows = _rows(payload, "images" if "images" in payload else "image", code="image_upload_mismatch")
        if any(not isinstance(row.get("path"), str) or not row["path"] for row in rows):
            raise Cafe24ApiError("image_upload_mismatch")
        return [row["path"] for row in rows]

    async def create_product(self, access_token, body) -> int:
        payload = await self._request("POST", "/admin/products", access_token=access_token, json=body)
        product_no = _object(payload, "product").get("product_no")
        if type(product_no) is not int or product_no <= 0:
            raise Cafe24ApiError()
        return product_no

    async def find_products_by_custom_code(self, access_token, code) -> list[int]:
        return await asyncio.wait_for(self._find_products(access_token, code), timeout=self.timeout)

    async def _find_products(self, access_token, code):
        matches = []
        offset = 0
        while True:
            payload = await self._request("GET", "/admin/products", access_token=access_token,
                params={"custom_product_code": code, "limit": 100, "offset": offset})
            products = _rows(payload, "products")
            for product in products:
                if product.get("custom_product_code") == code:
                    product_no = product.get("product_no")
                    if type(product_no) is not int or product_no <= 0:
                        raise Cafe24ApiError()
                    matches.append(product_no)
            if len(products) < 100:
                return matches
            offset += 100

    async def upload_product_image(self, access_token, product_no, jpeg_bytes) -> dict:
        if type(product_no) is not int or product_no <= 0:
            raise ValueError("invalid_product_no")
        payload = await self._request("POST", f"/admin/products/{product_no}/images", access_token=access_token,
            json={"shop_no": 1, "request": {"image_upload_type": "A",
                "detail_image": "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()}})
        return _object(payload, "image")

    async def categories(self, access_token, *, limit, offset) -> list[dict]:
        return _rows(await self._request("GET", "/admin/categories", access_token=access_token,
                                        params={"limit": limit, "offset": offset}), "categories")

    async def image_setting(self, access_token: str) -> 'Cafe24ImageSetting':
        image = _object(await self._request('GET', '/admin/images/setting', access_token=access_token),
                        'image', code='image_setting_unavailable')
        setting = _object(image, 'product_image_size', code='image_setting_unavailable')
        width, height = setting.get('detail_image_width'), setting.get('detail_image_height')
        if type(width) is not int or type(height) is not int or not 0 < width <= 20000 or not 0 < height <= 20000:
            raise Cafe24ApiError('image_setting_unavailable')
        return {'width': width, 'height': height}

    async def product_state(self, access_token: str, product_no: int) -> 'Cafe24ProductState':
        if type(product_no) is not int or product_no <= 0:
            raise ValueError('invalid_product_no')
        payload = await self._request('GET', f'/admin/products/{product_no}', access_token=access_token)
        return _product_state(payload, product_no, check='read_shape', code='unknown')

    async def variants(self, access_token: str, product_no: int) -> list['Cafe24Variant']:
        if type(product_no) is not int or product_no <= 0:
            raise ValueError('invalid_product_no')
        items = []
        for offset in range(0, 5000, 100):
            rows = _rows(await self._request('GET', f'/admin/products/{product_no}/variants',
                access_token=access_token, params={'limit': 100, 'offset': offset}), 'variants')
            for row in rows:
                if not isinstance(row.get('variant_code'), str) or not row['variant_code']:
                    raise Cafe24ApiError()
                # Missing inventory fields remain missing; they never imply a successful setup.
                items.append(_variant(row, row))
            if len(rows) < 100:
                return items
        raise Cafe24ApiError()

    async def update_variants(self, access_token: str, product_no: int, variants: list[dict]) -> list[Cafe24Variant]:
        if type(product_no) is not int or product_no <= 0 or not variants:
            raise ValueError('invalid variants')
        payload = await self._write_response(f'/admin/products/{product_no}/variants', access_token=access_token,
            body={'shop_no': 1, 'requests': variants}, code='inventory_setup_incomplete')
        rows = payload.get('variant')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            log_confirmation_failure('response_shape', {'variant.rows_valid': False}, {'variant.rows_valid': True})
            raise Cafe24ApiError('inventory_setup_incomplete')
        codes = [row.get('variant_code') for row in rows]
        valid_codes = all(isinstance(code, str) and code for code in codes)
        actual = {'variant.count': len(rows), 'variant.codes_match': bool(valid_codes) and
            len(set(codes)) == len(codes) and set(codes) == {v['variant_code'] for v in variants},
            'variant.error': any('error' in row for row in rows),
            'variant.inventories_valid': all(isinstance(row.get('inventories'), dict) and
                'error' not in row['inventories'] for row in rows),
            'variant.inventories_code_matches': all(isinstance(row.get('inventories'), dict) and
                row['inventories'].get('variant_code', row.get('variant_code')) == row.get('variant_code') for row in rows)}
        expected = {'variant.count': len(variants), 'variant.codes_match': True,
            'variant.error': False, 'variant.inventories_valid': True, 'variant.inventories_code_matches': True}
        if actual != expected:
            log_confirmation_failure('response_shape', actual, expected)
            raise Cafe24ApiError('inventory_setup_incomplete')
        return [_variant(row, row['inventories']) for row in rows]

    async def set_variant_inventory(self, access_token: str, product_no: int, variant_code: str, inventory: dict) -> None:
        if type(product_no) is not int or product_no <= 0 or not isinstance(variant_code, str) or not variant_code:
            raise ValueError('invalid variant')
        # Not called alongside bulk PUT, whose response confirms setup.
        await self._request('PUT', f'/admin/products/{product_no}/variants/{quote(variant_code, safe="")}/inventories',
            access_token=access_token, json={'shop_no': 1, 'request': inventory})

    async def upload_additional_images(self, access_token: str, product_no: int, images: list[bytes]) -> AdditionalImageResult:
        if type(product_no) is not int or product_no <= 0:
            raise ValueError('invalid_product_no')
        if not 1 <= len(images) <= 20 or any(len(image) > 5000000 for image in images):
            raise ValueError('additional image limit')
        # 미확인: 응답 유실 후 동일 이미지 집합을 확인할 목록 조회 계약. POST를 반복하지 않는다.
        body = {'shop_no': 1, 'request': {'additional_image': [
            'data:image/jpeg;base64,' + base64.b64encode(image).decode() for image in images]}}
        if len(json.dumps(body, separators=(',', ':')).encode()) > 30000000:
            raise ValueError('additional image request limit')
        payload = await self._request('POST', f'/admin/products/{product_no}/additionalimages', access_token=access_token, json=body)
        additional = _object(payload, 'additionalimage', code='additional_images_unknown')
        rows = _rows(additional, 'additional_image', code='additional_images_unknown')
        if len(rows) != len(images) or any('error' in row or not any(
                isinstance(row.get(key), str) and row[key].strip() for key in ('big', 'medium')) for row in rows):
            raise Cafe24ApiError('additional_images_unknown')
        return dict(count=len(rows))

    async def set_product_state(self, access_token: str, product_no: int, *, display: str, selling: str) -> Cafe24ProductState:
        if type(product_no) is not int or product_no <= 0 or display not in ('T', 'F') or selling not in ('T', 'F'):
            raise ValueError('invalid product state')
        payload = await self._write_response(f'/admin/products/{product_no}', access_token=access_token,
            body={'shop_no': 1, 'request': {'display': display, 'selling': selling}}, code='activation_unconfirmed')
        return _product_state(payload, product_no, check='response_shape', code='activation_unconfirmed')
