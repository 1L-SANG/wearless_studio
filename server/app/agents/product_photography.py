"""Shared, deterministic product-photo instructions. Importing never calls a provider."""
from functools import lru_cache
from pathlib import Path
import re


PRESENTATIONS = ("ghost", "flat", "natural_light")
_TEMPLATE = Path(__file__).resolve().parents[2] / "prompts" / "product_photography_v1.txt"
_MODEL_SECTIONS = {
    "gemini-nano-banana-2.1": "nano_banana",
    "gemini-3-pro-image": "gemini_pro",
    "gpt-image-2.5-flare": "flare",
    "gpt-image-2.5-sunburst": "sunburst",
}


def normalize(value, *, cut_type="product", shot="ghost") -> str | None:
    if cut_type == "product" and shot == "ghost" and isinstance(value, str) and value in PRESENTATIONS:
        return value
    return None


@lru_cache(maxsize=1)
def _sections() -> dict[str, str]:
    text = _TEMPLATE.read_text(encoding="utf-8")
    parts = re.split(r"^\[\[([^\]]+)\]\]\s*\n", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def render_prompt(presentation: str, *, model: str | None = None,
                  clothing_type: str | None = None) -> str:
    """Return the same source/mode contract for production and offline benchmarks.

    Model adapters are prompting hypotheses, not measured quality claims. Unknown
    model IDs use the common contract and are never interpolated into the prompt.
    """
    if normalize(presentation) is None:
        raise ValueError("invalid_product_presentation")
    sections = _sections()
    blocks = [f"PRODUCT PRESENTATION: {presentation}", sections["COMMON"]]
    if clothing_type in ("top", "bottom", "outer", "dress"):
        blocks.append(f"GARMENT TYPE: {clothing_type}\n{sections[f'TYPE:{clothing_type}']}")
    blocks.append(sections[presentation])
    adapter = _MODEL_SECTIONS.get(model) if isinstance(model, str) else None
    if adapter:
        blocks.append(sections[f"MODEL:{adapter}"])
    return "\n\n".join(blocks)
