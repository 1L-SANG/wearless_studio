"""모델 리스트는 등록된 셀러와 2차 등록까지 마친 모델만 본다(2026-09-23 오너 결정).

판정 = 관리자, 셀러 약관 동의 기록(seller_consents), 본인 모델에 발급된 라이선스
(active 또는 재등록 중 reverification_required). 지원서만 낸 사람, 2차 등록 중인 사람,
로그인만 한 계정은 화면 판정(catalog-access)도 목록 API 도 막힌다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

from app import facemarket, facemarket_admin_models
from app.facemarket_catalog_access import CATALOG_ACCESS_SQL
from app.main import create_app
from conftest import assert_query_binds, make_settings

ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SELLER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MODEL = "cccccccc-cccc-cccc-cccc-cccccccccccc"
APPLICANT = "dddddddd-dddd-dddd-dddd-dddddddddddd"  # 지원서만 냈거나 2차 등록 진행 중
STRANGER = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
ADMIN_SELLER = "ffffffff-ffff-ffff-ffff-ffffffffffff"

# 흉내 DB 의 계정별 기록. 지원자는 발급된 라이선스가 없어 모델로 치지 않는다.
FLAGS = {
    ADMIN: {"catalog_is_admin": True, "catalog_is_seller": False, "catalog_is_model": False},
    SELLER: {"catalog_is_admin": False, "catalog_is_seller": True, "catalog_is_model": False},
    MODEL: {"catalog_is_admin": False, "catalog_is_seller": False, "catalog_is_model": True},
    APPLICANT: {"catalog_is_admin": False, "catalog_is_seller": False, "catalog_is_model": False},
    ADMIN_SELLER: {"catalog_is_admin": True, "catalog_is_seller": True, "catalog_is_model": False},
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
        self.store["queries"].append((query, params))
        if "ready_for_identity_delete" in query:
            self.one = {"closed": False}   # 셀러 카탈로그의 계정 열림 확인
        elif "catalog_is_admin" in query:
            self.one = FLAGS.get(params[0], {
                "catalog_is_admin": False, "catalog_is_seller": False, "catalog_is_model": False,
            })
        elif "from fm_models" in query:
            self.many = []
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


@pytest.fixture
def api(monkeypatch, keypair, make_token):
    store = {"queries": []}

    @contextlib.asynccontextmanager
    async def get_conn(_request):
        yield Conn(store)

    monkeypatch.setattr(facemarket_admin_models, "get_conn", get_conn)
    monkeypatch.setattr(facemarket, "get_conn", get_conn)
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda _token: keypair[1]
    client = TestClient(app)
    as_user = lambda user: {"Authorization": f"Bearer {make_token(sub=user)}"}  # noqa: E731
    return client, store, as_user


@pytest.mark.parametrize("user, role", [
    (ADMIN, "admin"), (SELLER, "seller"), (MODEL, "model"), (ADMIN_SELLER, "admin"),
])
def test_registered_sellers_models_and_admins_may_see_the_list(api, user, role):
    client, _store, as_user = api
    access = client.get("/v1/facemarket/catalog-access", headers=as_user(user))
    assert access.status_code == 200, access.text
    assert access.json() == {"allowed": True, "role": role}
    assert access.headers["cache-control"] == "no-store"
    listing = client.get("/v1/facemarket/public/models", headers=as_user(user))
    assert listing.status_code == 200, listing.text
    assert listing.json() == {"items": []}


@pytest.mark.parametrize("user", [STRANGER, APPLICANT])
def test_accounts_without_seller_consent_or_finished_registration_are_turned_away(api, user):
    """로그인만 한 계정, 지원서만 냈거나 2차 등록 중인 사람은 목록을 못 봐요."""
    client, _store, as_user = api
    access = client.get("/v1/facemarket/catalog-access", headers=as_user(user))
    assert access.status_code == 200, access.text
    assert access.json() == {"allowed": False, "role": None}
    listing = client.get("/v1/facemarket/public/models", headers=as_user(user))
    assert listing.status_code == 403, listing.text
    assert listing.json()["error"]["code"] == "members_only"


def test_anonymous_visitors_get_nothing(api):
    client, store, _as_user = api
    for url in ("/v1/facemarket/catalog-access", "/v1/facemarket/public/models", "/v1/facemarket/models"):
        assert client.get(url).status_code == 401
        bad = client.get(url, headers={"Authorization": "Bearer not-a-token"})
        assert bad.status_code == 401
    # 판정도 목록도 DB 까지 가지 않아요.
    assert store["queries"] == []


def test_model_list_is_loaded_only_after_the_member_check(api):
    client, store, as_user = api
    client.get("/v1/facemarket/public/models", headers=as_user(STRANGER))
    assert [q for q, _ in store["queries"] if "from fm_models" in q and "catalog_is_admin" not in q] == []
    client.get("/v1/facemarket/public/models", headers=as_user(SELLER))
    kinds = ["access" if "catalog_is_admin" in q else "list" for q, _ in store["queries"]]
    assert kinds[-2:] == ["access", "list"]


def test_access_query_checks_each_record_for_the_same_account():
    query = " ".join(CATALOG_ACCESS_SQL.split()).lower()
    assert "from profiles where user_id = %s and role = 'admin'" in query
    assert "from seller_consents where user_id = %s" in query
    # 모델 = 2차 등록 완료 = 본인 모델에 발급된 라이선스. 발급 대기(pending)·취소(revoked)는 아니에요.
    assert ("from fm_licenses l join fm_models m on m.id = l.model_id "
            "where m.user_id = %s and l.status in ('active', 'reverification_required')") in query
    assert "fm_model_applications" not in query, "지원서만으로는 모델로 치지 않아요"
    assert query.count("%s") == 3


def test_registered_statuses_match_the_query():
    from app.facemarket_catalog_access import REGISTERED_MODEL_LICENSE_STATUSES
    statuses = ", ".join(f"'{status}'" for status in REGISTERED_MODEL_LICENSE_STATUSES)
    assert f"l.status in ({statuses})" in " ".join(CATALOG_ACCESS_SQL.split())


@pytest.mark.parametrize("user", [STRANGER, APPLICANT])
def test_seller_catalog_is_not_a_side_door(api, user):
    """셀러 카탈로그(GET /v1/facemarket/models)도 같은 모델과 대표 사진을 내준다. 여기가 로그인만
    요구하면 자격 없는 계정이 이 주소로 우회해 목록을 받는다(2026-09-23 리뷰에서 재현)."""
    client, store, as_user = api
    res = client.get("/v1/facemarket/models", headers=as_user(user))
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "members_only"
    # 판정에서 멈추고 목록은 읽지 않아요.
    assert not [q for q, _ in store["queries"] if "from fm_models m" in q and "catalog_is_admin" not in q]


@pytest.mark.parametrize("user", [ADMIN, SELLER, MODEL])
def test_seller_catalog_serves_registered_members(api, user):
    client, _store, as_user = api
    res = client.get("/v1/facemarket/models", headers=as_user(user))
    assert res.status_code == 200, res.text
    assert res.json() == []
