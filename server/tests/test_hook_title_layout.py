import json
from pathlib import Path

import pytest

from app.agents.page_assembler import _hook_title_layout, assemble


FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/hook-title-layout.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_hook_title_layout_matches_shared_contract(case):
    assert _hook_title_layout(case["input"]) == case["expected"]


def test_hook_title_metrics_and_limits_match_shared_contract():
    from app.agents.hook_title_metrics import HOOK_TITLE_LIMITS, HOOK_TITLE_METRICS

    assert HOOK_TITLE_METRICS == {key: value for key, value in FIXTURE["metrics"].items() if key != "source"}
    assert HOOK_TITLE_LIMITS == FIXTURE["limits"]


@pytest.mark.parametrize("case_id", ["D1", "B2"])
def test_signature_assembly_uses_title_layout(case_id):
    case = next(case for case in FIXTURE["cases"] if case["name"].startswith(case_id + " "))
    expected = case["expected"]
    storyboard = [{
        "id": "signature", "sectionRole": "hooking", "contentRole": "hero",
        "source": "ai", "cutType": "horizon", "hookTitleOverlay": True,
    }]
    block = assemble(storyboard, [], [], {"name": case["input"], "measurements": []}, True)[0]
    image = next(element for element in block["elements"] if element["type"] == "image")
    title = next(element for element in block["elements"] if element.get("copyRole") == "hookTitle")
    assert title["text"] == expected["text"]
    assert title["h"] == expected["h"]
    assert title["y"] == image["y"] + (image["h"] - expected["h"]) // 2
    assert title["style"]["size"] == expected["size"]
    assert title["style"]["lineHeight"] == expected["lineHeight"]
    assert "maxLines" not in title["style"]


@pytest.mark.parametrize("value", [None, " \t\r\n\f\v\u00a0\u3000 "])
def test_hook_title_empty_values(value):
    assert _hook_title_layout(value) == {"text": "", "lines": 1, "size": 60, "lineHeight": 72, "h": 72}


@pytest.mark.parametrize("value, text", [
    (" \t니트\r\n\f\v\u00a0\u3000 반팔 ", "니트 반팔"),
    ("\u2003니트\u2003", "\u2003니트\u2003"),
    (0, "0"),
])
def test_hook_title_normalizes_only_contract_whitespace(value, text):
    assert _hook_title_layout(value) == {"text": text, "lines": 1, "size": 60, "lineHeight": 72, "h": 72}
