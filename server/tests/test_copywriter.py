import asyncio

import pytest

from app.agents import copywriter as cw
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


PRODUCT = {
    "name": "핀턱 와이드 슬랙스",
    "clothingType": "bottom",
    "colors": [
        {"id": "col_black", "name": "차콜 블랙", "swatchId": "black", "isBase": True},
    ],
}
ANALYSIS = {
    "fit": "semi_over",
    "sellingPoints": ["허리 핀턱", "와이드 실루엣"],
    "materials": [{"name": "코튼"}],
    "matchClothing": [
        {"id": "match_knit", "name": "크림 골지 니트", "colorName": "크림"},
    ],
    "detailRecommendations": {
        "candidates": [{"id": "dt_waist", "label": "허리 핀턱"}],
    },
}


def _blocks():
    return [
        {
            "id": "blk_1",
            "source": "ai",
            "exampleId": "sig_opening_01",
            "contentRole": "hero",
            "cutType": "styling",
            "shot": "full",
        },
        {
            "id": "blk_2",
            "source": "ai",
            "contentRole": "coordination",
            "cutType": "styling",
            "shot": "full",
            "direction": "front",
            "colorId": "col_black",
            "matchIds": ["match_knit"],
        },
        {
            "id": "blk_3",
            "source": "ai",
            "contentRole": "detail",
            "cutType": "product",
            "shot": "detail",
            "direction": "front",
            "colorId": "col_black",
            "detailTargetId": "dt_waist",
        },
    ]


def test_copy_schema_is_page_level_headline_contract():
    schema = cw.copy_schema()
    assert schema["required"] == ["blocks"]
    item = schema["properties"]["blocks"]["items"]
    assert item["required"] == ["blockId", "headline"]
    assert set(item["properties"]) == {"blockId", "headline"}
    assert item["additionalProperties"] is False


def test_build_prompt_uses_human_labels_and_confirmed_names_only():
    prompt = cw.build_prompt(_blocks(), PRODUCT, ANALYSIS)

    assert "핀턱 와이드 슬랙스" in prompt
    assert "하의" in prompt
    assert "세미 오버핏" in prompt
    assert "차콜 블랙" in prompt
    assert "크림 골지 니트" in prompt
    assert "허리 핀턱" in prompt
    assert "코디" in prompt and "디테일" in prompt
    assert "전신" in prompt and "부위 클로즈업" in prompt
    assert "sig_opening_01" not in prompt
    for internal in (
        "contentRole", "sectionRole", "cutType", "detailTargetId",
        "styling", "horizon", "productOverview", "coordination",
        "col_black", "match_knit", "dt_waist",
    ):
        assert internal not in prompt


def test_build_prompt_does_not_leak_unknown_enum_tokens():
    prompt = cw.build_prompt(
        [{"id": "b1", "source": "ai", "contentRole": "custom",
          "cutType": "future_cut", "shot": "future_shot"}],
        {"name": "테스트 상품", "clothingType": "future_type", "colors": []},
        {"fit": "future_fit"},
    )
    for token in ("future_cut", "future_shot", "future_type", "future_fit"):
        assert token not in prompt


def test_generate_calls_model_once_for_page_and_maps_in_storyboard_order(monkeypatch):
    calls = []

    async def fake_complete(_settings, prompt, schema):
        calls.append((prompt, schema))
        return ({"blocks": [
            {"blockId": "blk_3", "headline": "핀턱을 담은 허리선"},
            {"blockId": "blk_2", "headline": "크림 니트와 차분한 조합"},
            {"blockId": "unknown", "headline": "알 수 없는 문구"},
        ]}, "gemini")

    monkeypatch.setattr(cw, "complete_json", fake_complete)
    out = run(cw.generate(make_settings(gemini_api_key="x"), blocks=_blocks(),
                          product=PRODUCT, analysis=ANALYSIS))

    assert len(calls) == 1
    assert out == [
        {"blockId": "blk_2", "texts": [
            {"role": "headline", "text": "크림 니트와 차분한 조합"},
        ]},
        {"blockId": "blk_3", "texts": [
            {"role": "headline", "text": "핀턱을 담은 허리선"},
        ]},
    ]


