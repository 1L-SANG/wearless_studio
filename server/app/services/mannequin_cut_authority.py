"""마네킹 컷의 **소비 권한** 판정 — 순수 함수, DB·provider·부수효과 0.

지금까지 이 판정은 서버에 없었다. `reviewState.js` 가 화면에서 컷을 막는 동안 서버는
`selected_mannequin_id` 에 아무 문자열이나 받고, 승인은 소유권만 보고, detail page 워커는
QC 를 읽지 않았다. 프론트가 막는 것과 서버가 막는 것은 다르다 — 전자는 렌더링 규칙이고
후자는 계약이다.

**보이는 것과 쓸 수 있는 것을 가른다.** 목록·이력·디버깅은 막힌 컷도 그대로 돌려준다.
막는 것은 그 컷을 *정본으로 소비*하는 행위뿐이다: 선택, 승인, 상세페이지 입력, 편집 부모,
시리즈 일관성 기준.

판정은 **서버가 이미 기록한 QC 사실**에서만 나온다. 새 임계값·새 점수·새 검수 워크플로를
만들지 않는다. `needsReview` 는 단독으로 권한을 뺏지 않는다 — 그것은 사람이 봐야 한다는
표시이지 "제품으로 쓸 수 없다"가 아니며, 그렇게 취급하면 과거 정상 컷이 통째로 막힌다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 판정 사유 — 내부 어휘. 공개 응답에는 싣지 않는다(라우트가 일반 코드로 바꾼다).
REASON_OUTCOME_REGENERATE = "qc_outcome_regenerate"
REASON_HYBRID_NOT_APPLIED = "hybrid_composite_not_applied"
REASON_PATTERN_FIDELITY_FAILED = "pattern_fidelity_failed"

AUTHORITY_VERSION = "mannequin_cut_authority_v1"


@dataclass(frozen=True)
class CutAuthority:
    allowed: bool
    reason: str | None = None
    version: str = AUTHORITY_VERSION


_ALLOWED = CutAuthority(True)


def _hybrid_blocks(qc_scores: dict) -> bool:
    """hybrid composite 가 이 컷을 제품으로 인정하지 않는가.

    `mode == "shadow"` 는 관측 전용이다 — shadow 에서 합성이 적용되지 않은 것은 정상이고,
    그것만으로 컷을 막으면 shadow 를 켜는 순간 모든 컷이 사용 불가가 된다(프론트도 같은
    이유로 shadow 를 무시한다).
    """
    hybrid = qc_scores.get("hybridComposite")
    if not isinstance(hybrid, dict):
        return False                      # legacy: 합성 단계 자체가 없던 컷
    if hybrid.get("mode") == "shadow":
        return False
    # `applied is False` 만 본다. 키가 없는 구 스냅샷을 False 로 읽으면 과거 컷이 막힌다.
    return hybrid.get("applied") is False


def _pattern_fidelity_failed(qc_scores: dict) -> bool:
    structured = qc_scores.get("structuredQC")
    if not isinstance(structured, dict):
        return False
    checks = structured.get("checks")
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(check, dict)
        and check.get("check") == "pattern_fidelity"
        and check.get("status") == "fail"
        for check in checks
    )


def evaluate_mannequin_cut_authority(qc_scores: dict | None) -> CutAuthority:
    """qc_scores → 이 컷을 정본으로 소비해도 되는가.

    qc_scores 가 없거나 dict 가 아니면 **허용**한다. 판정 신호가 없는 것은 나쁨이 아니다
    — 대부분의 기존 컷이 여기 해당하고, 이것을 실패로 읽으면 과거 프로젝트가 전부 막힌다
    (`score_outcome` 이 미채점을 auto_pass 로 눕히는 것과 같은 규율).
    """
    if not isinstance(qc_scores, dict):
        return _ALLOWED
    if qc_scores.get("outcome") == "regenerate":
        return CutAuthority(False, REASON_OUTCOME_REGENERATE)
    if _hybrid_blocks(qc_scores):
        return CutAuthority(False, REASON_HYBRID_NOT_APPLIED)
    if _pattern_fidelity_failed(qc_scores):
        return CutAuthority(False, REASON_PATTERN_FIDELITY_FAILED)
    return _ALLOWED


def cut_is_consumable(cut: dict | None) -> bool:
    """컷 행(qc_scores 포함) 단위 편의 판정 — 행이 없으면 False."""
    if not isinstance(cut, dict):
        return False
    return evaluate_mannequin_cut_authority(cut.get("qc_scores")).allowed
