"""D4 편집 쌍의 새 흠 관측. 판정은 기록만 하며 채택이나 재시도를 결정하지 않는다."""

import asyncio
from pathlib import Path
import time

from . import cut_output_qc
from .prompts import clean_text
from .vision_llm import analyze_with_fallback


_PROMPT = Path(__file__).resolve().parents[2] / "prompts/cut_repair_pair_qc_v1.txt"


def response_schema():
    return {"type": "object", "additionalProperties": False, "properties": {
        "target_resolved": {"type": "boolean"},
        "protected_regions_unchanged": {"type": "boolean"},
        "regression_reasons": {"type": "array", "items": {"type": "string"}},
    }, "required": ["target_resolved", "protected_regions_unchanged", "regression_reasons"]}


def build_prompt(qc_corrections, qc_result=None):
    allowed = cut_output_qc.allowed_repair_instructions(qc_result)
    if (not isinstance(qc_corrections, (tuple, list)) or not 0 < len(qc_corrections) <= 5
            or any(not isinstance(line, str) or not 0 < len(line) <= 240 or line not in allowed for line in qc_corrections)):
        raise ValueError("invalid_qc_corrections")
    context = cut_output_qc.repair_context(qc_result)
    result = cut_output_qc._mapping(qc_result)
    storyboard = cut_output_qc._mapping(cut_output_qc._mapping(result.get("contract")).get("storyboard"))
    plan = [f"{key}={storyboard[key]}" for key, choices in (("shot", cut_output_qc._SHOTS),
            ("direction", cut_output_qc._DIRECTIONS), ("face", cut_output_qc._FACES))
            if isinstance(storyboard.get(key), str) and storyboard[key] in choices]
    values = {"corrections": "\n".join(f"- {line}" for line in qc_corrections),
              "planTargets": "; ".join(plan) or "not supplied; do not guess",
              "poseParts": ", ".join(context["poseParts"]) or "none selected",
              "passedGates": ", ".join(context["passedGates"]) or "all untargeted visible facts"}
    prompt = _PROMPT.read_text().rstrip("\n")
    for key, value in values.items():
        prompt = prompt.replace("${" + key + "}", value)
    return prompt


async def verdict(settings, before, after, *, qc_corrections, qc_result=None):
    if getattr(settings, "cut_repair_pair_qc_mode", "off") != "shadow":
        return None
    started = time.perf_counter()
    model = settings.model_cut_repair_pair_qc
    report = {"version": 1, "mode": "shadow", "model": model}
    try:
        prompt = build_prompt(qc_corrections, qc_result)
        timeout = settings.cut_repair_pair_qc_timeout_seconds
        raw, provider = await asyncio.wait_for(analyze_with_fallback(
            settings, prompt, [before, after], response_schema(), order=["gemini"],
            models={"gemini": model}, thinking_level="low", require_complete_envelope=True,
            timeout_seconds=timeout,
        ), timeout=timeout)
        if (not isinstance(raw, dict) or set(raw) != set(response_schema()["properties"])
                or type(raw["target_resolved"]) is not bool or type(raw["protected_regions_unchanged"]) is not bool
                or not isinstance(raw["regression_reasons"], list)
                or any(not isinstance(reason, str) for reason in raw["regression_reasons"])):
            raise ValueError("invalid_pair_response")
        report.update(status="OBSERVED", provider=provider, target_resolved=raw["target_resolved"],
            protected_regions_unchanged=raw["protected_regions_unchanged"],
            regression_reasons=[text for reason in raw["regression_reasons"][:10] if (text := clean_text(reason, 240))])
    except Exception:
        report.update(status="UNAVAILABLE", provider=None, error="comparison_unavailable")
    report["elapsedMs"] = max(0, round((time.perf_counter() - started) * 1000))
    return report
