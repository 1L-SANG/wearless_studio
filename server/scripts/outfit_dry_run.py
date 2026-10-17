"""Offline outfit request accounting; no credentials, DB, R2 or provider calls.

Run from server: .venv/bin/python scripts/outfit_dry_run.py [--candidates-per-role 8]
Fixtures use 24 catalog rows per available role, six color groups and local PNGs.
Counts measure the production shortlist, prompt, schema and image preparation.
They are not token counts or quality measurements. To measure a real local export,
call measure_request with a local get_bytes adapter, source bytes and shortlists.
"""
import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import outfit_recommender as agent
from app.agents.vision_llm import _to_gemini_schema
from app.services import outfit


async def measure_request(local_images, source_images, pools, *, context, fixed):
    """Only prepares a request; local_images.get_bytes must read local bytes."""
    request = await agent.build_request(local_images, source_images, pools, context=context, fixed=fixed)
    sizes = []
    for image in request["images"]:
        with Image.open(BytesIO(image.data)) as decoded:
            sizes.append(decoded.size)
    # Same schema conversion as _call_gemini, without creating a client or sending.
    schema_text = json.dumps(_to_gemini_schema(request["schema"]), ensure_ascii=False, separators=(",", ":"))
    prompt_chars = len(request["prompt"])
    system_chars = len(request["system_prompt"])
    return {
        "prompt_version": agent.PROMPT_VERSION,
        "candidates_per_role": {role: len(pool) for role, pool in pools.items()},
        "image_count": len(sizes),
        "product_image": {"width": sizes[0][0], "height": sizes[0][1]},
        "candidate_image_sizes": [{"width": w, "height": h} for w, h in sorted(set(sizes[1:]))],
        "image_max_edges": {"product": agent.PRODUCT_IMAGE_MAX_EDGE, "candidate": agent.CANDIDATE_IMAGE_MAX_EDGE},
        "user_prompt_chars": prompt_chars,
        "system_prompt_chars": system_chars,
        "prompt_chars": prompt_chars + system_chars,
        "schema_chars": len(schema_text),
        "max_output_tokens": agent.MAX_OUTPUT_TOKENS,
    }


def _png(size):
    buffer = BytesIO()
    Image.new("RGBA", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _LocalImages:
    def __init__(self):
        self.thumbnail = _png((1024, 1024))

    def get_bytes(self, key):
        if not key.startswith("fixture/") or not key.endswith("-thumb.png"):
            raise ValueError("fixture_thumbnail_only")
        return self.thumbnail


def _catalog(kinds):
    colors = [("black", "블랙"), ("white", "화이트"), ("gray", "그레이"),
              ("blue", "블루"), ("beige", "베이지"), ("brown", "브라운")]
    items = []
    for kind in kinds:
        for index in range(24):
            color_group, color_name = colors[index % len(colors)]
            items.append({"id": str(uuid5(NAMESPACE_URL, f"outfit-dry-run/{kind}/{index}")),
                "name": f"{color_name} 코디 아이템 {index + 1}", "clothing_type": kind, "category": kind,
                "gender": "unisex", "is_active": True, "is_custom": False,
                "image_asset_id": f"fixture-{kind}-{index}", "image_key": f"fixture/{kind}-{index}.png",
                "thumb_key": f"fixture/{kind}-{index}-thumb.png", "sort_order": index,
                "color_group": color_group, "color_name": color_name, "fit": "regular", "length": "regular",
                "style_tags": [["minimal", "daily"], ["casual", "sporty"], ["classic", "formal"]][index % 3]})
    return items


async def representative_runs(candidates_per_role=6):
    if type(candidates_per_role) is not int or not 2 <= candidates_per_role <= 8:
        raise ValueError("candidates_per_role_must_be_2_to_8")
    scenarios = [
        ("top_bottom_only", "top", "knit", ["bottom"]),
        ("top_all_roles", "top", "shirt", ["bottom", "shoes", "bag", "accessory"]),
        ("outer_all_roles", "outer", "jacket", ["inner", "bottom", "shoes", "bag", "accessory"]),
    ]
    local_images = _LocalImages()
    source = [(_png((768, 1024)), "image/png")]
    results = []
    for name, kind, subtype, kinds in scenarios:
        context = outfit.product_context({"clothingType": kind, "colors": [
            {"id": "base", "name": "네이비", "swatchId": "navy", "isBase": True}]},
            {"subCategory": subtype, "fit": "regular", "styleTags": ["minimal", "daily"],
             "materials": [{"name": "코튼", "ratio": 100}]})
        fixed = outfit.normalize_selections(None, kind)
        pools = outfit.candidate_pools(_catalog(kinds), context, fixed, limit=candidates_per_role)
        measured = await measure_request(local_images, source, pools, context=context, fixed=fixed)
        results.append({"scenario": name, "fixture_catalog_count": 24 * len(kinds), **measured})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-per-role", type=int, choices=range(2, 9), default=6)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(representative_runs(args.candidates_per_role)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
