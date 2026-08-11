"""Seed ONE real garment through the official product flow, and let analyze decide.

P3's stripe resolver only engages for `SUPPORTED_PATTERN_TYPES = {"stripe", "stripes"}`,
and the QA account currently holds exactly one product whose approved truth says STRIPE.
One source is not a dataset, so this registers a second candidate.

The point of care: the file is called 줄무늬나시, which means "striped sleeveless top" — and
that is not evidence. The pattern type comes from the real analyze worker and the truth
draft it produces. This script **refuses to approve** a draft that does not already say
STRIPE, because approving one that does not would be manufacturing the number P3 activation
is supposed to measure.

Same endpoints the existing harness uses: projects → assets → product → analyze →
truth:draft → truth:approve. No direct DB writes, no schema of its own, no approval bypass.
Vision/text provider calls happen inside analyze; no mannequin image is generated here.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import unicodedata
from pathlib import Path

import httpx

from scripts._env import load_env

load_env()

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import Api, InlineWorker, ensure_smoke_session  # noqa: E402

API_BASE = os.getenv("QA_API_BASE", "http://127.0.0.1:8000")
INLINE_HEADER = "X-Wearless-Frame-Calibration"
GARMENTS = Path("/Users/nojeong-un/Downloads/노션에 있는 의상들")

#: role is decided by the filename the user already uses, not by content
ROLE_HINTS = (("디테일", "Detail"), ("앞면", "Front"), ("뒷면", "Back"), ("후면", "Back"))


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def collect(group: str) -> list:
    """Every file of one product group, with its slot. Grouping matches the inventory."""
    out = []
    for f in sorted(GARMENTS.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        stem = nfc(f.stem)
        if group not in stem:
            continue
        slot = next((r for hint, r in ROLE_HINTS if hint in stem), "Front")
        data = f.read_bytes()
        out.append({"path": f, "name": nfc(f.name), "slot": slot, "bytes": data,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mime": "image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg")
                            else "image/heic"})
    # one Front, and Detail is what the pattern model prefers
    seen_front = False
    for e in out:
        if e["slot"] == "Front" and seen_front:
            e["slot"] = "Detail"
        seen_front = seen_front or e["slot"] == "Front"
    return out


def upload(api: Api, project_id: str, entry: dict) -> dict:
    up = api.call("POST", "/v1/assets/upload-url", json={
        "filename": entry["name"], "mime": entry["mime"],
        "size": len(entry["bytes"]), "projectId": project_id})
    with httpx.Client(timeout=120) as c:
        r = c.put(up["uploadUrl"], content=entry["bytes"],
                  headers={"Content-Type": entry["mime"]})
        r.raise_for_status()
    asset = api.call("POST", f"/v1/assets/{up['assetId']}/complete", json={
        "projectId": project_id, "mime": entry["mime"], "filename": entry["name"]})
    return {"slot": entry["slot"], "id": up["assetId"], "url": asset["url"],
            "sha256": entry["sha256"], "file": entry["name"]}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="줄무늬나시")
    ap.add_argument("--name", default="goldenset-stripe-tank")
    ap.add_argument("--clothing-type", default="top")
    ap.add_argument("--out", required=True, help="batch2 root for the seed report")
    args = ap.parse_args()

    entries = collect(nfc(args.group))
    if not entries:
        print(f"no files for group {args.group}")
        return 1
    print(f"group {args.group}: {len(entries)} files")
    for e in entries:
        print(f"   {e['slot']:7s} {e['name']}  {len(e['bytes'])}B  {e['sha256'][:12]}")

    secret = os.environ["FRAME_CALIBRATION_INLINE_SECRET"]
    api = Api(API_BASE, ensure_smoke_session())
    worker = InlineWorker()
    await worker.open()
    report = {"group": args.group, "files": [
        {"file": e["name"], "slot": e["slot"], "sha256": e["sha256"],
         "bytes": len(e["bytes"])} for e in entries]}
    try:
        project = api.call("POST", "/v1/projects")
        pid = project["id"]
        report["projectId"] = pid
        print(f"\nproject {pid}")

        assets = [upload(api, pid, e) for e in entries]
        report["assets"] = assets
        print(f"uploaded {len(assets)} assets")

        api.call("PATCH", f"/v1/projects/{pid}/product", json={
            "name": args.name, "clothingType": args.clothing_type,
            "metadata": {"goldenset": True, "stripeDatasetCandidate": True},
            "colors": [{"id": "base", "label": "unknown", "isBase": True,
                        "images": [{"slot": a["slot"], "id": a["id"], "url": a["url"]}
                                   for a in assets]}]})

        analyze = api.call("POST", f"/v1/projects/{pid}/analyze",
                           headers={INLINE_HEADER: secret})
        if analyze.get("jobId"):
            who = await worker.run_preclaimed(analyze["jobId"], analyze["leaseToken"])
            if who != "claimed":
                raise RuntimeError("analyze job was not owned by the local preclaim")
            done = api.poll_job(analyze["jobId"], timeout_s=300)
            report["analyzeJobId"] = analyze["jobId"]
            report["analyzeStatus"] = done.get("status")
            print(f"analyze {analyze['jobId'][:8]} -> {done.get('status')}")
            if done.get("status") != "done":
                report["result"] = "ANALYZE_FAILED"
                return 1

        draft = api.call("POST", f"/v1/projects/{pid}/product-truth:draft")
        spec = (draft.get("patternSpec") or draft.get("pattern_spec") or {})
        pattern_type = str(spec.get("type") or "UNKNOWN").upper()
        report["truthDraftId"] = draft.get("id")
        report["draftPatternSpec"] = spec
        report["draftPatternType"] = pattern_type
        print(f"\ntruth draft {draft.get('id')}  patternType={pattern_type}")
        print("  patternSpec:", json.dumps(spec, ensure_ascii=False)[:400])

        # The refusal that matters. A draft that does not already say STRIPE is not made
        # to say it — the whole point of the dataset is that the classification is earned.
        if pattern_type not in ("STRIPE", "STRIPES"):
            report["eligibleForStripeDataset"] = False
            report["result"] = "NOT_STRIPE_FOR_P3_DATASET"
            report["approved"] = False
            print(f"\nanalyze did not classify this as STRIPE ({pattern_type}); "
                  "leaving the draft unapproved")
            return 0

        approved = api.call("POST", f"/v1/projects/{pid}/product-truth/{draft['id']}:approve")
        report["approvedStatus"] = approved.get("status")
        report["approved"] = approved.get("status") == "approved"
        report["eligibleForStripeDataset"] = bool(report["approved"])
        report["result"] = ("STRIPE_TRUTH_APPROVED" if report["approved"]
                            else "APPROVAL_FAILED")
        print(f"approve -> {approved.get('status')}")
        return 0
    finally:
        await worker.close()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "seed_stripe_candidate.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1))
        print(f"\nwrote {out / 'seed_stripe_candidate.json'}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
