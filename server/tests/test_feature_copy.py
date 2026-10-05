import asyncio

import pytest

from app import repo
from app.agents import feature_copy as fc
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def test_feature_copy_uses_the_selected_fit_and_omits_photo_as_is_claims():
    product = {"name": "티셔츠", "clothingType": "top"}
    selected = {"fit": "slim", "fitProfile": {"axes": {"fit": "over"}}}
    facts = fc._facts_block(product, selected)
    assert '- fit: over' in facts
    assert '- fit: slim' not in facts
    assert '- fit:' not in fc._facts_block(product, {"fit": "slim", "fitProfile": None})


def test_lookup_exact_alias():
    assert fc.lookup("하이웨이스트") == "하이웨이스트 디테일을 확인할 수 있습니다."


def test_lookup_substring_prefers_longer_alias():
    # '언밸런스 햄라인' 은 '햄라인' 도 포함하지만 더 긴 alias 가 이긴다
    assert fc.lookup("언밸런스 햄라인") == "앞뒤 길이가 다른 밑단을 확인할 수 있습니다."
    assert fc.lookup("둥근 햄라인") == "밑단 마감 디테일을 확인할 수 있습니다."


@pytest.mark.parametrize(("point", "expected"), [
    ("앞 지퍼 여밈", "지퍼 여밈 디테일을 확인할 수 있습니다."),
    ("크롭 팬츠", "짧은 팬츠 기장을 확인할 수 있습니다."),
    ("롤업 밑단", "접어 올린 밑단 마감을 확인할 수 있습니다."),
    ("소매 밴딩", "밴딩으로 마감한 소맷단을 확인할 수 있습니다."),
    ("밑단 밴딩", "밴딩으로 마감한 밑단을 확인할 수 있습니다."),
    ("투버튼 카라", "두 단추가 더해진 카라를 확인할 수 있습니다."),
    ("투웨이 지퍼", "양방향 지퍼 여밈을 확인할 수 있습니다."),
    ("후드 스트링", "후드의 스트링 디테일을 확인할 수 있습니다."),
    ("와이드 밑단", "여유 있게 넓은 밑단을 확인할 수 있습니다."),
    ("소매 단추", "소매의 단추 디테일을 확인할 수 있습니다."),
    ("옆선 이중 스티치", "옆선의 이중 스티치를 확인할 수 있습니다."),
])
def test_lookup_regression_does_not_misdescribe_specific_details(point, expected):
    assert fc.lookup(point) == expected


@pytest.mark.parametrize(("point", "expected"), [
    ("옆 포켓", "옆 포켓 디테일을 확인할 수 있습니다."),
    ("스트랩", "스트랩 디테일을 확인할 수 있습니다."),
    ("단추", "단추 디테일을 확인할 수 있습니다."),
    ("와펜", "와펜 디테일을 확인할 수 있습니다."),
    ("밴딩", "밴딩 마감을 확인할 수 있습니다."),
    ("롤업", "접어 올린 마감을 확인할 수 있습니다."),
    ("스티치", "스티치 마감을 확인할 수 있습니다."),
    ("앞주머니", "앞쪽 포켓 디테일을 확인할 수 있습니다."),
])
def test_lookup_generic_aliases_do_not_invent_narrower_details(point, expected):
    assert fc.lookup(point) == expected


@pytest.mark.parametrize(("point", "expected"), [
    ("길이 조절 스트랩 디테일", "길이를 조절하는 스트랩을 확인할 수 있습니다."),
    ("롤업 소매 디테일", "접어 올린 소매 마감을 확인할 수 있습니다."),
    ("배색 스티치 마감", "배색 스티치 마감을 확인할 수 있습니다."),
    ("소매 단추 디테일", "소매의 단추 디테일을 확인할 수 있습니다."),
])
def test_lookup_extended_specific_alias_ignores_nested_generic_alias(point, expected):
    assert fc.lookup(point) == expected


def test_lookup_rejects_negated_phrases():
    # 셀러가 부정한 부위에 사전이 긍정 문구를 붙이면 칩 바로 아래에 정반대 문장이 나간다.
    # 부분일치는 흘려보내고(=None) 모델이 부정을 읽게 한다.
    assert fc.lookup("주름 없는 원단") is None
    assert fc.lookup("안감 없이 시원한") is None
    assert fc.lookup("트임 없는 디자인") is None
    assert fc.lookup("트임 안 들어간 스커트") is None


def test_lookup_does_not_match_the_colour_homograph():
    # '칼라'(colourful)는 카라(collar)가 아니다
    assert fc.lookup("칼라풀한 배색") is None


