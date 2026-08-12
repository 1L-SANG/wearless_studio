import contextlib

import app.routes as routes
import pytest
from app.services import matching


def _it(id, ct, gender, cb, so, active=True):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "color_brightness": cb, "sort_order": so, "is_active": active}


def test_complementary_type():
    assert matching.complementary_type("top") == "bottom"
    # 원피스는 한 벌이라 맞춰 입힐 반대편이 없다(2026-08-01). 예전엔 "bottom" 을 돌려줘
    # 하의를 추천했다 — 셀러 화면에 불필요한 매칭 카드가 뜨고 컷에도 하의가 함께 들어갔다.
    assert matching.complementary_type("dress") is None
    assert matching.complementary_type("outer") == "bottom"
    assert matching.complementary_type("bottom") == "top"


def test_fit_category_uses_curated_metadata_only():
    assert matching.fit_category(
        {"clothing_type": "top", "category": "셔츠", "length": "regular"}) == "top"
    assert matching.fit_category(
        {"clothing_type": "bottom", "category": "데님팬츠", "length": "full"}) == "pants"
    assert matching.fit_category(
        {"clothing_type": "bottom", "category": "스커트", "length": "midi"}) == "skirt"
    assert matching.fit_category(
        {"clothing_type": "bottom", "category": "쇼츠", "length": "short"}) is None
    assert matching.fit_category(
        {"clothing_type": "bottom", "category": "버뮤다쇼츠", "length": "short"}) is None
    assert matching.fit_category({
        "name": "이름에는 스커트가 있음", "clothing_type": "bottom",
        "category": "미등록", "length": "full",
    }) is None


