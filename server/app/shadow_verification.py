"""Manifest 검증 **결과** — trust 를 값이 아니라 능력으로 다룬다 (Phase 3 P0-C 9/N).

직전까지 trust 는 `manifest_verified: bool` 이었다. 그러면 호출자가 그냥 True 를
넘길 수 있고, 실제로 `{}` 에 True 를 붙이면 calibration/trusted/enforceReady=true 가
나왔다. boolean 은 "검증했다"는 **주장**이지 증거가 아니다.

그래서 검증 결과를 객체로 만들고, `trusted` 상태는 이 모듈의 verifier 만 만들 수
있게 봉인한다(토큰). 밖에서는 `ManifestVerification(state="trusted", ...)` 를
직접 만들 수 없다 — 만들려면 실제 파일과 manifest 를 verifier 에 통과시켜야 한다.

manifest 와 검증 결과를 따로 들고 다니지 않는다. 결과가 manifest 를 **소유**하므로
"A 를 검증하고 B 를 리포트에 넘기는" 조합 자체가 성립하지 않는다.
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field

from . import shadow_provenance as sp

# 운영 기본 source 디렉터리 정본. 스크립트마다 따로 두면 서로 다른 곳을 보게 된다.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / "public" / "assets" / "fit-examples"

ABSENT = "absent"
UNVERIFIED = "unverified"
INVALID = "invalid"
TRUSTED = "trusted"

# trusted 를 봉인하는 토큰. 이 모듈 밖에서는 얻을 수 없다.
_TRUST_TOKEN = object()


@dataclass(frozen=True)
class ManifestVerification:
    """중앙 verifier 가 낸 판정. report 는 이것 하나만 본다.

    state 하나가 정본이고, reportKind·calibrationUsable·verdict 는 전부 여기서
    파생된다 — 같은 상태를 여러 if 문이 다시 해석하면 해석이 갈린다.
    """

    state: str = ABSENT
    manifest: dict | None = None
    problems: tuple = ()
    manifest_sha256: str | None = None
    artifacts_verified: bool = False
    dataset_id: str | None = None
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.state not in (ABSENT, UNVERIFIED, INVALID, TRUSTED):
            raise ValueError(f"알 수 없는 manifest 상태: {self.state!r}")
        if self.state is TRUSTED or self.state == TRUSTED:
            # 봉인 — trusted 는 verifier 만 만든다. 이게 없으면 boolean 시절로 돌아간다.
            if self._token is not _TRUST_TOKEN:
                raise ValueError(
                    "trusted 상태는 verify_manifest_for_report() 만 만들 수 있어요.")
            if self.problems:
                raise ValueError("문제가 남은 채로 trusted 일 수 없어요.")
            if not self.artifacts_verified:
                raise ValueError("artifact 검증 없이 trusted 일 수 없어요.")

    @property
    def trusted(self) -> bool:
        return self.state == TRUSTED

    @property
    def blocked_reasons(self) -> list[str]:
        """리포트에 실을 typed 사유. 상태 자체도 사유로 남긴다."""
        if self.state == TRUSTED:
            return []
        base = {ABSENT: ["manifest_absent"],
                UNVERIFIED: ["manifest_unverified"],
                INVALID: ["manifest_invalid"]}[self.state]
        return sorted(set(base) | set(self.problems))


def absent() -> ManifestVerification:
    return ManifestVerification(state=ABSENT)


def unverified(manifest, problems, *, manifest_sha256=None, dataset_id=None,
               artifacts_verified=False) -> ManifestVerification:
    return ManifestVerification(state=UNVERIFIED, manifest=manifest,
                                problems=tuple(sorted(set(problems))),
                                manifest_sha256=manifest_sha256,
                                artifacts_verified=artifacts_verified,
                                dataset_id=dataset_id)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_manifest_for_report(
    *, manifest, rows, samples_path, dataset_dir=None, source_dir=None,
    cli_dataset_id=None,
) -> ManifestVerification:
    """중앙 verifier — 모든 검사를 한 곳에서 순서대로 한다.

    검사 순서(앞에서 막히면 뒤는 의미가 없다):
      1) manifest 존재·타입
      2) 필수 스키마 + calibration_state 의미 모순
      3) samples.jsonl 실제 SHA 결합
      4) datasetId 결합(CLI 인자와)
      5) artifact 파일 검증(경계·존재·해시)
      6) output 번들 재계산 대조
      7) source 번들 재계산 대조
      8) manifest 자신의 validForCalibration
    """
    if manifest is None:
        return absent()
    if not isinstance(manifest, dict):
        return unverified(None, ["manifest_not_object"])

    samples_file = pathlib.Path(samples_path)
    dataset_dir = pathlib.Path(dataset_dir) if dataset_dir else samples_file.parent
    source_dir = pathlib.Path(source_dir) if source_dir else DEFAULT_SOURCE_DIR
    dataset_id = manifest.get("datasetId") if isinstance(
        manifest.get("datasetId"), str) else None
    has_out = any(sp.has_output(r) for r in rows)

    problems: list[str] = list(sp.manifest_binding_problems(
        manifest, has_output_rows=has_out))

    manifest_sha = None
    try:
        manifest_sha = _sha_bytes(samples_file.read_bytes())
    except OSError:
        problems.append("samples_unreadable")
    if manifest_sha and manifest.get("rawSampleManifestSha256") != manifest_sha:
        problems.append("manifest_samples_mismatch")

    if cli_dataset_id and cli_dataset_id != manifest.get("datasetId"):
        problems.append("manifest_dataset_id_mismatch")

    artifact = sp.artifact_problems(rows, dataset_dir=dataset_dir,
                                    source_dir=source_dir)
    problems += artifact

    if manifest.get("outputBundleSha256") != sp.output_bundle_sha256(
            rows, dataset_dir):
        problems.append("output_bundle_mismatch")
    if (manifest.get("sourceDataset") or {}).get("sha256") != \
            sp.source_bundle_sha256(rows, source_dir):
        problems.append("source_bundle_mismatch")

    problems = sorted(set(problems))
    self_invalid = manifest.get("validForCalibration") is False

    if problems:
        return unverified(manifest, problems, manifest_sha256=manifest_sha,
                          dataset_id=dataset_id, artifacts_verified=not artifact)
    if self_invalid:
        # 검증은 끝났지만 manifest 자신이 "쓸 수 없다"고 말한다 — 그건 존중한다.
        return ManifestVerification(
            state=INVALID, manifest=manifest,
            problems=tuple(manifest.get("invalidReasons") or ("manifest_invalid",)),
            manifest_sha256=manifest_sha, artifacts_verified=True,
            dataset_id=dataset_id)
    return ManifestVerification(state=TRUSTED, manifest=manifest, problems=(),
                                manifest_sha256=manifest_sha,
                                artifacts_verified=True, dataset_id=dataset_id,
                                _token=_TRUST_TOKEN)