def test_negation_guard_does_not_over_fire():
    # '안감' 의 '안' 은 부정어가 아니고, 부정어가 없는 부분일치는 그대로 살아 있어야 한다
    assert fc.lookup("안감 마감") == "안감을 덧댄 마감을 확인할 수 있습니다."
    assert fc.lookup("사이드 트임") == "옆선의 트임 디테일을 확인할 수 있습니다."
    assert fc.lookup("부드러운 안감 처리") == "안감을 덧댄 마감을 확인할 수 있습니다."
    assert fc.lookup("옆선 트임 디테일") == "옆선의 트임 디테일을 확인할 수 있습니다."


def test_lookup_is_case_and_space_insensitive():
    assert fc.lookup("  HIGH WAIST  ") == fc.lookup("하이웨이스트")


def test_lookup_misses_return_none():
    assert fc.lookup("전에 없던 표현") is None
    assert fc.lookup("") is None
    assert fc.lookup(None) is None


def test_lookup_does_not_guess_when_partial_match_names_multiple_details():
    assert fc.lookup("지퍼와 카고 포켓을 더한 디자인") is None


def test_dictionary_entries_are_well_formed():
    assert len(fc.DETAIL_COPY) >= 30
    for key, (desc, aliases) in fc.DETAIL_COPY.items():
        assert desc.endswith("다."), f"{key}: 합니다체 종결"
        assert len(desc) <= fc.MAX_DESC_CHARS, f"{key}: {len(desc)}자"
        assert aliases, f"{key}: alias 최소 1개"


def test_dictionary_makes_no_unverified_functional_claims():
    banned = ("통기성", "방수", "발수", "항균", "보온", "자외선", "냄새", "땀 흡수", "구김")
    for key, (desc, _aliases) in fc.DETAIL_COPY.items():
        for word in banned:
            assert word not in desc, f"{key}: 미확인 기능성 단정 '{word}'"


def test_dictionary_avoids_hype_words():
    for key, (desc, _aliases) in fc.DETAIL_COPY.items():
        for word in ("완벽", "특별한", "놀라운", "최고"):
            assert word not in desc, f"{key}: hype 어휘 '{word}'"


def test_copy_schema_shape():
    s = fc.copy_schema()
    item = s["properties"]["items"]["items"]
    assert set(item["required"]) == {"point", "desc"}
    assert item["additionalProperties"] is False


def test_build_prompt_lists_only_the_points_it_is_given():
    p = fc.build_prompt(
        ["빈티지한 워싱감"],
        {"name": "카고 팬츠", "clothingType": "bottom"},
        {"materials": [{"name": "코튼"}], "fit": "over"})
    assert "카고 팬츠" in p and "코튼" in p
    # 사전 히트가 안 실린다는 주장은 few-shot 예시가 아니라 HIGHLIGHTS 목록에 대한 것이다
    highlights = p.split("HIGHLIGHTS:")[1]
    assert highlights.strip() == "- 빈티지한 워싱감"


def test_generate_sends_only_dictionary_misses_to_the_model(monkeypatch):
    seen = []

    async def spy(_settings, prompt, _schema):
        seen.append(prompt)
        return ({"items": [{"point": "빈티지한 워싱감", "desc": "물 빠진 듯한 색이 자연스럽게 번집니다."}]}, "spy")

    monkeypatch.setattr(fc, "complete_json", spy)
    out = run(fc.generate(make_settings(), ["하이웨이스트", "빈티지한 워싱감"], {}, {}))
    assert len(seen) == 1
    highlights = seen[0].split("HIGHLIGHTS:")[1]
    assert "빈티지한 워싱감" in highlights
    assert "하이웨이스트" not in highlights
    assert out == [
        {"point": "하이웨이스트", "desc": "하이웨이스트 디테일을 확인할 수 있습니다."},
        {"point": "빈티지한 워싱감", "desc": "물 빠진 듯한 색이 자연스럽게 번집니다."},
    ]


def test_validate_keeps_matching_points_only():
    raw = {"items": [
        {"point": "빈티지한 워싱감", "desc": "물 빠진 듯한 색이 자연스럽게 번집니다."},
        {"point": "없는 포인트", "desc": "무시됩니다."},
    ]}
    out = fc.validate(raw, ["빈티지한 워싱감"])
    assert out == {"빈티지한 워싱감": "물 빠진 듯한 색이 자연스럽게 번집니다."}


def test_validate_maps_the_sanitized_echo_back_to_the_raw_point():
    # 프롬프트에는 _sanitize 된 칩이 실린다 — 모델 echo 는 공백이 접힌 형태로 돌아온다.
    # 키는 원문이어야 클라이언트의 exact-string 매칭이 산다.
    raw = {"items": [{"point": "롤업 소매", "desc": "소매를 걷어 올려 인상이 가볍습니다."}]}
    assert fc.validate(raw, ["롤업  소매"]) == {"롤업  소매": "소매를 걷어 올려 인상이 가볍습니다."}


