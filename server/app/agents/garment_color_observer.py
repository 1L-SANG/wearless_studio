"""Optional color-region observation, isolated from the fixed AG-01 evidence inputs."""
import asyncio
from pathlib import Path

from PIL import Image

from . import garment_color_evidence
from .gemini_image import InlineImage, run_cpu_bound
from .vision_llm import analyze_with_fallback

_PROMPT = Path(__file__).parents[2] / "prompts" / "garment_color_regions_v1.txt"


def _observer_image(data: bytes, mime: str) -> InlineImage | None:
    # One decode: the 1024 upright sRGB frame is sent as is. Oversize, broken or
    # unconvertible photos cannot be measured either, so they are not observed.
    try:
        frame, frame_mime = garment_color_evidence.vision_frame(data, mime, max_side=1024)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return InlineImage(frame_mime, frame)


async def observe(settings, sources: list[dict], *, product: dict | None = None):
    if not sources:
        return []
    images = await asyncio.gather(*(
        run_cpu_bound(_observer_image, row["data"], row["mime"]) for row in sources
    ))
    eligible = [(source, image) for source, image in zip(sources, images, strict=True) if image is not None]
    if not eligible:
        return []
    sources, images = map(list, zip(*eligible, strict=True))
    category = (product or {}).get("clothingType") or (product or {}).get("clothing_type")
    category = category if category in {"top", "bottom", "outer", "dress"} else "unknown"
    mapping = "\n".join(f"Image {index + 1}: sourceIndex={row['sourceIndex']}; slot={row['slot']}"
                        for index, row in enumerate(sources))
    prompt = _PROMPT.read_text(encoding="utf-8").replace("${clothingType}", category).replace("${sourceMap}", mapping)
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"regions": garment_color_evidence.regions_schema()}, "required": ["regions"]}
    models = ({"gemini": settings.model_text_gemini_analysis} if settings.model_text_gemini_analysis else None)
    raw, _provider = await analyze_with_fallback(settings, prompt, images, schema, models=models)
    return raw.get("regions") if isinstance(raw, dict) else None
