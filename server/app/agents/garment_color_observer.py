"""Optional color-region observation, isolated from the fixed AG-01 evidence inputs."""
import asyncio
from pathlib import Path
import time

import httpx
from PIL import Image

from . import garment_color_evidence, vision_llm
from .gemini_image import InlineImage, run_cpu_bound

_PROMPTS = Path(__file__).parents[2] / "prompts"
_DEVELOPER_PROMPT = _PROMPTS / "garment_color_regions_v2.dev.txt"
_USER_PROMPT = _PROMPTS / "garment_color_regions_v2.user.txt"


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
    system_prompt = _DEVELOPER_PROMPT.read_text(encoding="utf-8")
    prompt = _USER_PROMPT.read_text(encoding="utf-8").replace("${clothingType}", category).replace("${sourceMap}", mapping)
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"regions": garment_color_evidence.regions_schema()}, "required": ["regions"]}
    model = settings.model_garment_color_observer
    # As with the last provider in analyze_with_fallback, an open breaker never
    # skips the only provider. Keep cooldown release and success/failure accounting.
    vision_llm._breaker.should_skip("gpt", model, has_next=False)
    metadata = {}
    started = time.perf_counter()
    try:
        # The measurement caller still owns the shorter analysis_aux timeout.
        raw = await vision_llm._call_gpt(
            settings, model, prompt, images, schema, settings.analysis_timeout_seconds,
            metadata=metadata, system_prompt=system_prompt, reasoning_effort="none")
    except (vision_llm.VisionError, httpx.HTTPError, TimeoutError) as exc:
        reason = vision_llm._breaker_failure_reason(exc)
        if reason is not None:
            vision_llm._breaker.failure(settings, "gpt", model, reason)
        vision_llm.logger.warning(
            "vision_llm provider failed: %s model=%s %s(%s) after %.1fs images=%d",
            "gpt", model, type(exc).__name__, str(exc)[:200],
            time.perf_counter() - started, len(images), extra={"provider": "gpt", **metadata})
        # Preserve the fallback path's exception type for existing callers.
        raise vision_llm.VisionError("상품 분석에 실패했어요. 잠시 후 다시 시도해 주세요.") from exc
    vision_llm._breaker.success("gpt", model)
    vision_llm.logger.info("garment color observer completed", extra={"provider": "gpt", **metadata})
    return raw.get("regions") if isinstance(raw, dict) else None
