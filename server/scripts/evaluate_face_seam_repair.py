"""Local-only neck repair evaluation. Never uploads originals or writes a database.

Pass explicit local directories. --prepare-only saves neck crops and masks locally;
--live permits one Sunburst edit per cut; --replay-dir uses cached
original six-cut plans/responses with zero outbound requests.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
import sys
import unicodedata

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import image_usage
from app.agents import face_identity as fi, face_seam_repair as seam
from app.config import load_settings


def normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def keyed_pngs(directory: Path, suffix: str = "") -> dict[str, Path]:
    result = {}
    for path in sorted(directory.glob(f"*{suffix}.png")):
        name = normalized(path.stem.removesuffix(suffix)) if suffix else normalized(path.stem)
        if name in result:
            raise ValueError("duplicate_normalized_filename")
        result[name] = path
    return result


def load_api_env(path: Path | None) -> None:
    # Allowlist deliberately excludes database, storage and provider endpoints.
    if path is None:
        return
    allowed = {"OPENAI_API_KEY"}
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def save_pair(path: Path, before: Image.Image, after: Image.Image, box) -> None:
    x, y, side = box
    bounds = (x, y, x + side, y + side)
    pair = Image.new("RGB", (side * 2, side))
    pair.paste(before.crop(bounds), (0, 0))
    pair.paste(after.crop(bounds), (side, 0))
    pair.save(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--inputs", type=Path, required=True, help="Qwen *__face.png directory")
    result.add_argument("--originals", type=Path, required=True, help="matching original *.png directory")
    result.add_argument("--references", type=Path, required=True, help="local identity-reference PNG directory")
    result.add_argument("--model-dir", type=Path, required=True, help="existing local YuNet/SFace models")
    result.add_argument("--output-dir", type=Path, required=True, help="local, Git-ignored result directory")
    result.add_argument("--env-file", type=Path, help="read only the OpenAI API key")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay-dir", type=Path, help="original jobs.json/composition.json/raw/final directory")
    mode.add_argument("--responses-dir", type=Path, help="reuse prior live neck inputs/responses for local composition; no API calls")
    return result


def prepare_inputs(args):
    faces = keyed_pngs(args.inputs, "__face")
    originals = keyed_pngs(args.originals)
    if not faces or not faces.keys() <= originals.keys():
        raise ValueError("missing_matching_originals")
    refs = fi.reference_embeddings(
        [p.read_bytes() for p in sorted(args.references.glob("*.png"))],
        model_dir=str(args.model_dir),
    )
    if not refs:
        raise ValueError("identity_references_unavailable")
    inputs = {}
    for name, path in faces.items():
        current = Image.open(path).convert("RGB")
        original = Image.open(originals[name]).convert("RGB")
        base, plan, meta = fi.prepare_image(original, str(args.model_dir), pad_crop=True)
        if plan is None or meta.get("skipped_reason"):
            raise ValueError("original_face_plan_unavailable")
        if fi.unpad_edges(base, meta.get("crop_pad")).size != current.size:
            raise ValueError("original_qwen_dimensions_mismatch")
        context = seam.FaceSeamContext(
            plan=plan, crop_pad=meta.get("crop_pad"), references=refs,
            model_dir=str(args.model_dir),
            neck_offset=fi.neck_offset_meta(base, plan),
        )
        inputs[name] = (current, context)
    return inputs


def validate_output_dir(path: Path) -> Path:
    """Refuse image artifacts in a non-ignored directory inside this repository."""
    import subprocess

    path = path.expanduser().resolve()
    root = Path(__file__).resolve().parents[2]
    if path.is_relative_to(root):
        checked = subprocess.run(
            ["git", "check-ignore", "-q", str(path / "neck-result.png")],
            cwd=root, capture_output=True, check=False,
        )
        if checked.returncode != 0:
            raise ValueError("output_must_be_outside_git_or_ignored")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def original_plan_inputs(source: Path, inputs):
    jobs = json.loads((source / "jobs.json").read_text())
    composition = json.loads((source / "composition.json").read_text())
    for job in jobs:
        name = normalized(job["name"])
        current, context = inputs[name]
        x, y, side = job["crop"]
        pad_l, pad_t, *_ = seam._pad_ltrb(context.crop_pad)
        fx, fy, fw, fh = context.plan.box
        crop = seam.RepairCrop(
            current.crop((x, y, x + side, y + side)), (x, y, side),
            (fx - pad_l - x, fy - pad_t - y, fw, fh), current.size,
        )
        final = composition.get(job["id"], {})
        local = lambda polygons: [[(px - x, py - y) for px, py in poly] for poly in polygons]
        repair = seam.RepairPlan(
            True, job["detail"], local(job["polygons"]),
            local(final.get("polygons", job["polygons"])),
            (source / "alignment" / f"{job['id']}.f32").exists(),
            final.get("feather", 4),
        )
        yield name, current, context, crop, repair, job


def measure_result(current, repaired, context, meta):
    before = fi.identity_score(current, context.references, model_dir=context.model_dir)
    after = fi.identity_score(repaired, context.references, model_dir=context.model_dir)
    buf = BytesIO()
    repaired.save(buf, "PNG")
    decoded = Image.open(BytesIO(buf.getvalue())).convert("RGB")
    exact_png = np.array_equal(np.asarray(decoded), np.asarray(repaired))
    finite = before is not None and after is not None and np.isfinite([before, after]).all()
    drop = round(before - after, 6) if finite else None
    return {
        **meta, "identity_before": before if finite else None, "identity_after": after if finite else None,
        "identity_drop": drop, "png_roundtrip_exact": exact_png,
        "numeric_checks_passed": bool(exact_png and meta.get("outside_changed") == 0 and drop is not None and drop <= .02),
    }


async def evaluate(args):
    from app.agents.gemini_image import run_cpu_bound
    from app.agents.image_cost import estimate_cost

    output = validate_output_dir(args.output_dir)
    load_api_env(args.env_file)
    settings = replace(load_settings(), face_seam_repair="on", fm_face_qc_dir=str(args.model_dir))
    image_usage.configure(pool=None, persist=False)
    inputs = await run_cpu_bound(prepare_inputs, args)
    report = {
        "evaluation": "cached_manual_replay" if args.replay_dir else "cached_direct_replay" if args.responses_dir else "local_mask_preparation" if args.prepare_only else "direct_live",
        "human_review": "not_performed",
        "image_model": seam.SUNBURST_MODEL, "rows": [],
    }
    if args.replay_dir:
        items = list(original_plan_inputs(args.replay_dir, inputs))
    else:
        items = [(name, current, context, None, None, None) for name, (current, context) in sorted(inputs.items())]
    semaphore = asyncio.Semaphore(3)

    async def one(item):
        async with semaphore:
            name, current, context, crop, repair, job = item
            row = {"cut": name, "vision_calls": 0, "image_edit_calls": 0, "neck_offset": context.neck_offset}
            case_dir = output / name
            case_dir.mkdir(exist_ok=True, mode=0o700)
            try:
                crop = crop or await run_cpu_bound(seam.prepare_repair_crop, current, context)
                if args.live and (case_dir / "sunburst-response.png").exists():
                    raise ValueError("response_exists_use_responses_dir")
                crop.current.save(case_dir / "neck-input.png")
                row["crop"] = list(crop.box)
                repair = repair or seam.build_neck_repair_plan(crop)
                prompt = seam.PROMPT_HEAD + repair.instruction + seam.PROMPT_TAIL
                row["prompt_sha"] = hashlib.sha256(prompt.encode()).hexdigest()[:16]
                save_json(case_dir / "repair-plan.json", asdict(repair))
                row.update(align=repair.align, feather=repair.feather)
                masks = seam.build_repair_masks(crop, repair)
                masks.api_mask_rgba.save(case_dir / "edit-mask.png")
                Image.fromarray(masks.composition_mask.astype(np.uint8) * 255).save(case_dir / "composition-mask.png")
                if args.prepare_only:
                    row["status"] = "prepared"
                else:
                    if args.replay_dir:
                        generated = Image.open(args.replay_dir / "raw" / f"{job['id']}.png").convert("RGB")
                        row["plan_source"] = "original_manual_annotation"
                    elif args.responses_dir:
                        cached = args.responses_dir / name
                        before = Image.open(cached / "neck-input.png").convert("RGB")
                        if not np.array_equal(np.asarray(before), np.asarray(crop.current)):
                            raise ValueError("response_input_mismatch")
                        generated = Image.open(cached / "sunburst-response.png").convert("RGB")
                        row["response_source"] = "cached_direct_edit"
                    else:
                        row["image_edit_calls"] = 1
                        response = await seam.call_sunburst_edit(settings, prompt, crop, masks)
                        generated = response.image
                        generated.save(case_dir / "sunburst-response.png")
                        row.update(image_latency_ms=response.latency_ms, image_cost=asdict(estimate_cost(seam.SUNBURST_MODEL, "1024x1024", response.usage)))
                        save_json(case_dir / "image-call.json", {"image_latency_ms": row["image_latency_ms"], "image_cost": row["image_cost"]})
                    repaired, meta = await run_cpu_bound(seam.composite_repair, current, crop, repair, generated)
                    row.update(await run_cpu_bound(measure_result, current, repaired, context, meta))
                    row["status"] = "numeric_pass" if row["numeric_checks_passed"] else "numeric_reject"
                    repaired.save(case_dir / "candidate.png")
                    save_pair(case_dir / "before-after-native.png", current, repaired, crop.box)
                    if args.replay_dir:
                        approved = Image.open(args.replay_dir / "final" / f"{job['name']}_목보정.png").convert("RGB")
                        delta = np.abs(np.asarray(approved, np.int16) - np.asarray(repaired, np.int16))
                        x, y, side = crop.box
                        row.update(approved_changed_pixels=int(np.any(delta, axis=2).sum()), approved_neck_mae=float(delta[y:y + side, x:x + side].mean()))
                        save_pair(case_dir / "approved-port-native.png", approved, repaired, crop.box)
            except Exception as exc:
                # No provider bodies, keys or image payloads in evaluation output.
                row.update(status="failed", error_type=type(exc).__name__)
                if isinstance(exc, seam.SeamRepairUnavailable):
                    # Domain errors are fixed local reason codes, not HTTP bodies.
                    reason = str(exc)
                    if reason.isascii() and reason.replace("_", "").isalnum() and len(reason) < 60:
                        row["reason"] = reason
            report["rows"].append(row)
            report["rows"].sort(key=lambda item: item["cut"])
            save_json(output / "report.json", report)
            print(json.dumps({"cut": name, "status": row["status"], "image_edit_calls": row["image_edit_calls"]}, ensure_ascii=False), flush=True)

    await asyncio.gather(*(one(item) for item in items))
    report.update(vision_calls=sum(r["vision_calls"] for r in report["rows"]), image_edit_calls=sum(r["image_edit_calls"] for r in report["rows"]))
    save_json(output / "report.json", report)
    return 1 if any(r["status"] in {"failed", "numeric_reject"} for r in report["rows"]) else 0


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        raise SystemExit(asyncio.run(evaluate(parser().parse_args())))
    except Exception as exc:
        print(json.dumps({"evaluation_failed": type(exc).__name__}), flush=True)
        raise SystemExit(1)
