import asyncio
import types
from datetime import datetime, timezone

import httpx
import pytest

from app import facemarket, holder_client


LICENSE_ID = "44444444-4444-4444-4444-444444444444"
MODEL_ID = "11111111-1111-1111-1111-111111111111"
VALID_UNTIL = datetime(2027, 2, 3, tzinfo=timezone.utc)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _app(*, base="http://holder:8100", secret="shared-secret"):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            settings=types.SimpleNamespace(
                opendid_holder_url=base,
                opendid_holder_hmac_secret=secret,
            )
        )
    )


def _issue(app):
    return asyncio.run(facemarket.issue_face_vc(
        app,
        license_id=LICENSE_ID,
        model_id=MODEL_ID,
        allowed=["일반 여성 의류"],
        forbidden=["성인용품"],
        unit_price=4321,
        valid_until=VALID_UNTIL,
        digest="sha256-approved-front",
    ))


@pytest.mark.parametrize("wallet_status", [201, 409])
def test_issue_uses_one_signed_client_and_body_idempotency_key(monkeypatch, wallet_status):
    responses = iter([
        _Response(wallet_status, {"modelId": MODEL_ID}),
        _Response(200, {"flowAComplete": True, "userDid": "did:omn:user"}),
        _Response(200, {"vcId": "vc-1", "userDid": "did:omn:user"}),
    ])
    calls = []

    async def fake_post(client, **kwargs):
        calls.append((id(client), kwargs))
        return next(responses)

    monkeypatch.setattr(holder_client, "post", fake_post)

    issued = _issue(_app())

    assert issued == facemarket.FaceVcIssueResult("vc-1", "did:omn:user")
    assert len({client_id for client_id, _ in calls}) == 1
    assert [call["path"] for _, call in calls] == [
        f"/holder/models/{MODEL_ID}/wallet",
        f"/holder/models/{MODEL_ID}/register-did",
        f"/holder/models/{MODEL_ID}/issue-vc",
    ]
    assert all(call["base_url"] == "http://holder:8100" for _, call in calls)
    assert all(call["secret"] == "shared-secret" for _, call in calls)
    assert [call["payload"] for _, call in calls[:2]] == [{}, {}]
    assert calls[2][1]["payload"] == {
        "plan": "facelicense",
        "idempotencyKey": f"fm-license:{LICENSE_ID}",
        "claims": {
            "allowedUse": "일반 여성 의류",
            "forbiddenUse": "성인용품",
            "unitPrice": 4321,
            "licenseValidUntil": "2027-02-03",
            "faceImageDigest": "sha256-approved-front",
        },
    }


@pytest.mark.parametrize("missing", ["base", "secret"])
@pytest.mark.parametrize("missing_value", [None, "", " "])
def test_issue_requires_nonblank_holder_url_and_secret(missing, missing_value):
    values = {"base": "http://holder:8100", "secret": "shared-secret"}
    values[missing] = missing_value
    with pytest.raises(facemarket.FaceVcIssueError) as error:
        _issue(_app(**values))
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "upstream_error",
    [httpx.ConnectError("down"), httpx.TimeoutException("late")],
)
def test_issue_transport_failure_maps_to_503(monkeypatch, upstream_error):
    async def fail(_client, **_kwargs):
        raise upstream_error

    monkeypatch.setattr(holder_client, "post", fail)
    with pytest.raises(facemarket.FaceVcIssueError) as error:
        _issue(_app())
    assert error.value.status_code == 503


@pytest.mark.parametrize("status,expected", [(500, 503), (503, 503), (400, 502), (409, 502)])
def test_issue_http_failures_map_without_exposing_upstream(monkeypatch, status, expected):
    async def respond(_client, **_kwargs):
        return _Response(status, {"claims": "SECRET_CLAIM", "vcId": "SECRET_VC"})

    monkeypatch.setattr(holder_client, "post", respond)
    with pytest.raises(facemarket.FaceVcIssueError) as error:
        _issue(_app())
    assert error.value.status_code == expected
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "register",
    [[], None, "bad", {}, {"flowAComplete": False}, {"userDid": " "}],
)
def test_issue_rejects_malformed_register_as_502(monkeypatch, register):
    responses = iter([_Response(201, {}), _Response(200, register)])

    async def respond(_client, **_kwargs):
        return next(responses)

    monkeypatch.setattr(holder_client, "post", respond)
    with pytest.raises(facemarket.FaceVcIssueError) as error:
        _issue(_app())
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    "issue",
    [[], None, "bad", {}, {"vcId": ""}, {"vcId": " "}, {"vcId": 7}, ValueError("bad json")],
)
def test_issue_rejects_malformed_issue_as_502(monkeypatch, issue):
    responses = iter([
        _Response(201, {}),
        _Response(200, {"flowAComplete": True, "userDid": "did:omn:user"}),
        _Response(200, issue),
    ])

    async def respond(_client, **_kwargs):
        return next(responses)

    monkeypatch.setattr(holder_client, "post", respond)
    with pytest.raises(facemarket.FaceVcIssueError) as error:
        _issue(_app())
    assert error.value.status_code == 502


def test_post_issue_finalization_finishes_before_request_cancellation_propagates():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()

        async def finalize():
            started.set()
            await release.wait()
            completed.set()
            return "active"

        request = asyncio.create_task(
            facemarket._await_post_issue_finalization(finalize())
        )
        await started.wait()
        request.cancel()
        await asyncio.sleep(0)
        assert not request.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert completed.is_set()

    asyncio.run(scenario())
