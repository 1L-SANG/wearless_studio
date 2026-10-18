"""사용자가 지정한 상의별 배색을 실제 추천 경로에서 검증한다. 외부 호출 없음."""
import pytest

from app.agents import color_harmony
from app import routes
from app.routes import _matching_product_color
from app.services.retrieval import recommend_v1
from conftest import FakeR2, patch_route_db


def item(identifier, color, name=None, category="팬츠", clothing_type="bottom", **extra):
    return {
        "id": identifier, "clothing_type": clothing_type, "gender": "women",
        "is_active": True, "style_tags": ["daily"], "color_group": color,
        "color_name": name, "category": category, **extra,
    }


def recommend(items, product_color, clothing_type="top", **kwargs):
    return recommend_v1(
        items, clothing_type, ["women"], ["basic"], {("basic", "daily"): 0.9},
        product_color=product_color, harmony=color_harmony.harmony_map(), **kwargs,
    )


def test_yellow_top_promotes_brown_over_unlisted_gray():
    candidates = [item("a-gray", "gray"), item("z-brown", "brown")]
    assert [x["id"] for x in recommend(candidates, "yellow")] == ["z-brown", "a-gray"]


def test_upper_lower_direction_is_kept_when_selling_bottoms():
    # 옐로우 상의 + 브라운 하의가 추천 조합이다. 브라운 상의 + 옐로우 하의는 아니다.
    assert color_harmony.outfit_color_score("yellow", "brown", "top") == 0.95
    assert color_harmony.outfit_color_score("brown", "yellow", "bottom") == 0.95
    assert color_harmony.outfit_color_score("brown", "yellow", "top") == 0.68
    candidates = [item("a-gray", "gray", clothing_type="top"),
                  item("z-yellow", "yellow", clothing_type="top")]
    assert recommend(candidates, "brown", "bottom")[0]["id"] == "z-yellow"


def test_yellow_top_distinguishes_dark_denim_from_plain_navy():
    candidates = [item("a-navy", "navy", "네이비", "트라우저"),
                  item("z-denim", "navy", "네이비", "데님팬츠")]
    assert recommend(candidates, "yellow")[0]["id"] == "z-denim"


@pytest.mark.parametrize("group,name,category,expected", [
    ("red", "버건디", None, "burgundy"),
    ("gray", "차콜그레이", None, "charcoal"),
    ("green", "라이트 그린", None, "light_green"),
    ("ivory", "크림", None, "cream"),
    ("blue", "연청", None, "light_denim"),
    ("blue", "진청", None, "dark_denim"),
    ("black", "흑청", None, "black_denim"),
    ("blue", "아이스블루", "데님팬츠", "light_denim"),
    ("blue", "워시드블루", "데님팬츠", "mid_denim"),
    ("black", "워시드블랙", "데님팬츠", "black_denim"),
    ("blue", "라이트블루", "셔츠", "blue"),
    ("black", "버건디", None, "black"),  # 셀러가 바꾼 스와치를 오래된 이름이 덮지 않는다.
    ("unknown", "버건디", None, "unknown"),
    (None, None, None, None),
])
def test_known_shades_are_resolved_without_guessing(group, name, category, expected):
    assert color_harmony.matching_color(group, name, category) == expected


@pytest.mark.parametrize("group,name", [
    ("ivory", "아이보리"), ("silver", "실버"), ("gold", "골드"),
    ("green", "그린"), ("red", "레드"),
])
def test_october_catalog_colors_normalize_without_changing_groups(group, name):
    assert color_harmony.matching_color(f" {group.upper()} ", name) == group
    assert color_harmony.matching_color(group, "블랙", "가방") == group


@pytest.mark.parametrize("product,item_color,expected", [
    ("white", "ivory", 0.75), ("white", "silver", 0.5),
    ("white", "gold", 0.5), ("white", "green", 0.82),
    ("white", "red", 0.88), ("silver", "silver", 0.5),
    ("gold", "gold", 0.5), ("red", "green", 0.22),
])
@pytest.mark.parametrize("clothing_type", ["top", "bottom", "outer", "dress"])
def test_october_catalog_scores_keep_existing_harmony_and_neutral_metals(product, item_color, expected, clothing_type):
    # Red top + white bottom retains the existing directional recommendation bonus.
    if (product, item_color, clothing_type) == ("white", "red", "bottom"):
        expected = 0.95
    assert color_harmony.outfit_color_score(product, item_color, clothing_type) == expected


