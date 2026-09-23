"""Sponsorship preferences and notification interest stay separate from licensing."""

import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket, facemarket_admin_models
from app.main import create_app
from conftest import assert_query_binds, make_settings


OWNER = "11111111-1111-1111-1111-111111111111"
SELLER = "22222222-2222-2222-2222-222222222222"
MODEL_ID = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)
FIELDS = (
    "sponsorship_enabled", "instagram_handle", "instagram_followers",
    "instagram_followers_reported_at", "size_top", "size_bottom_waist",
    "sponsorship_profile_consent_at",
)
ENABLED = {
    "sponsorshipEnabled": True,
    "profileConsent": True,
    "instagramHandle": " @model.name ",
    "instagramFollowers": 0,
    "sizeTop": "FREE",
    "sizeBottomWaist": 34,
}


class Cursor:
    def __init__(self, store):
        self.store = store
        self.one, self.many = None, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params=None):
        assert_query_binds(sql, params)
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.store["queries"].append((query, params))
        self.one, self.many = None, []
        model = self.store["model"]
        if "ready_for_identity_delete" in query:
            self.one = {"closed": self.store["closed"]}
        elif query.startswith("update fm_models set sponsorship_enabled"):
            values, model_id, owner_id = params[:-2], params[-2], params[-1]
            if model_id == model["id"] and owner_id == model["user_id"]:
                model.update(zip(FIELDS, values))
                self.one = dict(model)
        elif query.startswith("insert into fm_sponsorship_consent_events"):
            pass
        elif query.startswith("insert into fm_sponsorship_interest"):
            assert "on conflict (seller_user_id, model_id) do nothing" in query
            self.store["interests"].add(tuple(params))
        elif "from fm_sponsorship_interest" in query:
            self.one = {"interested": tuple(params) in self.store["interests"]}
        elif "from seller_consents" in query:
            # 셀러 약관 동의 기록 = 셀러 판정. 모델 계정(OWNER)에는 없어요.
            self.one = ({"terms_version": "v1.2", "privacy_version": "v1.1", "age_attested": True,
                         "accepted_at": NOW} if params[0] in self.store["sellers"] else None)
        elif "from fm_models" in query:
            eligible = True
            if "where id = %s and user_id = %s" in query:
                eligible = (model["id"], model["user_id"]) == tuple(params)
            elif "where user_id = %s" in query:
                eligible = model["user_id"] == params[0]
            elif "where m.id = %s" in query:
                eligible = model["id"] == params[0]
            guards = {
                "m.status = 'verified'": model["status"] == "verified",
                "m.confirmed_at is not null": model["confirmed_at"] is not None,
                "m.cover_image_url like %s": (model["cover_image_url"] or "").startswith("facemarket/catalog/models/"),
                "m.fullbody_image_url like %s": (model["fullbody_image_url"] or "").startswith("facemarket/catalog/models/"),
                "l.status = 'active'": model["license_status"] == "active",
                "nullif(btrim(l.vc_id), '') is not null": bool(model["vc_id"]),
                "l.license_valid_until > now()": model["license_valid_until"] is None or model["license_valid_until"] > NOW,
            }
            eligible = eligible and all(value for guard, value in guards.items() if guard in query)
            if eligible:
                self.one, self.many = dict(model), [dict(model)]
        else:
            raise AssertionError(f"Unexpected query: {query}")

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return Cursor(self.store)

    async def commit(self):
        pass


@pytest.fixture
def sponsorship_api(monkeypatch, keypair, make_token):
    store = {
        "model": {
            "id": MODEL_ID, "user_id": OWNER, "display_name": "테스트 모델",
            "status": "verified", "created_at": NOW, "confirmed_at": NOW,
            "cover_image_url": f"facemarket/catalog/models/{MODEL_ID}/closeup.webp",
            "fullbody_image_url": f"facemarket/catalog/models/{MODEL_ID}/fullbody.webp",
            "license_status": "active", "vc_id": "vc-active",
            "unit_price": 14900, "license_valid_until": None, "license_valid_days": None,
            "sponsorship_enabled": False, "instagram_handle": "saved.model",
            "instagram_followers": 1200, "instagram_followers_reported_at": NOW,
            "size_top": "M", "size_bottom_waist": 28,
            "sponsorship_profile_consent_at": NOW,
        },
        "closed": False, "interests": set(), "queries": [], "sellers": {SELLER},
    }

    @contextlib.asynccontextmanager
    async def get_conn(_request):
        yield Conn(store)

    monkeypatch.setattr(facemarket, "get_conn", get_conn)
    monkeypatch.setattr(facemarket_admin_models, "get_conn", get_conn)
    monkeypatch.setattr(facemarket_admin_models, "_cover_serving_url", lambda _request, key: key)
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda _token: keypair[1]
    client = TestClient(app, headers={"Authorization": f"Bearer {make_token(sub=OWNER)}"})
    return client, store, make_token


