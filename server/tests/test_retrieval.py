from app.services import retrieval


def _it(id, ct, gender, style_tags=None, active=True):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "style_tags": style_tags or [], "is_active": active}


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
