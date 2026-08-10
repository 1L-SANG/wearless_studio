import asyncio

from app import repo
from app.agents import feature_copy as fc
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def test_lookup_exact_alias():
    assert fc.lookup("하이웨이스트") == "허리선이 높아 다리가 더 길어 보입니다."


def test_lookup_substring_prefers_longer_alias():
    # '언밸런스 햄라인' 은 '햄라인' 도 포함하지만 더 긴 alias 가 이긴다
    assert fc.lookup("언밸런스 햄라인") == "앞뒤 기장이 달라 옆에서 볼 때 리듬이 생깁니다."
    assert fc.lookup("둥근 햄라인") == "밑단 곡선을 살려 하의 위에 자연스럽게 떨어집니다."


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
    assert fc.lookup("안감 마감") == "안감을 덧대 겉감의 라인이 곱게 잡힙니다."
    assert fc.lookup("사이드 트임") == "옆선에 트임이 있어 걸을 때 다리가 편하게 움직입니다."
    assert fc.lookup("부드러운 안감 처리") == "안감을 덧대 겉감의 라인이 곱게 잡힙니다."
    assert fc.lookup("옆선 트임 디테일") == "옆선에 트임이 있어 걸을 때 다리가 편하게 움직입니다."


def test_lookup_is_case_and_space_insensitive():
    assert fc.lookup("  HIGH WAIST  ") == fc.lookup("하이웨이스트")


def test_lookup_misses_return_none():
    assert fc.lookup("전에 없던 표현") is None
    assert fc.lookup("") is None
    assert fc.lookup(None) is None


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
        {"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."},
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
    assert out == [{"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."}]
    assert called == [], "사전 히트만 있으면 LLM 을 부르지 않는다"


def test_generate_survives_model_failure(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(fc, "complete_json", boom)
    out = run(fc.generate(make_settings(), ["하이웨이스트", "설명 못 만들 표현"], {}, {}))
    assert out == [{"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."}]


def test_feature_copy_is_carried_across_analysis_replaces():
    # save_analysis 는 REPLACE 라, 셀러 클라가 안 보낸 서버 소유 키는 이월돼야 한다
    assert "featureCopy" in repo._SERVER_OWNED_ANALYSIS_KEYS


def test_merge_stored_lets_fresh_copy_win_for_the_same_point():
    stored = [{"point": "하이웨이스트", "desc": "옛 문구입니다."},
              {"point": "카고 포켓", "desc": "측면 카고 포켓이 밋밋함을 덜어냅니다."}]
    fresh = [{"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."}]
    assert fc.merge_stored(stored, fresh) == [
        {"point": "하이웨이스트", "desc": "허리선이 높아 다리가 더 길어 보입니다."},
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