def test_owner_can_enable_at_license_step_and_zero_followers_are_valid(sponsorship_api):
    client, store, _ = sponsorship_api
    store["model"]["status"] = "pending"
    before_save = datetime.now(timezone.utc)
    response = client.patch(f"/v1/facemarket/models/{MODEL_ID}/sponsorship", json=ENABLED)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == MODEL_ID
    assert body["instagramHandle"] == "model.name"
    assert body["instagramFollowers"] == 0
    assert body["sponsorshipEnabled"] is True
    assert body["sizeTop"] == "FREE"
    assert body["sizeBottomWaist"] == 34
    reported_at = datetime.fromisoformat(body["instagramFollowersReportedAt"].replace("Z", "+00:00"))
    assert before_save <= reported_at <= datetime.now(timezone.utc)
    consent_at = datetime.fromisoformat(body["sponsorshipProfileConsentAt"].replace("Z", "+00:00"))
    assert before_save <= consent_at <= datetime.now(timezone.utc)


def test_enable_requires_profile_consent_when_none_is_recorded(sponsorship_api):
    client, store, _ = sponsorship_api
    store["model"]["sponsorship_profile_consent_at"] = None
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship"
    for body in [{"sponsorshipEnabled": True}, {"sponsorshipEnabled": True, "profileConsent": False}]:
        response = client.patch(url, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "sponsorship_consent_required"
        assert store["model"]["sponsorship_enabled"] is False
    response = client.patch(url, json={"sponsorshipEnabled": True, "profileConsent": True})
    assert response.status_code == 200, response.text
    assert store["model"]["sponsorship_profile_consent_at"] is not None


def test_partial_updates_merge_and_off_purges_profile_and_consent(sponsorship_api):
    client, store, _ = sponsorship_api
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship"
    enabled = client.patch(url, json={"sponsorshipEnabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["instagramFollowersReportedAt"] == "2026-09-22T00:00:00Z"
    changed = client.patch(url, json={"sizeTop": "XS"})
    assert changed.status_code == 200, changed.text
    assert store["model"]["size_top"] == "XS"
    disabled = client.patch(url, json={"sponsorshipEnabled": False})
    assert disabled.status_code == 200, disabled.text
    assert store["model"]["sponsorship_enabled"] is False
    assert all(store["model"][field] is None for field in FIELDS[1:])
    assert disabled.json()["instagramHandle"] is None
    assert disabled.json()["sponsorshipProfileConsentAt"] is None
    # 지운 뒤 다시 켜려면 동의를 새로 받아요.
    again = client.patch(url, json={**ENABLED, "profileConsent": False})
    assert again.status_code == 422 and again.json()["error"]["code"] == "sponsorship_consent_required"


@pytest.mark.parametrize("field", ["instagram_handle", "instagram_followers", "size_top", "size_bottom_waist"])
def test_enable_requires_all_four_fields_after_merge(sponsorship_api, field):
    client, store, _ = sponsorship_api
    store["model"][field] = None
    response = client.patch(f"/v1/facemarket/models/{MODEL_ID}/sponsorship", json={"sponsorshipEnabled": True})
    assert response.status_code == 422, response.text
    assert store["model"]["sponsorship_enabled"] is False


@pytest.mark.parametrize("patch", [
    {"instagramFollowers": -1}, {"instagramFollowers": 1.2}, {"instagramFollowers": True},
    {"instagramFollowers": "12"}, {"instagramFollowers": 2147483648},
    {"sizeBottomWaist": 23}, {"sizeBottomWaist": 35}, {"sizeBottomWaist": 24.0},
    {"sizeBottomWaist": True}, {"sizeTop": "XXL"}, {"sponsorshipEnabled": "true"},
    {"sponsorshipEnabled": None}, {"instagramHandle": "https://instagram.com/a"},
    {"instagramHandle": "a..b"}, {"instagramHandle": ".a"}, {"instagramHandle": "a."},
    {"instagramHandle": "a" * 31}, {"instagramHandle": "@@a"},
    {"instagramFollowersReportedAt": "2020-01-01T00:00:00Z"},
    {"profileConsent": "yes"}, {"sponsorshipProfileConsentAt": "2020-01-01T00:00:00Z"},
])
def test_invalid_input_never_changes_saved_sponsorship(sponsorship_api, patch):
    client, store, _ = sponsorship_api
    before = dict(store["model"])
    response = client.patch(f"/v1/facemarket/models/{MODEL_ID}/sponsorship", json=patch)
    assert response.status_code == 422, response.text
    assert store["model"] == before


def test_update_requires_owner_and_open_account(sponsorship_api):
    client, store, make_token = sponsorship_api
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship"
    response = client.patch(url, json={"sponsorshipEnabled": True}, headers={"Authorization": f"Bearer {make_token(sub=SELLER)}"})
    assert response.status_code == 404
    store["closed"] = True
    assert client.patch(url, json={"sponsorshipEnabled": True}).status_code == 404
    client.headers.pop("Authorization")
    assert client.patch(url, json={"sponsorshipEnabled": True}).status_code == 401


def test_interest_is_persisted_and_duplicate_requests_are_idempotent(sponsorship_api):
    client, store, make_token = sponsorship_api
    store["model"]["sponsorship_enabled"] = True
    client.headers["Authorization"] = f"Bearer {make_token(sub=SELLER)}"
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship-interest"
    assert client.get(url).json() == {"interested": False}
    for _ in range(2):
        response = client.post(url)
        assert response.status_code == 200, response.text
        assert response.json() == {"interested": True}
    persisted = client.get(url)
    assert persisted.json() == {"interested": True}
    assert persisted.headers["cache-control"] == "no-store, private"
    assert store["interests"] == {(SELLER, MODEL_ID)}
    client.headers["Authorization"] = f"Bearer {make_token(sub=OWNER)}"
    assert client.get(url).json() == {"interested": False}


@pytest.mark.parametrize("change", [
    {"sponsorship_enabled": False}, {"status": "pending"}, {"confirmed_at": None},
    {"cover_image_url": "private/face.webp"}, {"fullbody_image_url": None},
    {"license_status": "revoked"}, {"vc_id": None},
    {"license_valid_until": NOW - timedelta(days=1)},
])
def test_interest_rejects_ineligible_models(sponsorship_api, change):
    client, store, _ = sponsorship_api
    store["model"]["sponsorship_enabled"] = True
    store["model"].update(change)
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship-interest"
    for response in [client.post(url), client.get(url)]:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert store["interests"] == set()


def test_interest_requires_login_and_open_account(sponsorship_api):
    client, store, _ = sponsorship_api
    store["model"]["sponsorship_enabled"] = True
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship-interest"
    store["closed"] = True
    assert client.post(url).status_code == 404
    assert client.get(url).status_code == 404
    client.headers.pop("Authorization")
    assert client.post(url).status_code == 401
    assert client.get(url).status_code == 401


@pytest.mark.parametrize("enabled", [False, True])
def test_public_and_catalog_hide_disabled_details_but_owner_keeps_them(sponsorship_api, enabled):
    client, store, make_token = sponsorship_api
    store["model"]["sponsorship_enabled"] = enabled
    seller = {"Authorization": f"Bearer {make_token(sub=SELLER)}"}
    public = client.get("/v1/facemarket/public/models", headers=seller)
    assert public.status_code == 200, public.text
    assert public.headers["cache-control"] == "no-store"
    catalog = client.get("/v1/facemarket/models", headers=seller)
    assert catalog.status_code == 200, catalog.text
    owner = client.get("/v1/facemarket/models/me")
    assert owner.status_code == 200, owner.text
    for item in [public.json()["items"][0], catalog.json()[0]]:
        assert item["sponsorshipEnabled"] is enabled
        assert item["instagramHandle"] == ("saved.model" if enabled else None)
        assert item["instagramFollowers"] == (1200 if enabled else None)
        assert item["sizeTop"] == ("M" if enabled else None)
        assert item["sizeBottomWaist"] == (28 if enabled else None)
        assert bool(item["instagramFollowersReportedAt"]) is enabled
        assert item["sponsorshipProfileConsentAt"] is None
    assert owner.json()[0]["instagramHandle"] == "saved.model"
    assert owner.json()[0]["sponsorshipProfileConsentAt"] == "2026-09-22T00:00:00Z"


def test_public_list_shows_only_the_badge_without_login(sponsorship_api):
    """비로그인 공개 목록은 '협찬 받는 중'만 알리고 계정·팔로워·사이즈는 감춰요(E-2b 동의 범위)."""
    client, store, _ = sponsorship_api
    store["model"]["sponsorship_enabled"] = True
    client.headers.pop("Authorization")
    public = client.get("/v1/facemarket/public/models")
    assert public.status_code == 200, public.text
    item = public.json()["items"][0]
    assert item["sponsorshipEnabled"] is True
    assert item["instagramHandle"] is None and item["instagramFollowers"] is None
    assert item["sizeTop"] is None and item["sizeBottomWaist"] is None
    assert item["instagramFollowersReportedAt"] is None
    # 잘못된 토큰도 비로그인과 같아요.
    bad = client.get("/v1/facemarket/public/models", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 200 and bad.json()["items"][0]["instagramHandle"] is None


def test_logged_in_non_seller_sees_only_the_badge(sponsorship_api):
    """동의문은 '로그인 셀러'에게 보인다고 해요. 셀러 약관 동의 기록이 없는 로그인 계정(다른 모델,
    이메일 가입만 한 사람)에게는 공개 목록도 셀러 카탈로그도 배지만 실어요."""
    client, store, _ = sponsorship_api
    store["model"]["sponsorship_enabled"] = True
    # 기본 헤더는 OWNER(모델 계정) 토큰 — seller_consents 에 없어요.
    for url in ("/v1/facemarket/public/models", "/v1/facemarket/models"):
        res = client.get(url)
        assert res.status_code == 200, res.text
        item = res.json()["items"][0] if url.endswith("public/models") else res.json()[0]
        assert item["sponsorshipEnabled"] is True
        assert item["instagramHandle"] is None and item["instagramFollowers"] is None
        assert item["sizeTop"] is None and item["sizeBottomWaist"] is None
    assert any("from seller_consents" in q and p == (OWNER,) for q, p in store["queries"])


def test_sponsorship_patch_preflight_allows_screen_header(sponsorship_api):
    """브라우저는 PATCH 전에 preflight 를 보내요. 화면이 붙이는 X-Facemarket-Screen 이 허용 목록에 없으면
    실서버(api.wearless.kr 교차 출처)에서 협찬 저장이 전부 막혀요 — 켜 둔 모델은 증서 발급도 못 해요."""
    client, _, _ = sponsorship_api
    res = client.options(
        f"/v1/facemarket/models/{MODEL_ID}/sponsorship",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type,x-facemarket-screen",
        },
    )
    assert res.status_code == 200, res.text
    assert "x-facemarket-screen" in res.headers["access-control-allow-headers"].lower()


def test_consent_notice_text_matches_the_screen():
    """동의 이력에 남는 해시는 '모델이 실제로 본 문구'의 증빙이에요. 서버 문구와 화면 문구가 한 글자라도
    다르면 증빙이 깨져요. 화면 소스에서 태그를 걷어낸 뒤 서버 문장이 그대로 있는지 봐요."""
    import pathlib
    import re
    from app.facemarket_sponsorship import SPONSORSHIP_NOTICES

    root = pathlib.Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text()
        for path in ("src/features/model/SponsorshipSettings.jsx", "src/features/model/sponsorshipOptions.js")
    )
    screen = re.sub(r"</?strong>", "", source)
    for consent_type, notice in SPONSORSHIP_NOTICES.items():
        for key, value in notice.items():
            for sentence in (value if isinstance(value, list) else [value]):
                assert sentence in screen, f"{consent_type}.{key}: {sentence}"


def test_sponsorship_records_separate_versioned_grants_and_withdrawals(sponsorship_api):
    client, store, _ = sponsorship_api
    url = f"/v1/facemarket/models/{MODEL_ID}/sponsorship"
    assert client.patch(url, json=ENABLED).status_code == 200
    grants = [p for q, p in store['queries'] if q.startswith('insert into fm_sponsorship_consent_events')]
    assert len(grants) == 2
    assert {p[3] for p in grants} == {'sponsorship_participation', 'sponsorship_profile_collection'}
    for p in grants:
        assert p[0] == OWNER and p[1] == MODEL_ID and p[2] == OWNER
        assert p[4] == 'granted'
        assert p[5] == '2026-09-sponsorship-v1-draft'
        assert len(p[6]) == 64
        assert 'model.name' not in p[9]
    assert client.patch(url, json={'sponsorshipEnabled': False}).status_code == 200
    events = [p for q, p in store['queries'] if q.startswith('insert into fm_sponsorship_consent_events')]
    assert len(events) == 4
    assert all(p[4] == 'withdrawn' for p in events[2:])
