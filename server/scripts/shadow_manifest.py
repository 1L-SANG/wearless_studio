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

from app import shadow_cases as scases  # noqa: E402
from app import shadow_provenance as sp  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.agents.edit_intent_vision import PROMPT_VERSION  # noqa: E402
from app.services.edit_qc_scope import QC_POLICY_VERSION  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "server" / "prompts" / "edit_intent_qc_v1.txt"


def _sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dataset_checksum(names, base: pathlib.Path) -> str:
    """이름 + 내용 — 파일이 바뀌면 체크섬도 바뀐다."""
    h = hashlib.sha256()
    # 실패 row 는 source 가 비어 있을 수 있다 — 없는 이름은 체크섬에 넣지 않는다.
    for name in sorted({n for n in names if isinstance(n, str) and n}):
        h.update(name.encode())
        f = base / name
        if f.exists():
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def _bundle_sha(rows) -> str | None:
    """결과 이미지 묶음 전체의 해시 — 한 장만 바뀌어도 달라진다."""
    from app.safe_paths import is_sha256_hex
    h = hashlib.sha256()
    done = [r for r in rows if sp.has_output(r)]
    for r in sorted(done, key=lambda x: str(x.get("id"))):
        o = (r.get("provenance") or {}).get("outputSha256")
        # 형식 검증을 먼저 한다 — bytes.fromhex 가 먼저 터지면 manifest 자체가
        # 안 만들어지고, 그러면 "왜 못 쓰는지"도 알 수 없다.
        if not is_sha256_hex(o):
            return None
        h.update(str(r.get("id")).encode()); h.update(bytes.fromhex(o))
    return h.hexdigest() if done else None


def _raw_artifacts(rows, samples_path: str, *, ok: bool = True) -> dict:
    base = pathlib.Path(samples_path).parent
    # 기대 수는 **성공 output row** 수다. 실패 row 를 세면 늘 부족해 보인다.
    done = [r for r in rows if sp.has_output(r)]
    present = sum(1 for r in done if (base / f"{r.get('id')}.png").is_file())
    return {"samplesJsonl": str(samples_path),
            "outputDir": str(base),
            "outputImagesExpected": len(done),
            "outputImagesPresent": present,
            # provenance·artifact 문제가 하나라도 있으면 라벨링 대상이 아니다.
            "humanLabelingAvailable": ok and present == len(done) and bool(done),
            "retention": ("로컬 보존. 이미지는 git 에 넣지 않는다(용량). 비운영 오브젝트 "
                          "스토리지로 옮기려면 사용자 승인이 필요하다."),
            "location": str(base)}


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
    # 누락값을 추측하지 않는다. row 가 증명하지 못하면 null 이고, 그러면 이 데이터셋은
    # 캘리브레이션에 못 쓴다 — 무엇으로 만들어졌는지 모르는 표본이기 때문이다.
    # 수집기가 기록하는 정본 구조를 **그대로** 검사한다. 예전에는 manifest 만 옛
    # 평면 필드를 봤고, 그래서 run/case 가 없는 legacy 도 run 이 섞인 dataset 도
    # validForCalibration=true 를 받았다. 검증과 기록이 다른 걸 보면 검증이 아니다.
    provs = [r.get("provenance") or {} for r in rows]
    # case 정의를 못 읽으면 "검사 생략"이 아니라 **문제**다. 확인하지 못한 것을
    # 통과로 세는 순간 manifest 는 아무것도 보증하지 않는다.
    try:
        expected = scases.expected_case_fingerprints(load_settings())
        missing = sp.validate_dataset(rows, expected_cases=expected)
    except scases.CaseDefinitionError:
        # 원문·경로·환경값은 싣지 않는다 — 코드만 남긴다.
        missing = ["case_definition_unavailable"]
    # 실제 파일까지 대조한다. 해시가 맞다고 적혀 있는 것과 맞는 것은 다른 일이다.
    missing = sorted(set(missing) | set(sp.artifact_problems(
        rows, dataset_dir=pathlib.Path(samples_path).parent, source_dir=src_dir)))
    unverified = bool(missing) or any(pr.get("provenanceUnverified") for pr in provs)
    models = sorted({sp.run_of(r).get("generationModel") for r in rows
                     if sp.has_output(r) and sp.run_of(r).get("generationModel")})
    if unverified and "provenance_unverified" not in invalid_reasons:
        invalid_reasons = [*invalid_reasons, "provenance_unverified"]
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
        "manifestGeneratedByCommit": commit,
        "model": (models[0] if len(models) == 1 else (models or None)),
        "provenanceUnverified": unverified,
        "provenanceProblems": missing,
        "visionPromptVersionAtManifestTime": PROMPT_VERSION,
        "visionPromptSha256AtManifestTime": _sha256_file(PROMPT_FILE),
        "qcPolicyVersionAtManifestTime": QC_POLICY_VERSION,
        "provenanceNote": ("…AtManifestTime 값은 manifest 생성 시점의 코드 상태이지 "
                           "수집 시점의 값이 아니다. 수집 시점 값은 row.provenance 에만 있다."),
        "sourceDataset": {
            "path": "public/assets/fit-examples",
            "files": len({r.get("source") for r in rows
                          if isinstance(r.get("source"), str)}),
            "sha256": _dataset_checksum([r.get("source") for r in rows], src_dir)},
        "rawSampleManifestSha256": hashlib.sha256(raw).hexdigest(),
        "outputBundleSha256": _bundle_sha(rows),
        "rawArtifacts": _raw_artifacts(rows, samples_path, ok=not missing),
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
