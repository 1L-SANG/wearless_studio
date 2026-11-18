"""origin/main 코드로 캡처한 기준 글. 테스트 실행 중에는 git을 부르지 않는다."""

import json
from pathlib import Path

import pytest

from app.agents import cut_generator, confirmed_gpt_prompt
from test_confirmed_gpt_prompt import _top_request, _dress_request


BASELINE = json.loads((Path(__file__).parent / "fixtures/fit_cut_prompt_baseline.json").read_text())


@pytest.mark.parametrize("case", BASELINE["generic"], ids=lambda case: case["name"])
def test_generic_with_mannequin_is_byte_identical_to_main(case):
    actual = cut_generator.build_prompt(case["spec"], case["product"], analysis=case["analysis"], manifest=case["manifest"])
    assert actual.encode() == case["expected"].encode()


@pytest.mark.parametrize("case", BASELINE["precise"], ids=lambda case: case["name"])
def test_precise_with_mannequin_is_byte_identical_to_main(case):
    request = {"top": _top_request, "dress": _dress_request}[case["name"]]()
    assert confirmed_gpt_prompt.compile_confirmed_gpt_prompt(request).encode() == case["expected"].encode()
