"""협찬 동의 VC — holder 요청 모양과 실패 코드. SQL 전이는 _db 테스트가 본다."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app import facemarket_sponsorship_vc as svc

ROW = {
    "id": "5f0c3c52-8a1f-4d8e-9b41-3a0d2f7b9c11",
    "model_id": "33333333-3333-3333-3333-333333333333",
    "consent_doc_version": "2026-09-sponsorship-v1",
    "participation_doc_sha256": "a" * 64,
    "profile_doc_sha256": "b" * 64,
    "consented_at": datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc),
    "attempts": 0,
}
SETTINGS = SimpleNamespace(opendid_holder_url="http://holder", opendid_holder_hmac_secret="s")


def _fake_holder(monkeypatch, responses):
    calls = []

    async def post(_client, *, base_url, secret, path, payload):
        calls.append((path, payload))
        status, body = responses[path.rsplit("/", 1)[-1]]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json=body)

    monkeypatch.setattr(svc.holder_client, "post", post)
    return calls


OK = {
    "wallet": (201, {}),
    "register-did": (200, {"userDid": "did:omn:model"}),
    "issue-vc": (200, {"vcId": "vc-sp-1"}),
}


def test_issue_sends_sponsorship_plan_with_exactly_six_deterministic_claims(monkeypatch):
    calls = _fake_holder(monkeypatch, OK)
    assert asyncio.run(svc.request_holder_issue(SETTINGS, ROW)) == "vc-sp-1"
    assert [path for path, _ in calls] == [
        f"/holder/models/{ROW['model_id']}/wallet",
        f"/holder/models/{ROW['model_id']}/register-did",
        f"/holder/models/{ROW['model_id']}/issue-vc",
    ]
    payload = calls[-1][1]
    assert payload["plan"] == "fmsponsorship-v1"
    assert payload["idempotencyKey"] == f"fm-sponsorship:{ROW['id']}"
    assert payload["claims"] == {
        "modelDid": "did:omn:model",
        "credentialId": ROW["id"],
        "consentDocVersion": "2026-09-sponsorship-v1",
        "participationDocSha256": "a" * 64,
        "profileDocSha256": "b" * 64,
        "consentedAt": "2026-09-25T12:30:00Z",
    }
    # 인스타·팔로워·사이즈 같은 가변 개인정보는 싣지 않는다.
    assert not {"instagramHandle", "instagramFollowers", "sizeTop"} & set(payload["claims"])


@pytest.mark.parametrize("responses,code", [
    ({**OK, "issue-vc": (400, {"error": "unknown plan"})}, "http_status"),
    ({**OK, "register-did": (200, {})}, "invalid_body"),
    ({**OK, "issue-vc": (200, {"vcId": " "})}, "invalid_body"),
    ({**OK, "wallet": (httpx.ConnectError("down"), None)}, "transport"),
])
def test_issue_failures_map_to_retry_codes(monkeypatch, responses, code):
    _fake_holder(monkeypatch, responses)
    with pytest.raises(svc.SponsorshipVcIssueError) as caught:
        asyncio.run(svc.request_holder_issue(SETTINGS, ROW))
    assert caught.value.code == code


def test_missing_holder_config_is_unavailable():
    with pytest.raises(svc.SponsorshipVcIssueError) as caught:
        asyncio.run(svc.request_holder_issue(SimpleNamespace(
            opendid_holder_url="", opendid_holder_hmac_secret=""), ROW))
    assert caught.value.code == "holder_unavailable"


def test_issue_one_records_failure_or_success(monkeypatch):
    recorded = []

    async def failure(_pool, row, code):
        recorded.append(("fail", code))

    async def issued(_pool, row, vc_id):
        recorded.append(("ok", vc_id))
        return True

    monkeypatch.setattr(svc, "record_failure", failure)
    monkeypatch.setattr(svc, "record_issued", issued)
    app = SimpleNamespace(state=SimpleNamespace(settings=SETTINGS, pool=None))
    _fake_holder(monkeypatch, {**OK, "issue-vc": (503, {})})
    assert asyncio.run(svc.issue_one(app, ROW)) is False
    _fake_holder(monkeypatch, OK)
    assert asyncio.run(svc.issue_one(app, ROW)) is True
    assert recorded == [("fail", "http_status"), ("ok", "vc-sp-1")]
