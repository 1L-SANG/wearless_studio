"""새 QC 결과와 기존 mannequin_cuts.qc_scores 사이의 호환 조립기."""

from __future__ import annotations


def assemble_qc_result(*, generation_output_id: str | None, truth_package_id: str | None,
                       checks: list[dict], decision: dict, policy_version: str) -> dict:
    scores = {
        str(c.get("check")): round(float(c["score"]) * 100)
        for c in checks if c.get("score") is not None
    }
    return {
        "generationOutputId": generation_output_id,
        "truthPackageId": truth_package_id,
        "policyVersion": policy_version,
        "overallDecision": decision["overallDecision"],
        "scores": scores,
        "criticalErrors": list(decision.get("criticalErrors") or []),
        "warnings": list(decision.get("warnings") or []),
        "failedRegions": list(decision.get("failedRegions") or []),
        "regenerationInstructions": list(decision.get("regenerationInstructions") or []),
        "checks": checks,
        # 구형 UI가 읽는 이름. pass만 auto_pass이며 review/reject를 정상 완료로 위장하지 않는다.
        "outcome": "auto_pass" if decision["overallDecision"] == "pass" else "needs_review",
        "salvaged": False,
    }
