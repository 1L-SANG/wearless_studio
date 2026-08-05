"""Real-input AG-06 generation experiment (local, no DB/R2 writes).

This harness deliberately calls the same ``cut_generator.generate`` path used by
the service.  It compares the current prompt with/without a compact server-owned
directing profile while keeping mannequin, identity, product, matching garment,
and generation example inputs identical.

Outputs stay under ``server/ab_out/runtime_cut_pipeline_20260804`` (gitignored).

Examples:
  cd server
  .venv/bin/python -m scripts.experiment_runtime_cut_pipeline --prepare
  .venv/bin/python -m scripts.experiment_runtime_cut_pipeline --gemini --max-calls 12
  .venv/bin/python -m scripts.experiment_runtime_cut_pipeline --colorway
"""

from __future__ import annotations

import argparse
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

from app.agents import cut_generator, mannequin_colorway  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402


OUT = SERVER / "ab_out" / "runtime_cut_pipeline_20260804"
GENEX = ROOT / "reference/genexamples/individual_release"
POSE_INPUTS = ROOT / "reference/genexamples/prompt-experiments/pose_scope_v2/inputs"

COMMON = {
    "mannequin": SERVER / "ab_out/fit_campaign/T02.png",
    "model_face": ROOT / "public/models/women/w1.webp",
    "model_sheet": ROOT / "spike/runs/facepack-w1v2-2026-07-13T23-49-24/grid-fullbody.png",
    "front": POSE_INPUTS / "product_front.png",
    "back": POSE_INPUTS / "product_back.png",
    "detail": POSE_INPUTS / "product_logo_detail.jpg",
    "matching": ROOT / "outputs/coor_matching/generated_v2/women_bottom/05_무난_릴랙스드_플루이드_와이드_레그_트라우저_(그레이)_cos.png",
}

CASES = {
    "style_03_window": {
        "exampleId": "ex_styling_women_top_medium_snapshot_03",
        "example": GENEX / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_top_medium_snapshot_03.png",
        "cutType": "styling", "direction": "front", "shot": "medium",
        "environment": "indoor window light and terrace threshold",
        "profile": {
            "directionMode": "exact", "poseDynamics": "reference_kinematics",
            "camera": "handheld_oblique", "framing": "reference_crop",
            "capture": "phone_snapshot", "scene": "reference_location",
            "light": "reference_integrated",
        },
    },
    "style_04_wall": {
        "exampleId": "ex_styling_women_top_medium_snapshot_04",
        "example": GENEX / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_top_medium_snapshot_04.png",
        "cutType": "styling", "direction": "front", "shot": "medium",
        "environment": "outdoor wall under soft diffuse light",
        "profile": {
            "directionMode": "exact", "poseDynamics": "natural_asymmetry",
            "camera": "handheld_eye_level", "framing": "reference_crop",
            "capture": "phone_snapshot", "scene": "reference_location",
            "light": "natural_soft",
        },
    },
    "style_05_park": {
        "exampleId": "ex_styling_women_top_medium_snapshot_05",
        "example": GENEX / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_top_medium_snapshot_05.png",
        "cutType": "styling", "direction": "side", "shot": "medium",
        "environment": "outdoor park with foliage bounce and dappled light",
        "profile": {
            "directionMode": "exact", "poseDynamics": "reference_kinematics",
            "camera": "handheld_oblique", "framing": "reference_crop",
            "capture": "phone_snapshot", "scene": "reference_location",
            "light": "reference_integrated",
        },
    },
    "style_06_cafe": {
        "exampleId": "ex_styling_women_top_medium_snapshot_06",
        "example": GENEX / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_styling_women_top_medium_snapshot_06.png",
        "cutType": "styling", "direction": "front", "shot": "medium",
        "environment": "dim cafe with mixed available light and raised arm",
        "profile": {
            "directionMode": "exact", "poseDynamics": "reference_kinematics",
            "camera": "handheld_eye_level", "framing": "reference_crop",
            "capture": "phone_snapshot", "scene": "reference_location",
            "light": "mixed_available",
        },
    },
    "horizon_medium": {
        "exampleId": "ex_horizon_women_top_medium_02",
        "example": GENEX / "2026-08-03-individual-styling/asset_root/assets/all/ex_horizon_women_top_medium_02.png",
        "cutType": "horizon", "direction": "front", "shot": "medium",
        "environment": "neutral gray horizon studio",
        "profile": {
            "directionMode": "exact", "poseDynamics": "controlled_stillness",
            "camera": "reference_geometry", "framing": "reference_crop",
            "capture": "studio_catalog", "scene": "horizon_studio",
            "light": "studio_soft",
        },
    },
    "product_ghost": {
        "exampleId": "ex_product_top_ghost_01",
        "example": GENEX / "2026-08-03-individual-styling-v2/asset_root/assets/all/ex_product_top_ghost_01.png",
        "cutType": "product", "direction": "front", "shot": "ghost",
        "environment": "product-only ghost presentation",
        "profile": {
            "camera": "product_camera", "framing": "product_close",
            "capture": "product_catalog", "scene": "product_studio",
            "light": "product_diffused",
        },
    },
}

