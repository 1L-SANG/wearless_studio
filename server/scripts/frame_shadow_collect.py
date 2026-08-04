"""Frame Lock 1K shadow collection guard.

This script deliberately does not reuse editor-vary shadow rows.  It prepares
and validates a Frame Lock dataset boundary, then refuses to proceed unless the
runtime is exactly the calibration runtime:

  MANNEQUIN_IMAGE_SIZE=1K
  MANNEQUIN_IMAGE_SIZE_CAP=1K
  MANNEQUIN_FRAME_QC=shadow
  MANNEQUIN_HYBRID_COMPOSITE=off
  MANNEQUIN_TEXTURE_PROJECTION_2D=off

Provider execution is opt-in with --execute.  The default path is safe preflight
only and performs zero Gemini/R2 writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from scripts._env import load_env

load_env()

from app import frame_calibration as fc  # noqa: E402
from app.agents import mannequin_frame_vision  # noqa: E402
from app.agents.prompts import load_prompt_template  # noqa: E402
from app.config import load_settings  # noqa: E402
from scripts.smoke_realwire import Api, InlineWorker, ensure_smoke_session  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "ab_out" / "frame_lock"
BASE_KEY = {"women": "seed/mannequin/base-women-2K.png", "men": "seed/mannequin/base-men-2K.png"}
SOURCE_IMAGE_SLOTS = ("Front", "Back", "Detail")
SOURCE_UPLOAD_ATTEMPTS = 2
INLINE_CALIBRATION_HEADER = "X-Wearless-Frame-Calibration"
PROJECTION_METRIC_KEYS = {
    "period_rel_err_max", "repeat_count_rel_err_max", "direction_error_max",
    "color_delta_e00_max", "color_delta_e00_median", "mask_coverage",
    "outside_drift_frac", "outside_mean_de76", "outside_ssim",
}


class _UploadImage:
    def __init__(self, *, slot: str, original_path: Path, original_bytes: bytes,
                 original_mime: str, upload_name: str, upload_bytes: bytes,
                 uploaded_mime: str, conversion: dict):
        self.slot = slot
        self.original_path = original_path
        self.original_bytes = original_bytes
        self.original_mime = original_mime
        self.upload_name = upload_name
        self.upload_bytes = upload_bytes
        self.uploaded_mime = uploaded_mime
        self.conversion = conversion


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("arms") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise SystemExit("manifest 에 arms 배열이 필요합니다")
    return rows


def _validate_execute_manifest(arms: list[dict], *, minimum_arms: int = 5) -> list[str]:
    problems: list[str] = []
    if len(arms) < minimum_arms:
        problems.append("manifest_less_than_5_arms")
    for arm in arms:
        arm_id = arm.get("arm") or "<missing>"
        has_source_path = bool(arm.get("sourcePath"))
        has_source_images = isinstance(arm.get("sourceImages"), list) and bool(arm.get("sourceImages"))
        if not has_source_path and not has_source_images:
            problems.append(f"manifest_missing_sourcePath:{arm_id}")
        if has_source_images:
            slots = [str(i.get("slot") or "") for i in arm.get("sourceImages") or []
                     if isinstance(i, dict)]
            for slot in SOURCE_IMAGE_SLOTS:
                if slot not in slots:
                    problems.append(f"manifest_missing_sourceImageSlot:{slot}:{arm_id}")
            for item in arm.get("sourceImages") or []:
                if not isinstance(item, dict) or not item.get("slot") or not item.get("path"):
                    problems.append(f"manifest_invalid_sourceImage:{arm_id}")
                    continue
                source_path = Path(item["path"]).expanduser()
                if not source_path.is_file():
                    problems.append(
                        f"manifest_source_file_missing:{item['slot']}:{arm_id}")
        elif has_source_path and not Path(arm["sourcePath"]).expanduser().is_file():
            problems.append(f"manifest_source_file_missing:Front:{arm_id}")
        for key in ("name", "clothingType"):
            if not arm.get(key):
                problems.append(f"manifest_missing_{key}:{arm_id}")
        if arm.get("metadata", {}).get("goldenset") is not True and \
                not str(arm.get("name") or "").startswith("goldenset-"):
            problems.append(f"manifest_not_test_project:{arm_id}")
    return problems


def _validate_collection_shape(arms: list[dict], *, reps: int, smoke: bool) -> list[str]:
    """Separate a one-call smoke from data that may be used for calibration.

    A smoke proves wiring only and must never be accepted by the blinded-label
    report as a calibration dataset.  Calibration keeps the roadmap contract of
    at least five products and three independent generations per product.
    """
    problems: list[str] = []
    if smoke:
        if reps != 1:
            problems.append("smoke_requires_one_rep")
        return problems
    if len(arms) < 5:
        problems.append("calibration_less_than_5_arms")
    elif len({_source_identity(arm) for arm in arms}) < 5:
        problems.append("calibration_less_than_5_distinct_sources")
    if reps < 3:
        problems.append("calibration_reps_less_than_3")
    return problems


def _validate_projection_smoke_shape(arms: list[dict], *, reps: int) -> list[str]:
    problems: list[str] = []
    if len(arms) != 1:
        problems.append("projection_smoke_requires_one_arm")
    if reps != 1:
        problems.append("projection_smoke_requires_one_rep")
    return problems


def _source_identity(arm: dict) -> tuple:
    images = arm.get("sourceImages")
    if isinstance(images, list) and images:
        return tuple(sorted(
            (str(item.get("slot") or ""), str(Path(item.get("path") or "").expanduser().resolve()))
            for item in images if isinstance(item, dict)
        ))
    return (("Front", str(Path(arm.get("sourcePath") or "").expanduser().resolve())),)


def _arm_rows(arms: list[dict], reps: int) -> list[tuple[dict, int]]:
    out: list[tuple[dict, int]] = []
    for arm in arms:
        for key in ("arm",):
            if not arm.get(key):
                raise SystemExit(f"manifest arm 누락: {key}")
        for rep in range(reps):
            out.append((arm, rep))
    return out


def _expected_run(settings, *, image_size: str | None = None) -> dict:
    prompt_body = load_prompt_template(settings)
    vision_prompt = Path(mannequin_frame_vision._PROMPT_FILE).read_text(encoding="utf-8")
    model = getattr(settings, "model_image_high", None) or \
        getattr(settings, "model_image_light", None) or "unknown"
    return fc.run_fingerprint(
        generation_model=model,
        generation_prompt_version=settings.mannequin_prompt_version,
        generation_prompt=prompt_body,
        frame_vision_prompt_version=mannequin_frame_vision.PROMPT_VERSION,
        frame_vision_prompt=vision_prompt,
        code_commit=_git_sha(),
        image_size=image_size or settings.mannequin_image_size,
        image_size_cap=settings.mannequin_image_size_cap,
        aspect_ratio=settings.mannequin_aspect_ratio,
        frame_qc_mode=settings.mannequin_frame_qc,
        hybrid_composite_mode=settings.mannequin_hybrid_composite,
        texture_projection_mode=settings.mannequin_texture_projection_2d,
    )


def _write_preflight_failure(run_dir: Path, dataset_id: str, problems: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "datasetId": dataset_id,
        "source": "frame_shadow_collect",
        "validForCalibration": False,
        "provenanceUnverified": True,
        "provenanceProblems": problems,
        "sampleCount": 0,
        "policyVersion": fc.POLICY_VERSION,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_test_truth_guard(arms: list[dict], *, actor_id: str | None) -> list[str]:
    """Safety guard for future test-data Product Truth preparation.

    This function intentionally does not mutate DB yet.  It blocks accidental
    production use by requiring manifest-level test markers and an explicit actor.
    """
    problems = []
    if not actor_id:
        problems.append("prepare_test_truth_missing_actor")
    for arm in arms:
        name = str(arm.get("name") or arm.get("arm") or "")
        meta = arm.get("metadata") if isinstance(arm.get("metadata"), dict) else {}
        if meta.get("goldenset") is not True and not name.startswith("goldenset-"):
            problems.append(f"prepare_test_truth_not_test_arm:{arm.get('arm')}")
    return problems


def _asset_url(api: Api, src: str) -> str:
    if src.startswith("http://") or src.startswith("https://"):
        return src
    return urljoin(str(api.c.base_url), src.lstrip("/"))


def _front_product_payload(arm: dict, assets: list[dict] | dict) -> dict:
    name = str(arm.get("name") or arm["arm"])
    if not name.startswith("goldenset-"):
        name = f"goldenset-{name}"
    if isinstance(assets, dict):
        image_refs = [{"slot": "Front", "id": assets["id"], "url": assets["url"]}]
    else:
        image_refs = [{"slot": a["slot"], "id": a["id"], "url": a["url"]} for a in assets]
    return {
        "name": name,
        "clothingType": arm["clothingType"],
        "metadata": {"goldenset": True, "frameCalibration": True, "arm": arm["arm"]},
        "colors": [{
            "id": "base",
            "label": arm.get("color") or "unknown",
            "isBase": True,
            "images": image_refs,
        }],
    }


def _mime_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext in (".heic", ".heif"):
        return "image/heic"
    return "application/octet-stream"


def _source_image_specs(arm: dict) -> list[tuple[str, Path]]:
    if isinstance(arm.get("sourceImages"), list) and arm.get("sourceImages"):
        by_slot = {
            str(item["slot"]): Path(item["path"]).expanduser().resolve()
            for item in arm["sourceImages"]
            if isinstance(item, dict) and item.get("slot") and item.get("path")
        }
        return [(slot, by_slot[slot]) for slot in SOURCE_IMAGE_SLOTS if slot in by_slot]
    return [("Front", Path(arm["sourcePath"]).expanduser().resolve())]


def _convert_to_jpeg_with_sips(path: Path) -> bytes:
    sips = shutil.which("sips")
    if not sips:
        raise RuntimeError("source_image_converter_unavailable:sips")
    with tempfile.TemporaryDirectory(prefix="frame-source-") as tmp:
        out = Path(tmp) / f"{path.stem}.jpg"
        proc = subprocess.run(
            [sips, "-s", "format", "jpeg", str(path), "--out", str(out)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError("source_image_conversion_failed:sips")
        data = out.read_bytes()
    if data[:3] != b"\xff\xd8\xff":
        raise RuntimeError("source_image_conversion_not_jpeg")
    return data


def _prepare_upload_image(path: Path, slot: str, *, force_jpeg: bool = False) -> _UploadImage:
    original_bytes = path.read_bytes()
    original_mime = _mime_for_path(path)
    if force_jpeg and original_mime != "image/jpeg":
        upload_bytes = _convert_to_jpeg_with_sips(path)
        uploaded_mime = "image/jpeg"
        upload_name = f"{path.stem}.jpg"
        conversion = {"tool": "sips", "from": original_mime, "to": uploaded_mime}
    else:
        upload_bytes = original_bytes
        uploaded_mime = original_mime
        upload_name = path.name
        conversion = {"tool": "none", "from": original_mime, "to": uploaded_mime}
    if uploaded_mime not in ("image/jpeg", "image/png"):
        raise RuntimeError(f"source_image_upload_mime_unsupported:{uploaded_mime}")
    return _UploadImage(
        slot=slot,
        original_path=path,
        original_bytes=original_bytes,
        original_mime=original_mime,
        upload_name=upload_name,
        upload_bytes=upload_bytes,
        uploaded_mime=uploaded_mime,
        conversion=conversion,
    )


def _source_bundle_sha256(images: list[_UploadImage]) -> str:
    if len(images) == 1:
        return fc.sha256_hex(images[0].original_bytes)
    return fc.sha256_hex(fc.canonical([
        {"slot": image.slot, "originalSha256": fc.sha256_hex(image.original_bytes)}
        for image in images
    ]))


def _source_image_provenance(images: list[_UploadImage]) -> list[dict]:
    return [{
        "slot": image.slot,
        "originalName": image.original_path.name,
        "originalSha256": fc.sha256_hex(image.original_bytes),
        "uploadedSha256": fc.sha256_hex(image.upload_bytes),
        "originalMime": image.original_mime,
        "uploadedMime": image.uploaded_mime,
        "conversion": dict(image.conversion),
    } for image in images]


def _bundle_hash_bytes(images: list[_UploadImage]) -> bytes:
    if len(images) == 1:
        return images[0].original_bytes
    return fc.canonical([
        {"slot": image.slot, "originalSha256": fc.sha256_hex(image.original_bytes)}
        for image in images
    ])


def _upload_source_image(api: Api, *, project_id: str, image: _UploadImage) -> dict:
    upload = api.call("POST", "/v1/assets/upload-url", json={
        "filename": image.upload_name,
        "mime": image.uploaded_mime,
        "size": len(image.upload_bytes),
        "projectId": project_id,
    })
    with httpx.Client(timeout=60) as c:
        for attempt in range(SOURCE_UPLOAD_ATTEMPTS):
            try:
                put = c.put(
                    upload["uploadUrl"],
                    content=image.upload_bytes,
                    headers={"Content-Type": image.uploaded_mime},
                )
            except httpx.TransportError:
                if attempt + 1 < SOURCE_UPLOAD_ATTEMPTS:
                    time.sleep(0.25)
                    continue
                raise RuntimeError("source_image_upload_failed:transport") from None

            status = getattr(put, "status_code", None)
            if status is None:
                put.raise_for_status()
                break
            if 200 <= status < 300:
                break
            if status >= 500 and attempt + 1 < SOURCE_UPLOAD_ATTEMPTS:
                time.sleep(0.25)
                continue
            raise RuntimeError(f"source_image_upload_failed:http_{status}") from None
    asset = api.call("POST", f"/v1/assets/{upload['assetId']}/complete", json={
        "projectId": project_id,
        "mime": image.uploaded_mime,
        "filename": image.upload_name,
    })
    return {"slot": image.slot, "id": upload["assetId"], "url": asset["url"]}


def _result_src(job: dict) -> str:
    data = ((job.get("result") or {}).get("data") or [])
    if len(data) != 1 or not data[0].get("src"):
        raise RuntimeError("generation_result_not_single_cut")
    return data[0]["src"]


def _image_calls_attempted(job: dict, events: list[dict] | None = None) -> int:
    """Count actual image provider calls visible in job steps.

    `prompt_rendered` is emitted once for the main Gemini image call.  The
    automatic edit passes emit their own typed statuses after provider calls.
    Frame calibration requires exactly one image call per sample.
    """
    count = 0
    for step in (events or (job.get("steps") or [])):
        status = step.get("status")
        if status == "prompt_rendered":
            count += 1
        elif status in {"untuck_pass", "bust_pass"} and step.get("outcome") != "skipped":
            count += 1
        elif status == "axis_retry" and step.get("fired") is True:
            count += 1
    return count


def _latest_frame_qc(events: list[dict]) -> dict | None:
    found = None
    for step in events:
        if step.get("status") == "frame_qc" and step.get("phase") in ("pre", "final"):
            found = step
    if not found:
        return None
    return {
        "decision": found.get("decision"),
        "criticalErrors": found.get("criticalErrors") or [],
        "warnings": found.get("warnings") or [],
        "checks": found.get("checks") or {},
        "metrics": found.get("metrics") or {},
        "visionMeta": found.get("visionMeta") or {},
    }


def _projection_smoke_summary(events: list[dict], *, strict: bool = True) -> dict:
    """Return a bounded, non-sensitive projection wiring/quality summary."""
    started = next(
        (step for step in events if step.get("status") == "hybrid_composite_started"),
        None,
    )
    projection = next(
        (step for step in events if step.get("status") == "hybrid_texture_projection_plan"),
        None,
    )
    deterministic = next(
        (step for step in events if step.get("status") == "hybrid_deterministic_qc"),
        None,
    )
    completed = next(
        (step for step in reversed(events)
         if step.get("status") == "hybrid_composite_completed"),
        None,
    )
    missing = None
    if started is None:
        missing = "projection_smoke_hybrid_not_executed"
    elif projection is None:
        missing = "projection_smoke_projection_not_executed"

    projection_keys = (
        "mode", "ok", "targetPeriodPx", "targetAxis", "confidence", "version", "reason"
    )
    completion_keys = (
        "outcome", "mode", "fail_closed", "needs_review", "coverage", "output_hash"
    )
    deterministic_metrics = (
        deterministic.get("metrics") if isinstance(deterministic, dict) else {}
    )
    if not isinstance(deterministic_metrics, dict):
        deterministic_metrics = {}
    deterministic_summary = None
    if deterministic is not None:
        deterministic_summary = {
            "passed": deterministic.get("passed") is True,
            "failures": [str(v)[:64] for v in (deterministic.get("failures") or [])][:8],
            "metrics": {
                key: deterministic_metrics[key]
                for key in PROJECTION_METRIC_KEYS
                if key in deterministic_metrics
            },
        }
    summary = {
        "wiringPassed": missing is None and completed is not None,
        "qualityPassed": bool(
            missing is None
            and projection.get("ok") is True
            and deterministic_summary is not None
            and deterministic_summary["passed"]
            and completed is not None
            and completed.get("outcome") in {"applied", "would_apply"}
            and completed.get("needs_review") is not True
        ),
        "hybrid": ({
            "mode": started.get("mode"),
            "pipelineVersion": started.get("pipeline_version"),
        } if started else None),
        "projection": ({key: projection.get(key) for key in projection_keys
                        if projection.get(key) is not None}
                       if projection else None),
        "deterministicQc": deterministic_summary,
        "completion": ({key: completed.get(key) for key in completion_keys
                        if completed.get(key) is not None}
                       if completed else None),
    }
    if missing:
        summary["failureReason"] = missing
        if strict:
            raise RuntimeError(missing)
    return summary


async def _job_event_payloads(worker: InlineWorker, job_id: str) -> list[dict]:
    if hasattr(worker, "job_events"):
        return await worker.job_events(job_id)
    async with worker.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select payload
                from job_events
                where job_id = %s
                order by created_at asc, id asc
                """,
                (job_id,),
            )
            rows = await cur.fetchall()
    return [r["payload"] for r in rows if isinstance(r.get("payload"), dict)]


