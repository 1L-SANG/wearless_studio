"""불확정 경로의 산출물 권한 — 폴백이 만든 컷은 쓸 수 있고, 미검증 후보는 못 쓴다.

이 스위트가 지키는 계약:
  · 승격을 통과한 결정론 산출물은 **소비 가능**해야 한다(아니면 폴백이 무의미하다).
  · 승격 못 한 carrier 후보는 **소비 불가**여야 한다(그것이 원래 결함이었다).
  · 주기 진실은 두 경우 모두 UNCERTAIN 으로 남는다 — 권한과 진실은 다른 축이다.
"""

from app.services.mannequin_cut_authority import evaluate_mannequin_cut_authority
from app.workers.mannequin_job import (
    TEXTURE_TRUTH_UNCERTAIN, _hc_direct_fallback_summary, _hc_uncertain_summary)


class _Fallback:
    version = "direct_transfer_fallback_v1"
    detail = {"metrics": {"interiorPx": 275156}, "carrierPanelConfidence": 0.91}


def test_a_promoted_fallback_cut_is_consumable():
    """폴백이 산출물을 냈으면 그것은 제품으로 쓸 수 있어야 한다."""
    summary = _hc_direct_fallback_summary("enforce", _Fallback(), "a" * 64, "b" * 64)
    assert summary["applied"] is True
    verdict = evaluate_mannequin_cut_authority({"hybridComposite": summary})
    assert verdict.allowed is True, verdict.reason


def test_an_unpromoted_candidate_stays_unconsumable():
    """원래 결함 — 검증되지 않은 후보가 정본으로 나가던 것."""
    summary = _hc_uncertain_summary("guided_period_unvalidated_harmonic", "", mode="enforce")
    assert summary["applied"] is False
    verdict = evaluate_mannequin_cut_authority({"hybridComposite": summary})
    assert verdict.allowed is False
    assert verdict.reason


def test_the_period_truth_stays_uncertain_either_way():
    """권한을 준다고 주기를 알게 되는 것은 아니다 — 진실을 덮어쓰지 않는다."""
    applied = _hc_direct_fallback_summary("enforce", _Fallback(), "a" * 64, None)
    held = _hc_uncertain_summary("authoritative_period_unavailable", "", mode="enforce")
    assert applied["textureTruth"] == TEXTURE_TRUTH_UNCERTAIN
    assert held["textureTruth"] == TEXTURE_TRUTH_UNCERTAIN
    # 그리고 무엇이 그 픽셀을 만들었는지 남긴다.
    assert applied["textureSource"] == "direct_source_transfer"
    assert applied["directTransfer"]["version"] == "direct_transfer_fallback_v1"


def test_shadow_mode_does_not_claim_authority():
    """shadow 는 관측 전용이다 — 켜는 것만으로 컷이 바뀌면 안 된다."""
    summary = _hc_direct_fallback_summary("shadow", _Fallback(), "a" * 64, None)
    assert summary["applied"] is False
    assert summary["wouldApply"] is True
    assert evaluate_mannequin_cut_authority({"hybridComposite": summary}).allowed is True
