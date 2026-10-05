import asyncio

from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings


def run(coro):
    return asyncio.run(coro)


def _blocks():
    return [
        {"id": "b1", "source": "ai", "contentRole": "benefit", "cutType": "horizon"},
        {"id": "b2", "source": "ai", "contentRole": "detail", "cutType": "product"},
    ]


def test_gen_copy_calls_page_generator_once_and_maps_qc_by_text_id(monkeypatch):
    calls = []
    reviewed = []

    async def fake_generate(_settings, *, blocks, product, analysis):
        calls.append((blocks, product, analysis))
        return [
            {"blockId": "b1", "texts": [{"role": "headline", "text": "허리선을 잡는 핀턱"}]},
            {"blockId": "b2", "texts": [{"role": "headline", "text": "소맷단의 버튼 포인트"}]},
        ]

    async def fake_review(_settings, items, _confirmed):
        reviewed.extend(items)
        return [
            {"textId": "b1:headline", "blockId": "b1", "verdict": "revise",
             "revisedText": "허리선에 더한 핀턱", "reason": "더 자연스럽게"},
            {"textId": "b2:headline", "blockId": "b2", "verdict": "pass",
             "revisedText": None, "reason": None},
        ]

    monkeypatch.setattr(dpj.copywriter, "generate", fake_generate)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    app = fake_worker_app(make_settings(gemini_api_key="x"))

    result, generated_name = run(dpj._gen_copy(
        app, {"id": "job-1"}, _blocks(), {"name": "슬랙스"}, {"sellingPoints": ["핀턱"]},
    ))

    assert len(calls) == 1
    assert [item["textId"] for item in reviewed] == ["b1:headline", "b2:headline"]
    assert result == [
        {"blockId": "b1", "texts": [{"role": "headline", "text": "허리선에 더한 핀턱"}]},
        {"blockId": "b2", "texts": [{"role": "headline", "text": "소맷단의 버튼 포인트"}]},
    ]
    assert generated_name is None


def test_gen_copy_page_timeout_returns_empty_without_blocking_job(monkeypatch):
    never = asyncio.Event()

    async def fake_generate(*_args, **_kwargs):
        await never.wait()

    monkeypatch.setattr(dpj.copywriter, "generate", fake_generate)
    app = fake_worker_app(make_settings(gemini_api_key="x", analysis_timeout_seconds=0.01))

    result = run(asyncio.wait_for(
        dpj._gen_copy(app, {"id": "job-1"}, _blocks(), {"name": "슬랙스"}, {}),
        timeout=0.2,
    ))

    assert result == ([], None)


def test_gen_copy_drops_qc_revision_that_breaks_code_rules(monkeypatch):
    async def fake_generate(*_args, **_kwargs):
        return [{"blockId": "b1", "texts": [
            {"role": "headline", "text": "허리선을 잡는 핀턱"},
        ]}]

    async def fake_review(*_args, **_kwargs):
        return [{"textId": "b1:headline", "blockId": "b1", "verdict": "revise",
                 "revisedText": "완벽한 체형 교정 핀턱", "reason": "bad revision"}]

    monkeypatch.setattr(dpj.copywriter, "generate", fake_generate)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    app = fake_worker_app(make_settings(gemini_api_key="x"))

    result, _ = run(dpj._gen_copy(
        app, {"id": "job-1"}, _blocks()[:1], {"name": "슬랙스"}, {},
    ))

    assert result == []


def test_gen_copy_rechecks_page_repetition_after_qc_revisions(monkeypatch):
    blocks = _blocks() + [
        {"id": "b3", "source": "ai", "contentRole": "fit", "cutType": "horizon"},
    ]

    async def fake_generate(*_args, **_kwargs):
        return [
            {"blockId": "b1", "texts": [{"role": "headline", "text": "허리선을 잡는 핀턱"}]},
            {"blockId": "b2", "texts": [{"role": "headline", "text": "소맷단의 버튼 포인트"}]},
            {"blockId": "b3", "texts": [{"role": "headline", "text": "목선을 정돈하는 카라"}]},
        ]

    async def fake_review(_settings, items, _confirmed):
        return [
            {"textId": item["textId"], "blockId": item["blockId"], "verdict": "revise",
             "revisedText": "허리선에 더한 핀턱", "reason": "same revision"}
            for item in items
        ]

    monkeypatch.setattr(dpj.copywriter, "generate", fake_generate)
    monkeypatch.setattr(dpj.copy_qc, "review", fake_review)
    app = fake_worker_app(make_settings(gemini_api_key="x"))

    result, _ = run(dpj._gen_copy(
        app, {"id": "job-1"}, blocks, {"name": "슬랙스"}, {"sellingPoints": ["핀턱"]},
    ))

    assert [item["blockId"] for item in result] == ["b1", "b2"]
