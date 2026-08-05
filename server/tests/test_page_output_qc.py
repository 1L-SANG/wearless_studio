import asyncio
import json

import pytest

from app.agents import page_output_qc as qc
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from conftest import make_settings


def img(name):
    return InlineImage("image/png", name.encode())


def plan():
    return [
        {
            "outputIndex": 0,
            "blockId": "b0",
            "targetColor": "black",
            "clothingType": "outer",
            "cutType": "styling",
            "outerClosureState": "open",
            "modelId": "model-a",
            "matchingIds": ["pants-a"],
            "spaceGroupId": "space-a",
            "productTruthIndexes": [0],
        },
        {
            "outputIndex": 1,
            "blockId": "b1",
            "targetColor": "black",
            "clothingType": "outer",
            "cutType": "styling",
            "outerClosureState": "partial",
            "modelId": "model-a",
            "matchingIds": ["pants-a"],
            "spaceGroupId": "space-a",
            "productTruthIndexes": [0],
        },
        {
            "outputIndex": 2,
            "blockId": "b2",
            "targetColor": "ivory",
            "clothingType": "top",
            "cutType": "product",
            "modelId": None,
            "matchingIds": [],
            "productTruthIndexes": [1],
        },
    ]


def raw_result(statuses=None, *, overall="PASS", outliers=None):
    statuses = statuses or {}
    return {
        "overall": overall,
        "gates": [
            {
                "gate": gate,
                "status": statuses.get(gate, "PASS"),
                "evidence": [],
                "correction": None,
            }
            for gate in qc.GATES
        ],
        "outliers": outliers or [],
    }


def test_prompt_maps_product_refs_then_outputs_and_limits_space_gate():
    prompt = qc.build_prompt(plan(), 2)

    assert "first 2 image(s)" in prompt
    assert "following 3 image(s)" in prompt
    assert "Do NOT require all page cuts to share one place" in prompt
    assert "same non-null spaceGroupId" in prompt
    assert "Real camera movement is expected" in prompt
    assert "physically plausible changes" in prompt
    assert "compare that output only with its listed PRODUCT TRUTH images" in prompt
    assert "graphics, embroidery, and logos" in prompt
    assert "${" not in prompt
    embedded = prompt[prompt.index("[\n"):prompt.index("\n]\n") + 2]
    provider_plan = json.loads(embedded)
    assert provider_plan[0] == {
        "outputIndex": 0,
        "blockId": "B0",
        "targetColor": "C0",
        "productTruthIndexes": [0],
        "clothingType": "outer",
        "cutType": "styling",
        "outerClosureState": "open",
        "modelId": "M0",
        "matchingIds": ["G0"],
        "spaceGroupId": "S0",
    }
    assert provider_plan[1]["blockId"] == "B1"
    assert provider_plan[1]["targetColor"] == "C0"
    assert provider_plan[2]["targetColor"] == "C1"
    for raw_value in ("b0", "b1", "b2", "black", "ivory", "model-a", "pants-a", "space-a"):
        assert raw_value not in prompt


def test_schema_is_strict_and_bounded():
    schema = qc.schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"overall", "gates", "outliers"}
    assert schema["properties"]["overall"]["enum"] == list(qc.OVERALLS)
    assert schema["properties"]["gates"]["maxItems"] == len(qc.GATES)
    gate = schema["properties"]["gates"]["items"]
    assert gate["additionalProperties"] is False
    assert set(gate["required"]) == {"gate", "status", "evidence", "correction"}
    assert gate["properties"]["evidence"]["maxItems"] == qc.MAX_GATE_EVIDENCE
    outlier = schema["properties"]["outliers"]["items"]
    assert set(outlier["required"]) == {"blockId", "gate", "evidence", "correction"}


def test_validate_passes_complete_consistent_page():
    result = qc.validate(raw_result(), plan())

    assert result["overall"] == "PASS"
    assert result["outliers"] == []
    assert len(result["gates"]) == len(qc.GATES)


def test_unjudgeable_applicable_gate_fails_closed_and_gets_outlier():
    result = qc.validate(
        raw_result({"sku_fidelity": "UNJUDGEABLE"}, overall="PASS"), plan()
    )

    assert result["overall"] == "UNJUDGEABLE"
    assert any(
        item["blockId"] == "b0" and item["gate"] == "sku_fidelity"
        for item in result["outliers"]
    )


