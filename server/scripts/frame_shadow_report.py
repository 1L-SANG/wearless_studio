"""Frame Lock calibration report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import frame_calibration as fc


def main() -> int:
    ap = argparse.ArgumentParser(description="Frame Lock shadow report")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--dataset-id", required=True)
    args = ap.parse_args()

    dataset_dir = Path(args.dataset_dir)
    rows = fc.load_jsonl(dataset_dir / "samples.jsonl")
    try:
        labels = fc.load_labels(dataset_dir / "labels.jsonl")
    except fc.FrameCalibrationError as exc:
        print(f"REFUSING report: {exc}")
        return 4
    manifest_path = dataset_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) \
        if manifest_path.exists() else None
    out = fc.report(rows, labels, manifest=manifest)
    out["datasetId"] = args.dataset_id
    (dataset_dir / "frame_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["enforceReadyCandidate"] else 5


if __name__ == "__main__":
    sys.exit(main())
