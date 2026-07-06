import asyncio

from app.agents import copy_qc as qc
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def test_review_schema_shape():
    s = qc.review_schema()
    item = s["properties"]["results"]["items"]
    assert item["properties"]["verdict"]["enum"] == ["pass", "revise"]
    assert set(item["required"]) == {"blockId", "verdict", "revisedText", "reason"}


def test_build_prompt_injects_items_and_facts():
    p = qc.build_prompt(
        [{"blockId": "b1", "text": "완벽 방수 니트"}],
        {"materials": [{"name": "코튼"}], "sellingPoints": ["촉감"], "measurementsKnown": True})
    assert "blockId=b1: 완벽 방수 니트" in p
    assert "materials: 코튼" in p and "measurementsKnown: True" in p


def test_validate_revise_adopts_revised():
    items = [{"blockId": "b1", "text": "완벽 방수"}]
    raw = {"results": [{"blockId": "b1", "verdict": "revise",
                        "revisedText": "생활방수 수준이에요", "reason": "과장"}]}
    out = qc.validate(raw, items)
    assert out == [{"blockId": "b1", "verdict": "revise",
                    "revisedText": "생활방수 수준이에요", "reason": "과장"}]


def test_validate_missing_result_defaults_pass():
    items = [{"blockId": "b1", "text": "x"}, {"blockId": "b2", "text": "y"}]
    raw = {"results": [{"blockId": "b1", "verdict": "pass", "revisedText": None, "reason": None}]}
    out = qc.validate(raw, items)
    assert len(out) == 2  # 입력 item 마다 1개 보장
    assert out[1] == {"blockId": "b2", "verdict": "pass", "revisedText": None, "reason": None}


def test_validate_revise_without_text_falls_back_to_pass():
    items = [{"blockId": "b1", "text": "x"}]
    raw = {"results": [{"blockId": "b1", "verdict": "revise", "revisedText": "  ", "reason": "r"}]}
    out = qc.validate(raw, items)
    assert out[0]["verdict"] == "pass"  # 수정안 없으면 원문 유지


def test_review_orchestrates(monkeypatch):
    items = [{"blockId": "b1", "text": "완벽 방수"}]

    async def fake_complete(settings, prompt, schema):
        assert "CONFIRMED FACTS" in prompt
        return ({"results": [{"blockId": "b1", "verdict": "revise",
                              "revisedText": "생활방수", "reason": "과장"}]}, "gpt")
    monkeypatch.setattr(qc, "complete_json", fake_complete)
    out = run(qc.review(make_settings(openai_api_key="sk-x"), items, {"materials": []}))
    assert out[0]["verdict"] == "revise" and out[0]["revisedText"] == "생활방수"