@pytest.mark.parametrize(
    "gate", ["model_continuity", "matching_continuity", "space_continuity"]
)
def test_not_applicable_is_rejected_when_plan_makes_gate_applicable(gate):
    result = qc.validate(raw_result({gate: "NOT_APPLICABLE"}), plan())
    gate_result = next(item for item in result["gates"] if item["gate"] == gate)

    assert result["overall"] == "UNJUDGEABLE"
    assert gate_result["status"] == "UNJUDGEABLE"
    assert "hard gate applicable" in gate_result["evidence"][-1]


def test_missing_gate_and_conflicting_overall_are_unjudgeable():
    raw = raw_result()
    raw["gates"] = raw["gates"][:-1]
    result = qc.validate(raw, plan())
    completeness = next(item for item in result["gates"] if item["gate"] == "completeness")

    assert result["overall"] == "UNJUDGEABLE"
    assert completeness["status"] == "UNJUDGEABLE"


def test_validation_bounds_and_filters_outliers():
    long_text = "x" * 1000
    raw = raw_result(
        {"target_color": "FAIL"},
        overall="FAIL",
        outliers=[
            {
                "blockId": "B1",
                "gate": "target_color",
                "evidence": long_text,
                "correction": long_text,
            },
            {
                "blockId": "b1",
                "gate": "target_color",
                "evidence": "bad",
                "correction": "bad",
            },
        ],
    )
    result = qc.validate(raw, plan())

    assert result["overall"] == "FAIL"
    assert len(result["outliers"]) == 1
    assert result["outliers"][0]["blockId"] == "b1"
    assert len(result["outliers"][0]["evidence"]) == qc.MAX_EVIDENCE
    assert len(result["outliers"][0]["correction"]) == qc.MAX_CORRECTION


def test_orchestration_prepends_truth_refs_and_validates(monkeypatch):
    captured = {}

    async def fake_analyze(settings, prompt, images, response_schema, thinking_level=None):
        captured.update(
            prompt=prompt,
            image_data=[image.data for image in images],
            schema=response_schema,
            thinking_level=thinking_level,
        )
        return raw_result(), "gemini"

    monkeypatch.setattr(qc, "analyze_with_fallback", fake_analyze)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"),
        plan(),
        [img("g0"), img("g1"), img("g2")],
        product_truth_refs=[img("p0"), img("p1")],
    ))

    assert result["overall"] == "PASS"
    assert captured["image_data"] == [b"p0", b"p1", b"g0", b"g1", b"g2"]
    assert captured["thinking_level"] == "low"
    assert captured["schema"] == qc.schema()
    assert "first 2 image(s)" in captured["prompt"]
    assert result["provider"] == "gemini"
    assert result["qcVersion"] == 1


def test_outer_inner_is_applicable_for_two_open_outer_worn_cuts():
    result = qc.validate(
        raw_result({"outer_inner_continuity": "NOT_APPLICABLE"}), plan()
    )
    gate = next(item for item in result["gates"] if item["gate"] == "outer_inner_continuity")
    assert gate["status"] == "UNJUDGEABLE"
    assert result["overall"] == "UNJUDGEABLE"


def test_product_cut_drops_stale_model_and_matching_for_continuity():
    product_only = [{
        "outputIndex": 0, "blockId": "p0", "targetColor": None,
        "clothingType": "top", "cutType": "product",
        "modelId": "stale-model", "matchingIds": ["stale-match"],
    }]
    normalized = qc.normalize_page_plan(product_only)
    assert normalized[0]["targetColor"] == "base"
    assert normalized[0]["modelId"] is None
    assert normalized[0]["matchingIds"] == []
    assert normalized[0]["productTruthIndexes"] == []


def test_non_applicable_gates_override_provider_failures():
    product_only = [{
        "outputIndex": 0, "blockId": "p0", "targetColor": "blue",
        "clothingType": "top", "cutType": "product",
        "modelId": None, "matchingIds": [], "productTruthIndexes": [0],
    }]
    statuses = {
        "model_continuity": "FAIL",
        "matching_continuity": "UNJUDGEABLE",
        "outer_inner_continuity": "FAIL",
        "space_continuity": "UNJUDGEABLE",
    }
    result = qc.validate(raw_result(statuses, overall="FAIL"), product_only)

    assert result["overall"] == "PASS"
    assert result["outliers"] == []
    by_gate = {item["gate"]: item for item in result["gates"]}
    for gate in statuses:
        assert by_gate[gate] == {
            "gate": gate, "status": "NOT_APPLICABLE",
            "evidence": [], "correction": None,
        }


def test_provider_failure_returns_unjudgeable_instead_of_fail_open(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise VisionError("offline")

    monkeypatch.setattr(qc, "analyze_with_fallback", unavailable)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"), plan(), [img("0"), img("1"), img("2")],
        product_truth_refs=[img("p0"), img("p1")],
    ))

    assert result["overall"] == "UNJUDGEABLE"
    assert all(item["status"] == "UNJUDGEABLE" for item in result["gates"])
    assert result["outliers"]
    assert result["provider"] is None
    assert result["qcVersion"] == 1


