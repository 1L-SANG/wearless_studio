"""검증된 데이터셋 capability — manifest 와 **rows 를 함께** 소유한다 (Phase 3 P0-C 9/N).

직전 구조는 manifest 만 소유했다. 그래서 2행 데이터셋을 검증해 얻은 trusted 결과에
전혀 다른 60행을 붙이면 total=60·trusted·graded=60·enforceReady=true 가 나왔다.
검증한 것과 집계하는 것이 다른 물건이면 검증은 장식이다.

이제 verifier 가 rows 까지 소유해 돌려주고, report 는 그 capability 하나만 받는다.
`report(rows_b, verification_a)` 같은 조합은 **API 로 표현할 수 없다**.

라벨은 검증 뒤에 정상적으로 붙는 데이터라 전체 행 해시로 막으면 안 된다. 그래서
"라벨이 건드려도 되는 필드"와 "calibration 입력 정본"을 나누고, 후자만 digest 로
묶어 report 진입 시 다시 확인한다.
"""

from __future__ import annotations

import enum
import hashlib
import pathlib
from dataclasses import dataclass, field, replace

from . import shadow_provenance as sp

# 운영 기본 source 디렉터리 정본. 스크립트마다 두면 서로 다른 곳을 보게 된다.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / "public" / "assets" / "fit-examples"


class ManifestState(str, enum.Enum):
    ABSENT = "absent"
    UNVERIFIED = "unverified"
    INVALID = "invalid"
    TRUSTED = "trusted"


ABSENT = ManifestState.ABSENT
UNVERIFIED = ManifestState.UNVERIFIED
INVALID = ManifestState.INVALID
TRUSTED = ManifestState.TRUSTED

# calibration 입력의 정본. 라벨이 붙어도 이 값들은 바뀌면 안 된다 — 바뀌면 그건
# 다른 표본이고, 검증은 그 다른 표본에 대해 이뤄진 적이 없다.
PROTECTED_FIELDS = ("id", "output_id", "source", "source_kind", "edit_type",
                    "case", "provenance", "edit_qc_result", "image_calls",
                    "vision_calls", "created_at", "completed_at", "status",
                    "machine_decision")

# 라벨 결합이 **추가**할 수 있는 필드. 이 밖의 키를 넣거나 기존 값을 바꾸면 거부한다.
LABEL_FIELDS = ("human_label", "label_reviewer_id", "label_reviewed_at",
                "label_policy_version", "label_evidence", "label_note")

# trusted 를 봉인하는 토큰. 이 모듈 밖에서는 얻을 수 없다.
_TRUST_TOKEN = object()


def _canonical(obj) -> bytes:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def protected_digest(rows) -> str:
    """calibration 입력 정본만 뽑아 만든 해시.

    전체 행을 해시하면 라벨 한 줄 붙는 순간 달라져 정상 흐름이 막힌다. 반대로 아무
    것도 안 묶으면 rows 를 통째로 바꿔치기할 수 있다. 그 사이가 이 목록이다.
    """
    body = sorted(({k: r.get(k) for k in PROTECTED_FIELDS if k in r} for r in rows),
                  key=lambda d: str(d.get("id")))
    return hashlib.sha256(_canonical(body)).hexdigest()


def _freeze(rows) -> tuple:
    """검증 시점의 행을 깊은 사본으로 고정한다 — 밖에서 원본 리스트를 바꿔도 무관하게."""
    import copy
    return tuple(copy.deepcopy(r) for r in rows)


@dataclass(frozen=True)
class VerifiedDataset:
    """중앙 verifier 가 낸 판정 + **그 판정이 대상으로 삼은 행들**.

    state 하나가 정본이고 reportKind·manifestTrust·calibrationUsable·verdict 가
    전부 여기서 파생된다 — 같은 상태를 여러 곳이 다시 해석하면 해석이 갈린다.
    """

    state: ManifestState = ABSENT
    rows: tuple = ()
    manifest: dict | None = None
    problems: tuple = ()
    samples_sha256: str | None = None
    protected_digest: str | None = None
    artifacts_verified: bool = False
    dataset_id: str | None = None
    labels_bound: bool = False
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.state, ManifestState):
            raise ValueError(f"알 수 없는 manifest 상태: {self.state!r}")
        if self.state is TRUSTED:
            # 봉인 — trusted 는 verifier 만 만든다. 없으면 boolean 시절로 돌아간다.
            if self._token is not _TRUST_TOKEN:
                raise ValueError("trusted 상태는 verify_dataset() 만 만들 수 있어요.")
            if self.problems:
                raise ValueError("문제가 남은 채로 trusted 일 수 없어요.")
            if not self.artifacts_verified:
                raise ValueError("artifact 검증 없이 trusted 일 수 없어요.")

    @property
    def trusted(self) -> bool:
        return self.state is TRUSTED

    @property
    def blocked_reasons(self) -> list[str]:
        if self.trusted:
            return []
        base = {ABSENT: ["manifest_absent"], UNVERIFIED: ["manifest_unverified"],
                INVALID: ["manifest_invalid"]}[self.state]
        return sorted(set(base) | set(self.problems))

    def integrity_problems(self) -> list[str]:
        """report 진입 시 재확인 — 검증 이후 정본 필드가 바뀌었는지."""
        if self.protected_digest is None:
            return []
        if protected_digest(self.rows) != self.protected_digest:
            return ["verified_rows_tampered"]
        return []


