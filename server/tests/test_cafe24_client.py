import asyncio
import base64
import hashlib
import hmac
import importlib
import importlib.util
import json
import logging
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest


def module():
    assert importlib.util.find_spec("app.services.cafe24_client"), "Cafe24 client is missing"
    return importlib.import_module("app.services.cafe24_client")


def client(handler, **kwargs):
    return module().Cafe24Client("myshop", client_id="client-id", client_secret="client-secret",
                                 api_version="2026-09-01", transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize("raw,want", [
    (" MyShop ", "myshop"), ("https://MyShop.cafe24.com/admin/?a=1#top", "myshop"),
    ("http://MyShop.cafe24api.com/api/v2", "myshop"), ("myshop.cafe24.com/", "myshop"),
    ("myshop.cafe24api.com?x=1", "myshop"), ("shop_01-test", "shop_01-test"),
    ("a" * 30, "a" * 30), ("a" * 31, None), ("a", None), ("", None), (None, None),
    ("-shop", None), ("shop name", None), ("쇼핑몰", None), ("shop.com", None),
    ("https://shop.cafe24.com.evil.test", None), ("https://user@shop.cafe24.com", None),
    ("https://shop.cafe24.com:443", None), ("https://127.0.0.1/", None),
    ("//evil.test/shop", None), ("ftp://shop.cafe24.com", None),
    ("shop%2ecafe24.com", None), ("shop\\evil", None), ("shop\nname", None),
])
def test_normalize_mall_id_strips_url_parts_and_rejects_bad_chars(raw, want):
    assert module().normalize_mall_id(raw) == want


def test_authorize_url_has_space_separated_scope_and_state():
    c24 = module()
    for scopes in (["mall.read_product", "mall.write_product"], "mall.read_product mall.write_product"):
        url = urlsplit(c24.build_authorize_url("myshop", client_id="client-id",
            redirect_uri="https://app.test/callback?a=1&b=2", scopes=scopes, state="signed+state/value"))
        assert (url.scheme, url.netloc, url.path) == (
            "https", "myshop.cafe24api.com", "/api/v2/oauth/authorize")
        assert parse_qs(url.query) == {"response_type": ["code"], "client_id": ["client-id"],
            "redirect_uri": ["https://app.test/callback?a=1&b=2"],
            "scope": ["mall.read_product mall.write_product"], "state": ["signed+state/value"]}
    with pytest.raises(ValueError):
        c24.build_authorize_url("evil.test", client_id="c", redirect_uri="https://app.test", scopes=[], state="s")


def test_signed_token_roundtrip_expiry_and_tamper():
    c24 = module()
    key = c24.derive_state_key("client-secret")
    assert key == hmac.new(b"client-secret", b"wearless-cafe24-state", hashlib.sha256).digest()
    payload = {"a": "attempt-id", "e": 1700000600}
    token = c24.make_signed(key, payload, now=1700000000)
    body, mac = token.split(".")
    assert json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))) == payload
    assert mac == hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    assert c24.parse_signed(key, token, now=1700000599) == payload
    for bad in ("", "no-dot", token + "x", "x" + token, body + "." + "0" * 64):
        with pytest.raises(c24.SignatureInvalid):
            c24.parse_signed(key, bad, now=1700000000)
    for now in (1700000600, 1700000601):
        with pytest.raises(c24.SignatureInvalid):
            c24.parse_signed(key, token, now=now)
    for data in ([], {}, {"e": "tomorrow"}, {"e": float("nan")}, {"e": True}):
        encoded = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
        signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(c24.SignatureInvalid):
            c24.parse_signed(key, f"{encoded}.{signature}", now=1700000000)