def test_validate_drops_unverified_functional_claims():
    raw = {"items": [{"point": "메쉬 소재", "desc": "통기성이 좋아 시원합니다."}]}
    assert fc.validate(raw, ["메쉬 소재"]) == {}
    assert fc.validate({"items": [{"point": "메쉬 소재", "desc": "통풍이 잘 됩니다."}]}, ["메쉬 소재"]) == {}
    assert fc.validate({"items": [{"point": "메쉬 소재", "desc": "땀을 잘 흡수합니다."}]}, ["메쉬 소재"]) == {}


@pytest.mark.parametrize("desc", [
    "바람을 막아 주는 방풍 기능입니다.",
    "UV 차단 기능이 있습니다.",
    "보풀이 생기지 않습니다.",
    "체형 교정에 도움을 줍니다.",
    "유일한 1위 디자인입니다.",
    "100% 만족할 디테일입니다.",
])
def test_validate_drops_all_page_copy_banned_claims(desc):
    assert fc.validate({"items": [{"point": "테스트", "desc": desc}]}, ["테스트"]) == {}


def test_validate_drops_hype_and_overlong_desc():
    raw = {"items": [
        {"point": "a", "desc": "완벽한 마감입니다."},
        {"point": "b", "desc": "가" * fc.MAX_DESC_CHARS + "습니다."},
    ]}
    assert fc.validate(raw, ["a", "b"]) == {}


def test_generate_uses_dictionary_without_calling_the_model(monkeypatch):
    # generate 가 예외를 삼키므로(카피는 게이트 아님) 여기서 raise 하면 테스트가
    # 통과해 버린다 — 호출 여부는 스파이로 센다.
    called = []

    async def spy(*_args, **_kwargs):
        called.append(1)
        return ({"items": []}, "spy")

    monkeypatch.setattr(fc, "complete_json", spy)
    out = run(fc.generate(make_settings(), ["하이웨이스트"], {}, {}))
    assert out == [{"point": "하이웨이스트", "desc": "하이웨이스트 디테일을 확인할 수 있습니다."}]
    assert called == [], "사전 히트만 있으면 LLM 을 부르지 않는다"


def test_generate_survives_model_failure(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(fc, "complete_json", boom)
    out = run(fc.generate(make_settings(), ["하이웨이스트", "설명 못 만들 표현"], {}, {}))
    assert out == [{"point": "하이웨이스트", "desc": "하이웨이스트 디테일을 확인할 수 있습니다."}]


def test_feature_copy_is_carried_across_analysis_replaces():
    # save_analysis 는 REPLACE 라, 셀러 클라가 안 보낸 서버 소유 키는 이월돼야 한다
    assert "featureCopy" in repo._SERVER_OWNED_ANALYSIS_KEYS


def test_merge_stored_lets_fresh_copy_win_for_the_same_point():
    stored = [{"point": "하이웨이스트", "desc": "옛 문구입니다."},
              {"point": "카고 포켓", "desc": "측면 카고 포켓이 밋밋함을 덜어냅니다."}]
    fresh = [{"point": "하이웨이스트", "desc": "하이웨이스트 디테일을 확인할 수 있습니다."}]
    assert fc.merge_stored(stored, fresh) == [
        {"point": "하이웨이스트", "desc": "하이웨이스트 디테일을 확인할 수 있습니다."},
        {"point": "카고 포켓", "desc": "측면 카고 포켓이 밋밋함을 덜어냅니다."},
    ]


def test_merge_stored_keeps_points_this_run_could_not_write():
    # 이번 호출이 못 만든 항목까지 날리면 셀러가 이미 받은 문구가 사라진다
    stored = [{"point": "카고 포켓", "desc": "측면 카고 포켓이 밋밋함을 덜어냅니다."}]
    assert fc.merge_stored(stored, []) == stored


def test_merge_stored_drops_malformed_and_empty_entries():
    stored = [{"point": "a", "desc": ""}, {"point": None, "desc": "x"}, "nope", None]
    fresh = [{"point": "b", "desc": "문장입니다."}, {"desc": "point 없음"}]
    assert fc.merge_stored(stored, fresh) == [{"point": "b", "desc": "문장입니다."}]


def test_merge_stored_tolerates_missing_stored():
    assert fc.merge_stored(None, [{"point": "a", "desc": "문장입니다."}]) == [{"point": "a", "desc": "문장입니다."}]
    assert fc.merge_stored(None, None) == []
