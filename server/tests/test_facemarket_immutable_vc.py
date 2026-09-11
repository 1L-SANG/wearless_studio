"""New certificates bind stable evidence; mutable usage terms stay in PostgreSQL."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import facemarket, holder_client


LICENSE = "44444444-4444-4444-4444-444444444444"
MODEL = "11111111-1111-1111-1111-111111111111"
CREATED = datetime(2026, 9, 11, 1, 2, 3, 456789, tzinfo=timezone.utc)


def test_retry_sends_same_evidence_despite_changed_usage_terms(monkeypatch):
    requests = []

    async def post(_client, **request):
        path = request["path"]
        if path.endswith("/wallet"):
            return SimpleNamespace(status_code=201)
        if path.endswith("/register-did"):
            return SimpleNamespace(status_code=200, json=lambda: {
                "flowAComplete": True, "userDid": "did:omn:model-1",
            })
        requests.append(request["payload"])
        return SimpleNamespace(status_code=200, json=lambda: {
            "vcId": "vc-1", "userDid": "did:omn:model-1",
        })

    monkeypatch.setattr(holder_client, "post", post)
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(
        opendid_holder_url="http://holder.invalid", opendid_holder_hmac_secret="test-secret",
    )))

    async def issue():
        for allowed, price in ((["일반 의류"], 14900), (["액티브웨어"], 7999)):
            result = await facemarket.issue_face_vc(
                app, license_id=LICENSE, model_id=MODEL,
                allowed=allowed, forbidden=[], unit_price=price, valid_until=None,
                digest="sha256-evidence", issued_at=CREATED,
                consent_doc_version="v1.1",
            )
            assert result.vc_id == "vc-1"

    asyncio.run(issue())
    expected = {
        "plan": "facelicense-v2", "idempotencyKey": f"fm-license:{LICENSE}",
        "claims": {
            "modelDid": "did:omn:model-1", "licenseId": LICENSE,
            "issuedAt": "2026-09-11T01:02:03.456789Z",
            "faceImageDigest": "sha256-evidence", "agreementVersion": "v1",
            "consentDocVersion": "v1.1",
        },
    }
    assert requests == [expected, expected]


@pytest.mark.parametrize("consent", [None, "", " "])
def test_missing_stored_consent_cannot_be_replaced_with_current_version(consent):
    with pytest.raises(ValueError):
        facemarket.build_face_vc_claims(
            model_did="did:omn:model-1", license_id=LICENSE, issued_at=CREATED,
            digest="sha256-evidence", consent_doc_version=consent,
        )


def test_naive_stored_issue_time_has_deterministic_utc_interpretation():
    claims = facemarket.build_face_vc_claims(
        model_did="did:omn:model-1", license_id=LICENSE,
        issued_at=CREATED.replace(tzinfo=None), digest="sha256-evidence",
        consent_doc_version="v1.1",
    )
    assert claims["issuedAt"] == "2026-09-11T01:02:03.456789Z"