def test_exchange_code_uses_basic_auth_and_form_body():
    payload = {"access_token": "access", "refresh_token": "refresh", "mall_id": "myshop",
        "scopes": ["mall.read_store"], "expires_at": "2026-10-26T20:00:00.000"}

    def respond(request):
        assert request.method == "POST"
        assert str(request.url) == "https://myshop.cafe24api.com/api/v2/oauth/token"
        assert request.headers["Authorization"] == "Basic Y2xpZW50LWlkOmNsaWVudC1zZWNyZXQ="
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert request.extensions["timeout"]["read"] == 8
        assert parse_qs(request.content.decode()) == {"grant_type": ["authorization_code"],
            "code": ["code+with&chars"], "redirect_uri": ["https://app.test/callback?x=1"]}
        return httpx.Response(200, json=payload)

    assert asyncio.run(client(respond).exchange_code("code+with&chars", "https://app.test/callback?x=1")) == payload


def test_refresh_returns_rotated_tokens():
    def respond(request):
        assert request.url.path == "/api/v2/oauth/token"
        assert request.headers["Authorization"].startswith("Basic ")
        assert parse_qs(request.content.decode()) == {"grant_type": ["refresh_token"], "refresh_token": ["old-refresh"]}
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh"})

    assert asyncio.run(client(respond).refresh("old-refresh")) == {
        "access_token": "new-access", "refresh_token": "new-refresh"}
    with pytest.raises(module().ReconnectRequired) as error:
        asyncio.run(client(lambda _: httpx.Response(400, json={"error": "invalid_grant",
            "error_description": "old-refresh"})).refresh("old-refresh"))
    assert module().classify_error(error.value) == "reconnect_required"