def test_missing_generated_output_fails_completeness_without_provider_call(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("provider must not run")

    monkeypatch.setattr(qc, "analyze_with_fallback", should_not_run)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"), plan(), [img("0"), img("1")],
        product_truth_refs=[img("p0"), img("p1")],
    ))

    assert result["overall"] == "FAIL"
    assert result["outliers"] == [
        {
            "blockId": "b2",
            "gate": "completeness",
            "evidence": "No generated image was mapped to output index 2.",
            "correction": "Generate this planned cut before publishing the page.",
        }
    ]
    assert result["provider"] is None
    assert result["qcVersion"] == 1


def test_none_outputs_report_exact_missing_positions_without_provider_call(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("provider must not run")

    monkeypatch.setattr(qc, "analyze_with_fallback", should_not_run)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"), plan(), [None, img("1"), None],
        product_truth_refs=[img("p0"), img("p1")],
    ))

    assert result["overall"] == "FAIL"
    assert [(item["blockId"], item["gate"]) for item in result["outliers"]] == [
        ("b0", "completeness"), ("b2", "completeness")
    ]
    assert result["provider"] is None
    assert result["qcVersion"] == 1


def test_partially_unmapped_product_truth_is_unjudgeable_without_provider_call(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("provider must not run")

    partial_plan = plan()
    partial_plan[1]["productTruthIndexes"] = []
    monkeypatch.setattr(qc, "analyze_with_fallback", should_not_run)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"), partial_plan,
        [img("0"), img("1"), img("2")],
        product_truth_refs=[img("p0"), img("p1")],
    ))

    by_gate = {item["gate"]: item for item in result["gates"]}
    assert result["overall"] == "UNJUDGEABLE"
    assert by_gate["completeness"]["status"] == "PASS"
    assert by_gate["sku_fidelity"]["status"] == "UNJUDGEABLE"
    assert by_gate["target_color"]["status"] == "UNJUDGEABLE"
    assert result["outliers"] == [{
        "blockId": "b1",
        "gate": "sku_fidelity",
        "evidence": "No product-truth reference was mapped to this planned output.",
        "correction": "Map at least one product-truth reference to this cut before publishing.",
    }]
    assert result["provider"] is None
    assert result["qcVersion"] == 1


def test_zero_product_truth_refs_marks_every_block_unjudgeable_without_provider_call(
        monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("provider must not run")

    monkeypatch.setattr(qc, "analyze_with_fallback", should_not_run)
    result = asyncio.run(qc.judge(
        make_settings(gemini_api_key="x"), plan(),
        [img("0"), img("1"), img("2")], product_truth_refs=[],
    ))

    by_gate = {item["gate"]: item for item in result["gates"]}
    assert result["overall"] == "UNJUDGEABLE"
    assert by_gate["completeness"]["status"] == "PASS"
    assert by_gate["sku_fidelity"]["status"] == "UNJUDGEABLE"
    assert by_gate["target_color"]["status"] == "UNJUDGEABLE"
    assert [(item["blockId"], item["gate"]) for item in result["outliers"]] == [
        ("b0", "sku_fidelity"),
        ("b1", "sku_fidelity"),
        ("b2", "sku_fidelity"),
    ]
    assert result["provider"] is None
    assert result["qcVersion"] == 1


def test_product_truth_index_must_exist_in_attached_refs():
    with pytest.raises(qc.PageOutputQCError, match="product_truth_index_out_of_range"):
        qc.build_prompt(plan(), 1)


@pytest.mark.parametrize(
    ("bad_plan", "message"),
    [
        ([], "page_plan_required"),
        ([{**plan()[0], "outputIndex": 2}], "output_indexes_must_be_contiguous"),
        ([plan()[0], {**plan()[1], "blockId": "b0"}], "duplicate_block_id"),
        ([{**plan()[0], "matchingIds": ["x", "x"]}], "duplicate_matching_id"),
        ([{**plan()[0], "productTruthIndexes": [0, 0]}],
         "duplicate_product_truth_index"),
        ([{**plan()[0], "productTruthIndexes": [True]}],
         "invalid_product_truth_indexes"),
        ([{**plan()[0], "productTruthIndexes": ""}],
         "invalid_product_truth_indexes"),
    ],
)
def test_invalid_page_plan_fails_before_model_call(bad_plan, message):
    with pytest.raises(qc.PageOutputQCError, match=message):
        qc.normalize_page_plan(bad_plan)
