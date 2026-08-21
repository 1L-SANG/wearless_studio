import asyncio
import re
import time

import httpx
import pytest

from app import holder_client


SECRET = "shared-secret"
TARGET = "/holder/vc/verify"
TIMESTAMP = "1800000000"
NONCE = "nonce_1234567890123456789012"
BODY = b'{"vcId":"vc-1"}'
SIGNATURE = "c58f4b35c96bafee167cbbb9edccbb55d23d1b47822d5d6f245199d2dee12e6b"


def test_signature_matches_the_java_protocol_vector():
    assert holder_client.signature(
        SECRET, "POST", TARGET, TIMESTAMP, NONCE, BODY
    ) == SIGNATURE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "PUT"),
        ("target", "/holder/vc/revoke"),
        ("timestamp", "1800000001"),
        ("nonce", "nonce_1234567890123456789013"),
        ("body", b'{"vcId":"vc-2"}'),
    ],
)
def test_signature_changes_when_a_canonical_field_changes(field, value):
    request = {
        "method": "POST",
        "target": TARGET,
        "timestamp": TIMESTAMP,
        "nonce": NONCE,
        "body": BODY,
    }
    request[field] = value

    assert holder_client.signature(SECRET, **request) != SIGNATURE


def test_post_rejects_non_relative_holder_paths_before_serialization_or_transport():
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for path in ("@evil.test/x", "https://evil.test/x", "//evil.test/x", ""):
                with pytest.raises(ValueError, match="^invalid Holder path$"):
                    await holder_client.post(
                        client,
                        base_url="http://holder:8100",
                        secret=SECRET,
                        path=path,
                        payload={"not-json": object()},
                    )

    asyncio.run(scenario())

    assert requests == []


def test_post_sends_and_signs_the_exact_json_bytes():
    observed = {}

    async def handler(request: httpx.Request):
        observed["url"] = str(request.url)
        observed["body"] = request.content
        observed["headers"] = request.headers
        return httpx.Response(200, content=b'{"status":"valid"}')

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await holder_client.post(
                client,
                base_url="http://holder:8100/",
                secret=SECRET,
                path=TARGET,
                payload={"vcId": "vc-1"},
                timestamp=TIMESTAMP,
                nonce=NONCE,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.content == b'{"status":"valid"}'
    assert observed["url"] == "http://holder:8100/holder/vc/verify"
    assert observed["body"] == BODY
    assert observed["headers"]["content-type"] == "application/json"
    assert observed["headers"]["x-fm-timestamp"] == TIMESTAMP
    assert observed["headers"]["x-fm-nonce"] == NONCE
    assert observed["headers"]["x-fm-signature"] == SIGNATURE


def test_post_preserves_and_signs_the_raw_path_and_query():
    target = (
        "/holder/vc/verify?redirect=%2Fholder%2Fhealth"
        "&label=%ED%95%9C%EA%B8%80+test&tag=a&tag=b"
    )
    expected_signature = (
        "119456111358d673401243b6931deca818deb17050dce90fa4c860d212d8923e"
    )
    observed = {}

    async def handler(request: httpx.Request):
        observed["raw_path"] = request.url.raw_path
        observed["signature"] = request.headers["x-fm-signature"]
        return httpx.Response(204)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await holder_client.post(
                client,
                base_url="http://holder:8100/",
                secret=SECRET,
                path=target,
                payload={"vcId": "vc-1"},
                timestamp=TIMESTAMP,
                nonce=NONCE,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 204
    assert observed["raw_path"] == target.encode()
    assert observed["signature"] == expected_signature


def test_post_generates_a_fresh_urlsafe_nonce_and_epoch_timestamp_per_request():
    headers = []
    before = int(time.time())

    async def handler(request: httpx.Request):
        headers.append(request.headers)
        return httpx.Response(200)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(2):
                await holder_client.post(
                    client,
                    base_url="http://holder:8100",
                    secret=SECRET,
                    path=TARGET,
                    payload={"vcId": "vc-1"},
                )

    asyncio.run(scenario())
    after = int(time.time())

    nonces = [item["x-fm-nonce"] for item in headers]
    timestamps = [item["x-fm-timestamp"] for item in headers]
    assert len(set(nonces)) == 2
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{22,128}", nonce) for nonce in nonces)
    assert all(timestamp.isdigit() for timestamp in timestamps)
    assert all(before <= int(timestamp) <= after for timestamp in timestamps)


def test_post_does_not_rewrite_transport_exceptions():
    error = httpx.ConnectError("holder down")

    async def handler(_request: httpx.Request):
        raise error

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await holder_client.post(
                client,
                base_url="http://holder:8100",
                secret=SECRET,
                path=TARGET,
                payload={"vcId": "vc-1"},
            )

    with pytest.raises(httpx.ConnectError) as raised:
        asyncio.run(scenario())

    assert raised.value is error


def test_post_returns_holder_status_and_body_unchanged():
    async def handler(_request: httpx.Request):
        return httpx.Response(418, content=b"holder-body")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await holder_client.post(
                client,
                base_url="http://holder:8100",
                secret=SECRET,
                path=TARGET,
                payload={"vcId": "vc-1"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 418
    assert response.content == b"holder-body"