def distribution_dataset(rows) -> VerifiedDataset:
    """검증 없이 **분포만** 보는 데이터셋(DB 조회 경로).

    절대 trusted 가 되지 않는다 — 숫자는 그대로 나가되 판정 플래그는 닫힌다.
    """
    return VerifiedDataset(state=ABSENT, rows=_freeze(rows))


def unverified_dataset(rows, manifest, problems, *, samples_sha256=None,
                       dataset_id=None, artifacts_verified=False) -> VerifiedDataset:
    return VerifiedDataset(state=UNVERIFIED, rows=_freeze(rows), manifest=manifest,
                           problems=tuple(sorted({str(p) for p in problems})),
                           samples_sha256=samples_sha256,
                           artifacts_verified=artifacts_verified,
                           dataset_id=dataset_id)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# 구조 자체가 깨진 문제 — 이게 있으면 nested 값을 더 읽지 않는다.
_SHAPE_PROBLEM_PREFIXES = ("manifest_binding_missing:", "manifest_binding_invalid:")


def verify_dataset(*, manifest, rows, samples_path, dataset_dir=None,
                   source_dir=None, cli_dataset_id=None) -> VerifiedDataset:
    """중앙 verifier — 모든 검사를 한 곳에서 순서대로 한다.

    순서(앞이 막히면 뒤는 의미가 없다):
      1) manifest 존재·타입
      2) 필수 스키마 + 중첩 타입 + calibration_state 의미 모순
         → **여기서 문제가 나오면 즉시 중단**한다. nested 값에 .get() 을 부르거나
           파일을 읽기 시작하면 AttributeError 로 터지고(실제로 터졌다), 그 순간
           "왜 못 쓰는지"조차 못 남긴다.
      3) samples.jsonl 실제 SHA 결합
      4) datasetId 결합(CLI 인자)
      5) artifact 파일 검증(경계·존재·해시)
      6) output 번들 재계산
      7) source 번들 재계산
      8) manifest 자신의 validForCalibration
    """
    if manifest is None:
        return VerifiedDataset(state=ABSENT, rows=_freeze(rows))
    if not isinstance(manifest, dict):
        return unverified_dataset(rows, None, ["manifest_not_object"])

    samples_file = pathlib.Path(samples_path)
    dataset_dir = pathlib.Path(dataset_dir) if dataset_dir else samples_file.parent
    source_dir = pathlib.Path(source_dir) if source_dir else DEFAULT_SOURCE_DIR
    dataset_id = manifest.get("datasetId") if isinstance(
        manifest.get("datasetId"), str) else None
    has_out = any(sp.has_output(r) for r in rows)

    shape = list(sp.manifest_binding_problems(manifest, has_output_rows=has_out))
    if any(str(p).startswith(_SHAPE_PROBLEM_PREFIXES) for p in shape):
        # 구조가 깨졌으면 값 비교도 파일 읽기도 시작하지 않는다.
        return unverified_dataset(rows, manifest, shape, dataset_id=dataset_id)

    problems: list[str] = list(shape)
    samples_sha = None
    try:
        samples_sha = _sha_bytes(samples_file.read_bytes())
    except OSError:
        problems.append("samples_unreadable")
    if samples_sha and manifest.get("rawSampleManifestSha256") != samples_sha:
        problems.append("manifest_samples_mismatch")
    if cli_dataset_id and cli_dataset_id != manifest.get("datasetId"):
        problems.append("manifest_dataset_id_mismatch")

    artifact = sp.artifact_problems(rows, dataset_dir=dataset_dir,
                                    source_dir=source_dir)
    problems += artifact
    if manifest.get("outputBundleSha256") != sp.output_bundle_sha256(rows, dataset_dir):
        problems.append("output_bundle_mismatch")
    if (manifest.get("sourceDataset") or {}).get("sha256") != \
            sp.source_bundle_sha256(rows, source_dir):
        problems.append("source_bundle_mismatch")

    problems = sorted(set(problems))
    frozen = _freeze(rows)
    digest = protected_digest(frozen)
    if problems:
        return VerifiedDataset(state=UNVERIFIED, rows=frozen, manifest=manifest,
                               problems=tuple(problems), samples_sha256=samples_sha,
                               protected_digest=digest,
                               artifacts_verified=not artifact, dataset_id=dataset_id)
    if manifest.get("validForCalibration") is False:
        # 검증은 끝났지만 manifest 자신이 "쓸 수 없다"고 말한다 — 그건 존중한다.
        return VerifiedDataset(
            state=INVALID, rows=frozen, manifest=manifest,
            problems=tuple(manifest.get("invalidReasons") or ("manifest_invalid",)),
            samples_sha256=samples_sha, protected_digest=digest,
            artifacts_verified=True, dataset_id=dataset_id)
    return VerifiedDataset(state=TRUSTED, rows=frozen, manifest=manifest, problems=(),
                           samples_sha256=samples_sha, protected_digest=digest,
                           artifacts_verified=True, dataset_id=dataset_id,
                           _token=_TRUST_TOKEN)


