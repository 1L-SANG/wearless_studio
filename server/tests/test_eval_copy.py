import asyncio
from collections import Counter

from scripts import eval_copy


def test_copy_eval_fixture_has_five_products_per_clothing_type():
    cases = eval_copy.load_cases()
    assert len(cases) == 20
    assert Counter(case["category"] for case in cases) == {
        "상의": 5, "하의": 5, "아우터": 5, "원피스": 5,
    }
    roles = {
        block["contentRole"]
        for case in cases
        for block in case["blocks"]
        if not str(block.get("exampleId") or "").startswith("sig_")
    }
    assert {"benefit", "fit", "coordination", "detail", "productOverview"} <= roles


def test_evaluate_cases_uses_injected_generator_and_counts_missing_as_violation():
    calls = []
    cases = eval_copy.load_cases()[:1]

    async def fake_generate(settings, *, blocks, product, analysis):
        calls.append((settings, blocks, product, analysis))
        return [{"blockId": blocks[1]["id"], "texts": [
            {"role": "headline", "text": "목선을 정돈하는 라운드넥"},
        ]}]

    rows = asyncio.run(eval_copy.evaluate_cases(cases, object(), generator=fake_generate))

    assert len(calls) == 1
    assert any(row["headline"] == "목선을 정돈하는 라운드넥" for row in rows)
    assert any("빈 결과" in row["violations"] for row in rows)


def test_render_report_writes_rule_violation_total_and_table():
    report = eval_copy.render_report([
        {"category": "상의", "caseId": "top-1", "blockId": "b1",
         "headline": "목선을 정돈하는 라운드넥", "violations": []},
        {"category": "상의", "caseId": "top-1", "blockId": "b2",
         "headline": "", "violations": ["빈 결과"]},
    ])

    assert "규칙 위반 수: 1" in report
    assert "| 종류 | 입력 | 사진 | 소제목 | 위반 |" in report
    assert "top-1" in report and "빈 결과" in report