def _fetch_output_bytes(api: Api, src: str) -> bytes:
    url = _asset_url(api, src)
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        resp = c.get(url)
        resp.raise_for_status()
        data = resp.content
    if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
        raise RuntimeError("output_not_image")
    if len(data) < 32:
        raise RuntimeError("output_too_small")
    return data


async def _prepare_arm(*, api: Api, arm: dict, worker: InlineWorker | None = None) -> dict:
    """Create one test project and approve Product Truth once per arm."""
    source_specs = _source_image_specs(arm)
    force_bundle_jpeg = (
        isinstance(arm.get("sourceImages"), list) and bool(arm.get("sourceImages"))
    )
    source_images = [
        _prepare_upload_image(
            path,
            slot,
            force_jpeg=(
                force_bundle_jpeg or _mime_for_path(path) in ("image/heic", "image/heif")
            ),
        )
        for slot, path in source_specs
    ]
    project = api.call("POST", "/v1/projects")
    project_id = project["id"]

    asset_views = [
        _upload_source_image(api, project_id=project_id, image=image)
        for image in source_images
    ]
    api.call("PATCH", f"/v1/projects/{project_id}/product",
             json=_front_product_payload(arm, asset_views))

    inline_secret = os.getenv("FRAME_CALIBRATION_INLINE_SECRET") or None
    headers = {INLINE_CALIBRATION_HEADER: inline_secret} if inline_secret else None
    analyze = api.call(
        "POST", f"/v1/projects/{project_id}/analyze", headers=headers
    )
    if analyze.get("jobId"):
        if worker is None:
            raise RuntimeError("analysis_inline_worker_required")
        if inline_secret:
            lease_token = analyze.get("leaseToken")
            if not lease_token:
                raise RuntimeError("analysis_preclaimed_lease_missing")
            claimed = await worker.run_preclaimed(analyze["jobId"], lease_token)
        else:
            claimed = await worker.claim_and_run(analyze["jobId"])
        if claimed != "claimed":
            raise RuntimeError("analysis_job_not_inline_claimed")
        analyzed = api.poll_job(analyze["jobId"], timeout_s=240)
        if analyzed.get("status") != "done":
            raise RuntimeError("analysis_failed")
    draft = api.call("POST", f"/v1/projects/{project_id}/product-truth:draft")
    approved = api.call("POST", f"/v1/projects/{project_id}/product-truth/{draft['id']}:approve")
    if approved.get("status") != "approved":
        raise RuntimeError("product_truth_not_approved")
    return {
        "arm": arm,
        "projectId": project_id,
        "sourcePath": source_images[0].original_path,
        "sourceBytes": _bundle_hash_bytes(source_images),
        "sourceImages": source_images,
        "sourceImageBundleSha256": _source_bundle_sha256(source_images),
        "sourceImageProvenance": _source_image_provenance(source_images),
    }


