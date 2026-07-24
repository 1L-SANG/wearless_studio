"""T2 고도화 — 축 반영 측정 매트릭스 순수 로직 테스트.
극단쌍 선정·성별 카탈로그 교차·교차 생성 순서·좌우 counterbalance 의 값 동반 스왑·의심축 규칙."""

import pytest

from app.agents import fit_axis_matrix as FM


# ─────────────── catalog_values / extreme_pair ───────────────

def test_catalog_values_respects_gender():
    w = FM.catalog_values("top", "fit", "women")
    m = FM.catalog_values("top", "fit", "men")
    assert "tight" in w and "tight" not in m       # tight 는 women 전용
    assert "over" in w and "over" in m


def test_catalog_values_empty_for_skirt_dress_men():
    assert FM.catalog_values("skirt", "length", "men") == []
    assert FM.catalog_values("skirt", "silhouette", "men") == []
    assert FM.catalog_values("dress", "length", "men") == []
    assert FM.catalog_values("dress", "silhouette", "men") == []


def test_extreme_pair_top():
    assert FM.extreme_pair("top", "fit", "women") == ("tight", "over")
    assert FM.extreme_pair("top", "fit", "men") == ("slim", "over")       # men 엔 tight 없음
    assert FM.extreme_pair("top", "length", "women") == ("ultra_crop", "long")
    assert FM.extreme_pair("top", "length", "men") == ("crop", "long")    # men 엔 ultra_crop 없음


def test_extreme_pair_pants():
    assert FM.extreme_pair("pants", "cut", "women") == ("skinny", "wide")
    assert FM.extreme_pair("pants", "cut", "men") == ("slim", "wide")     # men 엔 skinny 없음
    assert FM.extreme_pair("pants", "length", "women") == ("above_ankle", "below_ankle")


def test_extreme_pair_skirt_dress_outer():
    assert FM.extreme_pair("skirt", "length", "women") == ("mini", "long")
    assert FM.extreme_pair("skirt", "silhouette", "women") == ("h_line", "mermaid")
    assert FM.extreme_pair("dress", "length", "women") == ("mini", "long")
    assert FM.extreme_pair("dress", "silhouette", "women") == ("h_line", "mermaid")
    assert FM.extreme_pair("outer", "fit", "women") == ("slim", "over")
    assert FM.extreme_pair("outer", "length", "women") == ("crop_short", "long")


def test_extreme_pair_none_when_gender_unavailable():
    for axis in ("length", "silhouette"):
        assert FM.extreme_pair("skirt", axis, "men") is None
        assert FM.extreme_pair("dress", axis, "men") is None


def test_extreme_pair_none_for_unknown_axis():
    assert FM.extreme_pair("top", "bogus", "women") is None
    assert FM.extreme_pair("hat", "fit", "women") is None


# ─────────────── all_pairs ───────────────

def test_all_pairs_women_covers_ten():
    pairs = FM.all_pairs("women")
    assert len(pairs) == 10                                  # 10개 (카테고리,축)
    keys = {(p["category"], p["axis"]) for p in pairs}
    assert keys == set(FM.AXIS_PAIRS)
    assert all(p["low"] != p["high"] for p in pairs)


def test_all_pairs_men_drops_skirt_and_dress():
    pairs = FM.all_pairs("men")
    keys = {(p["category"], p["axis"]) for p in pairs}
    assert ("skirt", "length") not in keys and ("dress", "silhouette") not in keys
    assert ("top", "fit") in keys and ("pants", "cut") in keys and ("outer", "length") in keys
    assert len(pairs) == 6                                   # top2 + pants2 + outer2


# ─────────────── cut_labels (교차 생성 순서) ───────────────

def test_cut_labels_interleaves_low_high():
    # 시간 드리프트가 값과 정렬되지 않도록 A0,B0,A1,B1 순
    assert FM.cut_labels(2) == [("A0", "low"), ("B0", "high"), ("A1", "low"), ("B1", "high")]


# ─────────────── comparison_plan (counterbalance + 값 동반 스왑) ───────────────

def test_comparison_plan_counts():
    plan = FM.comparison_plan("mini", "long", reps=2)
    treat = [c for c in plan if c["kind"] == "treatment"]
    ctrl = [c for c in plan if c["kind"] == "control"]
    assert len(treat) == 4          # rep 2개 × 양 배치
    assert len(ctrl) == 2           # (A0,A1), (B0,B1)


def test_comparison_plan_swaps_value_with_placement():
    """좌우를 바꾸면 값도 함께 바뀌어야 한다 — 채점 인자 불일치 원천 차단(codex P1)."""
    plan = FM.comparison_plan("mini", "long", reps=2)
    ab = next(c for c in plan if c["kind"] == "treatment" and c["orientation"] == "ab")
    ba = next(c for c in plan if c["kind"] == "treatment" and c["orientation"] == "ba")
    assert ab["leftCut"] == "A0" and ab["valueLeft"] == "mini"
    assert ab["rightCut"] == "B0" and ab["valueRight"] == "long"
    # 배치 반전 시 컷과 값이 동시에 반전
    assert ba["leftCut"] == "B0" and ba["valueLeft"] == "long"
    assert ba["rightCut"] == "A0" and ba["valueRight"] == "mini"


def test_comparison_plan_each_treatment_pair_appears_in_both_orientations():
    plan = FM.comparison_plan("a", "b", reps=2)
    for i in range(2):
        pair = {frozenset((c["leftCut"], c["rightCut"]))
                for c in plan if c["kind"] == "treatment"
                and {c["leftCut"], c["rightCut"]} == {f"A{i}", f"B{i}"}}
        orientations = {c["orientation"] for c in plan if c["kind"] == "treatment"
                        and {c["leftCut"], c["rightCut"]} == {f"A{i}", f"B{i}"}}
        assert pair and orientations == {"ab", "ba"}


def test_comparison_plan_controls_are_same_value_both_sides():
    plan = FM.comparison_plan("mini", "long", reps=2)
    for c in [x for x in plan if x["kind"] == "control"]:
        assert c["valueLeft"] == c["valueRight"]     # 동일값 → 기대 'similar'


# ─────────────── is_suspect (사전 등록 규칙) ───────────────

def test_is_suspect_absolute_fail_triggers():
    assert FM.is_suspect(treatment_pass=4, treatment_scored=4, absolute_fail=1) is True


def test_is_suspect_low_treatment_pass_triggers():
    assert FM.is_suspect(treatment_pass=1, treatment_scored=4, absolute_fail=0) is True   # 25%
    assert FM.is_suspect(treatment_pass=2, treatment_scored=4, absolute_fail=0) is False  # 50%


def test_is_suspect_all_abstained_is_suspect_not_pass():
    # 채점 가능한 treatment 가 0 → 반영 미확인 → 의심(통과 처리 금지)
    assert FM.is_suspect(treatment_pass=0, treatment_scored=0, absolute_fail=0) is True


def test_is_suspect_clean_axis_passes():
    assert FM.is_suspect(treatment_pass=4, treatment_scored=4, absolute_fail=0) is False
