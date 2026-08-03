"""Product Truth에서 실행 비용·검수 위험 정책을 결정하는 순수 규칙 엔진."""

from __future__ import annotations

COMMON_MODULES = ["composition", "image_quality", "color_fidelity", "style_consistency"]
COMPLEX_TRAITS = {"lace", "sheer", "transparent", "openwork", "sequins", "embroidery_dense"}
PATTERNS = {"stripe", "check", "plaid", "gingham", "tartan"}


def build_garment_profile(truth: dict) -> dict:
    garment = truth.get("garmentSpec") or truth.get("garment_spec") or {}
    pattern = truth.get("patternSpec") or truth.get("pattern_spec") or {}
    protected = (truth.get("protectedAssets") or truth.get("protectedDetails")
                 or truth.get("protected_details") or {})
    category = str(truth.get("category") or garment.get("category") or "unknown").strip().lower()
    subcategory = str(garment.get("subcategory") or "").strip().lower()
    if category in {"top", "outer", "unknown"} and subcategory not in {"", "unknown"}:
        category = subcategory
    traits = {str(v).strip().lower() for v in garment.get("materialTraits", []) if v}
    pattern_type = str(pattern.get("type") or "unknown").strip().lower()
    def count_value(value) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(value is not None and value != "")

    protected_count = sum(max(count_value(protected.get(key)) for key in aliases) for aliases in (
        ("logo", "logos"),
        ("textPrint", "graphicPrint", "print", "prints"),
        ("embroidery",),
    ))
    risk = 0
    reasons: list[str] = []
    if pattern_type in PATTERNS:
        risk += 30
        reasons.append("periodic_pattern")
    if protected_count:
        risk += 30
        reasons.append("protected_detail")
    if traits & COMPLEX_TRAITS:
        risk += 50
        reasons.append("complex_material")
    if category in {"blouse", "dress", "jacket"}:
        risk += 10
    return {
        "category": category,
        "patternType": pattern_type,
        "protectedDetailCount": protected_count,
        "materialTraits": sorted(traits),
        "riskScore": min(100, risk),
        "riskReasons": reasons,
    }


def select_pipeline_policy(profile: dict, *, policy_version: str = "pipeline-v1") -> dict:
    risk = int(profile.get("riskScore") or 0)
    traits = set(profile.get("materialTraits") or [])
    if traits & COMPLEX_TRAITS or risk >= 70:
        lane = "MANUAL"
    elif risk >= 25:
        lane = "GUARDED"
    else:
        lane = "FAST"
    modules = list(COMMON_MODULES)
    if profile.get("patternType") in PATTERNS:
        modules.append("pattern_fidelity")
    if int(profile.get("protectedDetailCount") or 0):
        modules.append("protected_detail")
    if lane == "MANUAL":
        modules.append("advanced_structure")
    if traits & COMPLEX_TRAITS:
        modules.append("material")
    return {
        "policyVersion": policy_version,
        "lane": lane,
        "riskScore": risk,
        "modules": modules,
        "candidateCount": 1 if lane == "FAST" else 2,
        "resolution": "1K" if lane == "FAST" else "2K",
        "autoApproval": lane != "MANUAL",
    }
