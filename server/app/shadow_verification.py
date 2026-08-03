"""검증된 데이터셋 capability — manifest 와 **rows 를 함께** 소유한다 (Phase 3 P0-C 9/N).

직전 구조는 manifest 만 소유했다. 그래서 2행 데이터셋을 검증해 얻은 trusted 결과에
전혀 다른 60행을 붙이면 total=60·trusted·graded=60·enforceReady=true 가 나왔다.
검증한 것과 집계하는 것이 다른 물건이면 검증은 장식이다.

이제 verifier 가 rows 까지 소유해 돌려주고, report 는 그 capability 하나만 받는다.
`report(rows_b, verification_a)` 같은 조합은 **API 로 표현할 수 없다**.

봉인은 **행 전체**에 건다. 필드 whitelist 로 묶었더니 목록 밖 필드(human_label·
review_decision·analysis…)를 trusted 상태에서 그냥 고칠 수 있었고, 나중에 생길
필드는 영원히 무방비였다. 라벨이 붙으면 digest 도 달라지는 게 맞다 — 그때는 typed
bind 가 새 capability 를 만들며 다시 봉인한다.
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

# 라벨 결합이 **추가**할 수 있는 필드. 이 밖의 키를 넣거나 기존 값을 바꾸면 거부한다.
# 선언만 있고 절대 기록되지 않는 필드는 두지 않는다 — blinded label 계약에 있는
# note 만 남긴다(evidence 는 그 계약에 없다).
LABEL_FIELDS = ("human_label", "label_reviewer_id", "label_reviewed_at",
                "label_policy_version", "label_note")

# trusted 를 봉인하는 토큰. 이 모듈 밖에서는 얻을 수 없다.
_TRUST_TOKEN = object()


def _canonical(obj) -> bytes:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def rows_digest(rows) -> str:
    """**행 전체**의 해시. 필드 whitelist 에 기대지 않는다.

    직전 구현은 일부 필드만 해시했다. 그래서 human_label·review_decision·
    has_pattern_or_logo·analysis 같은 리포트 입력을 trusted 상태에서 그냥 고칠 수
    있었고, 목록에 없는 **미래 필드**는 영원히 무방비였다.

    라벨이 붙으면 digest 도 달라지는 게 맞다 — 그때는 typed bind 가 새 capability 를
    만들며 **다시 봉인**한다. digest 에서 라벨을 영구 제외하는 게 아니라, 전환마다
    새로 찍는다.
    """
    body = sorted((dict(r) for r in rows), key=lambda d: str(d.get("id")))
    return hashlib.sha256(_canonical(body)).hexdigest()


def rows_with_label_fields(rows) -> list[str]:
    """typed bind 전에 라벨이 박혀 있는 행 — 그건 검증된 라벨이 아니다."""
    return [str(r.get("id")) for r in rows
            if any(k in r for k in LABEL_FIELDS)]


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
    rows_digest: str | None = None
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
        """report 진입 시 재확인 — 검증 이후 행이 하나라도 달라졌는지.

        추가·변경·삭제를 모두 잡는다. 필드 목록에 기대지 않으므로 나중에 생기는
        리포트 입력도 자동으로 보호된다.
        """
        out = []
        if self.rows_digest is not None and rows_digest(self.rows) != self.rows_digest:
            out.append("verified_rows_tampered")
        if not self.labels_bound and rows_with_label_fields(self.rows):
            # typed bind 를 거치지 않은 라벨은 누가 넣었는지 증명할 수 없다.
            out.append("unbound_label_fields")
        return out


def distribution_dataset(rows) -> VerifiedDataset:
    """검증 없이 **분포만** 보는 데이터셋(DB 조회 경로).

    절대 trusted 가 되지 않는다 — 숫자는 그대로 나가되 판정 플래그는 닫힌다.
    """
    frozen = _freeze(rows)
    # 분포용도 자기 행의 일관성은 지킨다 — 소유한 것이 바뀌면 그것도 사실이 아니다.
    return VerifiedDataset(state=ABSENT, rows=frozen, rows_digest=rows_digest(frozen))


def unverified_dataset(rows, manifest, problems, *, samples_sha256=None,
                       dataset_id=None, artifacts_verified=False) -> VerifiedDataset:
    frozen = _freeze(rows)
    return VerifiedDataset(state=UNVERIFIED, rows=frozen, manifest=manifest,
                           problems=tuple(sorted({str(p) for p in problems})),
                           samples_sha256=samples_sha256,
                           rows_digest=rows_digest(frozen),
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
        return distribution_dataset(rows)
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

    # typed bind 를 거치지 않은 라벨이 raw 에 박혀 있으면, 그건 누가 언제 넣었는지
    # 증명할 수 없는 값이다 — 검증된 표본으로 칠 수 없다.
    prelabeled = rows_with_label_fields(rows)
    if prelabeled:
        problems.append("raw_rows_contain_label_fields")
    problems = sorted(set(problems))
    frozen = _freeze(rows)
    digest = rows_digest(frozen)
    if problems:
        return VerifiedDataset(state=UNVERIFIED, rows=frozen, manifest=manifest,
                               problems=tuple(problems), samples_sha256=samples_sha,
                               rows_digest=digest,
                               artifacts_verified=not artifact, dataset_id=dataset_id)
    if manifest.get("validForCalibration") is False:
        # 검증은 끝났지만 manifest 자신이 "쓸 수 없다"고 말한다 — 그건 존중한다.
        return VerifiedDataset(
            state=INVALID, rows=frozen, manifest=manifest,
            problems=tuple(manifest.get("invalidReasons") or ("manifest_invalid",)),
            samples_sha256=samples_sha, rows_digest=digest,
            artifacts_verified=True, dataset_id=dataset_id)
    return VerifiedDataset(state=TRUSTED, rows=frozen, manifest=manifest, problems=(),
                           samples_sha256=samples_sha, rows_digest=digest,
                           artifacts_verified=True, dataset_id=dataset_id,
                           _token=_TRUST_TOKEN)



class LabelBindingError(Exception):
    """라벨이 정본을 건드렸거나 결합이 성립하지 않는다."""


def bind_verified_labels(dataset: VerifiedDataset,
                         effective_labels: dict) -> tuple[VerifiedDataset, list[dict]]:
    """검증된 데이터셋에 사람 라벨을 붙인다 → (새 dataset, 격리 목록).

    dataset_id 는 인자로 받지 않는다. 호출자가 정할 수 있으면 다른 데이터셋의 라벨을
    그대로 붙일 수 있고(실제로 evil-ds 라벨이 quarantine 0 으로 통과했다), 그 순간
    "이 표본에 대한 판단"이라는 전제가 사라진다. 정본은 dataset.dataset_id 뿐이다.

    라벨은 **추가**만 한다. 붙인 뒤 새 행 전체로 digest 를 다시 봉인하므로, 이후
    어떤 필드를 고쳐도 report 진입에서 걸린다.
    """
    from . import blinded_audit as ba

    dataset_id = dataset.dataset_id
    if not dataset_id:
        # 무엇에 대한 라벨인지 말할 수 없으면 붙이지 않는다.
        raise LabelBindingError("dataset 에 datasetId 가 없어 라벨을 결합할 수 없어요.")

    import copy

    rows = [copy.deepcopy(r) for r in dataset.rows]
    by_id = {str(r.get("id")): r for r in rows}
    quarantined: list[dict] = []
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
        if lab.get("note"):
            # blinded label 계약의 정규화된 note 만 옮긴다(길이는 make_label 이 이미 제한).
            row["label_note"] = lab["note"]

    # 붙인 결과가 허용 범위를 벗어나지 않았는지 스스로 확인한다.
    # 수동 protected whitelist가 아니라 라벨 필드를 제외한 전체 행을 비교한다.
    # 그래야 미래에 새 리포트 필드가 생겨도 자동으로 보호된다.
    for before, after in zip(dataset.rows, rows):
        extra = set(after) - set(before) - set(LABEL_FIELDS)
        if extra:
            raise LabelBindingError(f"라벨이 허용되지 않은 필드를 추가했어요: {sorted(extra)}")
        before_body = {k: v for k, v in before.items() if k not in LABEL_FIELDS}
        after_body = {k: v for k, v in after.items() if k not in LABEL_FIELDS}
        if before_body != after_body:
            raise LabelBindingError("라벨 결합이 라벨 외 행 데이터를 바꿨어요.")

    state = dataset.state
    token = _TRUST_TOKEN if state is TRUSTED else None
    problems = dataset.problems
    if quarantined:
        # 결합이 하나라도 실패하면 이 데이터셋은 캘리브레이션 입력이 아니다.
        state = UNVERIFIED
        token = None
        problems = tuple(sorted(set(problems) |
                                {f"label_{q['reason']}" for q in quarantined}))
    frozen = tuple(rows)
    # 전환마다 **다시** 봉인한다 — 라벨을 digest 에서 영구 제외하지 않는다.
    return replace(dataset, state=state, rows=frozen, problems=problems,
                   rows_digest=rows_digest(frozen), labels_bound=True,
                   _token=token), quarantined
