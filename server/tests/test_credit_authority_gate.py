"""결제 권한 게이트 — **직접** 시험한다. "전체 스위트 green" 은 증거가 아니다.

이 파일이 지키는 계약(제품 계약 15·16·24):
  · 크레딧은 **권한 있는 최종 산출물**이 있을 때만 확정된다.
  · 청구는 소비와 **같은 술어**를 쓴다 — 청구 전용 규칙을 만들지 않는다.
  · 판정기가 터지면 **닫는 쪽**으로 읽는다(확인 못 한 것에 과금하지 않는다).
  · 예약액은 후보 수와 무관하게 **한 번만** 확정된다.

각 시험은 사후 결과만 보지 않고, **의도한 분기에 실제로 들어갔는지**도 확인한다.
"""

import pytest

from app.services import mannequin_cut_authority as authority
from app.services.mannequin_cut_authority import (
    REASON_NO_CONSUMABLE_CUT, cut_is_consumable, resolve_billable_charge)

RESERVED = 2


def _authorized_cut():
    """권한 있는 컷 — 정본으로 소비 가능하다."""
    cut = {"qc_scores": {"outcome": "pass",
                         "hybridComposite": {"mode": "enforce", "applied": True}}}
    # 픽스처가 실제로 그 상태인지 먼저 못 박는다(이름만 그럴듯한 픽스처 방지).
    assert cut_is_consumable(cut) is True
    return cut


def _blocked_cut(reason_shape="hybrid"):
    """소비 불가 컷 — 서버 권한 판정이 막는다."""
    if reason_shape == "hybrid":
        qc = {"outcome": "pass", "hybridComposite": {"mode": "enforce", "applied": False}}
    elif reason_shape == "regenerate":
        qc = {"outcome": "regenerate"}
    else:
        qc = {"outcome": "pass",
              "structuredQC": {"checks": [{"check": "pattern_fidelity", "status": "fail"}]}}
    cut = {"qc_scores": qc}
    assert cut_is_consumable(cut) is False
    return cut


# ── CASE A — 권한 있는 최종 컷이 있으면 예약액을 정확히 한 번 확정한다 ──────
def test_case_a_an_authorized_cut_charges_the_reserved_amount_once():
    billable = resolve_billable_charge([_authorized_cut()], RESERVED)
    assert billable.charge == RESERVED
    assert billable.consumable == 1
    assert billable.reason is None


# ── CASE B — 후보는 있지만 권한이 없으면 과금하지 않는다 ────────────────────
@pytest.mark.parametrize("shape", ["hybrid", "regenerate", "pattern"])
def test_case_b_an_unauthorized_candidate_is_never_charged(shape):
    """원래 결함 — 권한 없는 후보가 `done` 으로 나가며 예약액을 그대로 차감했다."""
    billable = resolve_billable_charge([_blocked_cut(shape)], RESERVED)
    assert billable.charge == 0
    assert billable.consumable == 0
    assert billable.reason == REASON_NO_CONSUMABLE_CUT


# ── CASE C — 섞여 있으면 정상 금액을 **한 번만** 확정한다 ───────────────────
def test_case_c_mixed_candidates_charge_exactly_once():
    """소비 가능한 컷이 둘이어도 두 배로 받지 않는다 — 예약액은 잡 단위 견적이다."""
    candidates = [_blocked_cut(), _authorized_cut(), _authorized_cut()]
    billable = resolve_billable_charge(candidates, RESERVED)
    assert billable.charge == RESERVED          # 2배가 아니다
    assert billable.consumable == 2             # 그래도 몇 개인지는 관측된다
    assert billable.reason is None


# ── CASE D — 결정론 폴백이 승격되지 못하면 진단 후보만 남고 과금 0 ──────────
def test_case_d_an_unpromoted_fallback_leaves_a_diagnostic_candidate_unbilled():
    """폴백이 시도됐지만 승격 못 한 상태 — 후보는 남기되 돈은 받지 않는다."""
    from app.workers.mannequin_job import _hc_uncertain_summary
    summary = _hc_uncertain_summary("guided_period_unvalidated_harmonic", "",
                                    mode="enforce")
    assert summary["applied"] is False           # 폴백이 승격되지 않은 실제 shape
    candidate = {"qc_scores": {"outcome": "pass", "hybridComposite": summary}}
    assert cut_is_consumable(candidate) is False

    billable = resolve_billable_charge([candidate], RESERVED)
    assert billable.charge == 0
    assert billable.reason == REASON_NO_CONSUMABLE_CUT


