"""QC check들을 자유 추론 없이 pass/review/reject로 접는 고정 정책."""

from __future__ import annotations


def _unique(values):
    return list(dict.fromkeys(v for v in values if v))


def decide(checks: list[dict], *, policy_version: str, auto_approval: bool = True) -> dict:
    critical = _unique(e for c in checks for e in (c.get("criticalErrors") or []))
    warnings = _unique(w for c in checks for w in (c.get("warnings") or []))
    unavailable = [str(c.get("check") or "unknown") for c in checks
                   if c.get("status") in {"unavailable", "error", "timeout"}]
    failed = [c for c in checks if c.get("status") == "fail"]
    for name in unavailable:
        warnings.append(f"qc_unavailable:{name}")
    if not auto_approval:
        warnings.append("manual_review_required")
    if critical:
        decision = "reject"
    elif unavailable or not auto_approval:
        decision = "review"
    elif any(c.get("severity") == "critical" for c in failed):
        decision = "reject"
    elif failed:
        decision = "reject" if any(c.get("check") in {
            "color_fidelity", "pattern_fidelity", "garment_structure", "protected_detail"
        } for c in failed) else "review"
    else:
        decision = "pass"
    return {
        "overallDecision": decision,
        "policyVersion": policy_version,
        "criticalErrors": critical,
        "warnings": _unique(warnings),
        "failedRegions": [r for c in checks for r in (c.get("failedRegions") or [])],
        "regenerationInstructions": _unique(
            i for c in checks for i in (c.get("regenerationInstructions") or [])
        ),
    }
