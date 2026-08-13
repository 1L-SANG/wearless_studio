"""Latency/memory benchmark for the SAM2 service. Runs the SAME code path production runs.

Purpose: the deployment target is CPU-only ECS Fargate and every number measured so far came
from Apple MPS, which says nothing about it. This script is what gets run inside a staging
Fargate task, unchanged, to produce the numbers that decide compute sizing.

It measures four things separately, because they size different things:

  cold model load     -> healthcheck `start_period` and task warmup
  warm view inference -> the per-view figure that matters for the fallback path
  full HTTP request   -> what the caller actually waits for (fetch + inference + encode)
  RSS                 -> the `memory:` value in the Copilot manifest

Concurrency is deliberately 1. Establish the single-request cost before reasoning about load.

Usage (local, forcing CPU so the number is comparable to Fargate):
    SAM_DEVICE=cpu uv run python -m sam_service.benchmark --image path/to/front.jpg

Inside a deployed task (weights already baked, device is whatever the task has):
    python -m sam_service.benchmark --image /tmp/front.jpg --views Front,Back --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import time


def _rss_mb() -> float | None:
    """Resident set size without adding a dependency. Linux first, then macOS."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS reports bytes.
        return peak / 1024 if platform.system() == "Linux" else peak / (1024 * 1024)
    except Exception:                            # noqa: BLE001
        return None


def _environment() -> dict:
    env = {"platform": platform.platform(), "machine": platform.machine(),
           "processor": platform.processor() or None,
           "python": platform.python_version(),
           "cpuCount": os.cpu_count(),
           "samDevice": os.getenv("SAM_DEVICE") or "(auto)"}
    try:
        import torch
        env["torch"] = torch.__version__
        env["torchThreads"] = torch.get_num_threads()
    except Exception:                            # noqa: BLE001
        env["torch"] = None
    return env


def run(image_path: str, views: list[str]) -> dict:
    from sam_service.model import get_segmenter, reset_for_tests
    from sam_service.segmentation import MODEL_VERSION

    import asyncio

    data = pathlib.Path(image_path).read_bytes()
    out: dict = {"environment": _environment(), "modelVersion": MODEL_VERSION,
                 "image": {"path": image_path, "bytes": len(data)},
                 "rssBeforeMb": _rss_mb()}

    reset_for_tests()
    t0 = time.monotonic()
    segmenter = asyncio.run(get_segmenter())
    out["coldLoadSeconds"] = round(time.monotonic() - t0, 2)
    out["device"] = segmenter.device
    out["rssAfterLoadMb"] = _rss_mb()

    out["views"] = []
    for view in views:
        t0 = time.monotonic()
        try:
            cut = segmenter.cutout(data, view=view)
            out["views"].append({
                "view": view, "status": "ready",
                "warmInferenceSeconds": round(time.monotonic() - t0, 2),
                "width": cut.width, "height": cut.height, "areaFrac": cut.area_frac,
                "pngBytes": len(cut.png), "rssMb": _rss_mb()})
        except Exception as e:                   # noqa: BLE001
            out["views"].append({"view": view, "status": "failed",
                                 "seconds": round(time.monotonic() - t0, 2),
                                 "error": f"{type(e).__name__}: {e}"})

    out["rssPeakMb"] = _rss_mb()
    return out


def run_http(image_path: str, views: list[str]) -> dict:
    """Full /segment-garment latency, in-process, with only R2 stubbed."""
    from fastapi.testclient import TestClient

    from sam_service.api import create_app, get_settings
    from sam_service.config import SamSettings

    data = pathlib.Path(image_path).read_bytes()
    key = ("users/11111111-1111-1111-1111-111111111111/"
           "projects/22222222-2222-2222-2222-222222222222/uploads/front.jpg")

    class LocalSource:
        def fetch(self, _key):
            return data, "image/jpeg"

    app = create_app(source_factory=lambda _s: LocalSource())
    app.dependency_overrides[get_settings] = lambda: SamSettings(
        internal_token="bench", r2_account_id=None, r2_access_key_id=None,
        r2_secret_access_key=None, r2_bucket=None, r2_endpoint=None, model_id="")
    client = TestClient(app)

    t0 = time.monotonic()
    r = client.post("/segment-garment",
                    json={"views": {v: {"key": key} for v in views}},
                    headers={"Authorization": "Bearer bench"})
    elapsed = round(time.monotonic() - t0, 2)
    body = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    return {"httpStatus": r.status_code, "requestSeconds": elapsed,
            "status": body.get("status"),
            "perView": {v: {"status": d.get("status"), "latencyMs": d.get("latencyMs"),
                            "areaFrac": d.get("areaFrac")}
                        for v, d in (body.get("views") or {}).items()},
            "rssMb": _rss_mb()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--views", default="Front")
    ap.add_argument("--http", action="store_true", help="also time a full HTTP request")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    views = [v.strip() for v in args.views.split(",") if v.strip()]

    result = run(args.image, views)
    if args.http:
        result["http"] = run_http(args.image, views)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        env = result["environment"]
        print(f"device={result['device']} machine={env['machine']} cpus={env['cpuCount']} "
              f"torch={env['torch']} threads={env.get('torchThreads')}")
        print(f"cold model load : {result['coldLoadSeconds']}s")
        for v in result["views"]:
            if v["status"] == "ready":
                print(f"warm {v['view']:<6}     : {v['warmInferenceSeconds']}s "
                      f"({v['width']}x{v['height']}, areaFrac={v['areaFrac']})")
            else:
                print(f"warm {v['view']:<6}     : FAILED {v['error']}")
        if "http" in result:
            print(f"HTTP request    : {result['http']['requestSeconds']}s "
                  f"status={result['http']['status']}")
        print(f"RSS after load  : {result['rssAfterLoadMb']} MB")
        print(f"RSS peak        : {result['rssPeakMb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
