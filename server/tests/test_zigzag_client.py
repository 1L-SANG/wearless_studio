import asyncio
import hashlib
import hmac
import importlib
import importlib.util
import json
import time

import httpx
import pytest


def module():
    assert importlib.util.find_spec("app.services.zigzag_client"), "Zigzag client is missing"
    return importlib.import_module("app.services.zigzag_client")


def client(handler):
    return module().ZigzagClient("https://zigzag.invalid/graphql", "access-key", "secret-key",
                                 algorithm="sha1", transport=httpx.MockTransport(handler))


def test_authorization_header_format():
    # Official Python3 example inputs, with a fixed millisecond clock.
    actual = module().build_authorization("access-key", "secret-key", "graphql-query", signed_date_ms=1700000000000)
    assert actual == "CEA algorithm=HmacSHA256, access-key=access-key, signed-date=1700000000000, signature=4a33b23217bb216caaea19209f68459519727897"


def test_authorization_sha256_option():
    actual = module().build_authorization("access-key", "secret-key", "graphql-query", signed_date_ms=1700000000000, algorithm="sha256")
    assert actual.endswith("signature=cf5af55d650da910d70646d989d92fa8b171ea1d4f667a66d401e40ca3b26349")


def test_authorization_collapses_whitespace_without_stripping():
    build = module().build_authorization
    assert build("a", "s", " \n query {\tshop }\n", signed_date_ms=1) == build("a", "s", " query { shop } ", signed_date_ms=1)


def test_query_body_matches_signed_query(monkeypatch):
    monkeypatch.setattr(module().time, "time", lambda: 1700000000)
    def respond(request):
        query = json.loads(request.content)["query"]
        assert query == " query { shop { shop_id } } "
        signature = hmac.new(b"secret-key", f"1700000000000.{query}".encode(), hashlib.sha1).hexdigest()
        assert request.headers["Authorization"].endswith(f"signature={signature}")
        return httpx.Response(200, json={"data": {"shop": {"shop_id": "1"}}})
    assert asyncio.run(client(respond).query(" \n query {\tshop {\n shop_id } }\n")) == {"shop": {"shop_id": "1"}}


def test_shop_parses_response():
    def respond(request):
        body = json.loads(request.content)
        assert "shop" in body["query"] and "allowed_brand_list" in body["query"]
        assert "CEA algorithm=HmacSHA256" in request.headers["Authorization"]
        assert request.extensions["timeout"]["read"] == 8
        return httpx.Response(200, json={"data": {"shop": {"shop_id": "1", "shop_name": "니트", "allowed_brand_list": [{"brand_id": "b1", "brand_name": "브랜드"}], "site_country_list": [{"site": "ZIGZAG", "site_name": "지그재그", "country_code": "KR", "country_name": "한국"}]}}})
    assert asyncio.run(client(respond).shop()) == {"shop_id": "1", "shop_name": "니트", "allowed_brands": [{"brand_id": "b1", "brand_name": "브랜드"}], "sites": [{"site": "ZIGZAG", "site_name": "지그재그", "country_code": "KR", "country_name": "한국"}]}


def test_shipping_addresses_parses_schema_list():
    def respond(request):
        assert "limit_count: 50" in json.loads(request.content)["query"]
        return httpx.Response(200, json={"data": {"shop_shipping_address_list": {"total_count": 1, "item_list": [{"id": "a1", "name": "창고", "address": "서울", "address_detail": "1층", "postcode": "12345", "is_default": True}]}}})
    assert asyncio.run(client(respond).shipping_addresses()) == [{"id": "a1", "name": "창고", "address": "서울 1층", "postcode": "12345", "is_default": True}]


def test_graphql_errors_raise():
    zz = module()
    with pytest.raises(zz.ZigzagApiError) as error:
        asyncio.run(client(lambda _: httpx.Response(200, json={"data": None, "errors": [{"message": "Permission denied", "extensions": {"code": "FORBIDDEN"}}]})).shop())
    assert zz.classify_error(error.value) == "missing_permission"
    assert "Permission denied" not in str(error.value)


def test_classify_error_invalid_key():
    zz = module()
    for status in (401, 403):
        response = httpx.Response(status, request=httpx.Request("POST", "https://zigzag.invalid"))
        assert zz.classify_error(httpx.HTTPStatusError("bad", request=response.request, response=response)) == "invalid_key"
    assert zz.classify_error(zz.ZigzagApiError("AUTHENTICATION_FAILED", "인증 오류")) == "invalid_key"


def test_classify_error_missing_permission():
    zz = module()
    for message in ("권한 부족", "permission denied", "missing scope"):
        assert zz.classify_error(zz.ZigzagApiError("FORBIDDEN", message)) == "missing_permission"


def test_classify_error_unreachable():
    assert module().classify_error(httpx.ConnectError("offline")) == "unreachable"


def test_classify_error_unknown():
    assert module().classify_error(ValueError("unrecognized")) == "unknown"


def test_timeout_maps_to_unreachable():
    def timeout(_):
        raise httpx.ReadTimeout("offline")
    with pytest.raises(httpx.ReadTimeout) as error:
        asyncio.run(client(timeout).shop())
    assert module().classify_error(error.value) == "unreachable"


@pytest.mark.parametrize("stage", ["request", "body", "close"])
def test_total_timeout_is_bounded(stage):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in (b'{"data":', b'{"ok":', b'true}}'):
                if stage == "body":
                    await asyncio.sleep(0.02)
                yield part

    async def respond(_):
        if stage == "request":
            await asyncio.sleep(0.2)
        return httpx.Response(200, stream=Stream())

    class Transport(httpx.MockTransport):
        async def aclose(self):
            if stage == "close":
                await asyncio.sleep(0.2)

    zigzag = module().ZigzagClient("https://zigzag.invalid/graphql", "access", "secret",
        algorithm="sha1", timeout=0.03, transport=Transport(respond))
    started = time.monotonic()
    with pytest.raises(TimeoutError) as error:
        asyncio.run(zigzag.query("query { ok }"))
    assert time.monotonic() - started < 0.15
    assert module().classify_error(error.value) == "unreachable"