def test_invalid_grant_requires_reconnect_only_for_refresh():
    def respond(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(httpx.HTTPStatusError) as error:
        asyncio.run(client(respond).exchange_code("expired-code", "https://app.test/callback"))
    assert module().classify_error(error.value) == "invalid_request"


@pytest.mark.parametrize("envelope", ["image", "images"])
def test_upload_description_images_sends_single_batch_and_rejects_over_twenty(envelope):
    calls = []
    images = [f"image-{i}".encode() for i in range(20)]
    urls = [f"https://myshop.cafe24.com/web/upload/{i}.jpg" for i in range(20)]

    def respond(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path == "/api/v2/admin/products/images"
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["X-Cafe24-Api-Version"] == "2026-09-01"
        assert json.loads(request.content) == {"requests": [
            {"image": base64.b64encode(image).decode()} for image in images]}
        return httpx.Response(201, json={envelope: [{"path": url} for url in urls]})

    api = client(respond)
    assert asyncio.run(api.upload_description_images("access", images)) == urls
    assert len(calls) == 1
    with pytest.raises(ValueError):
        asyncio.run(api.upload_description_images("access", images + [b"one-too-many"]))
    assert asyncio.run(api.upload_description_images("access", [])) == []
    assert len(calls) == 1


def test_find_products_by_custom_code_counts_exact_matches_only():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/api/v2/admin/products"
        assert request.url.params["custom_product_code"] == "wl0123456789abcdef"
        assert request.url.params["limit"] == "100"
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json={"products": [
                {"product_no": i, "custom_product_code": "prefix-wl0123456789abcdef"} for i in range(100)]})
        assert request.url.params["offset"] == "100"
        return httpx.Response(200, json={"products": [
            {"product_no": 101, "custom_product_code": "wl0123456789abcdef"},
            {"product_no": 102, "custom_product_code": "wl0123456789abcdef-suffix"},
            {"product_no": 103, "custom_product_code": "WL0123456789ABCDEF"},
            {"product_no": 104, "custom_product_code": "wl0123456789abcdef"}]})

    assert asyncio.run(client(respond).find_products_by_custom_code("access", "wl0123456789abcdef")) == [101, 104]
    assert len(calls) == 2


def test_create_product_uses_price_excluding_tax_when_setting_is_b():
    c24 = module()
    assert c24.price_fields("B", "39000") == {"price_excluding_tax": "39000"}
    for setting in ("S", "A", "P"):
        assert c24.price_fields(setting, "39000") == {"price": "39000"}
    for setting in (None, "", "unknown"):
        with pytest.raises(ValueError):
            c24.price_fields(setting, "39000")
    body = {"shop_no": 1, "request": {"product_name": "니트", "supply_price": "39000",
        "custom_product_code": "wl0123456789abcdef", "price_excluding_tax": "39000"}}
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path == "/api/v2/admin/products"
        assert json.loads(request.content) == body
        assert "price" not in json.loads(request.content)["request"]
        return httpx.Response(201, json={"product": {"product_no": 123}})

    assert asyncio.run(client(respond).create_product("access", body)) == 123
    assert "price" not in body["request"] and len(calls) == 1


@pytest.mark.parametrize("basis", ["S", "A", "P", "B"])
def test_product_setting_parses_confirmed_field(basis):
    def respond(request):
        assert request.method == "GET" and request.url.path == "/api/v2/admin/products/setting"
        return httpx.Response(200, json={"products_setting": {"calculate_price_based_on": basis,
            "price_rounding_unit": "0", "price_rounding_rule": "U"}})

    assert asyncio.run(client(respond).product_setting("access")) == basis
    for payload in ({}, {"products_setting": {}}, {"products_setting": {"calculate_price_based_on": "X"}}):
        with pytest.raises(Exception) as error:
            asyncio.run(client(lambda _: httpx.Response(200, json=payload)).product_setting("access"))
        assert module().classify_error(error.value) == "price_setting_unavailable"


def test_description_html_matches_frontend_snippet_shape():
    urls = ['https://myshop.cafe24.com/1.jpg?a=1&b="x"', "https://myshop.cafe24.com/2.jpg"]
    name = '니트 <A> & "B" \'C\''
    expected = ('<div style="text-align:center;">\n'
        '<img src="https://myshop.cafe24.com/1.jpg?a=1&amp;b=&quot;x&quot;" '
        'alt="니트 &lt;A&gt; &amp; &quot;B&quot; &#39;C&#39; 상세 1" '
        'style="display:block;width:100%;max-width:860px;margin:0 auto;">\n'
        '<img src="https://myshop.cafe24.com/2.jpg" '
        'alt="니트 &lt;A&gt; &amp; &quot;B&quot; &#39;C&#39; 상세 2" '
        'style="display:block;width:100%;max-width:860px;margin:0 auto;">\n</div>')
    assert module().build_description_html(urls, name, 860) == expected
    assert module().build_description_html([], "", 860) == '<div style="text-align:center;">\n\n</div>'
    # Execute the frontend's actual builder without loading its browser-only imports.
    assert shutil.which("node"), "Node is required to compare the frontend HTML builder"
    source = Path(__file__).resolve().parents[2] / "src/features/editor/detailPublish.js"
    script = """const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const escape = source.match(/const escapeHtml = .*;/)[0];
const builder = source.match(/export function buildPublicationHtml[\\s\\S]*?\\n}/)[0].replace('export ', '');
const [urls, productName] = JSON.parse(process.argv[2]);
process.stdout.write(eval(escape + '\\n' + builder + '\\nbuildPublicationHtml(urls.map(url => ({url})), {productName, width: 860})'));
"""
    result = subprocess.run(["node", "-e", script, str(source), json.dumps([urls, name])],
                            check=True, text=True, capture_output=True)
    assert result.stdout == expected


def test_classify_error_401_403_422_429_timeout_5xx():
    c24 = module()
    cases = [(401, {}, "reconnect_required"), (403, {"message": "missing scope"}, "missing_permission"),
        (403, {"message": "The app has been removed from the store."}, "app_removed"),
        (403, {"message": "The app is not installed in the store."}, "app_removed"),
        (403, {"code": "app_not_installed"}, "app_removed"),
        (403, {"message": "쇼핑몰에서 앱이 삭제되었습니다."}, "app_removed"),
        (400, {}, "invalid_request"), (422, {}, "invalid_request"), (429, {}, "rate_limited"),
        (500, {}, "unknown"), (503, {}, "unknown")]
    for status, error_body, want in cases:
        def respond(request):
            return httpx.Response(status, json={"error": error_body}, headers={"Retry-After": "12"})
        with pytest.raises(Exception) as error:
            asyncio.run(client(respond).store("access"))
        assert c24.classify_error(error.value) == want
        if status == 403:
            assert isinstance(error.value, c24.AppRemoved if want == "app_removed" else c24.MissingPermission)
        if status == 429:
            assert error.value.response.headers["Retry-After"] == "12"
        response = httpx.Response(status, request=httpx.Request("GET", "https://myshop.cafe24api.com"),
                                  json={"error": error_body})
        assert c24.classify_error(httpx.HTTPStatusError("upstream", request=response.request, response=response)) == want
        assert c24.ERROR_COPY[want]
    for exc in (TimeoutError(), httpx.ReadTimeout("offline"), httpx.ConnectError("offline")):
        assert c24.classify_error(exc) == "unreachable"
    for cls, want in ((c24.ConnectionChanged, "connection_changed"), (c24.ReconnectRequired, "reconnect_required"),
                      (c24.AppRemoved, "app_removed"), (c24.MissingPermission, "missing_permission")):
        assert c24.classify_error(cls()) == want
    assert c24.classify_error(ValueError("unknown")) == "unknown"


@pytest.mark.parametrize("failure", ["401", "403", "422", "429", "500", "json", "error", "transport", "decode", "refresh"])
def test_client_never_logs_tokens_or_upstream_bodies(caplog, failure):
    caplog.set_level(logging.DEBUG)
    secret = "sensitive-access-refresh-client-secret"
    upstream = "private-upstream-body-marker"

    def respond(request):
        if failure == "transport":
            raise httpx.ReadTimeout(f"{secret} {upstream}", request=request)
        if failure == "decode":
            raise httpx.DecodingError(f"{secret} {upstream}", request=request)
        if failure == "json":
            return httpx.Response(200, text=f"{secret} {upstream}")
        if failure == "refresh":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": f"{secret} {upstream}"})
        return httpx.Response(200 if failure == "error" else int(failure), json={"error": {
            "message": f"{secret} {upstream}", "more_info": secret}})

    api = module().Cafe24Client("myshop", client_id=secret, client_secret=secret, api_version="2026-09-01",
                                transport=httpx.MockTransport(respond))
    with pytest.raises(Exception) as error:
        asyncio.run(api.refresh(secret) if failure == "refresh" else api.store(secret))
    rendered = "".join(traceback.format_exception(error.value))
    observable = caplog.text + str(error.value) + repr(error.value) + repr(error.value.args) + rendered
    assert secret not in observable and upstream not in observable
    if isinstance(error.value, httpx.HTTPStatusError):
        assert not error.value.response.content and not error.value.request.content
        assert "Authorization" not in error.value.request.headers


@pytest.mark.parametrize("status", [200, 204])
def test_revoke_accepts_empty_success_response(status):
    assert asyncio.run(client(lambda _: httpx.Response(status)).revoke("refresh", "refresh_token")) is None


@pytest.mark.parametrize("payload", [{}, {"access_token": "access"},
    {"access_token": "access", "refresh_token": ""}, {"access_token": [], "refresh_token": "refresh"}])
def test_oauth_success_requires_both_rotated_tokens(payload):
    for operation in ("refresh", "exchange_code"):
        api = client(lambda _: httpx.Response(200, json=payload))
        with pytest.raises(Exception) as error:
            asyncio.run(api.refresh("old") if operation == "refresh" else api.exchange_code("code", "https://app.test"))
        assert module().classify_error(error.value) == "unknown"


def test_successful_oauth_does_not_log_credentials(caplog):
    caplog.set_level(logging.DEBUG)
    payload = {"access_token": "private-new-access", "refresh_token": "private-new-refresh"}
    api = client(lambda _: httpx.Response(200, json=payload))
    assert asyncio.run(api.refresh("private-old-refresh")) == payload
    assert asyncio.run(api.exchange_code("private-code", "https://app.test/callback")) == payload
    for secret in (*payload.values(), "private-old-refresh", "private-code", "client-secret",
                   "Y2xpZW50LWlkOmNsaWVudC1zZWNyZXQ="):
        assert secret not in caplog.text


@pytest.mark.parametrize("operation,payload,code", [
    ("store", {"store": "private-body"}, "unknown"),
    ("images", {"images": [{"wrong": "private-body"}]}, "image_upload_mismatch"),
    ("images", {"images": "private-body"}, "image_upload_mismatch"),
    ("create", {"product": {"product_no": "private-body"}}, "unknown"),
    ("find", {"products": [{"custom_product_code": "code", "product_no": "private-body"}]}, "unknown"),
    ("thumbnail", {"image": "private-body"}, "unknown"),
    ("categories", {"categories": ["private-body"]}, "unknown"),
])
def test_malformed_success_responses_are_safe_failures(operation, payload, code):
    api = client(lambda _: httpx.Response(200, json=payload))
    calls = {"store": lambda: api.store("access"), "images": lambda: api.upload_description_images("access", [b"image"]),
        "create": lambda: api.create_product("access", {}), "find": lambda: api.find_products_by_custom_code("access", "code"),
        "thumbnail": lambda: api.upload_product_image("access", 1, b"jpeg"),
        "categories": lambda: api.categories("access", limit=100, offset=0)}
    with pytest.raises(Exception) as error:
        asyncio.run(calls[operation]())
    assert module().classify_error(error.value) == code
    assert "private-body" not in str(error.value)


def test_store_revoke_categories_and_thumbnail_use_confirmed_contracts():
    calls = []

    def respond(request):
        calls.append(request)
        path = request.url.path
        if path == "/api/v2/oauth/revoke":
            assert request.method == "POST" and request.headers["Authorization"].startswith("Basic ")
            assert parse_qs(request.content.decode()) == {"token": ["refresh"], "token_hint": ["refresh_token"]}
            return httpx.Response(204)
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["X-Cafe24-Api-Version"] == "2026-09-01"
        if path == "/api/v2/admin/store":
            return httpx.Response(200, json={"store": {"shop_name": "니트", "mall_id": "myshop"}})
        if path == "/api/v2/admin/categories":
            assert dict(request.url.params) == {"limit": "100", "offset": "10"}
            return httpx.Response(200, json={"categories": [{"category_no": 27, "category_name": "상의"}]})
        assert path == "/api/v2/admin/products/123/images" and request.method == "POST"
        assert json.loads(request.content) == {"shop_no": 1, "request": {
            "image_upload_type": "A", "detail_image": "data:image/jpeg;base64,anBlZw=="}}
        return httpx.Response(201, json={"image": {"detail_image": "https://myshop.cafe24.com/big.jpg"}})

    api = client(respond)
    assert asyncio.run(api.store("access")) == {"shop_name": "니트", "mall_id": "myshop"}
    assert asyncio.run(api.revoke("refresh", "refresh_token")) is None
    assert asyncio.run(api.categories("access", limit=100, offset=10)) == [{"category_no": 27, "category_name": "상의"}]
    assert asyncio.run(api.upload_product_image("access", 123, b"jpeg")) == {
        "detail_image": "https://myshop.cafe24.com/big.jpg"}
    assert len(calls) == 4


@pytest.mark.parametrize("stage", ["request", "body", "close", "pagination"])
def test_total_timeout_covers_request_body_close_and_pagination(stage):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in (b'{"store":', b'{"shop_name":', b'"test"}}'):
                await asyncio.sleep(0.02)
                yield part

    async def respond(request):
        if stage == "request":
            await asyncio.sleep(0.2)
        if stage == "body":
            return httpx.Response(200, stream=Stream())
        if stage == "pagination":
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"products": [
                {"product_no": i, "custom_product_code": "prefix-code"} for i in range(100)]})
        return httpx.Response(200, json={"store": {"shop_name": "test"}})

    class Transport(httpx.MockTransport):
        async def aclose(self):
            if stage == "close":
                await asyncio.sleep(0.2)

    api = module().Cafe24Client("myshop", client_id="c", client_secret="s", api_version="2026-09-01",
                                timeout=0.03, transport=Transport(respond))
    started = time.monotonic()
    with pytest.raises(TimeoutError) as error:
        asyncio.run(api.find_products_by_custom_code("access", "code") if stage == "pagination" else api.store("access"))
    assert time.monotonic() - started < 0.15
    assert module().classify_error(error.value) == "unreachable"


