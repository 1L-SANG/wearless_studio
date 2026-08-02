"""Blinded audit — 캘리브레이션 전용 사람 라벨링 (Phase 3 P0-C 9/N 보정).

운영 검수 API 는 `review_required` 전용으로 남는다. 그 계약을 넓히면 사용자 화면에서
"기계가 통과시킨 컷"까지 검수 대상이 되고, 사용자 승인 이력과 캘리브레이션 라벨이
같은 테이블에서 섞인다. 둘은 목적이 다르다 — 하나는 책임 기록, 하나는 측정값이다.

그래서 별도 경로를 둔다. 여기서는 pass·review_required·reject 를 **전부** 라벨할 수
있다. false pass 는 기계가 통과시킨 것을 사람이 봐야만 드러나므로, pass 를 라벨할 수
없으면 false pass 율은 영원히 0 이 아니라 **미측정**이다.

blinded: 라벨러에게 기계 판정을 먼저 보여주지 않는다. 판정을 보고 나서 누르면 그건
사람의 판단이 아니라 기계 판단의 복사본이고, 그걸로 기계를 검증할 수는 없다.

append-only: 마음이 바뀌면 새 행이다. 유효 라벨은 가장 최근 행이고 이전 행도 남는다.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time

LABELS = ("fidelity_pass", "fidelity_fail")
POLICY_VERSION = "blinded_audit_v1"
ALLOWED_POLICY_VERSIONS = ("blinded_audit_v1",)

# 이 artifact 는 **로컬 캘리브레이션 감사 기록**이다. 운영 승인 이력이 아니다.
# reviewer 는 인증된 신원이 아니라 사람이 입력한 식별자다 — manifest 에 그 사실을 적는다.
REVIEWER_SCHEME = "local_self_declared"
ARTIFACT_KIND = "local_calibration_audit"

# 라벨러가 보면 안 되는 것들. 기계 판정과 그 근거는 전부 가린다.
_BLINDED_FIELDS = ("machine_decision", "status", "edit_qc_result", "decision",
                   "review_decision", "human_label")


# 화면에 나갈 수 있는 키는 이것뿐이다. 화이트리스트라 새 필드가 실수로 새지 않는다.
_PRESENTABLE = ("sampleId", "requestedChanges", "sourceImage", "resultImage", "editType")


def presentation(sample: dict) -> dict:
    """라벨러에게 보여줄 것만 남긴다 — 판정과 근거는 빼고 이미지와 요청만.

    만드는 자리에서 바로 검증한다. "만든 뒤에 검사하기"로 두면 검사를 안 부르는
    호출자가 하나 생기는 순간 판정이 새고, 그 라벨은 기계 판단의 복사본이 된다.
    """
    view = {
        "sampleId": sample.get("id"),
        "requestedChanges": sample.get("case"),
        "sourceImage": sample.get("source"),
        "resultImage": f"{sample.get('id')}.png",
        # editType 은 요청 종류라 라벨러가 "무엇을 요청했는지" 알아야 판단할 수 있다.
        "editType": sample.get("edit_type"),
    }
    extra = set(view) - set(_PRESENTABLE)
    if extra:
        raise ValueError(f"제시 화면에 허용되지 않은 필드: {sorted(extra)}")
    assert_blinded(view)
    return view


def assert_blinded(view: dict) -> None:
    """제시 화면에 판정이 새면 그 라벨은 못 쓴다 — 만들기 전에 막는다."""
    leaked = [k for k in _BLINDED_FIELDS if k in view]
    if leaked:
        raise ValueError(f"blinded 위반 — 라벨러에게 판정이 노출됨: {sorted(leaked)}")


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def sample_sha256(sample: dict) -> str:
    """표본 동일성 — 요청 JSON + source SHA + output SHA 를 전부 묶는다.

    id 만으로 묶으면 같은 id 에 다른 이미지가 붙어도 못 알아챈다. 라벨은 "이 이미지
    쌍에 대한 판단"이므로 이미지가 바뀌면 다른 표본이다.
    """
    prov = sample.get("provenance") or {}
    return hashlib.sha256(_canonical({
        "id": sample.get("id"),
        "case": sample.get("case"),
        "editType": sample.get("edit_type"),
        "source": sample.get("source"),
        "sourceSha256": prov.get("sourceSha256"),
        "outputSha256": prov.get("outputSha256"),
    })).hexdigest()


def output_sha256(sample: dict) -> str | None:
    return (sample.get("provenance") or {}).get("outputSha256")


def make_label(*, sample: dict, label: str, reviewer_id: str, dataset_id: str,
               note: str | None = None, now: float | None = None) -> dict:
    """라벨은 **무엇에 대한 판단인지** 못 박아야 한다. 하나라도 비면 만들지 않는다."""
    if label not in LABELS:
        raise ValueError(f"label 은 {LABELS} 중 하나여야 해요: {label!r}")
    if not reviewer_id or not str(reviewer_id).strip():
        raise ValueError("reviewer_id 가 필요해요 — 누가 판단했는지 없으면 감사가 안 된다")
    if not dataset_id or not str(dataset_id).strip():
        raise ValueError("datasetId 가 필요해요 — 라벨은 데이터셋에 묶인다")
    if not sample.get("id"):
        raise ValueError("sampleId 가 없어요")
    out_sha = output_sha256(sample)
    if not out_sha:
        # 결과 이미지 해시가 없으면 "무엇을 보고 판단했는지"를 나중에 증명할 수 없다.
        raise ValueError("outputSha256 이 없어요 — 이 표본은 라벨할 수 없습니다")
    return {
        "datasetId": str(dataset_id).strip(),
        "sampleId": sample["id"],
        "sampleSha256": sample_sha256(sample),
        "outputSha256": out_sha,
        "label": label,
        "reviewerId": str(reviewer_id).strip(),
        "reviewerScheme": REVIEWER_SCHEME,
        "artifactKind": ARTIFACT_KIND,
        "note": (str(note)[:500] if note else None),
        "policyVersion": POLICY_VERSION,
        "labeledAt": now if now is not None else time.time(),
    }


class LabelChainError(Exception):
    """해시 체인이 깨졌다 — 중간 행이 고쳐졌거나 지워졌거나 순서가 바뀌었다."""


GENESIS = "0" * 64


def _event_hash(record: dict, previous: str) -> str:
    body = {k: v for k, v in record.items() if k not in ("eventHash",)}
    body["previousEventHash"] = previous
    return hashlib.sha256(_canonical(body)).hexdigest()


def append_label(path: str, record: dict) -> dict:
    """append-only + 해시 체인.

    append-only 는 "우리가 안 고친다"는 약속으로는 부족하다. 앞 행의 해시를 물고
    가면 중간을 고치거나 지우거나 순서를 바꾼 순간 뒤 전부가 안 맞는다.
    파일 잠금은 두 프로세스가 같은 꼬리에 각자 이어 붙이는 것을 막는다.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        # 잠금 안에서 기존 체인을 통째로 검증한다. 밖에서 검증하면 그 사이에 파일이
        # 바뀔 수 있고, 그러면 깨진 체인 위에 새 행을 얹게 된다.
        existing = [json.loads(l) for l in lines]
        verify_chain(existing)
        prev = existing[-1]["eventHash"] if existing else GENESIS
        rec = dict(record)
        rec["eventId"] = rec.get("eventId") or hashlib.sha256(
            _canonical([rec, prev, len(lines)])).hexdigest()[:32]
        rec["previousEventHash"] = prev
        rec["eventHash"] = _event_hash(rec, prev)
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, (json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return rec


def load_labels(path: str, *, verify: bool = True) -> list[dict]:
    """읽을 때 체인을 통째로 검증한다 — 깨진 파일을 조용히 쓰지 않는다."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    if verify:
        verify_chain(out)
    return out


def verify_chain(records) -> None:
    prev = GENESIS
    seen = set()
    for i, r in enumerate(records):
        eid = r.get("eventId")
        if not eid:
            raise LabelChainError(f"{i}번 행에 eventId 가 없어요")
        if eid in seen:
            raise LabelChainError(f"eventId 중복: {eid}")
        seen.add(eid)
        if r.get("previousEventHash") != prev:
            raise LabelChainError(
                f"{i}번 행에서 체인이 끊겼어요 — 중간 행이 수정·삭제·재정렬됐습니다")
        expected = _event_hash(r, prev)
        if r.get("eventHash") != expected:
            raise LabelChainError(f"{i}번 행의 내용이 기록된 해시와 달라요")
        prev = r["eventHash"]


def effective_labels(records) -> dict:
    """(datasetId, sampleId) → 최신 라벨.

    sampleId 만으로 묶으면 다른 데이터셋의 같은 id 라벨이 섞인다. 데이터셋이 다르면
    이미지도 프롬프트도 다르므로 그건 다른 표본에 대한 판단이다.
    """
    by_key: dict[tuple, list] = {}
    for r in records:
        by_key.setdefault((str(r.get("datasetId")), str(r.get("sampleId"))), []).append(r)
    out = {}
    for key, rs in by_key.items():
        ordered = sorted(rs, key=lambda x: (x.get("labeledAt") or 0))
        last = ordered[-1]
        out[key] = {"label": last.get("label"),
                    "reviewerId": last.get("reviewerId"),
                    "labeledAt": last.get("labeledAt"),
                    "policyVersion": last.get("policyVersion"),
                    "sampleSha256": last.get("sampleSha256"),
                    "outputSha256": last.get("outputSha256"),
                    "changed": len({r.get("label") for r in ordered}) > 1,
                    "history": [{"label": r.get("label"), "reviewerId": r.get("reviewerId"),
                                 "labeledAt": r.get("labeledAt")} for r in ordered]}
    return out


def apply_labels(rows, labels: dict, *, dataset_id: str,
                 strict: bool = True) -> tuple[list[dict], list[dict]]:
    """라벨을 표본에 붙인다 — **묶임이 맞을 때만**. → (rows, quarantined)

    데이터셋·표본 해시·출력 해시·정책 버전이 하나라도 어긋나면 조용히 무시하지 않고
    격리한다. 조용히 버리면 "라벨 30건 붙였다"는 잘못된 커버리지가 남는다.
    """
    by_id = {str(r.get("id")): r for r in rows}
    quarantined = []
    applied: dict[str, str] = {}
    for (ds, sid), lab in labels.items():
        reason = None
        row = by_id.get(sid)
        if ds != dataset_id:
            reason = "dataset_mismatch"
        elif row is None:
            reason = "sample_not_found"
        elif lab.get("policyVersion") not in ALLOWED_POLICY_VERSIONS:
            reason = "policy_version_unsupported"
        elif lab.get("sampleSha256") != sample_sha256(row):
            reason = "sample_hash_mismatch"
        elif lab.get("outputSha256") != output_sha256(row):
            reason = "output_hash_mismatch"
        if reason:
            quarantined.append({"datasetId": ds, "sampleId": sid, "reason": reason})
            continue
        applied[sid] = lab["label"]
    if strict and quarantined:
        raise ValueError(f"라벨 결합 실패 {len(quarantined)}건: "
                         f"{sorted({q['reason'] for q in quarantined})}")
    out = [{**r, "human_label": applied[str(r.get("id"))]}
           if str(r.get("id")) in applied else dict(r) for r in rows]
    return out, quarantined


def coverage(rows, labels: dict) -> dict:
    """어디에 라벨이 붙었는지 — 특히 pass 표본. 여기가 비면 enforce 는 불가다."""
    from app.shadow_report import machine_decision
    total = len(rows)
    ids = {sid for (_ds, sid) in labels}
    by_decision: dict[str, dict] = {}
    for r in rows:
        d = machine_decision(r)
        slot = by_decision.setdefault(d, {"samples": 0, "labeled": 0})
        slot["samples"] += 1
        if str(r.get("id")) in ids:
            slot["labeled"] += 1
    return {"samples": total, "labeled": sum(v["labeled"] for v in by_decision.values()),
            "byMachineDecision": by_decision,
            "passCoverage": (by_decision.get("pass", {}).get("labeled", 0)
                             / by_decision["pass"]["samples"])
            if by_decision.get("pass", {}).get("samples") else None}
