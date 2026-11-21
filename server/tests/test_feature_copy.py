import asyncio

import pytest

from app import repo
from app.agents import feature_copy as fc
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


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


def test_generate_sends_all_points_to_model_before_dictionary_fallback():
    requests = []

    async def fake(request, timeout):
        requests.append(request)
        return {"response": {"sections": [], "partDescriptionCopy": [], "featureCopy": [
            {"point": "앞 지퍼 여밈", "desc": "지퍼를 열고 닫아 입어요"},
            {"point": "어깨 셔링 퍼프 소매", "desc": "어깨에 잡힌 주름이 소매로 이어져요"},
        ]}, "usage": {}}

    points = ["앞 지퍼 여밈", "어깨 셔링 퍼프 소매"]
    out = run(fc.generate(make_settings(), points, {}, {}, overrides={"caller": fake}))
    assert len(requests) == 1
    assert [slot["point"] for slot in requests[0]["slots"]] == points
    assert requests[0]["system_prompt"] and requests[0]["prompt"]
    assert not any("{{"+key+"}}" in requests[0]["system_prompt"]+requests[0]["prompt"]
                   for key in ("rules", "facts", "plans", "examples", "slots"))
    assert out == [
        {"point": "앞 지퍼 여밈", "desc": "지퍼를 열고 닫아 입어요"},
        {"point": "어깨 셔링 퍼프 소매", "desc": "어깨에 잡힌 주름이 소매로 이어져요"},
    ]


@pytest.mark.parametrize("timeout", [None, 120, 7.5, 0])
def test_generate_forwards_optional_timeout(monkeypatch, timeout):
    from app.agents import page_copy
    seen = []

    async def spy(settings, slots, product, analysis, *, seed, timeout_seconds, overrides=None, feature=False):
        seen.append((timeout_seconds, seed, overrides, feature))
        return {"featureCopy": []}

    monkeypatch.setattr(page_copy, "generate_slots", spy, raising=False)
    run(fc.generate(make_settings(), ["빈티지한 워싱감"], {}, {}, timeout_seconds=timeout,
                    seed="editor-seed", overrides={"provider": "gemini"}))
    assert seen == [(timeout, "editor-seed", {"provider": "gemini"}, True)]


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


@pytest.mark.parametrize("template,expected", [
    ("emo", "지퍼 여밈 디테일을 확인할 수 있어요."),
    ("show", "지퍼 여밈 디테일"),
])
def test_generate_dictionary_fallback_follows_template_after_model_failure(template, expected):
    calls = []

    async def failure(request, timeout):
        calls.append(request)
        raise RuntimeError("provider down")

    out = run(fc.generate(make_settings(), ["앞 지퍼 여밈", "설명 못 만들 표현"], {},
                          {"pageStyle": {"templateId": template}}, overrides={"caller": failure}))
    assert calls
    assert out == [{"point": "앞 지퍼 여밈", "desc": expected}]


def test_generate_fills_only_missing_output_from_dictionary():
    async def fake(request, timeout):
        return {"response": {"featureCopy": [{"point": "앞 지퍼 여밈", "desc": "지퍼를 열고 닫아 입어요"}]}, "usage": {}}

    out = run(fc.generate(make_settings(), ["앞 지퍼 여밈", "오픈 카라"], {}, {}, overrides={"caller": fake}))
    assert out == [
        {"point": "앞 지퍼 여밈", "desc": "지퍼를 열고 닫아 입어요"},
        {"point": "오픈 카라", "desc": "열린 형태의 카라를 확인할 수 있어요."},
    ]


def test_generate_rejects_model_claim_before_using_dictionary():
    async def fake(request, timeout):
        return {"response": {"featureCopy": [{"point": "앞 지퍼 여밈", "desc": "방수 기능이 있어요"}]}, "usage": {}}

    assert run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, {}, overrides={"caller": fake})) == [
        {"point": "앞 지퍼 여밈", "desc": "지퍼 여밈 디테일을 확인할 수 있어요."},
    ]


