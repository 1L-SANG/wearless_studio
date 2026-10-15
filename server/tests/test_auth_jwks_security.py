"""Exercise the real JWKS cache with only its HTTP transport replaced."""
import io
import json
import time
from types import SimpleNamespace

import jwt
import pytest

from app.auth import jwks_key_resolver


@pytest.fixture
def jwks_session(client, keypair, monkeypatch):
    private_key, public_key = keypair
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public_key))
    public_jwk.update(kid="current", use="sig", alg="ES256")
    state = SimpleNamespace(keys=[public_jwk], fetches=0, clock=1000.0)

    def urlopen(request, **kwargs):
        assert request.full_url == "https://jwks.test/keys"
        state.fetches += 1
        return io.BytesIO(json.dumps({"keys": state.keys}).encode())

    monkeypatch.setattr(jwt.jwks_client.urllib.request, "urlopen", urlopen)
    # PyJWT 2.15 uses an opener to disable redirects; older releases use urlopen.
    monkeypatch.setattr(
        jwt.jwks_client.urllib.request, "build_opener",
        lambda *handlers: SimpleNamespace(open=urlopen),
    )
    clock = SimpleNamespace(monotonic=lambda: state.clock)
    monkeypatch.setattr(jwt.jwks_client, "time", clock, raising=False)
    monkeypatch.setattr(jwt.jwk_set_cache, "time", clock)
    monkeypatch.setattr(jwt.api_jwk, "time", clock)
    client.app.state.jwt_key_resolver = jwks_key_resolver("https://jwks.test/keys")

    def request(kid="current", *, fake=False):
        claims = {"sub": "user-42", "aud": "authenticated", "exp": int(time.time()) + 3600}
        if fake:
            header = jwt.utils.base64url_encode(json.dumps({"kid": kid, "alg": "ES256"}).encode())
            payload = jwt.utils.base64url_encode(json.dumps(claims).encode())
            token = (header + b"." + payload + b".AA").decode()
        else:
            token = jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": kid})
        return client.get("/v1/me/ping", headers={"Authorization": f"Bearer {token}"})

    return state, request


def test_unknown_kid_burst_is_401_without_repeated_jwks_fetches(jwks_session):
    state, request = jwks_session
    for index in range(10):
        response = request(f"unknown-{index}", fake=True)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    assert state.fetches == 1
    assert request().json() == {"userId": "user-42"}
    assert state.fetches == 1


def test_rotated_key_can_be_fetched_after_cooldown(jwks_session):
    state, request = jwks_session
    assert request().status_code == 200
    state.keys = [{**state.keys[0], "kid": "rotated"}]
    state.clock += 31
    assert request("rotated").status_code == 200
    assert state.fetches == 2


def test_removed_key_does_not_live_forever_in_signing_key_cache(jwks_session):
    state, request = jwks_session
    assert request().status_code == 200
    state.keys = [{**state.keys[0], "kid": "rotated"}]
    state.clock += 301
    assert request().status_code == 401
    assert request("rotated").status_code == 200
