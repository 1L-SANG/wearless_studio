"""T2 pairwise 심판 — 순수 로직(정답 매핑·채점·프롬프트·검증) 테스트.
실 Gemini judge(async)는 키·비용 필요라 여기서 제외(P1-2 실경로 세션에서 검증)."""

import pytest

from app.agents import mannequin_pairwise_qc as PQ
from app.agents.vision_llm import VisionError


# ─────────────── expected_more_side (외부 정답 매핑) ───────────────

def test_expected_more_side_length_top():
    # long > crop → long 쪽이 'more'(더 김)
    assert PQ.expected_more_side("top", "length", "long", "crop") == "left"
    assert PQ.expected_more_side("top", "length", "crop", "long") == "right"


def test_expected_more_side_fit_and_cut_and_silhouette():
    assert PQ.expected_more_side("top", "fit", "over", "slim") == "left"        # over 가 더 루즈
    assert PQ.expected_more_side("pants", "cut", "skinny", "wide") == "right"   # wide 가 더 넓음
    assert PQ.expected_more_side("skirt", "silhouette", "mermaid", "h_line") == "left"  # mermaid 가 더 플레어


def test_expected_more_side_equal_and_incomparable():
    assert PQ.expected_more_side("top", "length", "long", "long") == "equal"
    assert PQ.expected_more_side("top", "length", "long", "bogus") is None       # 미지 값
    assert PQ.expected_more_side("hat", "length", "a", "b") is None              # 미지 카테고리
    # dress silhouette: a_line 과 fit_and_flare 는 동순위 → equal
    assert PQ.expected_more_side("dress", "silhouette", "a_line", "fit_and_flare") == "equal"


# ─────────────── score_pair (채점) ───────────────

def test_score_pair_correct_direction_passes():
    v = {"moreSide": "left"}
    r = PQ.score_pair(v, "top", "length", "long", "crop")  # expected left
    assert r["directionalPass"] is True and r["abstain"] is False


def test_score_pair_wrong_direction_fails():
    v = {"moreSide": "right"}
    r = PQ.score_pair(v, "top", "length", "long", "crop")  # expected left
    assert r["directionalPass"] is False and r["abstain"] is False


def test_score_pair_abstain_not_scored():
    for side in ("unclear", "similar"):
        r = PQ.score_pair({"moreSide": side}, "pants", "cut", "wide", "skinny")
        assert r["directionalPass"] is None and r["abstain"] is True


def test_score_pair_equal_expected():
    # 동일 값 → expected 'equal'. 'similar' 답이 정답, 방향 답은 오답.
    assert PQ.score_pair({"moreSide": "similar"}, "top", "length", "long", "long")["directionalPass"] is True
    assert PQ.score_pair({"moreSide": "left"}, "top", "length", "long", "long")["directionalPass"] is False


def test_score_pair_incomparable_not_scored():
    r = PQ.score_pair({"moreSide": "left"}, "top", "length", "long", "bogus")
    assert r["directionalPass"] is None and r["abstain"] is True


# ─────────────── build_prompt / schema / validate ───────────────

def test_build_prompt_hides_expected_and_covers_axis():
    p = PQ.build_prompt("length")
    assert "longer" in p and "LEFT" in p and "RIGHT" in p
    assert "similar" in p and "unclear" in p        # abstain 계약
    # 'target' 은 오직 편향 방지 가드에만 등장(기대 방향/정답 미노출)
    assert "not assume either side is a target" in p.lower()


def test_build_prompt_rejects_unknown_axis():
    with pytest.raises(ValueError):
        PQ.build_prompt("bogus")


def test_schema_shape():
    s = PQ.schema()
    assert s["properties"]["moreSide"]["enum"] == ["left", "right", "similar", "unclear"]
    assert "moreSide" in s["required"]


def test_validate_rejects_bad_side():
    with pytest.raises(VisionError):
        PQ.validate({"moreSide": "maybe", "reason": "x"})
    ok = PQ.validate({"moreSide": "left", "reason": "hem lower"})
    assert ok["moreSide"] == "left"


def test_comparative_lookup():
    assert PQ.comparative("cut") and "WIDER" in PQ.comparative("cut")
    assert PQ.comparative("bogus") is None
