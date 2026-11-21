"""Reference table transfer and frontend/server care wording stay in sync."""

import json
from pathlib import Path
import re
import subprocess

import pytest
from page_copy_test_support import page_copy_prompt_dir


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "server/app/data"
PROMPTS = ROOT / "server/prompts/page_copy"


def read_data(name):
    return json.loads((DATA / f"page_copy_{name}.json").read_text())


# Hand-checked against FABRIC_FEEL.md. Parenthesized conditions are part of the permission.
FABRIC_ROWS = [
    ("면(코튼)", ["부드러운", "살에 닿는 느낌이 순한", "탄탄한(두꺼운 면)"], ["땀 흡수", "통기", "줄지 않는다"]),
    ("면 저지(티셔츠 원단)", ["매끈한 겉면", "부드러운", "가볍게 늘어나는"], ["늘어짐 없다", "목이 늘어나지 않는다"]),
    ("스웨트, 쭈리(맨투맨 원단)", ["도톰한", "탄탄한", "모양이 잘 잡히는"], ["무게 숫자", "따뜻하다는 단정"]),
    ("기모", ["안쪽이 보송한", "포근한", "도톰한"], ["한겨울까지", "보온 숫자"]),
    ("골지, 립", ["몸을 따라 늘어나는", "쫀쫀한", "결이 손에 느껴지는"], ["복원력", "늘어짐 없다"]),
    ("니트(일반)", ["포근한", "폭신한 짜임", "부드러운"], ["보풀이 없다", "따갑지 않다", "따뜻하다는 단정"]),
    ("울 혼방", ["포근한", "도톰한", "힘 있는"], ["까슬거리지 않는다", "보온 숫자"]),
    ("아크릴 혼방", ["가볍고 부드러운"], ["보풀이 없다"]),
    ("폴리에스터", ["가벼운", "매끈한", "찰랑이는(얇은 원단)"], ["구김이 없다", "시원하다", "통기"]),
    ("나일론", ["가벼운", "바스락한", "은은한 광택"], ["방수", "방풍"]),
    ("레이온, 비스코스", ["찰랑이는", "매끈한", "흐르듯 떨어지는"], ["시원하다는 단정", "구김이 없다"]),
    ("텐셀, 모달", ["부드러운", "은은한 윤기", "흐르는"], ["친환경", "항균"]),
    ("데님", ["탄탄한", "결이 살아 있는", "유연한(스판 혼방일 때)"], ["물이 빠지지 않는다", "무릎이 나오지 않는다"]),
    ("트윌, 치노", ["촘촘한", "탄탄한", "반듯하게 잡히는"], ["구김이 없다"]),
    ("와플", ["올록볼록한 감촉", "도톰한"], ["구김이 덜 간다"]),
    ("메시, 시스루", ["얇고 가벼운", "은은하게 비치는"], ["시원하다는 단정"]),
    ("스판 혼방(어느 소재든)", ["잘 늘어나는", "유연한. 혼용률에 스판(폴리우레탄)이 있을 때만"], ["늘어짐 없다", "복원력"]),
    ("패딩, 다운", ["폭신한", "가벼운"], ["보온 숫자", "충전재 비율", "한겨울까지"]),
    ("린넨", ["가벼운", "바스락한", "은은하게 비치는"], ["시원하다는 단정", "통기", "구김이 없다"]),
    ("코듀로이", ["도톰한", "겉면이 부드러운", "모양이 잘 잡히는"], ["따뜻하다는 단정"]),
    ("새틴", ["은은한 광택", "매끈한"], ["실크 같다는 말", "구김이 없다"]),
    ("플리스", ["포근한", "부드럽고 가벼운"], ["한겨울까지", "보온 숫자"]),
    ("트위드", ["짜임이 손에 느껴지는", "도톰한(겨울용)", "가벼운(여름용, 셀러 확인)"], ["산뜻하다는 단정"]),
    ("시폰", ["얇고 가벼운", "하늘거리는"], ["비침이 없다", "시원하다는 단정"]),
    ("레더, 스웨이드(합성 포함)", ["매끈한 겉면(레더)", "보슬보슬한 겉면(스웨이드)", "힘 있는"], ["진짜 가죽이라는 말", "방수"]),
]