def test_variant_bulk_write_requires_every_row_and_inventory_read_preserves_confirmation_fields():
    calls = []
    variant = dict(variant_code='sku', quantity=0, additional_amount='1000.00', display='T', selling='F',
                   use_inventory='T', display_soldout='T', safety_inventory=0,
                   options=[dict(name='색상', value='검정'), dict(name='사이즈', value='FREE')])
    changes = [{k: v for k, v in variant.items() if k != 'options'}]

    def respond(request):
        calls.append(request)
        assert request.url.path == '/api/v2/admin/products/123/variants'
        if request.method == 'PUT':
            assert json.loads(request.content) == dict(shop_no=1, request=dict(variants=changes))
        return httpx.Response(200, json={'variants': [variant]})

    api = client(respond)
    assert asyncio.run(api.update_variants('access', 123, changes)) == {'variantCodes': ['sku']}
    rows = asyncio.run(api.variants('access', 123))
    assert rows[0]['useInventory'] == 'T' and rows[0]['quantity'] == 0 and rows[0]['additionalAmount'] == '1000.00'
    assert rows[0]['displaySoldout'] == 'T' and rows[0]['safetyInventory'] == 0
    for rows in ([], [{'variant_code': 'other'}], [{'variant_code': 'sku', 'error': {'message': 'private'}}]):
        with pytest.raises(module().Cafe24ApiError):
            asyncio.run(client(lambda _: httpx.Response(200, json={'variants': rows})).update_variants('access', 123, changes))
    assert len(calls) == 2