# 이전 이름 — CLI·테스트가 함께 옮겨 갔다. 호출자 없으면 다음 정리에서 지운다.
verify_manifest_for_report = verify_dataset


class LabelBindingError(Exception):
    """라벨이 정본을 건드렸거나 결합이 성립하지 않는다."""


def bind_verified_labels(dataset: VerifiedDataset, effective_labels: dict, *,
                         dataset_id: str) -> tuple[VerifiedDataset, list[dict]]:
    """검증된 데이터셋에 사람 라벨을 붙인다 → (새 dataset, 격리 목록).

    라벨은 **추가**만 한다. 정본 필드를 하나라도 건드리면 그건 라벨이 아니라 다른
    표본을 만들어 낸 것이고, 그 표본은 검증된 적이 없다. 호출자가 임의의 rows 를
    "라벨 결과"라고 주장해 밀어 넣을 수 있는 통로를 두지 않는다.
    """
    from . import blinded_audit as ba

    rows = [dict(r) for r in dataset.rows]
    by_id = {str(r.get("id")): r for r in rows}
    quarantined: list[dict] = []
    applied = 0
    for (ds, sid), lab in (effective_labels or {}).items():
        reason = None
        row = by_id.get(sid)
        if ds != dataset_id:
            reason = "dataset_mismatch"
        elif row is None:
            reason = "sample_not_found"
        elif lab.get("policyVersion") not in ba.ALLOWED_POLICY_VERSIONS:
            reason = "policy_version_unsupported"
        elif lab.get("sampleSha256") != ba.sample_sha256(row):
            reason = "sample_hash_mismatch"
        elif lab.get("outputSha256") != ba.output_sha256(row):
            reason = "output_hash_mismatch"
        if reason:
            quarantined.append({"datasetId": ds, "sampleId": sid, "reason": reason})
            continue
        row["human_label"] = lab["label"]
        if lab.get("reviewerId"):
            row["label_reviewer_id"] = lab["reviewerId"]
        if lab.get("labeledAt"):
            row["label_reviewed_at"] = lab["labeledAt"]
        if lab.get("policyVersion"):
            row["label_policy_version"] = lab["policyVersion"]
        applied += 1

    # 붙인 결과가 정본을 건드리지 않았는지 스스로 확인한다.
    if dataset.protected_digest and protected_digest(rows) != dataset.protected_digest:
        raise LabelBindingError("라벨 결합이 calibration 입력 정본을 바꿨어요.")
    for before, after in zip(dataset.rows, rows):
        extra = set(after) - set(before) - set(LABEL_FIELDS)
        if extra:
            raise LabelBindingError(f"라벨이 허용되지 않은 필드를 추가했어요: {sorted(extra)}")

    state = dataset.state
    token = _TRUST_TOKEN if state is TRUSTED else None
    problems = dataset.problems
    if quarantined:
        # 결합이 하나라도 실패하면 이 데이터셋은 캘리브레이션 입력이 아니다.
        state = UNVERIFIED
        token = None
        problems = tuple(sorted(set(problems) |
                                {f"label_{q['reason']}" for q in quarantined}))
    return replace(dataset, state=state, rows=tuple(rows), problems=problems,
                   labels_bound=True, _token=token), quarantined
