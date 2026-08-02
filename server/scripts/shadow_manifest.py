"""수집 manifest 생성 — 손으로 쓰지 않는다 (Phase 3 P0-C 9/N 보정).

manifest 를 사람이 쓰면 "이 데이터셋이 무엇으로 만들어졌나"가 기억에 의존하게 되고,
그 기억은 몇 주 뒤에 틀린다. 실제 파일·커밋·프롬프트에서 뽑아 찍는다.

    python scripts/shadow_manifest.py --samples out/samples.jsonl --dataset-id d1
    python scripts/shadow_manifest.py ... --invalid empty_allowed_scope
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.edit_intent_vision import PROMPT_VERSION  # noqa: E402
from app.services.edit_qc_scope import QC_POLICY_VERSION  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "server" / "prompts" / "edit_intent_qc_v1.txt"


def _sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dataset_checksum(names, base: pathlib.Path) -> str:
    """이름 + 내용 — 파일이 바뀌면 체크섬도 바뀐다."""
    h = hashlib.sha256()
    for name in sorted(set(names)):
        h.update(name.encode())
        f = base / name
        if f.exists():
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def build(samples_path: str, *, dataset_id: str, invalid_reasons: list[str],
          image_usd: float, vision_usd: float, collected_at: str,
          command: str | None) -> dict:
    raw = pathlib.Path(samples_path).read_bytes()
    rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    src_dir = ROOT / "public" / "assets" / "fit-examples"
    img = sum(int(r.get("image_calls") or 0) for r in rows)
    vis = sum(int(r.get("vision_calls") or 0) for r in rows)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    models = sorted({str(r.get("model") or "gemini-3-pro-image") for r in rows})
    return {
        "datasetId": dataset_id,
        "collectedAt": collected_at,
        "validForCalibration": not invalid_reasons,
        "invalidReasons": invalid_reasons,
        "samples": len(rows),
        "pipelines": {"editor_vary": len(rows), "mannequin_edit": 0},
        # 시도 기준 — 실패·재시도도 돈이 나간다.
        "imageCallsAttempted": img,
        "visionCallsAttempted": vis,
        "cost": {"estimatedUsd": round(img * image_usd + vis * vision_usd, 4),
                 "assumed": True,
                 "unitPrices": {"imageUsd": image_usd, "visionUsd": vision_usd},
                 "note": "단가는 가정치다 — 실단가 미확인이라 이 금액을 근거로 쓰지 말 것."},
        "collectedByCommit": commit,
        "model": models[0] if len(models) == 1 else models,
        "visionPromptVersion": PROMPT_VERSION,
        "visionPromptSha256": _sha256_file(PROMPT_FILE),
        "qcPolicyVersion": QC_POLICY_VERSION,
        "sourceDataset": {
            "path": "public/assets/fit-examples",
            "files": len({r.get("source") for r in rows}),
            "sha256": _dataset_checksum([r.get("source") for r in rows], src_dir)},
        "rawSampleManifestSha256": hashlib.sha256(raw).hexdigest(),
        "collectorCommand": command,
        "humanLabels": {"labeled": 0, "path": None,
                        "note": "blinded audit 로만 채운다 — 수집기가 만들지 않는다."},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--collected-at", required=True)
    ap.add_argument("--invalid", action="append", default=[])
    ap.add_argument("--image-usd", type=float, default=0.15)
    ap.add_argument("--vision-usd", type=float, default=0.003)
    ap.add_argument("--command")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    m = build(a.samples, dataset_id=a.dataset_id, invalid_reasons=a.invalid,
              image_usd=a.image_usd, vision_usd=a.vision_usd,
              collected_at=a.collected_at, command=a.command)
    pathlib.Path(a.out).write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: m[k] for k in ("datasetId", "samples", "validForCalibration",
                                        "imageCallsAttempted", "visionCallsAttempted")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
