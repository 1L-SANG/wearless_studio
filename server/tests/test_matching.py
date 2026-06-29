from app.services import matching


def _it(id, ct, gender, cb, so, active=True):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "color_brightness": cb, "sort_order": so, "is_active": active}


def test_complementary_type():
    assert matching.complementary_type("top") == "bottom"
    assert matching.complementary_type("dress") == "bottom"
    assert matching.complementary_type("outer") == "bottom"
    assert matching.complementary_type("bottom") == "top"


def test_recommend_filters_type_and_sorts_by_brightness_then_sortorder():
    items = [
        _it("b1", "bottom", "women", 20, 2),
        _it("b2", "bottom", "women", 90, 5),
        _it("b3", "bottom", "women", 90, 1),  # 동률 → sort_order 우선
        _it("t1", "top", "women", 99, 1),     # 보색 아님 → 제외
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["b3", "b2", "b1"]


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