def test_case_d_a_promoted_fallback_is_billable():
    """반대 방향도 고정한다 — 승격된 폴백은 **정상 과금 대상**이다.

    이것이 없으면 "항상 0 을 반환" 하는 구현도 위 시험들을 전부 통과한다.
    """
    from app.workers.mannequin_job import _hc_direct_fallback_summary

    class _FB:
        version = "direct_transfer_fallback_v1"
        detail = {"metrics": {}}

    summary = _hc_direct_fallback_summary("enforce", _FB(), "a" * 64, "b" * 64)
    assert summary["applied"] is True
    candidate = {"qc_scores": {"outcome": "pass", "hybridComposite": summary}}
    assert cut_is_consumable(candidate) is True

    billable = resolve_billable_charge([candidate], RESERVED)
    assert billable.charge == RESERVED
    assert billable.reason is None


# ── CASE E — 중복 종결이 이중 과금이 되지 않는다 ────────────────────────────
def test_case_e_repeated_resolution_is_deterministic():
    """판정 자체는 순수·결정론적이다 — 같은 입력에 같은 금액."""
    candidates = [_authorized_cut(), _blocked_cut()]
    first = resolve_billable_charge(candidates, RESERVED)
    second = resolve_billable_charge(candidates, RESERVED)
    assert (first.charge, first.consumable) == (second.charge, second.consumable)
    assert first.charge == RESERVED


def test_case_e_settlement_is_fenced_by_an_idempotency_key():
    """실제 이중 과금 방지는 결제 계층의 fence 다 — 그 fence 가 있는지 코드로 확인한다.

    순수 함수가 결정론적인 것과, 두 번 호출돼도 한 번만 차감되는 것은 다른 보장이다.
    """
    import inspect

    from app import repo
    settle_src = inspect.getsource(repo._settle_credits)
    assert "settle_key" in settle_src, settle_src[:400]
    # 같은 settle_key 로 다시 들어오면 아무것도 쓰지 않고 빠져야 한다.
    assert "ON CONFLICT" in settle_src or "conflict" in settle_src.lower(), settle_src[:400]
    worker_src = inspect.getsource(
        __import__("app.workers.mannequin_job", fromlist=["run_mannequin_job"])
        .run_mannequin_job)
    assert "settle_key" in worker_src


# ── CASE F — 판정기가 터지거나 QC 가 망가져도 **열리지 않는다** ──────────────
def test_case_f_a_raising_authority_evaluator_does_not_charge(monkeypatch):
    """확인 실패는 권한 있음이 아니다 — 예외를 통과로 읽으면 안 된다."""
    def boom(_cut):
        raise RuntimeError("authority exploded")

    monkeypatch.setattr(authority, "cut_is_consumable", boom)
    billable = resolve_billable_charge([_authorized_cut()], RESERVED)
    assert billable.charge == 0
    assert billable.reason == REASON_NO_CONSUMABLE_CUT


@pytest.mark.parametrize("candidates", [None, [], [None], ["not-a-dict"]])
def test_case_f_a_missing_candidate_never_charges(candidates):
    """후보 자체가 없으면(또는 컷 행이 아니면) 과금 대상이 없다."""
    billable = resolve_billable_charge(candidates, RESERVED)
    assert billable.charge == 0, candidates
    assert billable.reason == REASON_NO_CONSUMABLE_CUT


@pytest.mark.parametrize("qc", [None, "garbage", [], {}])
def test_case_f_an_unscored_cut_follows_the_consumption_rule(qc):
    """QC 가 돌지 않은 컷은 **legacy 관용**으로 소비 가능하고, 따라서 과금 대상이다.

    `merge_qc_scores` 는 QC 미실행 시 정당하게 `None` 을 돌려준다 — 부재는 결함이 아니다.
    한때 청구만 "측정 증거"를 요구하도록 조였다가 멀쩡한 잡 36개가 실패했다. 청구가
    소비보다 엄격해지면 그 자체가 규칙 분기이고(제품 계약 24), 관용을 바꾸려면 소비
    술어를 바꿔야 한다.

    이 시험은 **현재 계약을 고정**한다 — 나중에 관용을 없애기로 하면 두 곳이 함께 바뀐다.
    """
    cut = {"qc_scores": qc}
    assert cut_is_consumable(cut) is True, "소비 술어가 바뀌었다면 이 시험도 함께 바뀌어야 한다"
    billable = resolve_billable_charge([cut], RESERVED)
    assert billable.charge == RESERVED
    assert billable.reason is None


