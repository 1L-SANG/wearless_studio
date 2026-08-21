import hashlib
import hmac
import json
import secrets
import time

import httpx


def canonical_request(
    method: str, target: str, timestamp: str, nonce: str, body: bytes
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"v1\n{method.upper()}\n{target}\n{timestamp}\n{nonce}\n{digest}".encode()


def signature(
    secret: str,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret.encode(),
        canonical_request(method, target, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()


async def post(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    secret: str,
    path: str,
    payload: dict,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> httpx.Response:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_urlsafe(24)
    headers = {
        "Content-Type": "application/json",
        "X-FM-Timestamp": timestamp,
        "X-FM-Nonce": nonce,
        "X-FM-Signature": signature(secret, "POST", path, timestamp, nonce, body),
    }
    return await client.post(
        f"{base_url.rstrip('/')}{path}", content=body, headers=headers
    )
