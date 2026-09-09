"""셀러 약관 동의 게이트 API(/v1/me/consents).

원칙: 로그인마다 묻지 않는다. 기록이 없거나 문서가 개정됐을 때만 needsConsent=true.
"""
import json
import pathlib

import pytest

from app import legal_versions, repo, routes
from conftest import auth_headers, patch_route_db

REQ = {"terms": legal_versions.SELLER_TERMS_VERSION, "privacy": legal_versions.SELLER_PRIVACY_VERSION}


def _row(terms=REQ["terms"], privacy=REQ["privacy"], age=True):
    return {"terms_version": terms, "privacy_version": privacy, "age_attested": age,
            "accepted_at": "2026-09-07T00:00:00+00:00"}


def test_get_without_record_needs_consent(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)

    async def none(conn, user_id):
        return None
    monkeypatch.setattr(repo, "get_seller_consent", none)

    r = client.get("/v1/me/consents", headers=auth_headers(make_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["needsConsent"] is True and body["accepted"] is None
    assert body["required"] == REQ


def test_get_with_current_versions_does_not_ask_again(client, make_token, monkeypatch):
    """핵심: 이미 동의한 사람은 다음 로그인에서 게이트가 뜨지 않는다."""
    patch_route_db(monkeypatch, routes)

    async def current(conn, user_id):
        return _row()
    monkeypatch.setattr(repo, "get_seller_consent", current)

    body = client.get("/v1/me/consents", headers=auth_headers(make_token)).json()
    assert body["needsConsent"] is False
    assert body["accepted"]["termsVersion"] == REQ["terms"]


def test_get_with_old_version_asks_again(client, make_token, monkeypatch):
    """문서가 개정되면(버전 불일치) 그때만 다시 묻는다."""
    patch_route_db(monkeypatch, routes)

    async def old(conn, user_id):
        return _row(terms="v0.9")
    monkeypatch.setattr(repo, "get_seller_consent", old)

    body = client.get("/v1/me/consents", headers=auth_headers(make_token)).json()
    assert body["needsConsent"] is True


def test_post_records_consent(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    seen = {}

    async def upsert(conn, user_id, *, terms_version, privacy_version, age_attested):
        seen.update(user_id=user_id, terms=terms_version, privacy=privacy_version, age=age_attested)
        return _row(terms_version, privacy_version, age_attested)
    monkeypatch.setattr(repo, "upsert_seller_consent", upsert)

    r = client.post(
        "/v1/me/consents", headers=auth_headers(make_token),
        json={"termsVersion": REQ["terms"], "privacyVersion": REQ["privacy"], "ageAttested": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["needsConsent"] is False
    assert seen == {"user_id": "user-1", "terms": REQ["terms"], "privacy": REQ["privacy"], "age": True}


def test_post_without_age_attestation_is_rejected(client, make_token, monkeypatch):
    patch_route_db(monkeypatch, routes)
    r = client.post(
        "/v1/me/consents", headers=auth_headers(make_token),
        json={"termsVersion": REQ["terms"], "privacyVersion": REQ["privacy"], "ageAttested": False},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "age_attestation_required"


def test_post_with_stale_version_is_conflict(client, make_token, monkeypatch):
    """게이트를 띄워둔 사이 문서가 개정되면 낡은 버전 동의는 기록하지 않는다."""
    patch_route_db(monkeypatch, routes)
    r = client.post(
        "/v1/me/consents", headers=auth_headers(make_token),
        json={"termsVersion": "v0.9", "privacyVersion": REQ["privacy"], "ageAttested": True},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "consent_version_mismatch"


def test_requires_auth(client):
    assert client.get("/v1/me/consents").status_code == 401


def test_server_versions_match_published_manifest():
    """서버 상수와 발행된 문서 버전이 어긋나면 모든 셀러에게 재동의가 뜨거나(과다) 개정을 못 잡는다(과소)."""
    manifest = pathlib.Path(__file__).resolve().parents[2] / "public" / "legal" / "manifest.json"
    if not manifest.exists():
        pytest.skip("public/legal/manifest.json 없음(서버 단독 체크아웃)")
    by_slug = {d["slug"]: d for d in json.loads(manifest.read_text(encoding="utf-8"))}
    assert by_slug["terms-seller"]["version"] == legal_versions.SELLER_TERMS_VERSION
    assert by_slug["privacy-seller"]["version"] == legal_versions.SELLER_PRIVACY_VERSION
