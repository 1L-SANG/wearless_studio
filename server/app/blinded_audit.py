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

import hashlib
import json
import os
import time

LABELS = ("fidelity_pass", "fidelity_fail")
POLICY_VERSION = "blinded_audit_v1"

# 라벨러가 보면 안 되는 것들. 기계 판정과 그 근거는 전부 가린다.
_BLINDED_FIELDS = ("machine_decision", "status", "edit_qc_result", "decision",
                   "review_decision", "human_label")


def presentation(sample: dict) -> dict:
    """라벨러에게 보여줄 것만 남긴다 — 판정과 근거는 빼고 이미지와 요청만."""
    return {
        "sampleId": sample.get("id"),
        "requestedChanges": sample.get("case"),
        "sourceImage": sample.get("source"),
        "resultImage": f"{sample.get('id')}.png",
        # editType 은 요청 종류라 라벨러가 "무엇을 요청했는지" 알아야 판단할 수 있다.
        "editType": sample.get("edit_type"),
    }


def assert_blinded(view: dict) -> None:
    """제시 화면에 판정이 새면 그 라벨은 못 쓴다 — 만들기 전에 막는다."""
    leaked = [k for k in _BLINDED_FIELDS if k in view]
    if leaked:
        raise ValueError(f"blinded 위반 — 라벨러에게 판정이 노출됨: {sorted(leaked)}")


def sample_sha256(sample: dict, image_bytes: bytes | None = None) -> str:
    """표본 동일성 — 같은 이미지·같은 요청에 붙은 라벨인지 나중에 확인할 수 있게."""
    h = hashlib.sha256()
    h.update(str(sample.get("id") or "").encode())
    h.update(str(sample.get("case") or "").encode())
    h.update(str(sample.get("source") or "").encode())
    if image_bytes:
        h.update(hashlib.sha256(image_bytes).digest())
    return h.hexdigest()


def make_label(*, sample: dict, label: str, reviewer: str, dataset_id: str,
               output_sha256: str | None = None, note: str | None = None,
               now: float | None = None) -> dict:
    if label not in LABELS:
        raise ValueError(f"label 은 {LABELS} 중 하나여야 해요: {label!r}")
    if not reviewer or not str(reviewer).strip():
        raise ValueError("reviewer 가 필요해요 — 누가 판단했는지 없으면 감사가 안 된다")
    return {
        "datasetId": dataset_id,
        "sampleId": sample.get("id"),
        "sampleSha256": sample_sha256(sample),
        "outputSha256": output_sha256,
        "label": label,
        "reviewer": str(reviewer).strip(),
        "note": (str(note)[:500] if note else None),
        "policyVersion": POLICY_VERSION,
        "labeledAt": now if now is not None else time.time(),
    }


def append_label(path: str, record: dict) -> dict:
    """append-only. 기존 행을 고치지 않는다 — 판단 변경도 새 행이다."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record


def load_labels(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def effective_labels(records) -> dict:
    """sampleId → 최신 라벨. 이전 판단도 history 로 함께 돌려준다."""
    by_sample: dict[str, list] = {}
    for r in records:
        by_sample.setdefault(str(r.get("sampleId")), []).append(r)
    out = {}
    for sid, rs in by_sample.items():
        ordered = sorted(rs, key=lambda x: (x.get("labeledAt") or 0))
        out[sid] = {"label": ordered[-1].get("label"),
                    "reviewer": ordered[-1].get("reviewer"),
                    "labeledAt": ordered[-1].get("labeledAt"),
                    "policyVersion": ordered[-1].get("policyVersion"),
                    "changed": len({r.get("label") for r in ordered}) > 1,
                    "history": [{"label": r.get("label"), "reviewer": r.get("reviewer"),
                                 "labeledAt": r.get("labeledAt")} for r in ordered]}
    return out


def apply_labels(rows, labels: dict) -> list[dict]:
    """표본에 사람 라벨을 붙인다(집계 입력용). 기계 판정은 건드리지 않는다."""
    out = []
    for r in rows:
        lab = labels.get(str(r.get("id")))
        out.append({**r, "human_label": lab["label"]} if lab else dict(r))
    return out


def coverage(rows, labels: dict) -> dict:
    """어디에 라벨이 붙었는지 — 특히 pass 표본. 여기가 비면 enforce 는 불가다."""
    from app.shadow_report import machine_decision
    total = len(rows)
    by_decision: dict[str, dict] = {}
    for r in rows:
        d = machine_decision(r)
        slot = by_decision.setdefault(d, {"samples": 0, "labeled": 0})
        slot["samples"] += 1
        if str(r.get("id")) in labels:
            slot["labeled"] += 1
    return {"samples": total, "labeled": sum(v["labeled"] for v in by_decision.values()),
            "byMachineDecision": by_decision,
            "passCoverage": (by_decision.get("pass", {}).get("labeled", 0)
                             / by_decision["pass"]["samples"])
            if by_decision.get("pass", {}).get("samples") else None}
