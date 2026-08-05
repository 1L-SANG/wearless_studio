"""Local-only matching-outfit anchor experiment.

This tests the production-like condition where the selected mannequin already
contains both the main product and the seller-selected coordinating garment.
It never reads or writes DB/R2.  Output stays under gitignored ``server/ab_out``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402


OUT = SERVER / "ab_out/runtime_cut_pipeline_20260804/diverse/outfit-anchor"
BASE = SERVER / "ab_out/fit_campaign/O04.png"
MATCHING = (
    ROOT
    / "outputs/coor_matching/generated_v2/women_bottom/"
    "05_무난_릴랙스드_플루이드_와이드_레그_트라우저_(그레이)_cos.png"
)
PROMPT = """You are editing one verified mannequin product photograph.

IMAGE 1 — CURRENT MANNEQUIN CUT: the exact canvas and exact MAIN OUTERWEAR.
IMAGE 2 — MATCHING BOTTOM: the exact seller-selected coordinating garment.

Make exactly one narrow edit: dress the SAME mannequin in IMAGE 1 with the exact
MATCHING BOTTOM from IMAGE 2. Preserve IMAGE 1's canvas size, crop, camera,
mannequin, pose, background, floor, lighting, shadows, and MAIN OUTERWEAR pixel
appearance. Do not recolor, reshape, shorten, lengthen, open, close, tuck, relight,
or redesign the outerwear.

The matching trousers must retain IMAGE 2's exact gray color, fluid material,
double front pleats, waistband and belt loops, very wide relaxed straight legs,
full floor-reaching length, hem width, drape and construction. Replace only the
currently bare lower body with those trousers. Keep the mannequin feet plain and
unshod. Add no person, face, hair, accessories, shoes, text, logo or prop.

Return one photorealistic mannequin catalog image, never a collage."""


def _load(path: Path) -> InlineImage:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return InlineImage(mime, path.read_bytes())


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = replace(load_settings(), mannequin_image_size="1K")
    client = GeminiImageClient(settings)
    started = time.perf_counter()
    result = await client.generate_content_image(
        resolve_model(settings, "image_light"),
        PROMPT,
        [_load(BASE), _load(MATCHING)],
        "1K",
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    suffix = ".png" if result.mime == "image/png" else ".jpg"
    output = OUT / f"charcoal_blazer_gray_trouser_flash{suffix}"
    output.write_bytes(result.image)
    audit = {
        "status": "generated",
        "model": resolve_model(settings, "image_light"),
        "imageSize": "1K",
        "inputs": [str(BASE), str(MATCHING)],
        "promptSha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "output": str(output),
        "outputSha256": hashlib.sha256(result.image).hexdigest(),
        "latencyMs": round((time.perf_counter() - started) * 1000),
    }
    (OUT / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
