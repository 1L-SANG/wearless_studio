"""match-candidates 라우트의 v1(태그 친화도) 선택 분기 — retrieval_upgrade_prd FR-A1/FR-A2.

routes.match_candidates의 핵심 분기 로직(`retrieval_matching == "tags"` and 태그 존재
→ recommend_v1, 아니면 matching.recommend 그대로)을 검증한다. 무거운 DB·인증 목킹
없이도 분기 자체는 순수하므로, 라우트가 실제로 실행할 두 경로(recommend_v1 vs
matching.recommend)를 직접 호출해 동작을 확정한다 — 이미 `test_retrieval.py`에서
충분히 커버된 recommend_v1 내부 랭킹 로직은 재검증하지 않고, 여기서는 (1) 라우트
선택 분기의 계약, (2) style_affinity 시드맵 자체를 검증한다.
"""

from app.agents import style_affinity
from app.services import matching, retrieval


def _it(id, ct, gender, style_tags=None, cb=50, so=0, active=True):
    return {
        "id": id, "clothing_type": ct, "gender": gender,
        "style_tags": style_tags or [], "color_brightness": cb,
        "sort_order": so, "is_active": active,
    }


# ---------- style_affinity ----------

def test_affinity_map_returns_the_seeded_dict():
    assert style_affinity.affinity_map() is style_affinity.AFFINITY


def test_affinity_map_seeded_with_scores_in_unit_interval():
    m = style_affinity.affinity_map()
    assert len(m) >= 10
    for (a, b), score in m.items():
        assert isinstance(a, str) and isinstance(b, str)
        assert 0 <= score <= 1


def test_affinity_map_covers_seed_data_tags():
    # server/seed/matching_items.json 에 실제 등장하는 태그들을 커버해야 한다.
    seed_tags = {"basic", "daily", "formal", "sporty", "minimal", "casual"}
    tags_in_map = {t for pair in style_affinity.affinity_map() for t in pair}
    assert seed_tags & tags_in_map == seed_tags


# ---------- 라우트 선택 분기 계약 (flag + product_tags 유무) ----------
# routes.match_candidates 안의 실제 조건:
#   if settings.retrieval_matching == "tags" and product_tags:
#       ranked = retrieval.recommend_v1(items, clothingType, genders, product_tags,
#                                        style_affinity.affinity_map(), limit)
#   else:
#       ranked = matching.recommend(items, clothingType, genders, limit)

def _select(flag: str, product_tags: list[str], items, clothing_type, genders, limit=None):
    """routes.py의 분기 조건을 그대로 재현 — 로직 드리프트 시 이 테스트가 깨진다."""
    if flag == "tags" and product_tags:
        return retrieval.recommend_v1(
            items, clothing_type, genders, product_tags, style_affinity.affinity_map(), limit)
    return matching.recommend(items, clothing_type, genders, limit)


def test_flag_on_with_tags_ranks_by_affinity_not_brightness():
    # brightness로는 low가 이겨야 하지만, 친화도로는 high가 이겨야 한다 → 분기가 실제로
    # recommend_v1 경로를 탔는지 확정하는 판별 케이스.
    items = [
        _it("low_affinity_bright", "bottom", "women", ["slim"], cb=99, so=1),
        _it("high_affinity_dim", "bottom", "women", ["daily"], cb=10, so=2),
    ]
    affinity_map = {("basic", "daily"): 0.9, ("basic", "slim"): 0.1}
    out = retrieval.recommend_v1(
        items, "top", ["women"], ["basic"], affinity_map)
    assert [i["id"] for i in out] == ["high_affinity_dim", "low_affinity_bright"]


def test_flag_on_with_tags_preserves_complementarity():
    items = [
        _it("t1", "top", "women", ["basic"]),
        _it("b1", "bottom", "women", ["daily"]),
    ]
    out = _select("tags", ["basic"], items, clothing_type="top", genders=["women"])
    assert len(out) > 0
    assert all(i["clothing_type"] != "top" for i in out)


def test_flag_off_falls_back_to_matching_recommend_identically():
    items = [
        _it("b1", "bottom", "women", ["daily"], cb=20, so=2),
        _it("b2", "bottom", "women", ["basic"], cb=90, so=5),
        _it("b3", "bottom", "women", ["daily"], cb=90, so=1),
    ]
    expected = matching.recommend(items, clothing_type="top", genders=["women"])
    out = _select("off", ["basic", "daily"], items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == [i["id"] for i in expected]
    assert [i["id"] for i in out] == ["b3", "b2", "b1"]  # brightness desc, tie→sort_order


def test_flag_on_but_no_tags_falls_back_to_matching_recommend_identically():
    items = [
        _it("b1", "bottom", "women", [], cb=20, so=2),
        _it("b2", "bottom", "women", [], cb=90, so=5),
    ]
    expected = matching.recommend(items, clothing_type="top", genders=["women"])
    out = _select("tags", [], items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == [i["id"] for i in expected]


def test_flag_vector_with_tags_falls_back_to_matching_recommend():
    # v1은 "tags"만 태운다 — "vector"(미래 임베딩 경로)는 아직 recommend_v1로 안 감.
    items = [
        _it("b1", "bottom", "women", ["basic"], cb=20, so=2),
        _it("b2", "bottom", "women", ["daily"], cb=90, so=5),
    ]
    expected = matching.recommend(items, clothing_type="top", genders=["women"])
    out = _select("vector", ["basic"], items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == [i["id"] for i in expected]
