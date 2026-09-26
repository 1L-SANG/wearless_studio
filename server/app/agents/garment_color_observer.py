"""Optional color-region observation, isolated from the fixed AG-01 evidence inputs."""
import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import garment_color_evidence
from .gemini_image import InlineImage
from .vision_llm import analyze_with_fallback

_PROMPT = Path(__file__).parents[2] / "prompts" / "garment_color_regions_v1.txt"


def _observer_image(data: bytes, mime: str) -> InlineImage | None:
    normalized, normalized_mime = garment_color_evidence.normalize_for_vision(data, mime)
    try:
        with Image.open(BytesIO(normalized)) as source:
            if source.width * source.height > garment_color_evidence.MAX_IMAGE_PIXELS:
                return None
            source.verify()
        with Image.open(BytesIO(normalized)) as source:
            if max(source.size) <= 1024:
                return InlineImage(normalized_mime, normalized)
            image = source.convert("RGB")
            image.thumbnail((1024, 1024))
            out = BytesIO()
            image.save(out, format="PNG")
            return InlineImage("image/png", out.getvalue())
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


async def observe(settings, sources: list[dict], *, product: dict | None = None):
    if not sources:
        return []
    images = await asyncio.gather(*(
        asyncio.to_thread(_observer_image, row["data"], row["mime"]) for row in sources
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