async def _generate_sample(*, api: Api, worker: InlineWorker, run_dir: Path,
                           dataset_id: str, prepared: dict, rep: int,
                           expected_run: dict,
                           projection_smoke: bool = False) -> dict:
    """Generate one rep for a prepared test arm."""
    arm = prepared["arm"]
    project_id = prepared["projectId"]
    source_path = prepared["sourcePath"]
    source_bytes = prepared["sourceBytes"]
    inline_secret = os.getenv("FRAME_CALIBRATION_INLINE_SECRET") or None
    headers = {INLINE_CALIBRATION_HEADER: inline_secret} if inline_secret else None
    endpoint = (
        f"/v1/projects/{project_id}/mannequins:generate" if rep == 0
        else f"/v1/projects/{project_id}/mannequins:regenerate"
    )
    gen = api.call(
        "POST", endpoint, headers=headers,
        **({"json": {}} if rep > 0 else {}),
    )
    job_id = gen["jobId"]
    if inline_secret:
        lease_token = gen.get("leaseToken")
        if not lease_token:
            raise RuntimeError("mannequin_preclaimed_lease_missing")
        who = await worker.run_preclaimed(job_id, lease_token)
    else:
        who = await worker.claim_and_run(job_id)
    if who != "claimed":
        raise RuntimeError("mannequin_job_not_inline_claimed")
    completed = api.poll_job(job_id, timeout_s=420)
    if completed.get("status") != "done":
        raise RuntimeError("mannequin_generation_failed")
    events = await _job_event_payloads(worker, job_id)
    image_calls = _image_calls_attempted(completed, events)
    if image_calls != 1:
        raise RuntimeError(f"image_calls_not_one:{image_calls}")

    output_src = _result_src(completed)
    output_bytes = _fetch_output_bytes(api, output_src)

    gender = arm.get("targetGender") or "women"
    base_key = BASE_KEY.get(gender, BASE_KEY["women"])
    base_bytes = await asyncio.to_thread(worker.app.state.r2.get_bytes, base_key)

    frame_qc = _latest_frame_qc(events) or {}
    prov = fc.provenance(
        run=expected_run,
        source_bytes=source_bytes,
        output_bytes=output_bytes,
        base_bytes=base_bytes,
        call_attempt_index=1,
        frame_qc_result=frame_qc or {"decision": "missing", "visionMeta": {"status": "missing"}},
    )
    if prepared.get("sourceImageProvenance"):
        prov["sourceImages"] = prepared["sourceImageProvenance"]
    if projection_smoke:
        prov["projectionSmoke"] = _projection_smoke_summary(events, strict=False)
    row = fc.sample_row(
        dataset_id=dataset_id,
        arm=arm["arm"],
        project_id=project_id,
        rep=rep,
        source_name=source_path.name if len(prepared.get("sourceImages") or []) == 1
        else f"{arm['arm']}_source_bundle",
        output_name=f"{arm['arm']}_rep{rep}.png",
        base_name=base_key.rsplit("/", 1)[-1],
        prov=prov,
        image_calls_attempted=image_calls,
    )
    row["testProject"] = True
    row["sourceProjectName"] = arm.get("name")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / row["outputImage"]).write_bytes(output_bytes)
    return row