PRODUCT = {
    "name": "블루 하트 로고 오버핏 반팔 티셔츠",
    "clothingType": "top",
    "colors": [{
        "id": "blue", "name": "블루", "isBase": True,
        "images": [
            {"slot": "Front", "id": "local-front"},
            {"slot": "Back", "id": "local-back"},
            {"slot": "Detail", "id": "local-detail"},
        ],
    }],
}

ANALYSIS = {
    "clothingType": "top",
    "subCategory": "short-sleeve crew-neck T-shirt",
    "targetGenders": ["women"],
    "materials": [{"name": "cotton", "ratio": 100}],
    "sellingPoints": [
        "black heart-and-letter chest embroidery",
        "relaxed oversized silhouette",
    ],
    "fitProfile": {
        "category": "top", "gender": "women", "source": "seller",
        "axes": {"fit": "over"}, "version": 1,
    },
}


def _sniff(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported_image_bytes")


def _load(path: Path) -> InlineImage:
    data = path.read_bytes()
    return InlineImage(_sniff(data), data)


def _suffix(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_inputs(case: dict) -> tuple[list[InlineImage], str, list[str]]:
    product_assets = [{"slot": slot} for slot in ("Front", "Back", "Detail")]
    if case["cutType"] == "product":
        paths = [COMMON["front"], COMMON["back"], COMMON["detail"], case["example"]]
        manifest = cut_generator.build_manifest(
            product_assets,
            has_mannequin=False,
            has_model_face=False,
            has_model_sheet=False,
            has_match=False,
            mood_count=0,
            example_scope="all",
            example_is_product=True,
        )
    else:
        paths = [
            COMMON["mannequin"], COMMON["model_face"], COMMON["model_sheet"],
            COMMON["front"], COMMON["back"], COMMON["detail"], COMMON["matching"],
            case["example"],
        ]
        manifest = cut_generator.build_manifest(
            product_assets,
            has_mannequin=True,
            has_model_face=True,
            has_model_sheet=True,
            has_match=True,
            mood_count=0,
            example_scope="all",
            example_is_product=False,
        )
    return [_load(path) for path in paths], manifest, [str(path) for path in paths]


def _spec(case: dict) -> dict:
    return {
        "cutType": case["cutType"],
        "direction": case["direction"],
        "shot": case["shot"],
        "colorId": "blue",
        "pose": "auto",
        "faceExposure": "same",
        "matchIds": ["local-gray-trouser"] if case["cutType"] != "product" else [],
        "exampleId": case["exampleId"],
        "refScope": "all",
        "modelId": "w1",
    }


def _selected_cases(raw: str | None) -> list[tuple[str, dict]]:
    if not raw:
        return list(CASES.items())
    wanted = [item.strip() for item in raw.split(",") if item.strip()]
    missing = [item for item in wanted if item not in CASES]
    if missing:
        raise SystemExit(f"unknown cases: {missing}")
    return [(item, CASES[item]) for item in wanted]


def _profile_for_arm(case: dict, arm: str) -> dict | None:
    if arm == "baseline":
        return None
    if arm == "profile":
        return case["profile"]
    # The full seven-axis profile can repeat constraints already expressed by
    # CUT SPEC and the generation example.  This lean arm isolates the three
    # observations that are not otherwise explicit: capture character, subject/
    # scene light integration, and natural/reference body dynamics.
    if arm == "lean_profile":
        return {
            key: value
            for key, value in case["profile"].items()
            if key in {"poseDynamics", "capture", "light"}
        }
    raise ValueError(f"unknown_experiment_arm:{arm}")


def prepare(cases: list[tuple[str, dict]], arms: list[str], replicas: int) -> list[dict]:
    prompt_dir = OUT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for case_id, case in cases:
        images, manifest, paths = _case_inputs(case)
        for arm in arms:
            profile = _profile_for_arm(case, arm)
            prompt = cut_generator.build_prompt(
                _spec(case), PRODUCT, analysis=ANALYSIS, manifest=manifest,
                directing_profile=profile,
            )
            for replica in range(1, replicas + 1):
                stem = f"{case_id}__{arm}__r{replica}"
                (prompt_dir / f"{stem}.txt").write_text(prompt, encoding="utf-8")
                record = {
                    "id": stem,
                    "caseId": case_id,
                    "arm": arm,
                    "replica": replica,
                    "environment": case["environment"],
                    "spec": _spec(case),
                    "directingProfile": profile,
                    "prompt": str(prompt_dir / f"{stem}.txt"),
                    "promptSha256": _sha(prompt.encode()),
                    "inputs": paths,
                    "inputMimes": [image.mime for image in images],
                    "manifest": manifest,
                }
                records.append(record)
    (OUT / "experiment_manifest.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


async def run_gemini(settings, records: list[dict], *, force: bool, concurrency: int) -> list[dict]:
    out_dir = OUT / "gemini-pro"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = GeminiImageClient(settings)
    sem = asyncio.Semaphore(concurrency)

    async def one(record: dict) -> dict:
        existing = list(out_dir.glob(f"{record['id']}.*"))
        if existing and not force:
            return {**record, "provider": "gemini", "status": "skipped_existing", "output": str(existing[0])}
        case = CASES[record["caseId"]]
        images, manifest, _paths = _case_inputs(case)
        async with sem:
            started = time.perf_counter()
            try:
                data, mime = await cut_generator.generate(
                    settings,
                    client,
                    _spec(case),
                    PRODUCT,
                    images,
                    analysis=ANALYSIS,
                    manifest=manifest,
                    directing_profile=record["directingProfile"],
                )
                latency = round((time.perf_counter() - started) * 1000)
                output = out_dir / f"{record['id']}{_suffix(mime)}"
                output.write_bytes(data)
                return {
                    **record, "provider": "gemini", "model": settings.model_image_high,
                    "imageSize": settings.mannequin_image_size,
                    "status": "generated", "output": str(output), "outputMime": mime,
                    "outputSha256": _sha(data), "latencyMs": latency,
                }
            except Exception as exc:
                return {
                    **record, "provider": "gemini", "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }

    results = []
    for offset in range(0, len(records), max(1, concurrency)):
        batch = records[offset:offset + max(1, concurrency)]
        results.extend(await asyncio.gather(*(one(record) for record in batch)))
        if offset + len(batch) < len(records):
            await asyncio.sleep(1)
    (out_dir / "run_audit.json").write_text(
        json.dumps({"records": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return results


async def run_colorway(settings, *, force: bool) -> list[dict]:
    out_dir = OUT / "colorway"
    out_dir.mkdir(parents=True, exist_ok=True)
    current_path = SERVER / "ab_out/fit_campaign/P02.png"
    target_path = ROOT / "outputs/coor_matching/generated_v2/women_bottom/10_무난_켄톤_플라이_스트레이트_데님_팬츠_(워시드블랙)_3282.png"
    current = _load(current_path)
    target = _load(target_path)
    variants = [
        ("flash_with_product_ref", settings, [target]),
        ("flash_text_only", settings, []),
        # Same prompt/canvas, only route image_light to Pro for a clean model comparison.
        ("pro_with_product_ref", replace(settings, model_image_light=settings.model_image_high), [target]),
    ]
    results = []
    for variant, variant_settings, refs in variants:
        existing = list(out_dir.glob(f"{variant}.*"))
        if existing and not force:
            results.append({"id": variant, "status": "skipped_existing", "output": str(existing[0])})
            continue
        started = time.perf_counter()
        try:
            data, mime = await mannequin_colorway.generate(
                variant_settings,
                GeminiImageClient(variant_settings),
                current,
                eligibility="catalog_verified_same_sku_color_only",
                target_color_name="black",
                target_product_images=refs,
            )
            output = out_dir / f"{variant}{_suffix(mime)}"
            output.write_bytes(data)
            results.append({
                "id": variant, "status": "generated", "output": str(output),
                "model": variant_settings.model_image_light,
                "input": str(current_path), "targetReference": str(target_path) if refs else None,
                "latencyMs": round((time.perf_counter() - started) * 1000),
                "outputSha256": _sha(data),
            })
        except Exception as exc:
            results.append({"id": variant, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
        await asyncio.sleep(1)
    (out_dir / "run_audit.json").write_text(
        json.dumps({"records": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument("--colorway", action="store_true")
    parser.add_argument("--cases")
    parser.add_argument("--arms", default="baseline,profile")
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--image-size", choices=("1K", "2K", "4K"), default="1K")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.gemini or args.colorway):
        parser.error("choose --prepare, --gemini, or --colorway")

    OUT.mkdir(parents=True, exist_ok=True)
    cases = _selected_cases(args.cases)
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    if not arms or any(arm not in {"baseline", "profile", "lean_profile"} for arm in arms):
        parser.error("--arms must contain baseline, profile, and/or lean_profile")
    if args.replicas < 1 or args.replicas > 5:
        parser.error("--replicas must be 1..5")
    records = prepare(cases, arms, args.replicas)
    print(f"prepared {len(records)} runtime arms in {OUT}", flush=True)
    if args.gemini:
        if len(records) > args.max_calls:
            raise SystemExit(f"planned Gemini calls {len(records)} exceed --max-calls {args.max_calls}")
        settings = replace(load_settings(), mannequin_image_size=args.image_size)
        results = await run_gemini(
            settings, records, force=args.force, concurrency=max(1, args.concurrency)
        )
        ok = sum(result["status"] in {"generated", "skipped_existing"} for result in results)
        print(f"Gemini completed {ok}/{len(results)}", flush=True)
    if args.colorway:
        settings = replace(load_settings(), mannequin_image_size=args.image_size)
        results = await run_colorway(settings, force=args.force)
        ok = sum(result["status"] in {"generated", "skipped_existing"} for result in results)
        print(f"Colorway completed {ok}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
