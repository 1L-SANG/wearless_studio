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
#: 청구 보류 사유 — 소비 가능한 컷이 하나도 없다.
REASON_NO_CONSUMABLE_CUT = "no_consumable_cut"
#: enforce 모드에서 판정을 시도했으나 실패 — "판정 없음"과 구분되는 상태다.
REASON_QC_NOT_MEASURED = "qc_enforced_but_not_measured"

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
    if qc_scores.get("imageQcErrored") is True:
        # enforce 모드에서 판정을 **시도했는데 실패**했다. 위의 관용("판정 신호가 없는
        # 것은 나쁨이 아니다")은 애초에 판정을 안 한 낡은 컷을 위한 것이지, 판정이
        # 필요하다고 선언해 놓고 못 잰 컷을 위한 것이 아니다. 둘 다 `p2=None` 이라
        # 구분되지 않던 것을 이 표식이 가른다. 낡은 컷에는 이 키가 없다.
        return CutAuthority(False, REASON_QC_NOT_MEASURED)
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


@dataclass(frozen=True)
class BillableCharge:
    """이번 잡에서 **확정할 금액**과 그 근거."""

    charge: int
    consumable: int
    reason: str | None = None
    version: str = AUTHORITY_VERSION



def resolve_billable_charge(candidates, reserved) -> BillableCharge:
    """예약액을 확정할지 판정한다 — **소비 가능한 컷이 하나라도 있어야** 한다.

    청구는 소비와 **같은 술어**를 쓴다(`cut_is_consumable`). 청구 전용 규칙을 따로 만들면
    "쓸 수는 없는데 돈은 받는" 상태가 두 규칙 사이의 틈에서 다시 생긴다 — 그 틈이 바로
    이 게이트가 막으려는 결함이었다.

    예외는 **닫는 쪽**으로 읽는다. 판정기가 터졌다는 것은 권한을 확인하지 못했다는 뜻이지
    권한이 있다는 뜻이 아니다. 확인하지 못한 것에 과금하지 않는다.

    `reserved` 는 예약 시점 견적이므로 후보 수와 무관하게 **한 번만** 확정한다 —
    소비 가능한 컷이 둘이어도 두 배로 받지 않는다.

    **청구를 소비보다 엄격하게 만들지 않는다.** 한때 "측정된 증거가 있어야 과금" 규칙을
    덧댔다가, `merge_qc_scores` 가 QC 미실행 시 정당하게 `None` 을 돌려준다는 사실 때문에
    멀쩡한 잡 36개가 실패했다. `qc_scores` 부재는 legacy 관용이지 결함이 아니다. 그 관용을
    바꾸려면 **소비 술어 자체**를 바꿔야 하고, 그것은 청구 쪽에서 몰래 할 일이 아니다.
    """
    try:
        reserved_amount = max(0, int(reserved or 0))
    except (TypeError, ValueError):
        reserved_amount = 0

    consumable = 0
    for cut in (candidates or []):
        try:
            if cut_is_consumable(cut):
                consumable += 1
        except Exception:          # noqa: BLE001 — 확인 실패 = 권한 없음
            continue

    if consumable == 0:
        return BillableCharge(0, 0, REASON_NO_CONSUMABLE_CUT)
    return BillableCharge(reserved_amount, consumable, None)
