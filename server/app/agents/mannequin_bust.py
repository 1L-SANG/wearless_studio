"""Optional, source-backed correction for a confirmed torso under-rendering.

This pass is off by default. It has no fixed cup, multiplier or beauty preset. A gate result may
authorize a correction only when the recognized defect is confirmed at the shared confidence
threshold; missing, unclear and failed assessments never authorize an autonomous edit.
"""

from . import edit_gate
from .prompts import load_bust_gate_prompt_template
from .vision_llm import analyze_with_fallback

GATE_SKIP_CONFIDENCE = edit_gate.GATE_SKIP_CONFIDENCE
_GATE_VERDICTS = ("adequate", "insufficient", "unclear")
_CHEST_COVERING = {"top", "outer", "dress"}


def gate_schema() -> dict:
    return edit_gate.schema(_GATE_VERDICTS)


def validate_gate(raw: dict | None) -> dict:
    return edit_gate.validate(raw, _GATE_VERDICTS)


def gate_skips(result: dict) -> bool:
    """Skip unless insufficiency is confidently confirmed."""
    return edit_gate.skips(result, "insufficient")


async def judge_gate(settings, cut_image) -> dict:
    prompt = load_bust_gate_prompt_template()
    model = getattr(settings, "mannequin_bust_gate_model", "") or ""
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [cut_image], gate_schema(),
        models={"gemini": model} if model else None,
        require_complete_envelope=True,
    )
    return validate_gate(raw)


def should_apply(gender: str, mode: str, clothing_type: str | None = None) -> bool:
    if mode != "on" or gender != "women":
        return False
    return clothing_type is None or str(clothing_type).lower() in _CHEST_COVERING


def build_prompt(template: str) -> str:
    """Reject unresolved template tokens rather than sending an ambiguous edit request."""
    if "${" in template:
        leftover = sorted({p.split("}")[0] + "}" for p in template.split("${")[1:]})
        raise ValueError(f"가슴 프롬프트 템플릿에 해결되지 않은 토큰: {leftover}")
    return template
