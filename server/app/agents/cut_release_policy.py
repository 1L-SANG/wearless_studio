"""Optional release guard for normalized cut-output QC, without delivery side effects."""

from collections.abc import Mapping
from typing import Any


_CRITICAL_GATES = ("modelIdentity", "garmentColor", "matchingGarmentIdentity")


def select_stage_qc(qc: Any) -> tuple[Any, str]:
    """Select the same normalized judgment for review attachment and release."""
    selected = qc if isinstance(qc, Mapping) else {}
    repair = selected.get("repair")
    if isinstance(repair, Mapping) and repair.get("accepted") is True:
        return repair.get("stage2Qc"), "stage2"
    return selected, "stage1"


def protected_color_axes(qc: Any, *, policy: str) -> tuple[str, ...]:
    """Only preserve already-approved colors during an accepted local edit.

    Global regeneration and lighting repair keep normal product-color QC; skipping
    this literal before/after check is not global color certification.
    """
    if policy != "critical" or not isinstance(qc, Mapping):
        return ()
    repair = qc.get("repair")
    if not isinstance(repair, Mapping) or repair.get("accepted") is not True or repair.get("route") != "EDIT_STAGE1":
        return ()
    gates = qc.get("gates")
    if not isinstance(gates, Mapping):
        return ()

    def passed(gate: str) -> bool:
        row = gates.get(gate)
        return (isinstance(row, Mapping) and row.get("status") == "PASS"
                and isinstance(row.get("evidence"), str) and bool(row["evidence"].strip()))

    if not passed("lightingShadowReflectionDrape"):
        return ()
    return tuple(axis for axis, gate in (("target", "garmentColor"), ("matching", "matchingGarmentIdentity")) if passed(gate))


def evaluate_cut_release(qc: Any, *, policy: str, baseline_sha256: str | None = None,
                         candidate_sha256: str | None = None) -> dict:
    """Judge the selected candidate, consuming ``cut_output_qc.validate`` results.

    NA applicability is owned by that validator, never by a raw provider response.
    Repair metadata belongs to the worker: only an explicitly accepted repair can
    select stage2. Missing/corrupt stage2 judgments cannot borrow stage1's pass.
    Reasons contain controlled gate/status labels, not provider text or image data.
    """
    selected, final_source = select_stage_qc(qc)
    if policy == "off":
        return {"allowed": True, "blockingGates": [], "reason": "policy_off", "finalSource": final_source}

    gates = selected.get("gates") if isinstance(selected, Mapping) else None
    gates = gates if isinstance(gates, Mapping) else {}
    blocking = []
    reasons = []
    for gate in _CRITICAL_GATES:
        row = gates.get(gate)
        status = row.get("status") if isinstance(row, Mapping) else None
        evidence = row.get("evidence") if isinstance(row, Mapping) else None
        if not isinstance(status, str) or status not in {"PASS", "NA", "FAIL", "UNJUDGEABLE"}:
            status = "INVALID"
        if not isinstance(evidence, str) or not evidence.strip():
            status = "INVALID"
        if status not in {"PASS", "NA"}:
            blocking.append(gate)
            reasons.append(f"{gate}:{status}")
    identity = gates.get("modelIdentity")
    if isinstance(identity, Mapping) and identity.get("status") == "PASS" and "modelIdentity" not in blocking:
        review = selected.get("identityReview")
        review_status = review.get("status") if isinstance(review, Mapping) else None
        evidence = review.get("evidence") if isinstance(review, Mapping) else None
        if review_status != "PASS" or not isinstance(evidence, str) or not evidence.strip():
            blocking.append("identityReview")
            reasons.append("identityReview:" + (review_status if review_status in ("FAIL", "UNJUDGEABLE") else "INVALID"))
    protected = protected_color_axes(qc, policy=policy)
    if protected:
        review = selected.get("colorReview") if isinstance(selected, Mapping) else None
        valid = (isinstance(review, Mapping) and review.get("status") == "PASS"
                 and isinstance(review.get("evidence"), str) and bool(review["evidence"].strip())
                 and isinstance(baseline_sha256, str) and len(baseline_sha256) == 64
                 and isinstance(candidate_sha256, str) and len(candidate_sha256) == 64
                 and review.get("baselineSha256") == baseline_sha256
                 and review.get("candidateSha256") == candidate_sha256)
        axes = review.get("axes") if isinstance(review, Mapping) else None
        for axis in protected:
            row = axes.get(axis) if isinstance(axes, Mapping) else None
            valid = valid and (isinstance(row, Mapping) and row.get("status") == "PASS"
                               and isinstance(row.get("evidence"), str) and bool(row["evidence"].strip()))
        if not valid:
            blocking.append("colorReview")
            reasons.append("colorReview:INVALID")
    return {
        "allowed": not blocking,
        "blockingGates": blocking,
        "reason": "; ".join(reasons) if blocking else "critical_gates_passed",
        "finalSource": final_source,
    }