def test_generate_replaces_model_copy_that_repeats_a_part_description():
    async def fake(request, timeout):
        return {"response": {"featureCopy": [{"point": "앞 지퍼 여밈", "desc": "지퍼를 열고 닫아 입어요"}]}, "usage": {}}

    analysis = {"partDescriptionCopy": [{"point": "지퍼", "desc": "지퍼를 열고 닫아 입어요"}]}
    assert run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, analysis, overrides={"caller": fake})) == [
        {"point": "앞 지퍼 여밈", "desc": "지퍼 여밈 디테일을 확인할 수 있어요."},
    ]


def test_generate_validates_dictionary_fallback_and_avoids_part_description_repeat(monkeypatch):
    async def empty(request, timeout):
        return {"response": {}, "usage": {}}

    monkeypatch.setattr(fc, "lookup", lambda point: "방수 기능을 확인할 수 있습니다.")
    assert run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, {}, overrides={"caller": empty})) == []
    monkeypatch.setattr(fc, "lookup", lambda point: "지퍼 여밈 디테일을 확인할 수 있습니다.")
    analysis = {"partDescriptionCopy": [{"point": "지퍼", "desc": "지퍼 여밈 디테일을 확인할 수 있어요."}]}
    assert run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, analysis, overrides={"caller": empty})) == []


@pytest.mark.parametrize("analysis,storyboard", [
    ({}, [{"pageSectionKey": "show.points"}]),
    ({"storyboard": [{"pageSectionKey": "show.points"}]}, None),
    ({"pageStyle": {"templateId": "show"}}, [{"pageSectionKey": "emo.points"}]),
])
def test_generate_resolves_template_from_page_style_or_storyboard(analysis, storyboard):
    requests = []
    async def fake(request, timeout):
        requests.append(request)
        return {"response": {"featureCopy": [{"point": "오픈 카라", "desc": "단추 없이 열린 카라"}]}, "usage": {}}

    assert run(fc.generate(make_settings(), ["오픈 카라"], {}, analysis, storyboard=storyboard,
                           overrides={"caller": fake})) == [{"point": "오픈 카라", "desc": "단추 없이 열린 카라"}]
    assert all(slot["template"] == "show" for slot in requests[0]["slots"])


def test_generate_preserves_raw_point_keys_and_does_not_mutate_analysis():
    from copy import deepcopy
    analysis = {"sellingPoints": ["오픈 카라"], "mdComment": "카라를 강조해 주세요"}
    before = deepcopy(analysis)
    async def fake(request, timeout):
        return {"response": {"featureCopy": [{"point": "어깨 셔링 퍼프 소매", "desc": "어깨에 잡힌 주름이 소매로 이어져요"}]}, "usage": {}}

    point = "  어깨 셔링 퍼프 소매  "
    assert run(fc.generate(make_settings(), [point], {}, analysis, overrides={"caller": fake})) == [
        {"point": point, "desc": "어깨에 잡힌 주름이 소매로 이어져요"},
    ]
    assert analysis == before


def test_generate_empty_points_do_not_call_model(monkeypatch):
    from app.agents import page_copy
    calls = []
    async def spy(*args, **kwargs):
        calls.append(1)
        return {"featureCopy": []}
    monkeypatch.setattr(page_copy, "generate_slots", spy, raising=False)
    assert run(fc.generate(make_settings(), [None, "", "  "], {}, {})) == []
    assert calls == []


def test_generate_does_not_swallow_hard_call_limit(monkeypatch):
    from app.agents import page_copy
    async def stop(*args, **kwargs):
        raise page_copy.CallLimitError("max calls")
    monkeypatch.setattr(page_copy, "generate_slots", stop, raising=False)
    with pytest.raises(page_copy.CallLimitError, match="max calls"):
        run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, {}))


def test_generate_missing_feature_prompt_is_an_error(tmp_path, monkeypatch):
    from app.agents import page_copy
    monkeypatch.setattr(page_copy, "PROMPT_DIR", tmp_path)
    async def unused(request, timeout):
        pytest.fail("missing prompt must fail before dispatch")
    with pytest.raises(FileNotFoundError):
        run(fc.generate(make_settings(), ["앞 지퍼 여밈"], {}, {}, overrides={"caller": unused}))


def test_feature_copy_is_preserved_across_analysis_merges():
    # 부분 병합에서 셀러 클라이언트가 생략한 생성 카피도 보존한다.
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
