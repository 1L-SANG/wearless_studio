from app.agents import color_harmony
from app.services import retrieval


def _it(id, ct, gender, style_tags=None, active=True, color_group=None):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "style_tags": style_tags or [], "is_active": active,
            "color_group": color_group}


def _chunk(id, category=None, style_tags=None):
    return {"id": id, "keys": {"category": category, "styleTags": style_tags or []}}


# ---------- rank_by_style_affinity ----------

def test_rank_by_style_affinity_is_deterministic():
    items = [
        _it("a", "bottom", "women", ["wide"]),
        _it("b", "bottom", "women", ["slim"]),
        _it("c", "bottom", "women", ["wide"]),
    ]
    affinity_map = {("knit", "wide"): 5, ("knit", "slim"): 1}
    out1 = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    out2 = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    assert [i["id"] for i in out1] == [i["id"] for i in out2]


def test_rank_by_style_affinity_orders_by_score_desc():
    items = [
        _it("low", "bottom", "women", ["slim"]),
        _it("high", "bottom", "women", ["wide"]),
        _it("zero", "bottom", "women", ["none"]),
    ]
    affinity_map = {("knit", "wide"): 5, ("knit", "slim"): 1}
    out = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    assert [i["id"] for i in out] == ["high", "low", "zero"]


def test_rank_by_style_affinity_tie_break_by_id_ascending():
    items = [
        _it("c3", "bottom", "women", ["wide"]),
        _it("a1", "bottom", "women", ["wide"]),
        _it("b2", "bottom", "women", ["wide"]),
    ]
    affinity_map = {("knit", "wide"): 5}
    out = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    # 전부 동점(5점) → id 오름차순만으로 정렬
    assert [i["id"] for i in out] == ["a1", "b2", "c3"]


def test_rank_by_style_affinity_symmetric_lookup_either_key_order():
    items = [_it("x", "bottom", "women", ["wide"])]
    # (tag_b, tag_a) 순서로만 저장돼도 (tag_a, tag_b) 조회가 맞아야 함
    affinity_map = {("wide", "knit"): 7}
    out = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    assert out[0]["id"] == "x"


def test_rank_by_style_affinity_missing_pair_scores_zero():
    items = [
        _it("known", "bottom", "women", ["wide"]),
        _it("unknown", "bottom", "women", ["floral"]),
    ]
    affinity_map = {("knit", "wide"): 3}
    out = retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    assert [i["id"] for i in out] == ["known", "unknown"]


# ---------- recommend_v1 ----------

def test_recommend_v1_complementarity_preserved_top_never_returns_top():
    items = [
        _it("t1", "top", "women", ["wide"]),
        _it("t2", "top", "women", ["knit"]),
        _it("b1", "bottom", "women", ["wide"]),
        _it("b2", "bottom", "women", ["slim"]),
    ]
    affinity_map = {("knit", "wide"): 5, ("knit", "slim"): 1}
    out = retrieval.recommend_v1(
        items, clothing_type="top", genders=["women"],
        product_tags=["knit"], affinity_map=affinity_map,
    )
    assert len(out) > 0
    assert all(i["clothing_type"] != "top" for i in out)
    assert {i["id"] for i in out} == {"b1", "b2"}


def test_recommend_v1_outer_and_dress_also_map_to_bottom():
    items = [
        _it("top_item", "top", "women", ["wide"]),
        _it("bottom_item", "bottom", "women", ["wide"]),
    ]
    affinity_map = {}
    for ct in ("outer", "dress"):
        out = retrieval.recommend_v1(
            items, clothing_type=ct, genders=["women"],
            product_tags=["knit"], affinity_map=affinity_map,
        )
        assert all(i["clothing_type"] == "bottom" for i in out)


def test_recommend_v1_bottom_input_returns_top_only():
    items = [
        _it("t1", "top", "women", ["wide"]),
        _it("b1", "bottom", "women", ["wide"]),
    ]
    out = retrieval.recommend_v1(
        items, clothing_type="bottom", genders=["women"],
        product_tags=["knit"], affinity_map={},
    )
    assert [i["id"] for i in out] == ["t1"]


