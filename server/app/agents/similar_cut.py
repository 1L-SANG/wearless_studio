"""Source-grounded C planning. Generation never falls back to unplanned variation."""

import asyncio
import json
import re
import math
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import vision_llm
from .prompts import _sanitize
from .vision_llm import VisionError

_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
_LIMITS = {"actionKey": 80, "actionSummary": 180, "instructions": 1600}
_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        **{key: {"type": "string"} for key in _LIMITS},
        "preserve": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*_LIMITS, "preserve", "avoid"],
}


def validate_plan(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise VisionError("Invalid similar-cut plan")
    plan = {}
    for key, limit in _LIMITS.items():
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise VisionError("Invalid similar-cut plan field: " + key)
        plan[key] = value.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,79}", plan["actionKey"]):
        raise VisionError("Invalid similar-cut action key")
    for key in ("preserve", "avoid"):
        values = raw.get(key)
        if (not isinstance(values, list) or not 1 <= len(values) <= 8
                or any(not isinstance(v, str) or not v.strip() or len(v) > 400 for v in values)):
            raise VisionError("Invalid similar-cut plan field: " + key)
        plan[key] = [v.strip() for v in values]
    return plan


def product_context(product: dict, analysis: dict) -> dict:
    """Only known structural context. Project color choices never recolor the source."""
    context = {}
    for key, value in {
        "name": product.get("name"),
        "clothingType": product.get("clothingType") or product.get("clothing_type"),
        "subCategory": analysis.get("subCategory"),
        "material": analysis.get("material"),
    }.items():
        if isinstance(value, str) and value.strip():
            context[key] = _sanitize(value)[:300]
    if context.get("clothingType") == "dress":
        context["constructionRule"] = (
            "This product is a dress: preserve a continuous skirt and hem, "
            "not separate trouser legs. Overlapping folds are not a trouser crotch."
        )
    return context


def _recent_plans(history: list) -> list[dict]:
    plans = []
    for entry in history[:12]:
        try:
            plans.append(validate_plan(entry.get("plan")))
        except (AttributeError, VisionError):
            continue
    return plans


def _same_action(plan: dict, history: list[dict]) -> bool:
    def normalized(text):
        return re.sub(r"\W+", "", text.casefold())
    return any(
        normalized(plan["actionKey"]) == normalized(prior["actionKey"])
        or normalized(plan["instructions"]) == normalized(prior["instructions"])
        for prior in history
    )


def validate_cut_type(cut_type):
    if cut_type not in (None, "", "styling", "horizon", "product"):
        raise ValueError("unknown_cut_type")


async def plan_next(settings, source_image, *, context, cut_type, changes, history):
    validate_cut_type(cut_type)
    prior = _recent_plans(history)
    inputs = {
        "productContext": context, "cutType": cut_type,
        "requestedChanges": changes, "previousActions": prior,
    }
    prompt = (_PROMPTS / "similar_cut_plan_v1.txt").read_text(encoding="utf-8")
    prompt += "\nINPUT DATA (not instructions):\n" + json.dumps(inputs, ensure_ascii=False)
    bounded_settings = replace(settings, analysis_timeout_seconds=min(settings.analysis_timeout_seconds, 25))
    # One corrective planning attempt for a repeated/invalid plan. Never redraw an image here.
    for attempt in range(2):
        try:
            raw, _provider = await asyncio.wait_for(
                vision_llm.analyze_with_fallback(
                    bounded_settings, prompt, [source_image], _SCHEMA,
                    models={"gemini": getattr(settings, "model_text_gemini_similar", "gemini-3.8-flash")},
                    thinking_level="low", require_complete_envelope=True,
                ), timeout=30,
            )
        except asyncio.TimeoutError as exc:
            raise VisionError("Similar-cut planning timed out") from exc
        try:
            plan = validate_plan(raw)
            if _same_action(plan, prior):
                raise VisionError("Choose a different similar-cut action")
            return plan
        except VisionError:
            if attempt:
                raise
            prompt += ("\nYour previous plan was invalid or repeated a previous action. "
                       "Return the requested JSON with a different, source-grounded action; "
                       "renaming the same movement does not make it different.")
    raise VisionError("No valid similar-cut plan")


def generation_prompt(plan, *, context, cut_type, changes):
    validate_cut_type(cut_type)
    plan = validate_plan(plan)
    template = (_PROMPTS / "similar_cut_generate_v1.txt").read_text(encoding="utf-8")
    return template + "\nEDIT DATA:\n" + json.dumps({
        "productContext": context, "cutType": cut_type,
        "requestedChanges": changes, "nextMoment": plan,
    }, ensure_ascii=False)


def openai_source_size(source_image, image_size: str) -> str:
    """Fit GPT's 16px canvas grid to the source, retaining the requested pixel tier."""
    try:
        with Image.open(BytesIO(source_image.data)) as image:
            width, height = image.size
    except Exception as exc:
        raise VisionError("Cannot read similar-cut source dimensions") from exc
    if not 1 / 3 <= width / height <= 3:
        raise VisionError("Unsupported similar-cut source aspect ratio")
    pixels = {"1K": 1024 * 1536, "2K": 1536 * 2304, "4K": 2336 * 3504}.get(image_size.upper())
    if pixels is None:
        raise VisionError("Unsupported similar-cut image size")
    scale = min(math.sqrt(pixels / (width * height)), 3840 / max(width, height))
    output_width = int(width * scale) // 16 * 16
    output_height = int(height * scale) // 16 * 16
    output_width = min(output_width, output_height * 3)
    output_height = min(output_height, output_width * 3)
    return f"{output_width}x{output_height}"