def test_fabric_reference_preserves_all_rows_and_conditions_without_examples():
    rows = read_data("fabric_feel")["rows"]
    assert [(row["name"], row["allowed"], row["forbidden"]) for row in rows] == FABRIC_ROWS
    for row in rows:
        assert set(row) == {"name", "lookup_words", "allowed", "forbidden"}
        assert row["lookup_words"]
        assert all(isinstance(word, str) and word.strip() for word in row["lookup_words"])
        assert len(set(row["lookup_words"])) == len(row["lookup_words"])


def test_category_reference_contains_only_supported_pairings():
    rows = read_data("category")["rows"]
    expected = {
        "셔츠": ["가디건", "데님", "니트 베스트"],
        "블라우스": ["스커트", "슬랙스", "가디건"],
        "티셔츠": ["가디건"],
        "니트": ["슬리브리스", "데님", "셔츠"],
        "맨투맨·후드": ["데님", "팬츠·조거 등", "스커트"],
        "가디건": ["데님", "스커트"],
        "재킷": ["코트", "니트"],
        "코트": ["스커트", "데님"],
        "패딩": [],
        "슬랙스": ["동일 세트 상품", "블라우스", "셔츠"],
        "데님": ["니트", "티셔츠", "블라우스"],
        "스커트": ["니트", "블라우스", "가디건"],
        "원피스": ["팬츠·조거 등", "블라우스"],
    }
    assert len(rows) == len(expected) == 13
    assert {row["name"]: row["pairings"] for row in rows} == expected
    for row in rows:
        assert set(row) == {"name", "lookup_words", "pairings"}
        assert row["lookup_words"]
        assert not any("종류 미지정" in item for item in row["pairings"])


def test_care_sentences_match_actual_frontend_exports():
    script = """
import { CARE_COPY_LIBRARY, CARE_LABEL_SENTENCE } from './src/features/editor/presets/infoPresets.js';
console.log(JSON.stringify({families: CARE_COPY_LIBRARY, label_sentence: CARE_LABEL_SENTENCE}));
"""
    frontend = json.loads(subprocess.check_output(
        ["node", "--input-type=module", "-e", script], cwd=ROOT, text=True,
    ))
    data = read_data("care")
    assert data["families"] == frontend["families"]
    assert data["label_sentence"] == frontend["label_sentence"]


@pytest.mark.parametrize("prefix", ["", "feature_"])
def test_provider_templates_load_and_fill_required_placeholders(prefix, page_copy_prompt_dir):
    from app.agents import page_copy
    for provider in ("gpt", "gemini", "claude"):
        text = (page_copy_prompt_dir / f"{prefix}{provider}_v1.md").read_text()
        assert set(re.findall(r"\{\{(\w+)\}\}", text)) == {"rules", "facts", "plans", "examples", "slots"}
        assert len(text.split("\n---USER---\n")) == 2
        request = page_copy.build_request([], {"fixture":"입력 확인"}, {"seed":"test","slots":{}},
                                          provider, "unconfigured-test-model", feature=bool(prefix))
        rendered = request["system_prompt"] + request["prompt"]
        assert "입력 확인" in rendered
        assert not re.search(r"\{\{(?:rules|facts|plans|examples|slots)\}\}", rendered)


def test_provider_settings_load_without_pinning_owner_tuning_values(page_copy_prompt_dir):
    from app.agents import page_copy
    settings = json.loads((page_copy_prompt_dir / "settings_v1.json").read_text())
    for provider in ("gpt", "gemini", "claude"):
        request = page_copy.build_request([], {}, {"seed":"test","slots":{}}, provider, "unconfigured-test-model")
        assert request["settings"] == settings["providers"][provider]
        assert type(request["settings"]["max_output_tokens"]) is int
        assert request["settings"]["max_output_tokens"] > 0