def test_recommend_v1_gender_filter_allows_unisex():
    items = [
        _it("m", "bottom", "men", ["wide"]),
        _it("w", "bottom", "women", ["wide"]),
        _it("u", "bottom", "unisex", ["wide"]),
    ]
    out = retrieval.recommend_v1(
        items, clothing_type="top", genders=["women"],
        product_tags=["knit"], affinity_map={},
    )
    assert {i["id"] for i in out} == {"w", "u"}


def test_recommend_v1_excludes_inactive():
    items = [
        _it("active_item", "bottom", "women", ["wide"]),
        _it("inactive_item", "bottom", "women", ["wide"], active=False),
    ]
    out = retrieval.recommend_v1(
        items, clothing_type="top", genders=["women"],
        product_tags=["knit"], affinity_map={},
    )
    assert [i["id"] for i in out] == ["active_item"]


def test_recommend_v1_ranks_pool_by_affinity():
    items = [
        _it("b_low", "bottom", "women", ["slim"]),
        _it("b_high", "bottom", "women", ["wide"]),
        _it("b_zero", "bottom", "women", ["floral"]),
    ]
    affinity_map = {("knit", "wide"): 9, ("knit", "slim"): 2}
    out = retrieval.recommend_v1(
        items, clothing_type="top", genders=["women"],
        product_tags=["knit"], affinity_map=affinity_map,
    )
    assert [i["id"] for i in out] == ["b_high", "b_low", "b_zero"]


def test_recommend_v1_applies_limit():
    items = [
        _it("a", "bottom", "women", ["wide"]),
        _it("b", "bottom", "women", ["wide"]),
        _it("c", "bottom", "women", ["wide"]),
    ]
    out = retrieval.recommend_v1(
        items, clothing_type="top", genders=["women"],
        product_tags=["knit"], affinity_map={}, limit=1,
    )
    assert len(out) == 1


# ---------- style + color harmony ----------

def test_color_harmony_map_is_complete_one_direction_and_unit_interval():
    colors = {
        "white", "gray", "black", "ivory", "beige", "brown",
        "red", "yellow", "green", "blue", "navy", "pink", "khaki",
    }
    harmony = color_harmony.harmony_map()
    assert harmony is color_harmony.HARMONY
    assert len(harmony) == 91  # 13색의 중복 없는 조합 수: 13 * 14 / 2
    assert {color for pair in harmony for color in pair} == colors
    for (left, right), score in harmony.items():
        assert 0 <= score <= 1
        if left != right:
            assert (right, left) not in harmony
    assert {score for (left, right), score in harmony.items() if left == right} == {0.55}


def test_recommend_v1_without_product_color_is_identical_to_existing_style_ranking():
    items = [
        _it("c", "bottom", "women", ["wide"], color_group="beige"),
        _it("a", "bottom", "women", ["wide"], color_group="black"),
        _it("b", "bottom", "women", ["slim"], color_group="white"),
    ]
    affinity_map = {("knit", "wide"): 5, ("knit", "slim"): 1}
    expected = retrieval.diversify_top_two(
        retrieval.rank_by_style_affinity(items, ["knit"], affinity_map)
    )
    actual = retrieval.recommend_v1(
        items, "top", ["women"], ["knit"], affinity_map,
        product_color=None, harmony=color_harmony.harmony_map(),
    )
    assert [item["id"] for item in actual] == [item["id"] for item in expected]


def test_recommend_v1_navy_product_ranks_beige_ahead_of_black():
    items = [
        _it("black", "bottom", "women", ["daily"], color_group="black"),
        _it("beige", "bottom", "women", ["daily"], color_group="beige"),
    ]
    actual = retrieval.recommend_v1(
        items, "top", ["women"], ["basic"], {("basic", "daily"): 0.9},
        product_color="navy", harmony=color_harmony.harmony_map(), color_weight=0.3,
    )
    assert [item["id"] for item in actual] == ["beige", "black"]


def test_unknown_color_pair_falls_back_to_neutral_half():
    assert retrieval._color_harmony_score(
        "ultraviolet", "khaki", color_harmony.harmony_map()
    ) == 0.5
    assert retrieval._color_harmony_score(
        "navy", None, color_harmony.harmony_map()
    ) == 0.5


