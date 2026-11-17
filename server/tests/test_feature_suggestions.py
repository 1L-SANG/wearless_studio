"""버리던 GPT 후보만 추천으로 보존한다. 칩 규칙과 AI 호출 수는 유지한다."""

import asyncio
from copy import deepcopy

import pytest

from app.agents import feature_extractor as fx
from conftest import make_settings


def candidate(point, evidence="사진에 디테일이 보여요", distinctive=True):
    return {"point": point, "visualEvidence": evidence, "distinctive": distinctive}


def test_selected_overlap_word_coverage_and_recommendation_duplicates_are_removed():
    raw = {"candidates": [candidate("왼쪽가슴로고자수"), candidate("가슴 로고"),
        candidate("깊은 비대칭 트임 밑단"), candidate("왼쪽 가슴 자수"),
        candidate("왼쪽 소매 와펜", "왼쪽 소매에 와펜이 보여요."), candidate("소매 와펜"),
        candidate("와펜 왼쪽 소매"), candidate("배색 카라", "카라\n끝에 배색이 보여요..."),
        candidate("이 옷에서 특별하게 보이는 아주 긴 문장"), candidate("옆선. 트임"),
        candidate("일반 단추", distinctive=False), candidate("밑단 프릴", distinctive=1)],
        "selected": ["왼쪽 가슴 로고 자수", "비대칭 트임 밑단"]}
    original = deepcopy(raw)
    points = ["왼쪽 가슴 로고 자수", "비대칭 트임 밑단"]
    actual = fx.select_feature_suggestions(raw, points, "gpt")
    assert actual == [{"point": "왼쪽 소매 와펜", "evidence": "왼쪽 소매에 와펜이 보여요"},
                      {"point": "배색 카라", "evidence": "카라 끝에 배색이 보여요"}]
    assert fx.validate(raw) == points and raw == original


def test_five_valid_recommendations_keep_model_order_and_fit_names_are_not_filtered():
    points = ["배색 카라", "가슴 포켓"]
    raw = {"candidates": [candidate(point) for point in [*points, "오버핏", "소매 리본", "밑단 프릴",
        "레이스 포인트", "투웨이 지퍼", "진주 단추", "셔링 라인"]]}
    assert [row["point"] for row in fx.select_feature_suggestions(raw, points, "gpt")] == [
        "오버핏", "소매 리본", "밑단 프릴", "레이스 포인트", "투웨이 지퍼"]


def test_unmatched_rewritten_chips_use_the_documented_leading_candidate_fallback():
    raw = {"candidates": [candidate(point) for point in ["셔링 소매", "가슴 와펜", "배색 카라", "옆선 트임"]]}
    assert [row["point"] for row in fx.select_feature_suggestions(raw, ["퍼프 포인트", "자수 장식"], "gpt")] == [
        "배색 카라", "옆선 트임"]


def test_recommendation_word_overlap_is_checked_in_both_directions():
    points = ["배색 카라", "가슴 포켓"]
    raw = {"candidates": [candidate(point) for point in [*points, "셔링 퍼프 소매", "넓은 퍼프 셔링 소매"]]}
    assert fx.select_feature_suggestions(raw, points, "gpt") == [
        {"point": "셔링 퍼프 소매", "evidence": "사진에 디테일이 보여요"}]


@pytest.mark.parametrize("evidence,wanted", [("어깨선이\n팔 쪽으로 내려와 있어요. ", "어깨선이 팔 쪽으로 내려와 있어요"),
    ("Visible detail", ""), ("소매에 주름", ""), ("가" * 40 + "요", ""),
    ("가" * 39 + "요", "가" * 39 + "요"), (None, "")])
def test_bad_evidence_is_hidden_without_removing_the_recommendation(evidence, wanted):
    assert fx.select_feature_suggestions({"candidates": [candidate("소매 셔링", evidence)]}, [], "gpt") == [
        {"point": "소매 셔링", "evidence": wanted}]


@pytest.mark.parametrize("provider", ["gemini", None, "unknown"])
def test_only_the_gpt_prompt_route_emits_recommendations(provider):
    assert fx.select_feature_suggestions({"candidates": [candidate("소매 셔링")]}, [], provider) == []


def test_client_normalization_checks_shape_and_length_without_language_or_fit_filtering():
    raw = [{"point": "오버핏", "evidence": "plain English", "extra": "ignore"},
           {"point": "소매 리본", "evidence": "가" * 41}, {"point": 123, "evidence": "있어요"},
           {"point": "이 옷에서 특별하게 보이는 아주 긴 문장", "evidence": "있어요"}]
    original = deepcopy(raw)
    assert fx.normalize_feature_suggestions(raw) == [{"point": "오버핏", "evidence": "plain English"},
                                                     {"point": "소매 리본", "evidence": ""}]
    assert raw == original


def test_client_normalization_caps_five_and_returns_an_empty_array_for_bad_shapes():
    assert fx.normalize_feature_suggestions(None) == []
    assert fx.normalize_feature_suggestions("not a list") == []
    assert len(fx.normalize_feature_suggestions([{"point": "소매 리본", "evidence": ""}] * 7)) == 5


@pytest.mark.parametrize("provider", ["gpt", "gemini"])
def test_extract_keeps_chips_and_details_while_preserving_unused_candidates_in_the_same_call(monkeypatch, provider):
    raw = {"candidates": [candidate(point) for point in ["배색 카라", "가슴 포켓", "소매 리본", "밑단 프릴"]],
           "selected": ["배색 카라", "가슴 포켓"], "detailCandidates": [{"kind": "fabric"}]}
    calls = []
    async def fake_request(*args, **kwargs):
        calls.append(args)
        return raw, provider
    monkeypatch.setattr(fx, "analyze_with_fallback", fake_request)
    points, actual_provider = asyncio.run(fx.extract(make_settings(), {}, []))
    assert list(points) == ["배색 카라", "가슴 포켓"] and actual_provider == provider
    assert points.detail_candidates == [{"kind": "fabric"}] and len(calls) == 1
    assert points.feature_suggestions == ([{"point": "소매 리본", "evidence": "사진에 디테일이 보여요"},
        {"point": "밑단 프릴", "evidence": "사진에 디테일이 보여요"}] if provider == "gpt" else [])
