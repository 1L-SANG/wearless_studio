import types

import pytest

from app import r2 as r2_module
from app.config import load_settings
from app.r2 import R2Client


def _client(*, public_base="https://images.example.test", zone="zone-1", token="token-1"):
    client = object.__new__(R2Client)
    client._public_base = public_base
    client._cloudflare_zone_id = zone
    client._cloudflare_cache_purge_token = token
    return client


def test_cloudflare_prefix_purge_batches_at_thirty_and_covers_query_variants(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return types.SimpleNamespace(status_code=200, json=lambda: {"success": True})

    monkeypatch.setattr(r2_module.httpx, "post", fake_post)
    monkeypatch.setattr(r2_module.time, "sleep", lambda _seconds: None)
    client = _client()
    client.purge_public_cache([f"users/u/ai/{i}.png" for i in range(65)])

    assert [len(call[1]["json"]["prefixes"]) for call in calls] == [30, 30, 5]
    assert calls[0][0] == "https://api.cloudflare.com/client/v4/zones/zone-1/purge_cache"
    assert calls[0][1]["json"]["prefixes"][0] == "images.example.test/users/u/ai/0.png"
    assert calls[-1][1]["json"]["prefixes"][-1] == "images.example.test/users/u/ai/64.png"
    assert all(call[1]["headers"] == {"Authorization": "Bearer token-1"} for call in calls)

@pytest.mark.parametrize("failure", ["success_false", "http_error", "transport"])
def test_cloudflare_purge_errors_never_expose_bearer(monkeypatch, failure):
    token = "never-log-this-token"

    def fake_post(_url, **kwargs):
        if failure == "transport":
            raise RuntimeError(f"transport leaked {kwargs['headers']}")
        return types.SimpleNamespace(
            status_code=403 if failure == "http_error" else 200,
            json=lambda: {"success": failure != "success_false"},
        )

    monkeypatch.setattr(r2_module.httpx, "post", fake_post)
    with pytest.raises(RuntimeError) as exc:
        _client(token=token).purge_public_cache(["private/result.png"])

    assert str(exc.value) == "cdn_purge_failed"
    assert token not in repr(exc.value)


def test_cloudflare_purge_requires_config_only_when_public_targets_exist(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(r2_module.httpx, "post", forbidden)
    _client(public_base=None, zone=None, token=None).purge_public_cache(["ignored.png"])
    _client(zone=None, token=None).purge_public_cache([])

    with pytest.raises(RuntimeError, match="^cdn_purge_failed$"):
        _client(zone=None, token=None).purge_public_cache(["must-purge.png"])


def test_cloudflare_prefix_purge_respects_bounded_retry_after(monkeypatch):
    calls = []
    sleeps = []

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return types.SimpleNamespace(
                status_code=429,
                headers={"Retry-After": "45"},
                json=lambda: {"success": False},
            )
        return types.SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {"success": True},
        )

    monkeypatch.setattr(r2_module.httpx, "post", fake_post)
    monkeypatch.setattr(r2_module.time, "sleep", sleeps.append)

    _client().purge_public_cache(["private/result.png"])

    assert len(calls) == 2
    assert sleeps == [45.0]


def test_cloudflare_prefix_purge_progresses_past_one_thousand_keys_under_free_tier_bucket(monkeypatch):
    calls = []
    sleeps = []
    clock = 0.0
    tokens = 5.0
    last_refill = 0.0

    def fake_post(_url, **kwargs):
        nonlocal tokens, last_refill
        tokens = min(5.0, tokens + ((clock - last_refill) / 12.0))
        last_refill = clock
        assert tokens >= 1.0, "modeled Free-tier prefix token bucket exhausted"
        tokens -= 1.0
        calls.append(tuple(kwargs["json"]["prefixes"]))
        return types.SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {"success": True},
        )

    monkeypatch.setattr(r2_module.httpx, "post", fake_post)
    def fake_sleep(seconds):
        nonlocal clock
        sleeps.append(seconds)
        clock += seconds

    monkeypatch.setattr(r2_module.time, "sleep", fake_sleep)
    keys = [f"users/u/ai/{index:04d}.png" for index in range(1005)]

    _client().purge_public_cache(keys)

    assert len(calls) == 34
    assert [len(batch) for batch in calls] == [30] * 33 + [15]
    assert calls[-1][-1] == "images.example.test/users/u/ai/1004.png"
    assert sleeps == [12.0] * 33


def test_cloudflare_purge_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ZONE_ID", "zone-env")
    monkeypatch.setenv("CLOUDFLARE_CACHE_PURGE_TOKEN", "token-env")

    settings = load_settings()

    assert settings.cloudflare_zone_id == "zone-env"
    assert settings.cloudflare_cache_purge_token == "token-env"
