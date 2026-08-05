"""Independent final QC for a generated detail-page cut series."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Sequence

from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback


_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "page_output_qc_v1.txt")

OVERALLS = ("PASS", "FAIL", "UNJUDGEABLE")
STATUSES = ("PASS", "FAIL", "UNJUDGEABLE", "NOT_APPLICABLE")
GATES = (
    "sku_fidelity", "target_color", "model_continuity", "matching_continuity",
    "outer_inner_continuity", "space_continuity", "completeness",
)
MAX_OUTPUTS, MAX_PRODUCT_REFS, MAX_MATCHING_IDS = 24, 8, 4
MAX_GATE_EVIDENCE, MAX_OUTLIERS = 4, 32
MAX_ID, MAX_COLOR, MAX_EVIDENCE, MAX_CORRECTION = 120, 80, 240, 300
_ALWAYS = {"sku_fidelity", "target_color", "completeness"}
_CLOTHING_TYPES = {"top", "bottom", "outer", "dress"}
_CUT_TYPES = {"styling", "horizon", "mirror", "product"}
_WORN_CUTS = {"styling", "horizon", "mirror"}
_OUTER_EXPOSED = {"open", "partial"}


class PageOutputQCError(ValueError):
    pass


def _text(value, limit, field, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise PageOutputQCError(f"invalid_{field}")
    text = clean_text(value, limit + 1)
    if (not text and not optional) or len(text) > limit:
        raise PageOutputQCError(f"invalid_{field}")
    return text or None


def normalize_page_plan(page_plan: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(page_plan, (list, tuple)) or not page_plan:
        raise PageOutputQCError("page_plan_required")
    if len(page_plan) > MAX_OUTPUTS:
        raise PageOutputQCError("too_many_outputs")
    out = []
    for item in page_plan:
        if not isinstance(item, Mapping):
            raise PageOutputQCError("invalid_page_plan_item")
        index = item.get("outputIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise PageOutputQCError("invalid_output_index")
        matching = item.get("matchingIds") or []
        if not isinstance(matching, (list, tuple)) or len(matching) > MAX_MATCHING_IDS:
            raise PageOutputQCError("invalid_matching_ids")
        matching = [_text(value, MAX_ID, "matching_id") for value in matching]
        if len(set(matching)) != len(matching):
            raise PageOutputQCError("duplicate_matching_id")
        truth_indexes = item.get("productTruthIndexes")
        if truth_indexes is None:
            truth_indexes = []
        if (not isinstance(truth_indexes, (list, tuple))
                or len(truth_indexes) > MAX_PRODUCT_REFS
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or not 0 <= value < MAX_PRODUCT_REFS for value in truth_indexes)):
            raise PageOutputQCError("invalid_product_truth_indexes")
        if len(set(truth_indexes)) != len(truth_indexes):
            raise PageOutputQCError("duplicate_product_truth_index")
        row = {
            "outputIndex": index,
            "blockId": _text(item.get("blockId"), MAX_ID, "block_id"),
            "targetColor": _text(
                item.get("targetColor") if item.get("targetColor") is not None else "base",
                MAX_COLOR, "target_color",
            ),
            "modelId": _text(item.get("modelId"), MAX_ID, "model_id", True),
            "matchingIds": matching,
            "productTruthIndexes": sorted(truth_indexes),
        }
        clothing_type = item.get("clothingType")
        cut_type = item.get("cutType")
        if clothing_type not in _CLOTHING_TYPES:
            raise PageOutputQCError("invalid_clothing_type")
        if cut_type not in _CUT_TYPES:
            raise PageOutputQCError("invalid_cut_type")
        row["clothingType"] = clothing_type
        row["cutType"] = cut_type
        closure = item.get("outerClosureState")
        if closure is not None and closure not in {"open", "partial", "closed"}:
            raise PageOutputQCError("invalid_outer_closure")
        if clothing_type == "outer" and cut_type in _WORN_CUTS:
            row["outerClosureState"] = closure or "open"
        else:
            row["outerClosureState"] = None
        if cut_type == "product":
            # 저장 데이터에 modelId/matchingIds가 남아 있어도 제품컷은 사람·코디 비교 대상이 아니다.
            row["modelId"] = None
            row["matchingIds"] = []
        space = _text(item.get("spaceGroupId"), MAX_ID, "space_group_id", True)
        if space:
            row["spaceGroupId"] = space
        out.append(row)
    out.sort(key=lambda item: item["outputIndex"])
    if [item["outputIndex"] for item in out] != list(range(len(out))):
        raise PageOutputQCError("output_indexes_must_be_contiguous")
    if len({item["blockId"] for item in out}) != len(out):
        raise PageOutputQCError("duplicate_block_id")
    return out


def _provider_plan(plan, product_ref_count=None):
    """Replace caller-controlled labels with stable equality-only aliases."""
    aliases = {"color": {}, "model": {}, "matching": {}, "space": {}}

    def alias(kind, value, prefix):
        if value is None:
            return None
        return aliases[kind].setdefault(value, f"{prefix}{len(aliases[kind])}")

    provider_plan, block_aliases = [], {}
    for item in plan:
        if product_ref_count is not None and any(
                index >= product_ref_count for index in item["productTruthIndexes"]):
            raise PageOutputQCError("product_truth_index_out_of_range")
        block = f"B{item['outputIndex']}"
        block_aliases[block] = item["blockId"]
        row = {
            "outputIndex": item["outputIndex"],
            "blockId": block,
            "targetColor": alias("color", item["targetColor"], "C"),
            "productTruthIndexes": item["productTruthIndexes"],
            "clothingType": item["clothingType"],
            "cutType": item["cutType"],
            "outerClosureState": item["outerClosureState"],
            "modelId": alias("model", item["modelId"], "M"),
            "matchingIds": [alias("matching", value, "G")
                            for value in item["matchingIds"]],
        }
        if item.get("spaceGroupId"):
            row["spaceGroupId"] = alias("space", item["spaceGroupId"], "S")
        provider_plan.append(row)
    return provider_plan, block_aliases


def build_prompt(page_plan: Sequence[Mapping[str, Any]], product_ref_count: int) -> str:
    plan = normalize_page_plan(page_plan)
    if isinstance(product_ref_count, bool) or not isinstance(product_ref_count, int) \
            or not 0 <= product_ref_count <= MAX_PRODUCT_REFS:
        raise PageOutputQCError("invalid_product_ref_count")
    provider_plan, _ = _provider_plan(plan, product_ref_count)
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    return (prompt.replace("${productRefCount}", str(product_ref_count))
            .replace("${outputCount}", str(len(plan)))
            .replace("${pagePlan}", json.dumps(provider_plan, ensure_ascii=False, indent=2)))


def schema() -> dict[str, Any]:
    gate = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "gate": {"type": "string", "enum": list(GATES)},
            "status": {"type": "string", "enum": list(STATUSES)},
            "evidence": {"type": "array", "maxItems": MAX_GATE_EVIDENCE,
                         "items": {"type": "string", "maxLength": MAX_EVIDENCE}},
            "correction": {"type": ["string", "null"], "maxLength": MAX_CORRECTION},
        },
        "required": ["gate", "status", "evidence", "correction"],
    }
    outlier = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "blockId": {"type": "string", "maxLength": MAX_ID},
            "gate": {"type": "string", "enum": list(GATES)},
            "evidence": {"type": "string", "maxLength": MAX_EVIDENCE},
            "correction": {"type": "string", "maxLength": MAX_CORRECTION},
        },
        "required": ["blockId", "gate", "evidence", "correction"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "overall": {"type": "string", "enum": list(OVERALLS)},
            "gates": {"type": "array", "maxItems": len(GATES), "items": gate},
            "outliers": {"type": "array", "maxItems": MAX_OUTLIERS, "items": outlier},
        },
        "required": ["overall", "gates", "outliers"],
    }


def _repeat_blocks(plan, field):
    groups = {}
    for item in plan:
        if field in {"modelId", "matchingIds"} and item["cutType"] not in _WORN_CUTS:
            continue
        value = tuple(sorted(item[field])) if field == "matchingIds" else item.get(field)
        if value:
            groups.setdefault(value, []).append(item["blockId"])
    return [block for group in groups.values() if len(group) > 1 for block in group]


def _affected(plan, gate):
    fields = {"model_continuity": "modelId", "matching_continuity": "matchingIds",
              "space_continuity": "spaceGroupId"}
    if gate in fields:
        return _repeat_blocks(plan, fields[gate])
    if gate == "outer_inner_continuity":
        return [item["blockId"] for item in plan if _outer_inner_applicable(item)]
    return [x["blockId"] for x in plan]


def _outer_inner_applicable(item):
    return (
        item["clothingType"] == "outer"
        and item["cutType"] in _WORN_CUTS
        and item.get("outerClosureState") in _OUTER_EXPOSED
    )


def validate(raw: Mapping[str, Any] | None,
             page_plan: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bound provider output and derive overall status from hard gates, fail-closed."""
    plan = normalize_page_plan(page_plan)
    raw = raw if isinstance(raw, Mapping) else {}
    applicable = set(_ALWAYS)
    for gate, field in (("model_continuity", "modelId"),
                        ("matching_continuity", "matchingIds"),
                        ("space_continuity", "spaceGroupId")):
        if _repeat_blocks(plan, field):
            applicable.add(gate)
    if sum(1 for item in plan if _outer_inner_applicable(item)) >= 2:
        applicable.add("outer_inner_continuity")
    items = raw.get("gates") if isinstance(raw.get("gates"), list) else []
    grouped = {gate: [x for x in items if isinstance(x, Mapping) and x.get("gate") == gate]
               for gate in GATES}
    results, ignored_nonapp_adverse = [], False
    for gate in GATES:
        if len(grouped[gate]) != 1:
            result = {"gate": gate, "status": "UNJUDGEABLE",
                      "evidence": ["The judge omitted or duplicated this gate."],
                      "correction": f"Re-run page QC for {gate}."}
        else:
            item = grouped[gate][0]
            status = item.get("status") if item.get("status") in STATUSES else "UNJUDGEABLE"
            evidence_raw = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            evidence = [x for x in (clean_text(v, MAX_EVIDENCE)
                                    for v in evidence_raw[:MAX_GATE_EVIDENCE]) if x]
            correction = clean_text(item.get("correction"), MAX_CORRECTION) or None
            if status in {"PASS", "NOT_APPLICABLE"}:
                correction = None
            elif not correction:
                correction = f"Regenerate or inspect the affected cut for {gate}."
            result = {"gate": gate, "status": status,
                      "evidence": evidence, "correction": correction}
        if gate not in applicable:
            ignored_nonapp_adverse |= result["status"] in {"FAIL", "UNJUDGEABLE"}
            result = {"gate": gate, "status": "NOT_APPLICABLE",
                      "evidence": [], "correction": None}
        elif result["status"] == "NOT_APPLICABLE":
            result.update(status="UNJUDGEABLE",
                          evidence=[*result["evidence"],
                                    "The page plan makes this hard gate applicable."]
                          [:MAX_GATE_EVIDENCE],
                          correction=f"Inspect every applicable cut for {gate}.")
        results.append(result)
    statuses = {item["status"] for item in results}
    overall = "FAIL" if "FAIL" in statuses else (
        "UNJUDGEABLE" if "UNJUDGEABLE" in statuses else "PASS")
    if (raw.get("overall") not in OVERALLS
            or (raw.get("overall") != overall and overall == "PASS"
                and not ignored_nonapp_adverse)):
        item = next(x for x in results if x["gate"] == "completeness")
        item.update(status="UNJUDGEABLE",
                    evidence=["Overall and gate results conflict or overall is invalid."],
                    correction="Re-run page QC with the complete inputs.")
        overall = "UNJUDGEABLE"

    _, block_aliases = _provider_plan(plan)
    bad_gates = {item["gate"] for item in results
                 if item["status"] in {"FAIL", "UNJUDGEABLE"}}
    outliers, seen = [], set()
    raw_outliers = raw.get("outliers") if isinstance(raw.get("outliers"), list) else []
    for item in raw_outliers[:MAX_OUTLIERS]:
        if not isinstance(item, Mapping):
            continue
        block = block_aliases.get(clean_text(item.get("blockId"), MAX_ID))
        gate = item.get("gate")
        if block is None or gate not in bad_gates or (block, gate) in seen:
            continue
        seen.add((block, gate))
        outliers.append({"blockId": block, "gate": gate,
                         "evidence": clean_text(item.get("evidence"), MAX_EVIDENCE)
                         or f"Outlier for {gate}.",
                         "correction": clean_text(item.get("correction"), MAX_CORRECTION)
                         or f"Inspect this cut for {gate}."})
    for result in results:
        gate = result["gate"]
        if gate not in bad_gates or any(x["gate"] == gate for x in outliers):
            continue
        block = (_affected(plan, gate) or [plan[0]["blockId"]])[0]
        outliers.append({"blockId": block, "gate": gate,
                         "evidence": (result["evidence"] or [f"Could not pass {gate}."])[0],
                         "correction": result["correction"] or f"Inspect this cut for {gate}."})
    return {"overall": overall, "gates": results, "outliers": outliers[:MAX_OUTLIERS]}


