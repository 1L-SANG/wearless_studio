"""Approved Baseline — 승인 시점 스냅샷 조립 (순수 함수).

승인의 핵심은 "이 이미지가 정본이다"가 아니라 **"이때 무엇이 고정됐는가"**를 남기는 것이다.
나중에 프로필·프롬프트·설정이 바뀌어도 과거 승인의 의미가 소급해 바뀌면 안 된다.

지금 코드에는 좌표 기반 framing/background/lighting 프로필이 **존재하지 않는다** — 포즈·
카메라·배경은 프롬프트 문장으로 고정된다. 그래서 값이 없는 항목은 거짓으로 채우지 않고
`unavailable` + 사유로 남기고, 실제로 그것들을 고정하는 **프롬프트 버전**을 함께 스냅샷한다.
"""

# 기획서 12.4 의 항상 잠글 항목. 값을 못 구하는 것도 목록에서 빼지 않는다 —
# 빠진 항목과 "값을 모르는 항목"은 완전히 다른 사실이다.
ALWAYS_LOCKED = (
    "mannequinIdentity",
    "pose",
    "camera",
    "framing",
    "background",
    "lighting",
    "garmentCategory",
)

# 프롬프트 문장으로만 고정되는 항목들 — 구조화 프로필이 생기면 그때 값이 채워진다.
_PROMPT_FIXED_REASON = "no_structured_profile_prompt_fixed"


def _recorded(value):
    return {"status": "recorded", "value": value}


def _unavailable(reason: str):
    return {"status": "unavailable", "reason": reason}


def build_locked_invariants(cut: dict) -> dict:
    """승인 컷 → locked invariants 스냅샷.

    cut: repo.get_mannequin_cut_for_approval 결과(generation_metadata·clothing_type 포함).
    """
    md = cut.get("generation_metadata") or {}
    if not isinstance(md, dict):
        md = {}
    gender = md.get("profileGender")
    category = md.get("profileCategory") or cut.get("clothing_type")

    identity = {
        "status": "partial" if gender else "unavailable",
        "gender": gender,
        # 어느 베이스 마네킹 asset 이 쓰였는지는 **설정값**이라 컷에 저장되지 않는다.
        # 현재 설정을 적으면 "승인 시점의 설정"이 아니라 "조회 시점의 설정"이 된다.
        "baseMannequinAssetId": None,
        "reason": None if gender else "no_profile_gender_on_cut",
    }
    out = {
        "mannequinIdentity": identity,
        "pose": _unavailable(_PROMPT_FIXED_REASON),
        "camera": _unavailable(_PROMPT_FIXED_REASON),
        "framing": _unavailable(_PROMPT_FIXED_REASON),
        "background": _unavailable(_PROMPT_FIXED_REASON),
        "lighting": _unavailable(_PROMPT_FIXED_REASON),
        "garmentCategory": (_recorded(category) if category
                            else _unavailable("no_category_on_cut_or_product")),
    }
    # 위 항목들을 실제로 고정하는 것은 프롬프트다 — 버전이 바뀌면 같은 "pose" 도 달라진다.
    out["promptVersion"] = (_recorded(md.get("promptVersion")) if md.get("promptVersion")
                            else _unavailable("no_prompt_version_on_cut"))
    out["generationPath"] = (_recorded(md.get("generationPath"))
                             if md.get("generationPath")
                             else _unavailable("no_generation_path_on_cut"))
    return out


def build_profile_snapshots(cut: dict) -> dict:
    """profile 스냅샷 4종. 구조화 프로필이 없으면 None 이 아니라 **사유가 담긴 dict** 다.

    None 으로 두면 "저장 안 함"과 "그런 개념이 아직 없음"이 구분되지 않는다.
    """
    md = cut.get("generation_metadata") or {}
    if not isinstance(md, dict):
        md = {}
    absent = {"available": False, "reason": _PROMPT_FIXED_REASON,
              "promptVersion": md.get("promptVersion")}
    return {
        "mannequin_profile": {
            "available": bool(md.get("profileGender")),
            "gender": md.get("profileGender"),
            "category": md.get("profileCategory"),
            "promptVersion": md.get("promptVersion"),
        },
        "framing_profile": dict(absent),
        "background_profile": dict(absent),
        "lighting_profile": dict(absent),
    }


def approval_review_state(cut: dict) -> dict:
    """승인 요청 시점의 QC 상태 — 자동 통과와 **명시적 사용자 승인**을 구분해 남긴다."""
    qs = cut.get("qc_scores")
    qs = qs if isinstance(qs, dict) else {}
    hybrid = qs.get("hybridComposite") if isinstance(qs.get("hybridComposite"), dict) else {}
    outcome = qs.get("outcome")
    return {
        "qcOutcome": outcome,
        "needsReview": bool(hybrid.get("needsReview")) or outcome == "needs_review",
        # 승인은 언제나 사람이 누른 것이다 — 성공 결과가 스스로 baseline 이 되는 경로는 없다.
        "explicitUserApproval": True,
    }
