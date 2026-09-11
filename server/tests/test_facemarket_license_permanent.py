"""Permanent licenses retain nullable API dates and dated credential claims."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.facemarket import CreateLicenseRequest, build_face_vc_claims
from app.facemarket_admin_models import ProfileLicenseView
from test_facemarket_licenses import (
    FakeCursor, _auth, _seed_license_pending_enrollment,
    biometric_fm, holder_stub, valid_license_body,
)
from test_facemarket_publication_verify import PUB_ID, _row, pub_client
from test_facemarket_publications import (
    LICENSE_ID, MODEL_ID, ROUTE_SECRET, _evidence_row, _presign,
    _seed_output_record, _sign, fp, prov,
)


@pytest.mark.parametrize("legacy_days", [None, 365])
def test_creation_stores_null_and_issues_permanent_claim(
    biometric_fm, holder_stub, make_token, monkeypatch, legacy_days
):
    client, store, _ = biometric_fm
    body = valid_license_body(_seed_license_pending_enrollment(store))
    body.pop("validDays", None)
    if legacy_days is not None:
        body["validDays"] = legacy_days
    inserts = []
    original = FakeCursor.execute

    async def capture(self, sql, params=None):
        if " ".join(sql.split()).lower().startswith("insert into fm_licenses"):
            inserts.append(params)
        return await original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", capture)
    response = client.post("/v1/facemarket/licenses", json=body, headers=_auth(make_token))
    assert response.status_code == 201, response.text
    assert inserts[0][9] is None
    assert store["licenses"][0]["license_valid_until"] is None
    assert response.json()["licenseValidUntil"] is None
    issue = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue["payload"]["claims"]["licenseValidUntil"] == "9999-12-31"
    public = client.get(f"/v1/facemarket/verify/{response.json()['id']}")
    assert public.status_code == 200, public.text
    assert public.json()["validUntil"] is None
    assert public.json()["valid"] is True
    assert public.json()["status"] == "active"


def test_old_client_valid_days_is_not_a_request_field():
    request = CreateLicenseRequest(enrollmentId="enrollment", validDays=365)
    assert "valid_days" not in request.model_dump()
    assert "validDays" not in request.model_dump(by_alias=True)


@pytest.mark.parametrize("value, expected", [
    (None, "9999-12-31"),
    (datetime(2027, 9, 7, 23, tzinfo=timezone.utc), "2027-09-08"),
])
def test_credential_claim_retains_key_and_kst_date(value, expected):
    claims = build_face_vc_claims(
        allowed=[], forbidden=[], unit_price=14900, valid_until=value, digest="sha256-x"
    )
    assert claims["licenseValidUntil"] == expected


def test_all_four_admin_eligibility_sql_gates_allow_null():
    source = (Path(__file__).parents[1] / "app/facemarket_admin_models.py").read_text()
    assert source.count("(l.license_valid_until is null or l.license_valid_until > now())") == 4
    assert source.count("l.license_valid_until > now()") == 4


def test_model_profile_license_serializes_permanent_dates():
    profile = ProfileLicenseView(unit_price=14900, valid_until=None, valid_days=None)
    assert profile.model_dump(by_alias=True)["validUntil"] is None
    assert profile.model_dump(by_alias=True)["validDays"] is None


def test_publication_verification_keeps_permanent_license_active(pub_client):
    client, store = pub_client
    store["publications"][PUB_ID] = _row(license_valid_until=None)
    response = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")
    assert response.status_code == 200, response.text
    assert response.json()["licenseValidUntil"] is None
    assert response.json()["valid"] is True
    assert response.json()["status"] == "active"


@pytest.mark.parametrize("value, expected", [
    (None, "9999-12-31"),
    (datetime(2027, 1, 1, tzinfo=timezone.utc), "2027-01-01 00:00:00+00:00"),
])
def test_publication_manifest_keeps_permanent_claim(prov, make_token, value, expected):
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row(license_valid_until=value)
    manifests = []

    class Signer:
        def sign(self, data, mime, manifest):
            manifests.append(manifest)
            return data

    client.app.state.fm_c2pa_signer = Signer()
    presign = _presign(client, make_token)
    assert presign.status_code == 200, presign.text
    token = presign.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, token)["key"]
    r2.objects[key] = b"\x89PNG-original-bytes"
    response = _sign(client, make_token, token)
    assert response.status_code == 200, response.text
    assertion = next(a["data"] for a in manifests[0]["assertions"] if a["label"] == "kr.wearless.facemarket")
    assert assertion["licenseValidUntil"] == expected
