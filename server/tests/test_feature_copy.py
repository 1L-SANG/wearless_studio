from app.agents import feature_copy as fc


def test_lookup_exact_alias():
    assert fc.lookup("하이웨이스트") == "허리선이 높아 다리가 더 길어 보입니다."


def test_lookup_substring_prefers_longer_alias():
    # '언밸런스 햄라인' 은 '햄라인' 도 포함하지만 더 긴 alias 가 이긴다
    assert fc.lookup("언밸런스 햄라인") == "앞뒤 기장이 달라 옆에서 볼 때 리듬이 생깁니다."
    assert fc.lookup("둥근 햄라인") == "밑단 곡선을 살려 하의 위에 자연스럽게 떨어집니다."


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
