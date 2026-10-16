import asyncio

from app.agents import copy_qc as qc
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def test_review_schema_uses_unique_text_id():
    schema = qc.review_schema()
    item = schema["properties"]["results"]["items"]
    assert item["properties"]["verdict"]["enum"] == ["pass", "revise"]
    assert set(item["required"]) == {"textId", "verdict", "revisedText", "reason"}
    assert "blockId" not in item["properties"]


def test_build_prompt_injects_text_ids_items_and_facts():
    prompt = qc.build_prompt(
        [{"textId": "b1:headline", "blockId": "b1", "text": "완벽 방수 니트"}],
        {"materials": [{"name": "코튼"}], "sellingPoints": ["촉감"], "measurementsKnown": True},
    )
    assert "textId=b1:headline: 완벽 방수 니트" in prompt
    assert "materials: 코튼" in prompt and "measurementsKnown: True" in prompt


def test_validate_maps_multiple_texts_in_same_block_by_text_id():
    items = [
        {"textId": "b1:headline", "blockId": "b1", "text": "완벽 방수"},
        {"textId": "b1:body", "blockId": "b1", "text": "코튼 소재"},
    ]
    raw = {"results": [
        {"textId": "b1:headline", "verdict": "revise",
         "revisedText": "여밈이 돋보이는 디자인", "reason": "기능 단정"},
        {"textId": "b1:body", "verdict": "pass", "revisedText": None, "reason": None},
    ]}

    assert qc.validate(raw, items) == [
        {"textId": "b1:headline", "blockId": "b1", "verdict": "revise",
         "revisedText": "여밈이 돋보이는 디자인", "reason": "기능 단정"},
        {"textId": "b1:body", "blockId": "b1", "verdict": "pass",
         "revisedText": None, "reason": None},
    ]


def test_validate_missing_result_defaults_pass_per_text():
    items = [
        {"textId": "b1:headline", "blockId": "b1", "text": "x"},
        {"textId": "b2:headline", "blockId": "b2", "text": "y"},
    ]
    raw = {"results": [
        {"textId": "b1:headline", "verdict": "pass", "revisedText": None, "reason": None},
    ]}
    out = qc.validate(raw, items)
    assert len(out) == 2
    assert out[1] == {"textId": "b2:headline", "blockId": "b2", "verdict": "pass",
                      "revisedText": None, "reason": None}


def test_validate_revise_without_text_falls_back_to_pass():
    items = [{"textId": "b1:headline", "blockId": "b1", "text": "x"}]
    raw = {"results": [
        {"textId": "b1:headline", "verdict": "revise", "revisedText": "  ", "reason": "r"},
    ]}
    assert qc.validate(raw, items)[0]["verdict"] == "pass"


def test_review_orchestrates(monkeypatch):
    items = [{"textId": "b1:headline", "blockId": "b1", "text": "완벽 방수"}]

    async def fake_complete(_settings, prompt, _schema, *, timeout_seconds=None):
        assert timeout_seconds == 120
        assert "CONFIRMED FACTS" in prompt
        return ({"results": [
            {"textId": "b1:headline", "verdict": "revise",
             "revisedText": "여밈이 돋보이는 디자인", "reason": "과장"},
        ]}, "gpt")

    monkeypatch.setattr(qc, "complete_json", fake_complete)
    out = run(qc.review(make_settings(openai_api_key="sk-x"), items, {"materials": []}))
    assert out[0]["textId"] == "b1:headline"
    assert out[0]["verdict"] == "revise"