@pytest.mark.parametrize("product_color", ["ivory", "silver", "gold", "green", "red"])
def test_october_catalog_colors_are_rankable_in_legacy_recommendations(product_color):
    candidates = [item(color, color) for color in ["ivory", "silver", "gold", "green", "red"]]
    ranked = recommend(candidates, product_color)
    assert sorted(candidate["id"] for candidate in ranked) == ["gold", "green", "ivory", "red", "silver"]
    assert ranked == recommend(candidates, product_color)


def test_base_color_and_analysis_fallback_keep_fine_color_names():
    product = {"colors": [{"id": "other", "swatchId": "blue", "name": "연청"},
                          {"id": "base", "isBase": True, "swatchId": "red", "name": "버건디"}]}
    assert _matching_product_color(product) == "burgundy"
    product["colors"][1] = {"id": "base", "isBase": True}
    analysis = {"swatchSuggestions": [
        {"colorGroupId": "other", "swatchId": "blue", "colorName": "연청"},
        {"colorGroupId": "base", "swatchId": "gray", "colorName": "차콜"},
    ]}
    assert _matching_product_color(product, analysis) == "charcoal"


def test_unlisted_unknown_and_weight_zero_keep_existing_fallback():
    assert color_harmony.outfit_color_score("ultraviolet", "brown", "top") == 0.5
    assert color_harmony.outfit_color_score("yellow", None, "top") == 0.5
    assert color_harmony.outfit_color_score("navy", "beige", "top") == 0.92
    candidates = [item("a-gray", "gray"), item("z-brown", "brown")]
    assert recommend(candidates, "yellow", color_weight=0)[0]["id"] == "a-gray"
    assert recommend(candidates, None)[0]["id"] == "a-gray"


def test_reference_cannot_override_type_gender_activity_or_dress_exclusion():
    candidates = [item("wrong-type", "brown", clothing_type="top"),
                  item("wrong-gender", "brown", gender="men"),
                  item("inactive", "brown", is_active=False), item("ok", "gray")]
    assert [x["id"] for x in recommend(candidates, "yellow")] == ["ok"]
    assert recommend(candidates, "yellow", "dress") == []
    assert recommend(candidates, "yellow", limit=0) == []


def test_strong_style_match_is_not_overridden_by_color_bonus():
    candidates = [item("a-gray", "gray"), item("z-brown", "brown", style_tags=["unknown"])]
    assert recommend(candidates, "yellow")[0]["id"] == "a-gray"


@pytest.mark.parametrize("clothing_type,swatch,name,candidates,first", [
    ("top", "yellow", "옐로우", [item("a-gray", "gray"), item("z-brown", "brown")], "z-brown"),
    ("bottom", "brown", "브라운", [item("a-gray", "gray", clothing_type="top"),
                                  item("z-yellow", "yellow", clothing_type="top")], "z-yellow"),
    ("top", "red", "버건디", [item("a-white", "white"), item("z-cream", "ivory", "크림")], "z-cream"),
])
def test_authenticated_candidate_endpoint_uses_saved_color_and_reference(
    client, make_token, monkeypatch, clothing_type, swatch, name, candidates, first,
):
    object.__setattr__(client.app.state.settings, "r2_public_base", "https://img.example.com")
    object.__setattr__(client.app.state.settings, "retrieval_matching", "tags")
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(routes, "_r2", lambda request: FakeR2())

    async def project(*args):
        return {"id": "p1"}

    async def product(*args):
        return {"colors": [{"isBase": True, "swatchId": swatch, "name": name}]}

    async def catalog(*args):
        return [{**candidate, "name": candidate["id"], "thumb_key": "thumb.png", "image_key": "image.png"}
                for candidate in candidates]

    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "get_product", product)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", catalog)
    response = client.get(
        f"/v1/projects/p1/analysis/match-candidates?clothingType={clothing_type}&gender=women&styleTags=basic",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == first