async def _recover_sample(*, api: Api, worker: InlineWorker, run_dir: Path,
                          dataset_id: str, prepared: dict, rep: int,
                          job_id: str, expected_run: dict) -> dict:
    """Materialize one already-completed sample without creating a new job.

    Recovery is intentionally read-only with respect to the service API.  It is
    used when the paid provider call and atomic DB finalize succeeded but the
    local artifact write failed afterwards.
    """
    completed = api.call("GET", f"/v1/jobs/{job_id}")
    if completed.get("kind") != "mannequin":
        raise RuntimeError("recovery_job_kind_mismatch")
    if completed.get("status") != "done":
        raise RuntimeError("recovery_job_not_done")
    if str(completed.get("projectId") or "") != str(prepared["projectId"]):
        raise RuntimeError("recovery_job_project_mismatch")

    events = await _job_event_payloads(worker, job_id)
    image_calls = _image_calls_attempted(completed, events)
    if image_calls != 1:
        raise RuntimeError(f"image_calls_not_one:{image_calls}")

    output_bytes = _fetch_output_bytes(api, _result_src(completed))
    arm = prepared["arm"]
    gender = arm.get("targetGender") or "women"
    base_key = BASE_KEY.get(gender, BASE_KEY["women"])
    base_bytes = await asyncio.to_thread(worker.app.state.r2.get_bytes, base_key)
    frame_qc = _latest_frame_qc(events) or {}
    prov = fc.provenance(
        run=expected_run,
        source_bytes=prepared["sourceBytes"],
        output_bytes=output_bytes,
        base_bytes=base_bytes,
        call_attempt_index=1,
        frame_qc_result=frame_qc or {
            "decision": "missing",
            "visionMeta": {"status": "missing"},
        },
    )
    if prepared.get("sourceImageProvenance"):
        prov["sourceImages"] = prepared["sourceImageProvenance"]
    source_images = prepared.get("sourceImages") or []
    source_path = prepared["sourcePath"]
    row = fc.sample_row(
        dataset_id=dataset_id,
        arm=arm["arm"],
        project_id=prepared["projectId"],
        rep=rep,
        source_name=source_path.name if len(source_images) == 1
        else f"{arm['arm']}_source_bundle",
        output_name=f"{arm['arm']}_rep{rep}.png",
        base_name=base_key.rsplit("/", 1)[-1],
        prov=prov,
        image_calls_attempted=image_calls,
    )
    row["testProject"] = True
    row["sourceProjectName"] = arm.get("name")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / row["outputImage"]).write_bytes(output_bytes)
    return row


