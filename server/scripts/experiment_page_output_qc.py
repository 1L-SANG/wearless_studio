"""Run two local, read-only page consistency QC probes.

The three images in each scenario are A/B alternatives for one SKU. Treating
them as one page is intentional: it checks whether the page judge detects
cross-cut SKU, target-color, model and matching-garment drift.

No DB or R2 write occurs. The JSON audit stays under gitignored ``server/ab_out``.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents import page_output_qc  # noqa: E402
from app.config import load_settings  # noqa: E402


BASE = SERVER / "ab_out/runtime_cut_pipeline_20260804"
OUT = BASE / "page-qc"


def _image(path: Path) -> InlineImage:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return InlineImage(data=path.read_bytes(), mime=mime)


SCENARIOS = {
    "striped_polo": {
        "product_refs": [
            SERVER / "ab_out/fit_campaign/T05.png",
            ROOT / "outputs/coor_matching/generated_v2/men_top/07_폴로_멀티_핀_스트라이프_폴로_셔츠_(블루)_3750.png",
        ],
        "outputs": [
            BASE / "diverse/gemini-pro/striped_polo_top__final_truth_v2__r1.jpg",
            BASE / "prompt-fair/gemini-pro/striped_polo_top__production_profile_v1.jpg",
            BASE / "prompt-fair/gemini-pro/striped_polo_top__candidate_v2.jpg",
        ],
        "clothing": "top", "model": "m1", "matching": ["gray-trouser"],
        "color": "navy-stripe", "closure": None,
    },
    "charcoal_outer": {
        "product_refs": [
            BASE / "diverse/outfit-anchor/charcoal_blazer_gray_trouser_flash.jpg",
            ROOT / "public/assets/fit-examples/outer-any-fit-slim.jpg",
        ],
        "outputs": [
            BASE / "diverse/gemini-pro/charcoal_blazer_outer_outfit_anchor__final_truth_v2__r1.jpg",
            BASE / "prompt-fair/gemini-pro/charcoal_blazer_outer_outfit_anchor__production_profile_v1.jpg",
            BASE / "prompt-fair/gemini-pro/charcoal_blazer_outer_outfit_anchor__candidate_v2.jpg",
        ],
        "clothing": "outer", "model": "w1", "matching": ["gray-trouser"],
        "color": "charcoal", "closure": "closed",
    },
}


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    records = []
    for name, scenario in SCENARIOS.items():
        refs = [_image(path) for path in scenario["product_refs"]]
        outputs = [_image(path) for path in scenario["outputs"]]
        plan = [
            {
                "outputIndex": index,
                "blockId": f"{name}-{index}",
                "targetColor": scenario["color"],
                "modelId": scenario["model"],
                "matchingIds": scenario["matching"],
                "productTruthIndexes": list(range(len(refs))),
                "clothingType": scenario["clothing"],
                "cutType": "styling",
                "outerClosureState": scenario["closure"],
            }
            for index in range(len(outputs))
        ]
        result = await page_output_qc.judge(
            settings, plan, outputs, product_truth_refs=refs
        )
        records.append({
            "scenario": name,
            "productRefs": [str(path) for path in scenario["product_refs"]],
            "outputs": [str(path) for path in scenario["outputs"]],
            "result": result,
        })
    destination = OUT / "run_audit.json"
    destination.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
