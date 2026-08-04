"""Append a blinded Frame Lock label.

The browser UI can be added later; this CLI is enough for deterministic tests
and small local calibration batches.  It validates the sample/provenance before
writing an append-only hash-chain event.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import frame_calibration as fc


def _bool_arg(value: str) -> bool:
    v = str(value).strip().lower()
    if v in ("1", "true", "yes", "y", "pass", "ok"):
        return True
    if v in ("0", "false", "no", "n", "fail", "bad"):
        return False
    raise argparse.ArgumentTypeError(f"boolean expected: {value}")


def _sample(samples_path: Path, sample_id: str) -> dict:
    rows = fc.load_jsonl(samples_path)
    for row in rows:
        if row.get("id") == sample_id:
            return row
    raise SystemExit("sample_not_found")


def main() -> int:
    ap = argparse.ArgumentParser(description="append blinded Frame Lock label")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--sample-id", required=True)
    ap.add_argument("--reviewer-id", required=True)
    ap.add_argument("--pose-ok", type=_bool_arg, required=True)
    ap.add_argument("--view-family-ok", type=_bool_arg, required=True)
    ap.add_argument("--full-body-crop-ok", type=_bool_arg, required=True)
    ap.add_argument("--framing-ok", type=_bool_arg, required=True)
    ap.add_argument("--background-ok", type=_bool_arg)
    ap.add_argument("--lighting-ok", type=_bool_arg)
    ap.add_argument("--note")
    args = ap.parse_args()

    dataset_dir = Path(args.dataset_dir)
    sample = _sample(dataset_dir / "samples.jsonl", args.sample_id)
    try:
        record = fc.make_label(
            sample=sample,
            reviewer_id=args.reviewer_id,
            dataset_id=args.dataset_id,
            pose_ok=args.pose_ok,
            view_family_ok=args.view_family_ok,
            full_body_crop_ok=args.full_body_crop_ok,
            framing_ok=args.framing_ok,
            background_ok=args.background_ok,
            lighting_ok=args.lighting_ok,
            note=args.note,
        )
        written = fc.append_label(dataset_dir / "labels.jsonl", record)
    except fc.FrameCalibrationError as exc:
        print(f"REFUSING label: {exc}")
        return 2
    print(json.dumps({
        "eventId": written["eventId"],
        "sampleId": written["sampleId"],
        "datasetId": written["datasetId"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