def test_each_paginated_read_checks_the_injected_request_fence():
    calls = []
    api = client(lambda req: calls.append(req) or httpx.Response(200, json={'variants': [
        dict(variant_code=f'sku{i}', options=[]) for i in range(100)]}))
    checks = []

    async def fence():
        checks.append('check')
        if len(checks) == 2:
            raise RuntimeError('lease_lost')

    api.before_request = fence
    with pytest.raises(RuntimeError, match='lease_lost'):
        asyncio.run(api.variants('access', 123))
    assert len(checks) == 2 and len(calls) == 1


def test_inventory_fallback_and_activation_only_send_absolute_explicit_values():
    calls = []
    api = client(lambda req: calls.append(req) or httpx.Response(200, json={'product': {'product_no': 123}}))
    inventory = dict(quantity=0, use_inventory='T', display_soldout='T', safety_inventory=0)
    assert asyncio.run(api.set_variant_inventory('access', 123, 'sku/code', inventory)) is None
    assert calls[-1].url.raw_path == b'/api/v2/admin/products/123/variants/sku%2Fcode/inventories'
    assert json.loads(calls[-1].content) == dict(shop_no=1, request=inventory)
    assert asyncio.run(api.set_product_state('access', 123, display='T', selling='F')) is None
    assert calls[-1].method == 'PUT' and calls[-1].url.path == '/api/v2/admin/products/123'
    assert json.loads(calls[-1].content) == dict(shop_no=1, request=dict(display='T', selling='F'))
