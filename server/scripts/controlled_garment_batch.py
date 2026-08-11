"""Controlled real-garment QA batch — Tier-1 evidence, three image calls per job.

P3.5 found zero replayable production guided cases in the whole history. Not because the
guided path never ran (it ran twice) but because the torso ROI only reaches disk when
HYBRID_COMPOSITE_ARTIFACT_DIR is set, and production does not set it. This batch exists to
produce evidence that CAN be replayed: same source bytes, same crop, same pattern model.

Design notes, all of them load-bearing:

* Jobs run INLINE in this process, via the harness `InlineWorker` that smoke_realwire
  already uses. That is what puts the artifact directory under our control — the API
  server is started with its dispatcher off so it cannot claim a job and write the
  evidence somewhere else.
* Every product belongs to the QA smoke user, which is who already owns the goldenset.
  No real-user project is touched.
* The provider budget committed in 36d131d is read back off the job row after every job.
  A job that somehow spent four image calls stops the batch rather than being averaged
  into a summary.
* Nothing here changes an algorithm, a threshold or a prompt. One batch, one policy.

Run:
  cd server && HYBRID_COMPOSITE_ARTIFACT_DIR=<batch dir> \
    .venv/bin/python -m scripts.controlled_garment_batch --run-id <id> --targets canary
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from scripts._env import load_env

load_env()

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.services import controlled_qa_preflight as preflight   # noqa: E402
from app.services import image_budget as ib                     # noqa: E402
from scripts.smoke_realwire import Api, InlineWorker, ensure_smoke_session  # noqa: E402

API_BASE = os.getenv("QA_API_BASE", "http://127.0.0.1:8000")
INLINE_HEADER = "X-Wearless-Frame-Calibration"

#: Products already in the QA account, seeded from the same garment folder this batch
#: draws from. Reusing them avoids re-uploading bytes that are already the production
#: source of record — and they carry approved truth packages.
GOLDENSET = {
    "stripe-shirt":   {"project": "c7f00166-92a1-4be2-8d47-338808fc4eca", "pattern": "STRIPE"},
    "check-shirt":    {"project": "96610dbd-7bb5-4133-a703-3630276fa66e", "pattern": "CHECK"},
    "lace-top":       {"project": "0db50de3-ab1f-490c-8cc2-c0dff8686a3e", "pattern": "LACE"},
    "sheer-top":      {"project": "719996ef-d750-4c7c-9b98-05c7c45c1416", "pattern": "SHEER"},
    "red-rib-blouse": {"project": "1f0ad6d8-cd87-48a4-bfd9-881e325a1667", "pattern": "RIB"},
    # batch2: seeded from the user's 줄무늬나시 photos. Its truth says STRIPE because the
    # analyze worker classified it that way, not because the filename does.
    "stripe-tank":    {"project": "f7ca2f60-224f-4804-9078-28c67030d6ab", "pattern": "STRIPE"},
}


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


async def read_budget(worker: InlineWorker, job_id: str) -> dict:
    from app import repo
    async with worker.pool.connection() as conn:
        return await repo.read_image_budget(conn, job_id=job_id)


async def job_step_events(worker: InlineWorker, job_id: str) -> list:
    async with worker.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select event_type, payload from job_events where job_id = %s order by id",
                (job_id,))
            return [dict(r["payload"] or {}) for r in await cur.fetchall()
                    if r["event_type"] == "step"]


def classify_outset(shadow: dict | None) -> str:
    """Baseline failed, then what — the three outcomes counted apart (P3.5 §9)."""
    if not shadow:
        return "NO_SHADOW"
    rois = shadow.get("roiResults") or []
    base = next((r for r in rois if r.get("roiId") == "baseline"), None)
    outs = [r for r in rois if r.get("roiId") != "baseline"]
    if shadow.get("uncertaintyReason") == "CONFLICTING_CLUSTERS":
        return "OUTSET_DISAGREEMENT"
    if base is None:
        return "NO_SHADOW"
    if base.get("success"):
        return "NO_RESCUE_NEEDED"
    if not any(o.get("success") for o in outs):
        return "NO_RESCUE_NEEDED"      # nothing succeeded anywhere; not a rescue case
    return ("RESCUE_TO_CONSENSUS" if shadow.get("status") == "RELIABLE"
            else "RESCUE_INSUFFICIENT")


def read_artifacts(batch_dir: Path, job_id: str) -> dict:
    """Everything the run wrote for this job. Absence is recorded, never assumed away."""
    root = batch_dir / str(job_id)
    out = {"root": str(root), "exists": root.exists(), "contextFiles": [],
           "provenanceFiles": [], "sourceTorsoRoi": None, "sourceContext": None,
           "guidedCandidates": None, "shadowMultiRoi": None, "executionScopes": []}
    if not root.exists():
        return out
    out["executionScopes"] = sorted(p.name for p in root.iterdir() if p.is_dir())
    for ctx in sorted(root.rglob("source_texture_context.json")):
        out["contextFiles"].append(str(ctx.relative_to(batch_dir)))
        try:
            out["sourceContext"] = json.loads(ctx.read_text())
        except Exception:
            pass
    for prov in sorted(root.rglob("provenance.json")):
        out["provenanceFiles"].append(str(prov.relative_to(batch_dir)))
        try:
            p = json.loads(prov.read_text())
        except Exception:
            continue
        out["sourceTorsoRoi"] = p.get("sourceRoi") or out["sourceTorsoRoi"]
        g = p.get("guided") or {}
        if g.get("candidates"):
            out["guidedCandidates"] = g["candidates"]
        if p.get("shadowMultiRoi"):
            out["shadowMultiRoi"] = p["shadowMultiRoi"]
    return out


def dataset_validity(record: dict) -> tuple[bool, list]:
    """§9 — a job missing any of these cannot be Tier-1, whatever else it produced."""
    missing = []
    a = record["artifacts"]
    if not a["exists"]:
        missing.append("no artifact directory")
    if record["legacyPath"] in ("SCAN", "GUIDED", "UNRESOLVED"):
        if not a["sourceTorsoRoi"]:
            missing.append("production torso ROI not persisted")
        if not a["sourceContext"]:
            missing.append("SourceTextureContext not persisted")
        if record["legacyPath"] == "GUIDED" and not a["guidedCandidates"]:
            missing.append("guided ran but its candidate table was not persisted")
    if not record.get("sourceSha256"):
        missing.append("source sha unknown")
    return (not missing), missing


async def run_one(*, api: Api, worker: InlineWorker, batch_dir: Path,
                  label: str, project_id: str, pattern: str) -> dict:
    """One product → one mannequin job → one evidence record."""
    print(f"\n=== {label}  project={project_id[:8]}  pattern={pattern}", flush=True)
    existing = api.call("GET", f"/v1/projects/{project_id}/mannequins")
    # the route returns a bare list of cuts; older callers expected {"data": [...]}
    has_cuts = bool(existing.get("data") if isinstance(existing, dict) else existing)
    endpoint = (f"/v1/projects/{project_id}/mannequins:regenerate" if has_cuts
                else f"/v1/projects/{project_id}/mannequins:generate")
    # Preclaimed, not polled. A deployed worker on this same database took our first job
    # within seconds and ran it on pre-budget code; the route claims the job inside its own
    # transaction so it is never visible as pending.
    secret = os.environ["FRAME_CALIBRATION_INLINE_SECRET"]
    gen = api.call("POST", endpoint, headers={INLINE_HEADER: secret},
                   **({"json": {}} if has_cuts else {}))
    job_id = gen["jobId"]
    lease = gen.get("leaseToken")
    if not lease:
        raise RuntimeError("route did not preclaim the job — a poller could still take it")
    print(f"    job {job_id}  ({'regenerate' if has_cuts else 'generate'}, preclaimed)",
          flush=True)

    t0 = time.time()
    who = await worker.run_preclaimed(job_id, lease)
    if who != "claimed":
        raise RuntimeError(f"job {job_id} was claimed by another dispatcher — batch unsafe")
    completed = api.poll_job(job_id, timeout_s=600)
    events = await job_step_events(worker, job_id)
    budget = await read_budget(worker, job_id)

    anchor = next((e for e in events if e.get("status") == "hybrid_scale_anchor"), None)
    model = next((e for e in events if e.get("status") == "hybrid_stripe_model"
                  and e.get("ok")), None)
    done = [e for e in events if e.get("status") == "hybrid_composite_completed"]
    palette = any(e.get("status") == "hybrid_palette_source" for e in events)
    artifacts = read_artifacts(batch_dir, job_id)

    if anchor is None:
        legacy = "NOT_REQUIRED"
    else:
        period = float(anchor.get("front_period_px") or 0)
        legacy = "SCAN" if palette or period % 1 else "GUIDED"
    if artifacts["guidedCandidates"]:
        legacy = "GUIDED"

    shadow = artifacts["shadowMultiRoi"]
    record = {
        "label": label, "pattern": pattern, "projectId": project_id, "jobId": job_id,
        "status": completed.get("status"),
        "sourceSha256": (artifacts["sourceContext"] or {}).get("sourceSha256")
                        or (model or {}).get("source_sha256"),
        "productionTorsoRoi": artifacts["sourceTorsoRoi"],
        "legacyPath": legacy,
        "legacyPeriodPx": (anchor or {}).get("front_period_px"),
        "legacyConfidence": (anchor or {}).get("anchor_corr"),
        "guidedCandidates": artifacts["guidedCandidates"],
        "shadow": shadow,
        "shadowStatus": (shadow or {}).get("status"),
        "outsetClass": classify_outset(shadow),
        "budget": budget,
        "providerImageCalls": budget.get("total"),
        "compositeOutcome": [d.get("outcome") for d in done],
        "elapsedS": round(time.time() - t0, 1),
        "artifacts": artifacts,
    }
    valid, missing = dataset_validity(record)
    record["tier1Valid"] = valid
    record["invalidReasons"] = missing
    print(f"    -> {record['status']}  legacy={legacy}  shadow={record['shadowStatus']}"
          f"  imageCalls={record['providerImageCalls']}  tier1={valid}", flush=True)
    if missing:
        print(f"       missing: {'; '.join(missing)}", flush=True)
    return record


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--targets", default="canary",
                    help="canary | goldenset | comma-separated goldenset keys")
    args = ap.parse_args()

    batch_dir = Path(os.environ["HYBRID_COMPOSITE_ARTIFACT_DIR"])
    report = preflight.require(artifact_dir=str(batch_dir))
    print("preflight:", json.dumps(report.as_dict(), ensure_ascii=False))

    if args.targets == "canary":
        keys = ["stripe-shirt", "lace-top"]
    elif args.targets == "goldenset":
        keys = list(GOLDENSET)
    else:
        keys = [k.strip() for k in args.targets.split(",") if k.strip()]

    token = ensure_smoke_session()
    api = Api(API_BASE, token)
    worker = InlineWorker()
    await worker.open()
    records, aborted = [], None
    try:
        for key in keys:
            spec = GOLDENSET[key]
            rec = await run_one(api=api, worker=worker, batch_dir=batch_dir,
                                label=f"goldenset-{key}", project_id=spec["project"],
                                pattern=spec["pattern"])
            records.append(rec)
            # §12 — a budget overrun stops the batch. Reporting it afterwards would mean
            # the next job already spent money under a rule we know is broken.
            if (rec["providerImageCalls"] or 0) > ib.MAX_TOTAL:
                aborted = f"{rec['jobId']} spent {rec['providerImageCalls']} image calls"
                break
    finally:
        await worker.close()

    # one file per run — a fixed name overwrote the previous product's record, and the
    # dataset rule is that earlier executions are never destroyed
    out = batch_dir / f"batch_records_{args.run_id}.json"
    out.write_text(json.dumps(
        {"runId": args.run_id, "targets": keys, "aborted": aborted, "records": records},
        ensure_ascii=False, indent=1))
    print(f"\nwrote {out}")
    print(json.dumps({"jobs": len(records), "aborted": aborted,
                      "tier1Valid": sum(1 for r in records if r["tier1Valid"]),
                      "maxImageCalls": max([r["providerImageCalls"] or 0
                                            for r in records] or [0])}, indent=1))
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