def test_generate_retries_once_after_invalid_json_result(monkeypatch):
    calls = 0

    async def fake_complete(_settings, _prompt, _schema):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid json")
        return ({"blocks": [
            {"blockId": "blk_2", "headline": "크림 니트와 차분한 조합"},
            {"blockId": "blk_3", "headline": "핀턱을 담은 허리선"},
        ]}, "gemini")

    monkeypatch.setattr(cw, "complete_json", fake_complete)
    out = run(cw.generate(make_settings(gemini_api_key="x"), blocks=_blocks(),
                          product=PRODUCT, analysis=ANALYSIS))

    assert calls == 2
    assert [item["blockId"] for item in out] == ["blk_2", "blk_3"]


def test_generate_returns_empty_after_second_page_failure(monkeypatch):
    calls = 0

    async def fake_complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(cw, "complete_json", fake_complete)
    out = run(cw.generate(make_settings(gemini_api_key="x"), blocks=_blocks(),
                          product=PRODUCT, analysis=ANALYSIS))

    assert calls == 2
    assert out == []


@pytest.mark.parametrize(("headline", "reason"), [
    ("완벽한 하루를 위한 팬츠", "금지어"),
    ("가" * 21, "길이"),
    ("디테일 컷에서 보는 핀턱", "내부 용어"),
    ("편안하게 입을 수 있어요", "종결"),
    ("첫 줄\n둘째 줄", "한 줄"),
    ("1 위를 기록한 팬츠", "금지어"),
    ("구김없음이 장점인 셔츠", "금지어"),
    ("보풀 없음 니트", "금지어"),
    ("UV차단 원피스", "금지어"),
    ("날씬해지는 허리 핏", "금지어"),
    ("제가 입어보니 편한 니트", "가상 후기"),
    ("원피스의 허리의 포인트", "번역투"),
    ("자외선을 차단하는 원피스", "금지어"),
    ("UV를 차단하는 아우터", "금지어"),
    ("구김이 생기지 않는 셔츠", "금지어"),
    ("보풀이 나지 않는 니트", "금지어"),
    ("구김 걱정 없는 팬츠", "금지어"),
])
def test_headline_violations_detects_copy_rules(headline, reason):
    assert reason in cw.headline_violations(headline)


def test_generate_repairs_only_invalid_blocks_once_then_leaves_them_empty(monkeypatch):
    prompts = []

    async def fake_complete(_settings, prompt, _schema):
        prompts.append(prompt)
        if len(prompts) == 1:
            return ({"blocks": [
                {"blockId": "blk_2", "headline": "크림 니트와 차분한 조합"},
                {"blockId": "blk_3", "headline": "완벽한 핀턱 디테일"},
            ]}, "gemini")
        return ({"blocks": [
            {"blockId": "blk_3", "headline": "디테일 컷 속 완벽한 핀턱"},
        ]}, "gemini")

    monkeypatch.setattr(cw, "complete_json", fake_complete)
    out = run(cw.generate(make_settings(gemini_api_key="x"), blocks=_blocks(),
                          product=PRODUCT, analysis=ANALYSIS))

    assert len(prompts) == 2
    assert "blk_2" not in prompts[1]
    assert "blk_3" in prompts[1]
    assert out == [{"blockId": "blk_2", "texts": [
        {"role": "headline", "text": "크림 니트와 차분한 조합"},
    ]}]


def test_generate_accepts_valid_repair_for_invalid_block(monkeypatch):
    calls = 0

    async def fake_complete(_settings, _prompt, _schema):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ({"blocks": [
                {"blockId": "blk_2", "headline": "크림 니트와 차분한 조합"},
                {"blockId": "blk_3", "headline": "최고의 핀턱"},
            ]}, "gemini")
        return ({"blocks": [
            {"blockId": "blk_3", "headline": "허리선을 잡아 주는 핀턱"},
        ]}, "gemini")

    monkeypatch.setattr(cw, "complete_json", fake_complete)
    out = run(cw.generate(make_settings(gemini_api_key="x"), blocks=_blocks(),
                          product=PRODUCT, analysis=ANALYSIS))

    assert calls == 2
    assert out[-1] == {"blockId": "blk_3", "texts": [
        {"role": "headline", "text": "허리선을 잡아 주는 핀턱"},
    ]}


def test_validate_product_name_keeps_existing_fallback_contract():
    assert cw.validate_product_name("소프트 골지 니트") == "소프트 골지 니트"
    assert cw.validate_product_name("새 상품") is None