async def judge(settings, page_plan, generated_images, *, product_truth_refs=()):
    plan, generated, refs = normalize_page_plan(page_plan), list(generated_images), list(product_truth_refs)
    if (len(refs) > MAX_PRODUCT_REFS
            or any(not isinstance(x, InlineImage) for x in refs)
            or any(x is not None and not isinstance(x, InlineImage) for x in generated)):
        raise PageOutputQCError("invalid_images")
    missing_indexes = [index for index, image in enumerate(generated[:len(plan)])
                       if image is None]
    if len(generated) < len(plan):
        missing_indexes.extend(range(len(generated), len(plan)))
    elif len(generated) > len(plan):
        missing_indexes.append(len(plan) - 1)
    missing_indexes = sorted(set(missing_indexes))
    if len(generated) != len(plan) or missing_indexes:
        count_evidence = f"Expected {len(plan)} outputs but received {len(generated)}."
        if len(generated) == len(plan):
            count_evidence = "Generated images are missing at output indexes " + \
                ", ".join(str(index) for index in missing_indexes) + "."
        raw = {"overall": "FAIL", "gates": [
            {"gate": gate, "status": "FAIL" if gate == "completeness" else "UNJUDGEABLE",
             "evidence": [count_evidence],
             "correction": "Generate every planned cut exactly once."} for gate in GATES], "outliers": []}
        result = validate(raw, plan)
        result["outliers"] = [{"blockId": plan[index]["blockId"], "gate": "completeness",
                               "evidence": f"No generated image was mapped to output index {index}.",
                               "correction": "Generate this planned cut before publishing the page."}
                              for index in missing_indexes]
        result.update({"provider": None, "qcVersion": 1})
        return result
    unmapped_truth_indexes = [item["outputIndex"] for item in plan
                              if not refs or not item["productTruthIndexes"]]
    if unmapped_truth_indexes:
        evidence = "Product-truth evidence was not mapped to every planned output."
        raw = {"overall": "UNJUDGEABLE", "gates": [
            {"gate": gate, "status": "PASS" if gate == "completeness" else "UNJUDGEABLE",
             "evidence": (["Every planned output has a generated image."]
                          if gate == "completeness" else [evidence]),
             "correction": (None if gate == "completeness"
                            else "Map product-truth evidence to every planned output.")}
            for gate in GATES], "outliers": []}
        result = validate(raw, plan)
        result["outliers"] = [
            {"blockId": plan[index]["blockId"], "gate": "sku_fidelity",
             "evidence": "No product-truth reference was mapped to this planned output.",
             "correction": "Map at least one product-truth reference to this cut before publishing."}
            for index in unmapped_truth_indexes
        ]
        result.update({"provider": None, "qcVersion": 1})
        return result
    _provider_plan(plan, len(refs))
    provider = None
    try:
        raw, provider = await analyze_with_fallback(
            settings,
            build_prompt(plan, len(refs)),
            [*refs, *generated],
            schema(),
            thinking_level="low",
        )
    except VisionError:
        raw = {"overall": "UNJUDGEABLE", "gates": [
            {"gate": gate, "status": "UNJUDGEABLE",
             "evidence": ["The independent page judge was unavailable."],
             "correction": "Re-run page QC before publishing."} for gate in GATES], "outliers": []}
    result = validate(raw, plan)
    result.update({"provider": provider, "qcVersion": 1})
    return result