def test_color_weight_zero_is_identical_to_style_only_with_product_color():
    items = [
        _it("style-high", "bottom", "women", ["wide"], color_group="black"),
        _it("color-high", "bottom", "women", ["slim"], color_group="beige"),
    ]
    affinity_map = {("knit", "wide"): 5, ("knit", "slim"): 1}
    expected = retrieval.recommend_v1(
        items, "top", ["women"], ["knit"], affinity_map,
    )
    actual = retrieval.recommend_v1(
        items, "top", ["women"], ["knit"], affinity_map,
        product_color="navy", harmony=color_harmony.harmony_map(), color_weight=0,
    )
    assert [item["id"] for item in actual] == [item["id"] for item in expected]


def test_color_harmony_lookup_is_symmetric_for_one_direction_map():
    one_direction = {("navy", "beige"): 0.92}
    assert retrieval._color_harmony_score("navy", "beige", one_direction) == 0.92
    assert retrieval._color_harmony_score("beige", "navy", one_direction) == 0.92


# ---------- select_kb_static ----------

def test_select_kb_static_matches_by_category():
    chunks = [
        _chunk("k1", category="coat"),
        _chunk("k2", category="dress"),
    ]
    out = retrieval.select_kb_static(chunks, {"category": "coat", "styleTags": []})
    assert [c["id"] for c in out] == ["k1"]


def test_select_kb_static_matches_by_style_tags_intersection():
    chunks = [
        _chunk("k1", category="other", style_tags=["minimal", "street"]),
        _chunk("k2", category="other", style_tags=["romantic"]),
    ]
    out = retrieval.select_kb_static(chunks, {"category": None, "styleTags": ["street", "casual"]})
    assert [c["id"] for c in out] == ["k1"]


def test_select_kb_static_no_match_excluded():
    chunks = [
        _chunk("k1", category="coat", style_tags=["minimal"]),
    ]
    out = retrieval.select_kb_static(chunks, {"category": "dress", "styleTags": ["romantic"]})
    assert out == []


def test_select_kb_static_deterministic_order_by_id():
    chunks = [
        _chunk("k3", category="coat"),
        _chunk("k1", category="coat"),
        _chunk("k2", category="coat"),
    ]
    out = retrieval.select_kb_static(chunks, {"category": "coat", "styleTags": []})
    assert [c["id"] for c in out] == ["k1", "k2", "k3"]


# ---------- build_retrieval_log ----------

def test_build_retrieval_log_shape():
    log = retrieval.build_retrieval_log(
        kind="matching", corpus_version=1, k=5, latency_ms=12, flag_state="tags",
    )
    assert log == {
        "kind": "matching",
        "corpus_version": 1,
        "k": 5,
        "latency_ms": 12,
        "flag_state": "tags",
    }


def test_recommend_v1_limit_zero_returns_empty():
    # limit=0 은 '빈 결과' (0은 falsy — 전체 반환 아님, 리뷰 반영)
    items = [_it("b1", "bottom", "women", ["wide"]), _it("b2", "bottom", "women", ["slim"])]
    out = retrieval.recommend_v1(items, clothing_type="top", genders=["women"],
                                 product_tags=["wide"], affinity_map={}, limit=0)
    assert out == []


def test_style_scores_quantized_so_summation_order_cannot_break_ties():
    # 같은 태그 집합·다른 순서 → 부동소수점 합산 노이즈로 비동점이 되면 id tie-break 이
    # 무력화되고 mock(부분합)과 순서가 어긋난다 (2026-08-12 리뷰 실측: 2.15 vs 2.15…04)
    from app.services.retrieval import rank_by_style_affinity
    amap = {("formal", "a"): 0.85, ("formal", "b"): 0.3, ("minimal", "a"): 0.5, ("minimal", "b"): 0.5}
    items = [
        {"id": "z-late", "style_tags": ["a", "b"]},
        {"id": "a-early", "style_tags": ["b", "a"]},   # 같은 집합, 다른 합산 순서
    ]
    ranked = rank_by_style_affinity(items, ["formal", "minimal"], amap)
    assert [i["id"] for i in ranked] == ["a-early", "z-late"]  # 동점 → id 오름차순