async def _collect_one(*, api: Api, worker: InlineWorker, run_dir: Path,
                       dataset_id: str, arm: dict, rep: int, expected_run: dict) -> dict:
    prepared = await _prepare_arm(api=api, worker=worker, arm=arm)
    return await _generate_sample(
        api=api, worker=worker, run_dir=run_dir, dataset_id=dataset_id,
        prepared=prepared, rep=rep, expected_run=expected_run)


async def _execute_collection(*, api_base: str, run_dir: Path, dataset_id: str,
                              arms: list[dict], reps: int, expected_run: dict,
                              existing_rows: list[dict] | None = None,
                              projection_smoke: bool = False) -> list[dict]:
    token = ensure_smoke_session()
    api = Api(api_base, token)
    worker = InlineWorker()
    rows: list[dict] = list(existing_rows or [])
    existing_ids = {r.get("id") for r in rows}
    existing_slots = {(r.get("arm"), r.get("rep")) for r in rows}
    await worker.open()
    try:
        for arm in arms:
            pending_reps = [
                rep for rep in range(reps)
                if (arm["arm"], rep) not in existing_slots
            ]
            if not pending_reps:
                continue
            prepared = await _prepare_arm(api=api, worker=worker, arm=arm)
            for rep in pending_reps:
                sample_id = fc.make_sample_id(
                    arm=arm["arm"], project_id=prepared["projectId"], rep=rep)
                if sample_id in existing_ids or (arm["arm"], rep) in existing_slots:
                    continue
                row = await _generate_sample(
                    api=api, worker=worker, run_dir=run_dir, dataset_id=dataset_id,
                    prepared=prepared, rep=rep, expected_run=expected_run,
                    projection_smoke=projection_smoke)
                rows.append(row)
                existing_ids.add(row["id"])
                existing_slots.add((row.get("arm"), row.get("rep")))
                fc.write_jsonl(run_dir / "samples.jsonl", rows)
    finally:
        await worker.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Frame Lock shadow collection preflight")
    ap.add_argument("--manifest", required=True, help="JSON arms manifest")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--dataset-id", default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--execute", action="store_true",
                    help="actually call the generation pipeline (provider cost)")
    smoke_modes = ap.add_mutually_exclusive_group()
    smoke_modes.add_argument(
        "--smoke", action="store_true",
        help="allow exactly one frame wiring sample; never calibration-eligible",
    )
    smoke_modes.add_argument(
        "--projection-smoke", action="store_true",
        help="run one hybrid/projection shadow sample; never calibration-eligible",
    )
    smoke_modes.add_argument(
        "--projection-enforce-smoke", action="store_true",
        help="run one 4K fine-pattern sample through the real fail-closed apply gate",
    )
    ap.add_argument("--prepare-test-truth", action="store_true",
                    help="future test-data-only Product Truth setup; guarded and default off")
    ap.add_argument("--actor-id", help="required with --prepare-test-truth")
    args = ap.parse_args()

    arms = _load_manifest(Path(args.manifest))
    dataset_id = args.dataset_id or f"frame-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = Path(args.out) / dataset_id
    samples_path = run_dir / "samples.jsonl"

    settings = load_settings()
    if args.projection_enforce_smoke:
        problems = fc.projection_enforce_smoke_env_preflight(
            os.environ, require_inline=args.execute
        )
    elif args.projection_smoke:
        problems = fc.projection_smoke_env_preflight(
            os.environ, require_inline=args.execute
        )
    else:
        problems = fc.env_preflight(os.environ, require_inline=args.execute)
    if len(_arm_rows(arms, args.reps)) != len(arms) * args.reps:
        problems.append("invalid_reps")
    if args.prepare_test_truth:
        problems.extend(_prepare_test_truth_guard(arms, actor_id=args.actor_id))
    source_manifest = any(arm.get("sourcePath") or arm.get("sourceImages") for arm in arms)
    if args.execute or source_manifest:
        problems.extend(_validate_execute_manifest(arms, minimum_arms=1))
        problems.extend(_validate_collection_shape(
            arms, reps=args.reps,
            smoke=args.smoke or args.projection_smoke or args.projection_enforce_smoke,
        ))
        if args.projection_smoke or args.projection_enforce_smoke:
            problems.extend(_validate_projection_smoke_shape(arms, reps=args.reps))
    if problems:
        _write_preflight_failure(run_dir, dataset_id, sorted(set(problems)))
        print(f"REFUSING frame collection: {sorted(set(problems))}")
        return 2

    expected_run = _expected_run(
        settings,
        image_size=(settings.mannequin_pattern_image_size
                    if args.projection_enforce_smoke else None),
    )
    if args.resume:
        try:
            fc.assert_resumable(samples_path, expected_run=expected_run)
        except fc.FrameCalibrationError as exc:
            print(f"REFUSING resume: {exc}")
            return 3
    existing_rows = fc.load_jsonl(samples_path) if args.resume else []

    if not args.execute:
        print("preflight ok; provider calls=0. Pass --execute to collect paid samples.")
        print(f"dataset={dataset_id} arms={len(arms)} reps={args.reps}")
        return 0

    rows = asyncio.run(_execute_collection(
        api_base=args.api, run_dir=run_dir, dataset_id=dataset_id,
        arms=arms, reps=args.reps, expected_run=expected_run,
        existing_rows=existing_rows,
        projection_smoke=args.projection_smoke or args.projection_enforce_smoke))
    manifest = fc.manifest_for_rows(dataset_id=dataset_id, rows=rows)
    row_provenance_valid = manifest["validForCalibration"]
    manifest["purpose"] = (
        "projection_enforce_smoke" if args.projection_enforce_smoke
        else "projection_smoke" if args.projection_smoke
        else "smoke" if args.smoke else "calibration"
    )
    manifest["smokeOnly"] = bool(
        args.smoke or args.projection_smoke or args.projection_enforce_smoke
    )
    if args.smoke or args.projection_smoke or args.projection_enforce_smoke:
        manifest["validForCalibration"] = False
        manifest["calibrationEligibilityProblems"] = [
            "projection_enforce_smoke_only" if args.projection_enforce_smoke
            else "projection_smoke_only" if args.projection_smoke else "smoke_only"
        ]
    projection_wiring_passed = True
    projection_quality_passed = True
    if args.projection_smoke or args.projection_enforce_smoke:
        projection_rows = [
            (row.get("provenance") or {}).get("projectionSmoke") or {}
            for row in rows
        ]
        projection_wiring_passed = bool(projection_rows) and all(
            row.get("wiringPassed") is True for row in projection_rows
        )
        projection_quality_passed = bool(projection_rows) and all(
            row.get("qualityPassed") is True for row in projection_rows
        )
        manifest["projectionSmoke"] = {
            "wiringPassed": projection_wiring_passed,
            "qualityPassed": projection_quality_passed,
            "sampleCount": len(projection_rows),
        }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"collected samples={len(rows)} provider_image_calls={sum(r['imageCallsAttempted'] for r in rows)}")
    if args.projection_enforce_smoke:
        return 0 if (row_provenance_valid
                     and projection_wiring_passed
                     and projection_quality_passed) else 6
    if args.projection_smoke:
        return 0 if row_provenance_valid and projection_wiring_passed else 6
    return 0 if (row_provenance_valid and args.smoke) or manifest["validForCalibration"] else 5


if __name__ == "__main__":
    sys.exit(main())
