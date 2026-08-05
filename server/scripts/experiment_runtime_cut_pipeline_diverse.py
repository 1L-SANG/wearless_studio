"""Diverse real-fixture AG-06 A/B experiment (local outputs only).

The production DB registry may be unavailable after a project reset.  These
fixtures are retained product-truth/mannequin pairs from the repository's real
fit-fidelity campaign, not unrelated stock images.  Each arm calls the same
``cut_generator.generate`` path as the service and keeps input ordering fixed.

No DB/R2 write occurs.  Outputs stay under the gitignored ``server/ab_out``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts import experiment_runtime_cut_pipeline as base  # noqa: E402

from app.agents import cut_generator  # noqa: E402
from app.agents.gemini_image import GeminiImageClient  # noqa: E402
from app.config import load_settings  # noqa: E402


OUT = base.OUT / "diverse"
GEN = ROOT / "outputs/coor_matching/generated_v2"
FIT = ROOT / "public/assets/fit-examples"
RELEASE = ROOT / "reference/genexamples/individual_release"

WOMEN_IDENTITY = (
    ROOT / "public/models/women/w1.webp",
    ROOT / "spike/runs/facepack-w1v2-2026-07-13T23-49-24/grid-fullbody.png",
)
MEN_IDENTITY = (
    ROOT / "public/models/men/m1.webp",
    ROOT / "spike/runs/facepack-m1v2-2026-07-13T23-55-07/grid-fullbody.png",
)
GRAY_TROUSER = (
    GEN / "women_bottom/05_무난_릴랙스드_플루이드_와이드_레그_트라우저_(그레이)_cos.png"
)
WHITE_TOP = GEN / "women_top/04_무난_페이퍼_코튼_셔츠_(화이트)_2670.png"


FIXTURES = {
    "striped_polo_top": {
        "mannequin": SERVER / "ab_out/fit_campaign/T05.png",
        "identity": MEN_IDENTITY,
        "productImages": [
            GEN / "men_top/07_폴로_멀티_핀_스트라이프_폴로_셔츠_(블루)_3750.png"
        ],
        "matching": GRAY_TROUSER,
        "exampleId": "ex_styling_men_top_medium_snapshot_03",
        "example": RELEASE / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_men_top_medium_snapshot_03.png",
        "product": {
            "name": "멀티 핀 스트라이프 폴로 셔츠",
            "clothingType": "top",
            "colors": [{"id": "stripe", "name": "navy stripe", "isBase": True,
                        "images": [{"slot": "Front", "id": "fixture-front"}]}],
        },
        "analysis": {
            "subCategory": "short-sleeve striped polo shirt", "targetGenders": ["men"],
            "materials": [{"name": "cotton knit"}],
            "sellingPoints": ["fine multicolor horizontal stripes", "dark ribbed polo collar", "three-button placket"],
            "fitProfile": {
                "category": "top", "gender": "men",
                "axes": {"fit": "slim", "length": "basic"},
                "matchCut": "wide", "source": "seller", "version": 1,
            },
        },
        "spec": {"cutType": "styling", "direction": "front", "shot": "medium"},
    },
    "washed_denim_bottom": {
        "mannequin": SERVER / "ab_out/fit_campaign/P02.png",
        "identity": WOMEN_IDENTITY,
        "productImages": [
            GEN / "women_bottom/03_무난_켄톤_플라이_스트레이트_데님_팬츠_(워시드블루)_3283.png"
        ],
        "matching": WHITE_TOP,
        "exampleId": "ex_styling_women_bottom_full_cafe_shop_04",
        "example": RELEASE / "2026-08-03-individual-styling/asset_root/assets/all/ex_styling_women_bottom_full_cafe_shop_04.png",
        "product": {
            "name": "워시드 블루 스트레이트 데님 팬츠",
            "clothingType": "bottom",
            "colors": [{"id": "washed-blue", "name": "washed blue", "isBase": True,
                        "images": [{"slot": "Front", "id": "fixture-front"}]}],
        },
        "analysis": {
            "subCategory": "full-length washed denim pants", "targetGenders": ["women"],
            "materials": [{"name": "denim"}],
            "sellingPoints": ["washed blue color", "five-pocket construction", "visible fly and waistband hardware"],
            "fitProfile": {
                "category": "pants", "gender": "women",
                "axes": {"cut": "wide", "length": "below_ankle"},
                "source": "seller", "version": 1,
            },
        },
        "spec": {"cutType": "styling", "direction": "front", "shot": "full"},
    },
    "charcoal_blazer_outer": {
        "mannequin": SERVER / "ab_out/fit_campaign/O04.png",
        "identity": WOMEN_IDENTITY,
        "productImages": [FIT / "outer-any-fit-slim.jpg"],
        "matching": GRAY_TROUSER,
        "exampleId": "ex_styling_women_outer_full_rain_street_01",
        "example": RELEASE / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_outer_full_rain_street_01.png",
        "product": {
            "name": "차콜 싱글 브레스티드 롱 블레이저",
            "clothingType": "outer",
            "colors": [{"id": "charcoal", "name": "charcoal", "isBase": True,
                        "images": [{"slot": "Front", "id": "fixture-front"}]}],
        },
        "analysis": {
            "subCategory": "single-breasted long blazer", "targetGenders": ["women"],
            "materials": [{"name": "wool blend"}],
            "sellingPoints": ["notched lapel", "single front button", "two flap pockets", "long tailored silhouette"],
            "fitProfile": {
                "category": "outer", "gender": "women",
                "axes": {"fit": "slim", "length": "long"},
                "matchCut": "wide", "source": "seller", "version": 1,
            },
        },
        "spec": {"cutType": "styling", "direction": "front", "shot": "full",
                 "outerClosureState": "closed"},
    },
    "black_fit_flare_dress": {
        "mannequin": SERVER / "ab_out/fit_campaign/D02.png",
        "identity": WOMEN_IDENTITY,
        "productImages": [FIT / "dress-women-silhouette-fit_and_flare.jpg"],
        "matching": None,
        "exampleId": "ex_styling_women_dress_full_home_04",
        "example": RELEASE / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_dress_full_home_04.png",
        "product": {
            "name": "블랙 핏앤플레어 롱 원피스",
            "clothingType": "dress",
            "colors": [{"id": "black", "name": "black", "isBase": True,
                        "images": [{"slot": "Front", "id": "fixture-front"}]}],
        },
        "analysis": {
            "subCategory": "short-sleeve fit-and-flare long dress", "targetGenders": ["women"],
            "materials": [{"name": "woven fabric"}],
            "sellingPoints": ["plain black fabric", "fitted waist", "long flared skirt", "round neckline"],
            "fitProfile": {
                "category": "dress", "gender": "women",
                "axes": {"length": "long", "silhouette": "fit_and_flare"},
                "source": "seller", "version": 1,
            },
        },
        "spec": {"cutType": "styling", "direction": "front", "shot": "full"},
    },
}

# Production-like follow-up: the selected mannequin already wears the seller's
# representative matching garment. The normal cut path still receives MATCHING
# separately because per-card authority and QC require the explicit truth source.
FIXTURES["charcoal_blazer_outer_outfit_anchor"] = {
    **FIXTURES["charcoal_blazer_outer"],
    "mannequin": (
        OUT / "outfit-anchor/charcoal_blazer_gray_trouser_flash.jpg"
    ),
}


LEAN_PROFILE = {
    "poseDynamics": "reference_kinematics",
    "capture": "phone_snapshot",
    "light": "reference_integrated",
}


def _spec(fixture: dict) -> dict:
    spec = {
        **fixture["spec"], "colorId": fixture["product"]["colors"][0]["id"],
        "pose": "auto", "faceExposure": "same",
        "matchIds": ["fixture-match"] if fixture.get("matching") else [],
        "exampleId": fixture["exampleId"], "refScope": "all",
        "modelId": "mB" if fixture["analysis"]["targetGenders"][0] == "men" else "mA",
    }
    return spec


def _inputs(fixture: dict):
    paths = [fixture["mannequin"], *fixture["identity"], *fixture["productImages"]]
    if fixture.get("matching"):
        paths.append(fixture["matching"])
    paths.append(fixture["example"])
    product_assets = [
        {"slot": image["slot"]}
        for image in fixture["product"]["colors"][0]["images"]
    ]
    manifest = cut_generator.build_manifest(
        product_assets,
        has_mannequin=True,
        has_model_face=True,
        has_model_sheet=True,
        has_match=bool(fixture.get("matching")),
        mood_count=0,
        example_scope="all",
    )
    return [base._load(path) for path in paths], manifest, [str(path) for path in paths]


def _imagegen_inputs(fixture: dict):
    """Return the closest valid input contract for the built-in image tool.

    The tool accepts at most five references.  Styling fixtures with matching
    clothes need six in production, so the secondary MODEL SHEET is omitted while
    keeping the face identity, product truth, matching garment and example.
    The prompt manifest is rebuilt to match that exact order; a six-item manifest
    must never be paired with five images.
    """

    include_model_sheet = (
        1  # mannequin
        + 2  # face + model sheet
        + len(fixture["productImages"])
        + (1 if fixture.get("matching") else 0)
        + 1  # example
        <= 5
    )
    paths = [fixture["mannequin"], fixture["identity"][0]]
    if include_model_sheet:
        paths.append(fixture["identity"][1])
    paths.extend(fixture["productImages"])
    if fixture.get("matching"):
        paths.append(fixture["matching"])
    paths.append(fixture["example"])
    product_assets = [
        {"slot": image["slot"]}
        for image in fixture["product"]["colors"][0]["images"]
    ]
    manifest = cut_generator.build_manifest(
        product_assets,
        has_mannequin=True,
        has_model_face=True,
        has_model_sheet=include_model_sheet,
        has_match=bool(fixture.get("matching")),
        mood_count=0,
        example_scope="all",
    )
    return [base._load(path) for path in paths], manifest, [str(path) for path in paths]


def prepare(names: list[str], arms: list[str]) -> list[dict]:
    prompt_dir = OUT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name in names:
        fixture = FIXTURES[name]
        images, manifest, paths = _inputs(fixture)
        for arm in arms:
            # Fix-labeled arms intentionally share the baseline directing profile.
            # Distinct names preserve earlier candidates for visual A/B.
            profile = LEAN_PROFILE if arm == "lean_profile" else None
            prompt = cut_generator.build_prompt(
                _spec(fixture), fixture["product"], analysis=fixture["analysis"],
                manifest=manifest, directing_profile=profile,
            )
            record_id = f"{name}__{arm}__r1"
            prompt_path = prompt_dir / f"{record_id}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            records.append({
                "id": record_id, "fixture": name, "arm": arm,
                "prompt": str(prompt_path), "promptSha256": base._sha(prompt.encode()),
                "directingProfile": profile, "inputs": paths,
                "inputMimes": [image.mime for image in images], "manifest": manifest,
            })
    (OUT / "experiment_manifest.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def prepare_imagegen(names: list[str], arms: list[str]) -> list[dict]:
    """Write prompts whose manifest exactly matches imagegen's five-ref cap."""

    prompt_dir = OUT / "imagegen-prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name in names:
        fixture = FIXTURES[name]
        images, manifest, paths = _imagegen_inputs(fixture)
        for arm in arms:
            profile = LEAN_PROFILE if arm == "lean_profile" else None
            prompt = cut_generator.build_prompt(
                _spec(fixture), fixture["product"], analysis=fixture["analysis"],
                manifest=manifest, directing_profile=profile,
            )
            record_id = f"{name}__{arm}__imagegen_r1"
            prompt_path = prompt_dir / f"{record_id}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            records.append({
                "id": record_id, "fixture": name, "arm": arm,
                "prompt": str(prompt_path), "promptSha256": base._sha(prompt.encode()),
                "directingProfile": profile, "inputs": paths,
                "inputMimes": [image.mime for image in images], "manifest": manifest,
                "limitation": (
                    None if len(paths) < 5 or "MODEL SHEET" in manifest
                    else "built-in imagegen max 5 refs; MODEL SHEET omitted"
                ),
            })
    (OUT / "imagegen_experiment_manifest.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


async def run(records: list[dict], *, force: bool, image_size: str) -> list[dict]:
    out_dir = OUT / "gemini-pro"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(load_settings(), mannequin_image_size=image_size)
    client = GeminiImageClient(settings)
    results = []
    for record in records:
        existing = list(out_dir.glob(f"{record['id']}.*"))
        if existing and not force:
            results.append({**record, "status": "skipped_existing", "output": str(existing[0])})
            continue
        fixture = FIXTURES[record["fixture"]]
        images, manifest, _paths = _inputs(fixture)
        started = time.perf_counter()
        try:
            data, mime = await cut_generator.generate(
                settings, client, _spec(fixture), fixture["product"], images,
                analysis=fixture["analysis"], manifest=manifest,
                directing_profile=record["directingProfile"],
            )
            output = out_dir / f"{record['id']}{base._suffix(mime)}"
            output.write_bytes(data)
            results.append({
                **record, "status": "generated", "output": str(output),
                "provider": "gemini", "model": settings.model_image_high,
                "imageSize": image_size, "outputMime": mime,
                "outputSha256": base._sha(data),
                "latencyMs": round((time.perf_counter() - started) * 1000),
            })
        except Exception as exc:
            results.append({
                **record, "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
        await asyncio.sleep(1)
    audit_path = out_dir / "run_audit.json"
    prior_records = []
    if audit_path.exists():
        try:
            prior_records = json.loads(audit_path.read_text(encoding="utf-8")).get(
                "records", []
            )
        except (OSError, ValueError, AttributeError):
            prior_records = []
    merged = {
        record.get("id"): record
        for record in prior_records
        if isinstance(record, dict) and record.get("id")
    }
    merged.update({record["id"]: record for record in results})
    audit_path.write_text(
        json.dumps({"records": list(merged.values())}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--prepare-imagegen", action="store_true")
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument("--fixtures", default=",".join(FIXTURES))
    parser.add_argument("--arms", default="baseline,lean_profile")
    parser.add_argument("--image-size", choices=("1K", "2K", "4K"), default="1K")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.prepare_imagegen or args.gemini):
        parser.error("choose --prepare, --prepare-imagegen and/or --gemini")
    names = [value.strip() for value in args.fixtures.split(",") if value.strip()]
    arms = [value.strip() for value in args.arms.split(",") if value.strip()]
    if any(name not in FIXTURES for name in names):
        parser.error("unknown fixture")
    if any(arm not in {
        "baseline", "lean_profile", "face_fix", "face_fix_4k", "leak_fix",
        "anchor_fix", "fit_truth", "final_truth_v2",
    } for arm in arms):
        parser.error("unknown arm")
    records = prepare(names, arms)
    print(f"prepared {len(records)} diverse arms in {OUT}", flush=True)
    if args.prepare_imagegen:
        imagegen_records = prepare_imagegen(names, arms)
        print(f"prepared {len(imagegen_records)} imagegen arms in {OUT}", flush=True)
    if args.gemini:
        if len(records) > args.max_calls:
            raise SystemExit(f"planned Gemini calls {len(records)} exceed --max-calls {args.max_calls}")
        results = await run(records, force=args.force, image_size=args.image_size)
        ok = sum(item["status"] in {"generated", "skipped_existing"} for item in results)
        print(f"Gemini completed {ok}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
