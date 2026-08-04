"""Frame Lock calibration artifacts.

Frame Lock calibration is intentionally separate from editor-vary shadow
calibration.  The question is not "did the requested edit preserve fidelity?"
but "did the generated mannequin keep the canonical camera/pose/frame?".

The module is pure except for the append-only label writer.  Collector scripts
can use it before any provider call to fail closed on mixed/stale conditions.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import time
from pathlib import Path

from .safe_paths import is_sha256_hex

POLICY_VERSION = "frame_lock_calibration_v1"
LABEL_POLICY_VERSION = "frame_blinded_label_v1"
GENESIS = "0" * 64

CRITICAL_FIELDS = ("poseOk", "viewFamilyOk", "fullBodyCropOk", "framingOk")
LABEL_FIELDS = CRITICAL_FIELDS + ("backgroundOk", "lightingOk")

RUN_FIELDS = (
    "generationModel",
    "generationPromptVersion",
    "generationPromptSha256",
    "frameVisionPromptVersion",
    "frameVisionPromptSha256",
    "qcPolicyVersion",
    "codeCommit",
    "imageSize",
    "imageSizeCap",
    "aspectRatio",
    "frameQcMode",
    "hybridCompositeMode",
    "textureProjectionMode",
)

ROW_FIELDS = (
    "sourceSha256",
    "outputSha256",
    "baseAssetSha256",
    "callAttemptIndex",
    "frameDeterministicDecision",
    "frameVisionStatus",
)


class FrameCalibrationError(RuntimeError):
    """Frame calibration artifact cannot be trusted."""


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def env_preflight(
    env: dict | None = None, *, require_inline: bool = False
) -> list[str]:
    """Return fail-closed preflight problem codes for Frame 1K calibration."""
    env = env or os.environ
    problems: list[str] = []
    if str(env.get("MANNEQUIN_IMAGE_SIZE", "")).upper() != "1K":
        problems.append("image_size_not_1k")
    if str(env.get("MANNEQUIN_IMAGE_SIZE_CAP", "")).upper() != "1K":
        problems.append("image_size_cap_not_1k")
    if str(env.get("MANNEQUIN_FRAME_QC", "")).lower() != "shadow":
        problems.append("frame_qc_not_shadow")
    if str(env.get("MANNEQUIN_HYBRID_COMPOSITE", "off")).lower() not in ("off", ""):
        problems.append("hybrid_composite_not_off")
    if str(env.get("MANNEQUIN_TEXTURE_PROJECTION_2D", "off")).lower() not in ("off", ""):
        problems.append("texture_projection_not_off")
    if str(env.get("MANNEQUIN_MAX_ATTEMPTS", "")) != "1":
        problems.append("max_attempts_not_1")
    if str(env.get("MANNEQUIN_UNTUCK_PASS", "off")).lower() not in ("off", ""):
        problems.append("untuck_pass_not_off")
    if str(env.get("MANNEQUIN_BUST_PASS", "off")).lower() not in ("off", ""):
        problems.append("bust_pass_not_off")
    if str(env.get("MANNEQUIN_AXIS_QC", "off")).lower() not in ("off", "shadow", ""):
        problems.append("axis_qc_not_nonblocking")
    if str(env.get("GARMENT_QC_EXTRA_CANDIDATES", "0")) not in ("0", ""):
        problems.append("extra_candidates_not_0")
    if str(env.get("GARMENT_QC_MODE", "off")).lower() not in ("off", ""):
        problems.append("garment_qc_mode_not_off")
    if str(env.get("IMAGE_QC", "off")).lower() not in ("off", ""):
        problems.append("image_qc_not_off")
    if str(env.get("MANNEQUIN_QC_ENABLED", "false")).lower() not in ("false", "0", ""):
        problems.append("legacy_mannequin_qc_enabled")
    if str(env.get("RETRIEVAL_REFIMAGES", "off")).lower() not in ("off", ""):
        problems.append("retrieval_refimages_not_off")
    if str(env.get("MANNEQUIN_STRUCTURED_QC", "off")).lower() not in ("off", ""):
        problems.append("structured_qc_not_off")
    if require_inline:
        if str(env.get("JOB_DISPATCHER_ENABLED", "true")).lower() not in ("false", "0"):
            problems.append("job_dispatcher_not_off")
        if str(env.get("FRAME_CALIBRATION_INLINE_JOBS", "false")).lower() != "true":
            problems.append("inline_jobs_not_enabled")
        if not str(env.get("FRAME_CALIBRATION_INLINE_SECRET", "")).strip():
            problems.append("inline_secret_missing")
    return problems


def projection_smoke_env_preflight(
    env: dict | None = None, *, require_inline: bool = False
) -> list[str]:
    """Fail closed unless the runtime isolates one shadow projection call.

    Frame calibration keeps both hybrid stages off.  A projection smoke uses
    the same one-call protections but requires both deterministic stages in
    shadow so the carrier image is preserved while wiring/quality metrics run.
    """
    env = env or os.environ
    common_env = dict(env)
    common_env["MANNEQUIN_HYBRID_COMPOSITE"] = "off"
    common_env["MANNEQUIN_TEXTURE_PROJECTION_2D"] = "off"
    problems = env_preflight(common_env, require_inline=require_inline)
    if str(env.get("MANNEQUIN_HYBRID_COMPOSITE", "")).lower() != "shadow":
        problems.append("hybrid_composite_not_shadow")
    if str(env.get("MANNEQUIN_TEXTURE_PROJECTION_2D", "")).lower() != "shadow":
        problems.append("texture_projection_not_shadow")
    return problems


def run_fingerprint(*, generation_model: str, generation_prompt_version: str,
                    generation_prompt: str | bytes,
                    frame_vision_prompt_version: str,
                    frame_vision_prompt: str | bytes,
                    qc_policy_version: str = POLICY_VERSION,
                    code_commit: str, image_size: str = "1K",
                    image_size_cap: str = "1K", aspect_ratio: str = "2:3",
                    frame_qc_mode: str = "shadow",
                    hybrid_composite_mode: str = "off",
                    texture_projection_mode: str = "off") -> dict:
    gp = generation_prompt if isinstance(generation_prompt, bytes) else \
        str(generation_prompt).encode()
    vp = frame_vision_prompt if isinstance(frame_vision_prompt, bytes) else \
        str(frame_vision_prompt).encode()
    return {
        "generationModel": generation_model,
        "generationPromptVersion": generation_prompt_version,
        "generationPromptSha256": sha256_hex(gp),
        "frameVisionPromptVersion": frame_vision_prompt_version,
        "frameVisionPromptSha256": sha256_hex(vp),
        "qcPolicyVersion": qc_policy_version,
        "codeCommit": code_commit,
        "imageSize": image_size,
        "imageSizeCap": image_size_cap,
        "aspectRatio": aspect_ratio,
        "frameQcMode": frame_qc_mode,
        "hybridCompositeMode": hybrid_composite_mode,
        "textureProjectionMode": texture_projection_mode,
    }


def make_sample_id(*, arm: str, project_id: str, rep: int) -> str:
    # project_id is deliberately excluded. Source-path based execute mode creates
    # test projects on first collection, so including the generated project id
    # would make resume unable to identify completed arm×rep samples.
    raw = f"{arm}:{rep}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def provenance(*, run: dict, source_bytes: bytes, output_bytes: bytes,
               base_bytes: bytes, call_attempt_index: int,
               frame_qc_result: dict | None) -> dict:
    result = frame_qc_result or {}
    return {
        "sourceSha256": sha256_hex(source_bytes),
        "outputSha256": sha256_hex(output_bytes),
        "baseAssetSha256": sha256_hex(base_bytes),
        "callAttemptIndex": int(call_attempt_index),
        "frameDeterministicDecision": result.get("decision"),
        "frameVisionStatus": ((result.get("visionMeta") or {}).get("status")
                              or ("missing" if not result.get("visionMeta") else None)),
        "run": dict(run),
        "frameQc": {
            "decision": result.get("decision"),
            "criticalErrors": list(result.get("criticalErrors") or []),
            "warnings": list(result.get("warnings") or []),
            "checks": result.get("checks") or {},
            "metrics": result.get("metrics") or {},
            "visionMeta": result.get("visionMeta") or {},
        },
    }


def sample_row(*, dataset_id: str, arm: str, project_id: str, rep: int,
               source_name: str, output_name: str, base_name: str,
               prov: dict, image_calls_attempted: int = 1) -> dict:
    return {
        "id": make_sample_id(arm=arm, project_id=project_id, rep=rep),
        "datasetId": dataset_id,
        "arm": arm,
        "projectId": project_id,
        "rep": int(rep),
        "sourceImage": source_name,
        "outputImage": output_name,
        "baseImage": base_name,
        "provenance": prov,
        "output_id": output_name,
        "imageCallsAttempted": int(image_calls_attempted),
    }


def _row_problems(row: dict) -> list[str]:
    prov = (row or {}).get("provenance") or {}
    if not prov:
        return ["missing_provenance"]
    out: list[str] = []
    for field in ROW_FIELDS:
        if prov.get(field) in (None, ""):
            out.append(f"missing_row_field:{field}")
    for field in ("sourceSha256", "outputSha256", "baseAssetSha256"):
        if prov.get(field) is not None and not is_sha256_hex(prov.get(field)):
            out.append(f"invalid_sha:{field}")
    run = prov.get("run") or {}
    if not run:
        out.append("missing_run_fingerprint")
    for field in RUN_FIELDS:
        if run.get(field) in (None, ""):
            out.append(f"missing_run_field:{field}")
    for field in ("generationPromptSha256", "frameVisionPromptSha256"):
        if run.get(field) is not None and not is_sha256_hex(run.get(field)):
            out.append(f"invalid_sha:run.{field}")
    return out


def dataset_problems(rows: list[dict], *, expected_run: dict | None = None,
                     manifest: dict | None = None) -> list[str]:
    problems: set[str] = set()
    if not rows:
        problems.add("no_rows")
    image_calls = 0
    run_keys = set()
    for row in rows:
        problems.update(_row_problems(row))
        run = ((row.get("provenance") or {}).get("run") or {})
        if run:
            run_keys.add(canonical({k: run.get(k) for k in RUN_FIELDS}))
        if manifest and row.get("datasetId") != manifest.get("datasetId"):
            problems.add("dataset_mismatch")
        calls = row.get("imageCallsAttempted")
        if not isinstance(calls, int) or calls < 1:
            problems.add("invalid_image_calls")
        else:
            image_calls += calls
    if rows and image_calls != len(rows):
        problems.add("image_calls_not_one_per_sample")
    if len(run_keys) > 1:
        problems.add("mixed_run_fingerprint")
    if expected_run:
        expected_key = canonical({k: expected_run.get(k) for k in RUN_FIELDS})
        for key in run_keys:
            if key != expected_key:
                problems.add("run_fingerprint_mismatch")
    if manifest:
        if manifest.get("validForCalibration") is False:
            problems.add("manifest_blocked")
        if manifest.get("provenanceProblems"):
            problems.add("manifest_provenance_problems")
    return sorted(problems)


def assert_resumable(samples_path: Path, *, expected_run: dict) -> None:
    if not samples_path.exists():
        return
    rows = load_jsonl(samples_path)
    problems = dataset_problems(rows, expected_run=expected_run)
    if problems:
        raise FrameCalibrationError(f"resume 조건 불일치: {problems}")


def manifest_for_rows(*, dataset_id: str, rows: list[dict],
                      source: str = "frame_shadow_collect") -> dict:
    problems = dataset_problems(rows)
    h = sha256_hex(canonical(rows)) if rows else None
    return {
        "datasetId": dataset_id,
        "source": source,
        "rawSampleManifestSha256": h,
        "validForCalibration": not problems,
        "provenanceUnverified": bool(problems),
        "provenanceProblems": problems,
        "sampleCount": len(rows),
        "policyVersion": POLICY_VERSION,
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _event_hash(record: dict, previous: str) -> str:
    body = {k: v for k, v in record.items() if k != "eventHash"}
    body["previousEventHash"] = previous
    return sha256_hex(canonical(body))


def verify_label_chain(records: list[dict]) -> None:
    prev = GENESIS
    seen = set()
    for i, rec in enumerate(records):
        event_id = rec.get("eventId")
        if not event_id:
            raise FrameCalibrationError(f"{i}번 라벨 eventId 없음")
        if event_id in seen:
            raise FrameCalibrationError(f"라벨 eventId 중복: {event_id}")
        seen.add(event_id)
        if rec.get("previousEventHash") != prev:
            raise FrameCalibrationError(f"{i}번 라벨 체인 단절")
        expected = _event_hash(rec, prev)
        if rec.get("eventHash") != expected:
            raise FrameCalibrationError(f"{i}번 라벨 해시 불일치")
        prev = rec["eventHash"]


def append_label(path: Path, record: dict) -> dict:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh.read().splitlines() if line.strip()]
        verify_label_chain(rows)
        prev = rows[-1]["eventHash"] if rows else GENESIS
        rec = dict(record)
        rec["eventId"] = rec.get("eventId") or sha256_hex(
            canonical([rec, prev, len(rows)]))[:32]
        rec["previousEventHash"] = prev
        rec["eventHash"] = _event_hash(rec, prev)
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return rec


def load_labels(path: Path, *, verify: bool = True) -> list[dict]:
    rows = load_jsonl(path)
    if verify:
        verify_label_chain(rows)
    return rows


def make_label(*, sample: dict, reviewer_id: str, dataset_id: str,
               pose_ok: bool, view_family_ok: bool, full_body_crop_ok: bool,
               framing_ok: bool, background_ok: bool | None = None,
               lighting_ok: bool | None = None, note: str | None = None,
               now: float | None = None) -> dict:
    prov = sample.get("provenance") or {}
    if sample.get("datasetId") != dataset_id:
        raise FrameCalibrationError("dataset_mismatch")
    if not prov.get("outputSha256"):
        raise FrameCalibrationError("missing_output_sha")
    for name, value in (
        ("poseOk", pose_ok),
        ("viewFamilyOk", view_family_ok),
        ("fullBodyCropOk", full_body_crop_ok),
        ("framingOk", framing_ok),
    ):
        if not isinstance(value, bool):
            raise FrameCalibrationError(f"label_field_invalid:{name}")
    label = {
        "poseOk": pose_ok,
        "viewFamilyOk": view_family_ok,
        "fullBodyCropOk": full_body_crop_ok,
        "framingOk": framing_ok,
        "backgroundOk": background_ok if isinstance(background_ok, bool) else None,
        "lightingOk": lighting_ok if isinstance(lighting_ok, bool) else None,
    }
    return {
        "datasetId": dataset_id,
        "sampleId": sample["id"],
        "sampleSha256": sha256_hex(canonical({
            "id": sample.get("id"),
            "sourceSha256": prov.get("sourceSha256"),
            "outputSha256": prov.get("outputSha256"),
            "baseAssetSha256": prov.get("baseAssetSha256"),
        })),
        "outputSha256": prov.get("outputSha256"),
        "label": label,
        "reviewerId": str(reviewer_id).strip(),
        "policyVersion": LABEL_POLICY_VERSION,
        "note": (str(note)[:500] if note else None),
        "labeledAt": now if now is not None else time.time(),
    }


def blind_presentation(sample: dict, *, seed: str = "") -> dict:
    """Return only images needed by a human; machine decision is never included."""
    rnd = random.Random(sha256_hex(f"{seed}:{sample.get('id')}".encode()))
    items = [
        {"kind": "base", "image": sample.get("baseImage")},
        {"kind": "candidate", "image": sample.get("outputImage")},
    ]
    rnd.shuffle(items)
    view = {"sampleId": sample.get("id"), "images": [
        {"slot": chr(ord("A") + i), "image": item["image"]} for i, item in enumerate(items)
    ]}
    forbidden = {"decision", "frameQc", "machineDecision", "criticalErrors", "warnings"}
    if forbidden & set(view):
        raise FrameCalibrationError("blinding_failed")
    return view


def _label_pass(label: dict) -> bool:
    return all(label.get(k) is True for k in CRITICAL_FIELDS)


def report(rows: list[dict], labels: list[dict], *,
           manifest: dict | None = None) -> dict:
    problems = dataset_problems(rows, manifest=manifest)
    by_sample = {r["sampleId"]: r for r in labels}
    false_passes = []
    labeled = 0
    for row in rows:
        lab = by_sample.get(row.get("id"))
        if not lab:
            continue
        labeled += 1
        prov = row.get("provenance") or {}
        sample_hash = sha256_hex(canonical({
            "id": row.get("id"),
            "sourceSha256": prov.get("sourceSha256"),
            "outputSha256": prov.get("outputSha256"),
            "baseAssetSha256": prov.get("baseAssetSha256"),
        }))
        if lab.get("sampleSha256") != sample_hash:
            problems.append("label_sample_hash_mismatch")
            continue
        machine = ((row.get("provenance") or {}).get("frameQc") or {}).get("decision")
        if machine == "pass" and not _label_pass(lab.get("label") or {}):
            false_passes.append(row.get("id"))
    ready = not problems and labeled == len(rows) and not false_passes
    return {
        "policyVersion": POLICY_VERSION,
        "validForCalibration": not problems,
        "enforceReadyCandidate": ready,
        "sampleCount": len(rows),
        "labeledCount": labeled,
        "criticalFalsePassCount": len(false_passes),
        "criticalFalsePassSampleIds": false_passes,
        "status": "enforce_candidate" if ready else (
            "blocked_by_manifest" if problems else "needs_labels_or_fixes"),
        "provenanceProblems": sorted(set(problems)),
    }