@pytest.mark.parametrize("gender", ["women", "men"])
def test_custom_candidate_is_first_gender_independent_and_can_be_incompatible(
    client, make_token, monkeypatch, gender
):
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        return [
            {
                "id": "curated-top", "name": "큐레이션 상의", "clothing_type": "top",
                "gender": gender, "category": "셔츠", "fit": "regular", "length": "regular",
                "color_brightness": 90, "sort_order": 1, "is_active": True,
                "is_custom": False, "image_key": "seed/top.png", "thumb_key": "seed/top-thumb.png",
            },
            {
                "id": "custom-private", "name": "내 하의", "clothing_type": "bottom",
                "gender": "unisex", "category": "스커트", "fit": "regular", "length": "midi",
                "color_brightness": 0, "sort_order": 999, "is_active": True,
                "is_custom": True, "image_key": "derived/grid.jpg", "thumb_key": "upload/first.png",
            },
        ]

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    _no_db(monkeypatch)

    response = client.get(
        f"/v1/projects/p1/analysis/match-candidates?clothingType=bottom&gender={gender}",
        headers=_auth(make_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body] == ["custom-private", "curated-top"]
    assert body[0]["isCustom"] is True
    assert body[0]["isCompatible"] is False
    assert body[0]["thumb"] == "https://img.example.com/upload/first.png"
    assert body[1]["isCustom"] is False
    assert body[1]["isCompatible"] is True


def test_recommend_filters_type_and_sorts_by_brightness_then_sortorder():
    items = [
        _it("b1", "bottom", "women", 20, 2),
        _it("b2", "bottom", "women", 90, 5),
        _it("b3", "bottom", "women", 90, 1),  # 동률 → sort_order 우선
        _it("t1", "top", "women", 99, 1),     # 보색 아님 → 제외
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    # 상위 두 밝은 후보가 같은 계열(밝기 band)이므로 2위는 다른 계열 b1로 다양화한다.
    assert [i["id"] for i in out] == ["b3", "b1", "b2"]


def test_diversify_top_two_uses_color_group_and_preserves_all_same_order():
    ranked = [
        {"id": "a", "color_group": "black"},
        {"id": "b", "color_group": "black"},
        {"id": "c", "color_group": "beige"},
        {"id": "d", "color_group": "blue"},
    ]
    assert [item["id"] for item in matching.diversify_top_two(ranked)] == ["a", "c", "b", "d"]
    all_black = [dict(item, color_group="black") for item in ranked]
    assert [item["id"] for item in matching.diversify_top_two(all_black)] == ["a", "b", "c", "d"]


def test_recommend_gender_filter_allows_unisex():
    items = [
        _it("m", "bottom", "men", 50, 1),
        _it("w", "bottom", "women", 50, 2),
        _it("u", "bottom", "unisex", 50, 3),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert {i["id"] for i in out} == {"w", "u"}


def test_recommend_excludes_inactive_and_applies_limit():
    items = [
        _it("a", "bottom", "women", 80, 1),
        _it("b", "bottom", "women", 70, 2),
        _it("c", "bottom", "women", 60, 3, active=False),
    ]
    out = matching.recommend(items, clothing_type="top", genders=[], limit=1)
    assert [i["id"] for i in out] == ["a"]


def test_recommend_missing_brightness_defaults_50():
    items = [
        {"id": "x", "clothing_type": "bottom", "gender": "women", "sort_order": 1, "is_active": True},
        _it("y", "bottom", "women", 90, 2),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["y", "x"]  # 90 > 50(default)


# ---------- 엔드포인트 (DB·R2 격리: monkeypatch) ----------

class _FakeR2:
    def public_url(self, key):
        return f"https://img.example.com/{key}"


def _no_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield None
    monkeypatch.setattr(routes, "get_conn", fake_conn)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_match_candidates_shape_and_public_url(client, make_token, monkeypatch):
    # frozen dataclass → object.__setattr__ 로 테스트 주입
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        assert (user_id, project_id) == ("user-1", "p1")
        return [{"id": "match_test_bottom_neutral", "name": "블랙 슬랙스",
                 "clothing_type": "bottom", "gender": "women", "category": "트라우저",
                 "color_name": "블랙", "color_group": "black", "style_tags": ["basic"],
                 "fit": "regular", "length": "full", "color_brightness": 0, "sort_order": 201,
                 "is_active": True, "is_custom": False,
                 "image_asset_id": "asset-image-neutral",
                 "thumbnail_asset_id": "asset-thumb-neutral",
                 "image_key": "seed/matching/match_test_bottom_neutral.png",
                 "thumb_key": "seed/matching/thumb/match_test_bottom_neutral.png"}]

    async def fake_get_product(conn, project_id):
        return {"colors": [{"isBase": True, "swatchId": "black"}]}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    _no_db(monkeypatch)

    res = client.get(
        "/v1/projects/p1/analysis/match-candidates?clothingType=top&gender=women",
        headers=_auth(make_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body[0]["thumb"] == "https://img.example.com/seed/matching/thumb/match_test_bottom_neutral.png"
    assert body[0]["selected"] is False
    assert body[0]["id"] == "match_test_bottom_neutral"
    assert body[0]["clothingType"] == "bottom"
    assert body[0]["category"] == "트라우저"
    assert body[0]["fit"] == "regular"
    assert body[0]["length"] == "full"
    assert body[0]["fitCategory"] == "pants"


def test_match_candidates_route_passes_style_tags_to_tag_ranker(
    client, make_token, monkeypatch,
):
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    object.__setattr__(client.app.state.settings, "retrieval_matching", "tags")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        return [{
            "id": "b1", "name": "하의", "clothing_type": "bottom", "gender": "women",
            "style_tags": ["daily"], "color_brightness": 50, "sort_order": 1,
            "is_active": True, "is_custom": False, "thumb_key": "thumb.png",
            "image_key": "image.png",
        }]

    async def fake_get_product(conn, project_id):
        return {"colors": [
            {"isBase": False, "swatchId": "red"},
            {"isBase": True, "swatchId": "navy"},
        ]}

    def fake_recommend(
        items, clothing_type, genders, product_tags, affinity_map, limit, **kwargs,
    ):
        seen["product_tags"] = product_tags
        seen.update(kwargs)
        return items

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    monkeypatch.setattr(routes.retrieval, "recommend_v1", fake_recommend)
    _no_db(monkeypatch)

    response = client.get(
        "/v1/projects/p1/analysis/match-candidates"
        "?clothingType=top&gender=women&styleTags=basic&styleTags=daily",
        headers=_auth(make_token),
    )

    assert response.status_code == 200, response.text
    assert seen["product_tags"] == ["basic", "daily"]
    assert seen["product_color"] == "navy"
    assert seen["harmony"] is routes.color_harmony.HARMONY
    assert seen["color_weight"] == 0.3


def test_match_candidates_falls_back_to_first_analysis_swatch(
    client, make_token, monkeypatch,
):
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    object.__setattr__(client.app.state.settings, "retrieval_matching", "tags")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        return [{
            "id": "b1", "name": "하의", "clothing_type": "bottom", "gender": "women",
            "style_tags": ["daily"], "color_group": "beige", "color_brightness": 50,
            "sort_order": 1, "is_active": True, "is_custom": False,
            "thumb_key": "thumb.png", "image_key": "image.png",
        }]

    async def fake_get_product(conn, project_id):
        return {"colors": [{"isBase": True, "swatchId": None}]}

    async def fake_get_analysis(conn, project_id):
        return {"swatchSuggestions": [
            {"colorGroupId": "base", "swatchId": "ivory"},
            {"colorGroupId": "other", "swatchId": "red"},
        ]}

    def fake_recommend(
        items, clothing_type, genders, product_tags, affinity_map, limit, **kwargs,
    ):
        seen.update(kwargs)
        return items

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.retrieval, "recommend_v1", fake_recommend)
    _no_db(monkeypatch)

    response = client.get(
        "/v1/projects/p1/analysis/match-candidates"
        "?clothingType=top&gender=women&styleTags=basic",
        headers=_auth(make_token),
    )

    assert response.status_code == 200, response.text
    assert seen["product_color"] == "ivory"


def test_match_candidates_product_lookup_failure_uses_style_only(
    client, make_token, monkeypatch,
):
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    object.__setattr__(client.app.state.settings, "retrieval_matching", "tags")
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        return [{
            "id": "b1", "name": "하의", "clothing_type": "bottom", "gender": "women",
            "style_tags": ["daily"], "color_group": "beige", "is_active": True,
            "is_custom": False, "thumb_key": "thumb.png", "image_key": "image.png",
        }]

    async def fake_get_product(conn, project_id):
        raise RuntimeError("optional color read failed")

    async def unexpected_get_analysis(conn, project_id):
        raise AssertionError("failed product lookup must not issue another query")

    def fake_recommend(
        items, clothing_type, genders, product_tags, affinity_map, limit, **kwargs,
    ):
        seen.update(kwargs)
        return items

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "get_analysis", unexpected_get_analysis)
    monkeypatch.setattr(routes.retrieval, "recommend_v1", fake_recommend)
    _no_db(monkeypatch)

    response = client.get(
        "/v1/projects/p1/analysis/match-candidates"
        "?clothingType=top&gender=women&styleTags=basic",
        headers=_auth(make_token),
    )

    assert response.status_code == 200, response.text
    assert seen["product_color"] is None


def test_dress_match_candidates_ignore_stale_men_query(
    client, make_token, monkeypatch
):
    object.__setattr__(
        client.app.state.settings,
        "r2_public_base",
        "https://img.example.com",
    )
    monkeypatch.setattr(routes, "_r2", lambda request: _FakeR2())

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list(conn, user_id, project_id):
        assert (user_id, project_id) == ("user-1", "p1")
        return [{
            "id": "women-bottom",
            "name": "여성용 하의",
            "clothing_type": "bottom",
            "gender": "women",
            "category": "트라우저",
            "color_brightness": 50,
            "sort_order": 1,
            "is_active": True,
            "is_custom": False,
            "image_asset_id": "asset-women-bottom",
            "thumbnail_asset_id": "thumb-women-bottom",
            "image_key": "seed/matching/women-bottom.png",
            "thumb_key": "seed/matching/thumb/women-bottom.png",
        }]

    async def fake_get_product(conn, project_id):
        return {"colors": []}

    async def fake_get_analysis(conn, project_id):
        return {}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    _no_db(monkeypatch)

    res = client.get(
        "/v1/projects/p1/analysis/match-candidates"
        "?clothingType=dress&gender=men",
        headers=_auth(make_token),
    )

    # 원피스는 매칭 후보가 없다 — 성별 쿼리가 무엇이든(묵은 men 포함) 빈 목록이어야 한다.
    # (옛 계약: 하의를 추천하되 성별만 여성으로 눕혔다. 2026-08-01 부터 추천 자체를 안 한다.)
    assert res.status_code == 200, res.text
    assert res.json() == []


def test_match_candidates_failfast_without_public_base(client, make_token, monkeypatch):
    object.__setattr__(client.app.state.settings, "r2_public_base", None)

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    _no_db(monkeypatch)

    res = client.get(
        "/v1/projects/p1/analysis/match-candidates?clothingType=top",
        headers=_auth(make_token))
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "r2_public_base_missing"


def test_recommend_limit_zero_returns_empty():
    # limit=0 은 '빈 결과' (0은 falsy — 전체 반환 아님, 리뷰 반영)
    items = [_it("b1", "bottom", "women", 80, 1), _it("b2", "bottom", "women", 70, 2)]
    assert matching.recommend(items, "top", ["women"], limit=0) == []


def test_dress_gets_no_matching_bottom():
    """원피스는 상·하의가 붙은 한 벌이라 맞춰 입힐 하의가 없다.

    예전에는 dress 를 상의쪽(_TOP_SIDE)으로 묶어 하의를 추천했다 — 셀러 화면에 필요 없는 매칭
    카드가 뜨고, 마네킹 컷에도 원피스 아래 바지·치마가 들어가 상품을 가렸다(2026-08-01 보고).
    """
    from app.services import matching

    assert matching.complementary_type("dress") is None
    items = [
        {"id": "b1", "is_active": True, "clothing_type": "bottom", "gender": "women",
         "color_brightness": 50, "sort_order": 1},
        {"id": "t1", "is_active": True, "clothing_type": "top", "gender": "women",
         "color_brightness": 50, "sort_order": 1},
    ]
    assert matching.recommend(items, "dress", ["women"]) == []
    # 상의·아우터는 그대로 하의를 받는다(회귀 방지)
    assert [i["id"] for i in matching.recommend(items, "top", ["women"])] == ["b1"]
    assert [i["id"] for i in matching.recommend(items, "outer", ["women"])] == ["b1"]
    assert [i["id"] for i in matching.recommend(items, "bottom", ["women"])] == ["t1"]


def test_matching_top_exposes_length_vocabulary_for_bottom_products():
    """하의 상품의 매칭 상의는 fitCategory='top' — 조정 스텝이 뜨기 위한 전제(WS2).

    2026-08-01 이전에는 상의 아이템이 전부 None 이라 하의 상품에서 매칭 조정 스텝이
    구조적으로 뜰 수 없었다. 상의 기장이 상품(바지)의 허리 노출을 결정하므로 축은 length 다.
    """
    from app.agents.fit_axes import _MATCHING_FIT_AXIS, normalize_fit_profile

    assert matching.fit_category({"clothing_type": "top", "category": "셔츠"}) == "top"
    assert _MATCHING_FIT_AXIS["top"] == "length"

    # 정규화 경로: top matchingFit 이 카탈로그 어휘로 살아남는다
    profile = {
        "category": "pants", "gender": "men", "axes": {}, "source": "seller", "version": 2,
        "matchingFit": {"clothingId": "m1", "fitCategory": "top", "axes": {"length": "crop"}},
    }
    out = normalize_fit_profile(profile)
    assert out and out.get("matchingFit") == {
        "clothingId": "m1", "fitCategory": "top", "axes": {"length": "crop"}}
    # 미지값·타 축은 여전히 버려진다 (allowlist 불변)
    bad = dict(profile, matchingFit={"clothingId": "m1", "fitCategory": "top",
                                     "axes": {"length": "banana"}})
    assert normalize_fit_profile(bad).get("matchingFit") is None