@pytest.mark.parametrize("reserved", [None, 0, -5, "x", float("nan")])
def test_case_f_a_malformed_reserved_amount_never_becomes_a_charge(reserved):
    """예약액 자체가 이상해도 임의의 금액이 만들어지지 않는다."""
    billable = resolve_billable_charge([_authorized_cut()], reserved)
    assert billable.charge >= 0
    assert billable.charge in (0, max(0, int(reserved)) if isinstance(reserved, int) else 0)


# ── 술어 공유 — 청구가 소비와 갈라지지 않았는지 구조로 고정한다 ─────────────
def test_billing_uses_the_canonical_consumption_predicate():
    """청구 전용 규칙을 따로 만들면 그 틈에서 결함이 되살아난다(제품 계약 24)."""
    import inspect
    src = inspect.getsource(resolve_billable_charge)
    assert "cut_is_consumable" in src
    for forked in ("hybridComposite", "outcome", "pattern_fidelity", "applied"):
        assert forked not in src, forked


def test_the_worker_charges_through_this_seam_and_not_inline():
    """워커가 자기만의 판정을 다시 쓰면 이 시험들이 아무것도 지키지 못한다."""
    import inspect

    from app.workers import mannequin_job
    src = inspect.getsource(mannequin_job.run_mannequin_job)
    assert "resolve_billable_charge" in src
    assert "charge = billable.charge" in src
    # 예약액을 무조건 확정하던 옛 **코드**가 남아 있으면 안 된다(주석 산문은 제외 —
    # 그 문장에 걸리면 시험이 코드가 아니라 설명을 검사하게 된다).
    code = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "\n        charge = reserved\n" not in code


# ── enforce 인데 못 쟀다 ────────────────────────────────────────────────────
def test_an_enforced_qc_that_failed_to_run_is_not_billable():
    """"QC 를 끈 것"과 "QC 가 실패한 것"은 같은 `p2=None` 이었다 — 이제 갈린다.

    계약 15: 권한 있는 산출물이 **있을 때만** 청구한다. 재지 못한 것은 있다는 증거가
    아니다. vision 이 죽은 채로 잘못된 옷이 그대로 나가고 전액 청구되던 경로다.
    """
    cut = {"candidate": "A", "qc_scores": {"outcome": "auto_pass",
                                           "imageQcErrored": True}}
    assert authority.cut_is_consumable(cut) is False
    verdict = authority.evaluate_mannequin_cut_authority(cut["qc_scores"])
    assert verdict.reason == authority.REASON_QC_NOT_MEASURED
    billable = authority.resolve_billable_charge([cut], 3)
    assert billable.charge == 0
    assert billable.reason == authority.REASON_NO_CONSUMABLE_CUT


def test_the_legacy_tolerance_for_unmeasured_cuts_is_untouched():
    """낡은 컷에는 이 키가 없다 — 과거 프로젝트를 막지 않는다는 관용은 그대로다."""
    for legacy in (None, {}, {"outcome": "auto_pass"}, "not-a-dict"):
        assert authority.evaluate_mannequin_cut_authority(legacy).allowed is True, legacy


def test_a_shadow_qc_failure_does_not_block_authority():
    """shadow 는 애초에 게이트가 아니다 — 관측 실패가 제품 판정을 바꾸면 안 된다.

    워커는 enforce 일 때만 표식을 단다. 그 규율을 구조로 고정한다.
    """
    import inspect

    from app.workers import mannequin_job
    src = inspect.getsource(mannequin_job)
    marker = src.index("image_qc_errored = True")
    window = src[marker - 400:marker]
    assert 'eff_image_qc == "enforce"' in window, window[-200:]


def test_an_explicit_false_marker_does_not_block():
    """표식이 False 면 정상적으로 쟀다는 뜻이다."""
    scores = {"outcome": "auto_pass", "imageQcErrored": False}
    assert authority.evaluate_mannequin_cut_authority(scores).allowed is True
