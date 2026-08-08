"""guided 주기는 결정론 투영의 권한이 아니다 — Branch-3 하모닉 커플링 차단.

배경(2026-08-08 감사, 재감사 금지):
  · warp 는 `period_profile_lab` 한 traversal 을 `target_period_px` 에 그대로 얹는다.
    따라서 스칼라 주기와 프로파일은 **같은 FULL_COLOR_REPEAT 단위**여야 한다.
  · `find_period_guided` 의 후보 격자는 autocorr peak × {1,2,3} 뿐이다. 실자산
    f91cbac5 에서 격자는 {15,30,45} 였고, 같은 바이트의 scan/shadow 계열은 ~19.2–21.5
    를 읽었다 — 정답이 격자에 없다. ROI 6% 이동에 승자가 45→30→15 로 뒤집혔다.
  · 실 production(fafe3ca8/b280efaa)은 45.0 으로 몸통 반복 32.89 를 렌더했고, 같은 옷
    scan 경로는 71.19 였다(2.16×).
하모닉이 어긋나도 색 순서·폭 비·팔레트는 전부 보존되므로 하류 어떤 검사도 못 잡는다.
그래서 차단은 **상류 권한 박탈**로만 가능하다.
"""

import dataclasses

import numpy as np
import pytest

from app.services.hybrid_composite.types import CompositeFailure
from app.workers import mannequin_job as mj

from test_hybrid_worker_integration import _run_job, _statuses

SHADOW = {"mannequin_hybrid_composite": "shadow",
          "mannequin_texture_projection_2d": "shadow"}
ENFORCE = {"mannequin_hybrid_composite": "enforce",
           "mannequin_texture_projection_2d": "shadow"}


def _break_front_torso_scan(monkeypatch):
    """Front torso 재추출만 실패시킨다 — Detail 모델(프로파일 정본)은 살려 둔다."""
    original = mj.hc_stripe.extract_stripe_model_scan

    def scan(roi_bgr, **kwargs):
        if kwargs.get("source_asset_id") == "front":
            return CompositeFailure("stripe_model_low_confidence", "front torso scan 강제 실패")
        return original(roi_bgr, **kwargs)

    monkeypatch.setattr(mj.hc_stripe, "extract_stripe_model_scan", scan)


def _guided_returns(monkeypatch, anchor, candidates=()):
    seen = {"called": False}

    def guided(_roi, _model, collect=None):
        seen["called"] = True
        if collect is not None:
            collect.extend(dict(c) for c in candidates)
        return anchor

    monkeypatch.setattr(mj.hc_stripe, "find_period_guided", guided)
    return seen


def _spy_projection(monkeypatch):
    """투영에 실제로 들어간 source 주기를 그대로 받아 적는다."""
    seen: list[float] = []
    original_plan = mj.hc_projection.plan_periodic_projection
    original_target = mj.hc_scale.target_period_px
    original_warp = mj.hc_warp.composite_stripe
    warp_calls: list[float] = []

    def plan(**kwargs):
        seen.append(float(kwargs["source_period_px"]))
        return original_plan(**kwargs)

    def target(**kwargs):
        seen.append(float(kwargs["source_period_px"]))
        return original_target(**kwargs)

    def warp(*args, **kwargs):
        warp_calls.append(float(kwargs["target_period_px"]))
        return original_warp(*args, **kwargs)

    monkeypatch.setattr(mj.hc_projection, "plan_periodic_projection", plan)
    monkeypatch.setattr(mj.hc_scale, "target_period_px", target)
    monkeypatch.setattr(mj.hc_warp, "composite_stripe", warp)
    return seen, warp_calls


def _hybrid(calls):
    return calls["success"][0]["candidates"][0]["qc_scores"]["hybridComposite"]


# ── A. Branch 1 — front scan 성공 + 구조 일치 ────────────────────────────────
def test_branch1_front_scan_authority_unchanged(monkeypatch):
    periods, warp_calls = _spy_projection(monkeypatch)
    _oplog, calls, r2_saved, emits = _run_job(monkeypatch, settings_kw=ENFORCE)

    assert calls["failure"] == []
    assert calls["success"]
    statuses = _statuses(emits)
    assert "hybrid_texture_projection_plan" in statuses
    assert all(p.get("texture_truth") is None for _e, p in emits)
    palette = next(p for _e, p in emits if p.get("status") == "hybrid_palette_source")
    assert palette["chosen"] == "front_scan"
    hybrid = _hybrid(calls)
    assert hybrid["applied"] is True
    assert "textureTruth" not in hybrid
    # 투영이 실제로 구동됐고, 그 스칼라는 front scan 이 준 값이다.
    assert periods and warp_calls
    assert r2_saved


# ── B. Branch 2 — front scan 성공 + 구조 불일치(Detail 프로파일 유지) ─────────
def test_branch2_front_period_detail_profile_unchanged(monkeypatch):
    """front 줌에서 잔줄이 2색으로 퇴화한 실측 조건 — 주기는 front, 프로파일은 Detail."""
    original = mj.hc_stripe.extract_stripe_model_scan
    front_period: list[float] = []

    def degenerate_front(roi_bgr, **kwargs):
        model = original(roi_bgr, **kwargs)
        if kwargs.get("source_asset_id") != "front" or isinstance(model, CompositeFailure):
            return model
        front_period.append(float(model.period_px))
        return dataclasses.replace(
            model,
            color_sequence_lab=model.color_sequence_lab[:2],
            line_width_ratios=(0.7, 0.3))

    monkeypatch.setattr(mj.hc_stripe, "extract_stripe_model_scan", degenerate_front)
    periods, warp_calls = _spy_projection(monkeypatch)
    _oplog, calls, _r2, emits = _run_job(monkeypatch, settings_kw=SHADOW)

    assert calls["failure"] == []
    statuses = _statuses(emits)
    assert "hybrid_texture_projection_plan" in statuses
    assert all(p.get("texture_truth") is None for _e, p in emits)
    palette = [p for _e, p in emits if p.get("status") == "hybrid_palette_source"]
    # 구조 불일치이므로 front_scan 팔레트 교체는 일어나지 않는다(Branch 2).
    assert all(p["chosen"] != "front_scan" for p in palette)
    hybrid = _hybrid(calls)
    assert "textureTruth" not in hybrid
    # 투영 권한은 그대로 살아 있고, 스칼라는 front scan 이 준 주기다.
    assert periods and warp_calls
    assert front_period
    assert periods[0] == pytest.approx(front_period[-1], rel=1e-3)


# ── C. front scan 실패 + guided 없음 — 같은 불확정 상태 ──────────────────────
def test_no_period_source_at_all_is_the_same_uncertainty_state(monkeypatch):
    """증거가 더 적다고 더 강한 실패로 가지 않는다 — guided 유무는 provenance 차이뿐."""
    _break_front_torso_scan(monkeypatch)
    _guided_returns(monkeypatch, None)
    periods, warp_calls = _spy_projection(monkeypatch)
    _oplog, calls, _r2, emits = _run_job(monkeypatch, settings_kw=SHADOW)

    assert periods == [] and warp_calls == []
    completed = next(p for _e, p in emits
                     if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "authoritative_period_unavailable"
    assert completed["texture_truth"] == "TEXTURE_TRUTH_UNCERTAIN"
    assert completed["fail_closed"] is False
    hybrid = _hybrid(calls)
    assert hybrid["textureTruth"] == "TEXTURE_TRUTH_UNCERTAIN"
    assert hybrid["failureReason"] == "authoritative_period_unavailable"
    assert hybrid["failClosed"] is False
    assert hybrid["applied"] is False
    assert hybrid["needsReview"] is True
    # guided 관측치는 없다 — 이것이 B/C 를 가르는 유일한 차이다.
    assert "guidedObservedPeriodPx" not in hybrid
    assert "guidedAuthorityAccepted" not in hybrid


def test_missing_guided_is_non_terminal_in_enforce_and_costs_no_provider_call(monkeypatch):
    _break_front_torso_scan(monkeypatch)
    _guided_returns(monkeypatch, None)
    periods, warp_calls = _spy_projection(monkeypatch)
    oplog, calls, r2_saved, emits = _run_job(monkeypatch, settings_kw=ENFORCE)

    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    assert r2_saved, "carrier 후보 바이트가 보존되어야 한다"
    assert periods == [] and warp_calls == []
    hybrid = _hybrid(calls)
    assert hybrid["mode"] == "enforce"
    assert hybrid["failClosed"] is False
    # provider 예산 회귀 방지 — 불확정만으로 재시도/추가 이미지 호출이 생기지 않는다.
    assert sum(1 for entry in oplog if entry[0] == "gen") == 1
    assert "hybrid_carrier_retry" not in _statuses(emits)


def test_both_missing_period_cases_share_one_state_and_differ_only_in_provenance():
    """B(guided 있음)와 C(guided 없음)의 차이는 reason/관측치뿐, severity 가 아니다."""
    def run(anchor):
        mp = pytest.MonkeyPatch()
        try:
            _break_front_torso_scan(mp)
            _guided_returns(mp, anchor)
            _spy_projection(mp)
            _oplog, calls, _r2, _emits = _run_job(mp, settings_kw=ENFORCE)
            return _hybrid(calls), calls
        finally:
            mp.undo()

    with_guided, calls_b = run(("vertical", 45.0, 0.7372))
    without_guided, calls_c = run(None)

    for hybrid in (with_guided, without_guided):
        assert hybrid["textureTruth"] == "TEXTURE_TRUTH_UNCERTAIN"
        assert hybrid["failClosed"] is False
        assert hybrid["applied"] is False
        assert hybrid["needsReview"] is True
    assert calls_b["failure"] == [] and calls_c["failure"] == []
    assert with_guided["failureReason"] == "guided_period_unvalidated_harmonic"
    assert without_guided["failureReason"] == "authoritative_period_unavailable"
    assert "guidedObservedPeriodPx" in with_guided
    assert "guidedObservedPeriodPx" not in without_guided


# ── D/E/F. guided 가 답을 내도 투영 권한은 없다 ──────────────────────────────
@pytest.mark.parametrize(
    "period,score,label",
    [
        (45.0, 0.7372, "multiplier_3"),      # 실 production 승자
        (30.0, 0.7254, "multiplier_2"),
        (15.0, 0.7463, "multiplier_1_autocorr_peak"),
    ],
)
def test_guided_period_never_authorizes_projection(monkeypatch, period, score, label):
    """multiplier=1(=실제 autocorr peak)도 권한이 없다.

    실증거에서 base peak 15 자체가 scan/shadow 계열 ~20 과 다른 단위였다.
    """
    _break_front_torso_scan(monkeypatch)
    seen = _guided_returns(
        monkeypatch, ("vertical", period, score),
        candidates=[{"axis": "vertical", "periodPx": 15.0, "score": 0.6315,
                     "autocorrelationPeak": True, "basePeakPx": 15.0, "multiplier": 1},
                    {"axis": "vertical", "periodPx": 30.0, "score": 0.6459,
                     "autocorrelationPeak": False, "basePeakPx": 15.0, "multiplier": 2},
                    {"axis": "vertical", "periodPx": 45.0, "score": 0.7372,
                     "autocorrelationPeak": False, "basePeakPx": 15.0, "multiplier": 3}])
    periods, warp_calls = _spy_projection(monkeypatch)
    _oplog, calls, _r2, emits = _run_job(monkeypatch, settings_kw=SHADOW)

    assert seen["called"] is True
    # 핵심 단언 — guided 스칼라는 투영/warp 어디에도 들어가지 않는다.
    assert periods == [], f"{label}: guided period reached projection: {periods}"
    assert warp_calls == []

    completed = next(p for _e, p in emits
                     if p.get("status") == "hybrid_composite_completed")
    assert completed["texture_truth"] == "TEXTURE_TRUTH_UNCERTAIN"
    assert completed["outcome"] == "guided_period_unvalidated_harmonic"
    assert completed["fail_closed"] is False
    hybrid = _hybrid(calls)
    assert hybrid["textureTruth"] == "TEXTURE_TRUTH_UNCERTAIN"
    assert hybrid["failureReason"] == "guided_period_unvalidated_harmonic"
    assert hybrid["applied"] is False
    assert hybrid["failClosed"] is False
    assert hybrid["needsReview"] is True
    assert hybrid["guidedObservedPeriodPx"] == pytest.approx(period)
    assert hybrid["guidedAuthorityAccepted"] is False
    assert hybrid["guidedCandidateCount"] == 3


# ── 후보 보존 + 터미널 실패 회귀 ─────────────────────────────────────────────
def test_guided_only_run_preserves_candidate_and_never_fails_closed(monkeypatch):
    _break_front_torso_scan(monkeypatch)
    _guided_returns(monkeypatch, ("vertical", 45.0, 0.7372))
    periods, warp_calls = _spy_projection(monkeypatch)
    oplog, calls, r2_saved, emits = _run_job(monkeypatch, settings_kw=ENFORCE)

    # enforce 인데도 잡이 죽지 않는다 — 후보는 그대로 출고된다.
    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    assert r2_saved, "carrier 후보 바이트가 보존되어야 한다"
    hybrid = _hybrid(calls)
    assert hybrid["mode"] == "enforce"
    assert hybrid["failClosed"] is False
    assert periods == [] and warp_calls == []
    completed = next(p for _e, p in emits
                     if p.get("status") == "hybrid_composite_completed")
    assert completed["fail_closed"] is False
    # provider 예산 회귀 방지 — 이 경로가 추가 이미지 호출을 만들지 않는다.
    assert sum(1 for entry in oplog if entry[0] == "gen") == 1
    assert "hybrid_carrier_retry" not in _statuses(emits)


# ── 실 자산 회귀 픽스처: 15 / 30 / 45 격자, 승자 45 ──────────────────────────
def test_historical_45px_guided_winner_cannot_authorize_projection(monkeypatch):
    """f91cbac5 실측 메타데이터. 이미지/provider 재실행 없음 — 메타데이터만 인코딩한다."""
    lattice = [
        {"axis": "vertical", "periodPx": 15.0, "score": 0.6315,
         "autocorrelationPeak": True, "basePeakPx": 15.0, "multiplier": 1},
        {"axis": "vertical", "periodPx": 30.0, "score": 0.6459,
         "autocorrelationPeak": False, "basePeakPx": 15.0, "multiplier": 2},
        {"axis": "vertical", "periodPx": 45.0, "score": 0.7372,
         "autocorrelationPeak": False, "basePeakPx": 15.0, "multiplier": 3},
    ]
    scan_shadow_family = (19.2, 21.5)     # 같은 바이트의 결정론 scan/shadow 판독 범위
    assert not any(lo <= c["periodPx"] <= hi
                   for c in lattice
                   for lo, hi in [scan_shadow_family]), \
        "격자에 scan/shadow 계열 값이 없다는 것이 이 버그의 정의다"

    _break_front_torso_scan(monkeypatch)
    _guided_returns(monkeypatch, ("vertical", 45.0, 0.7372), candidates=lattice)
    periods, warp_calls = _spy_projection(monkeypatch)
    _oplog, calls, _r2, emits = _run_job(monkeypatch, settings_kw=ENFORCE)

    assert 45.0 not in periods and periods == []
    assert warp_calls == []
    assert calls["failure"] == []
    hybrid = _hybrid(calls)
    assert hybrid["guidedObservedPeriodPx"] == pytest.approx(45.0)
    assert hybrid["guidedCandidateCount"] == 3


# ── guided 는 진단 증거로 남는다 ─────────────────────────────────────────────
def test_guided_observation_is_still_recorded_as_evidence(monkeypatch):
    _break_front_torso_scan(monkeypatch)
    _guided_returns(monkeypatch, ("horizontal", 45.0, 0.7372))
    _spy_projection(monkeypatch)
    _oplog, calls, _r2, _emits = _run_job(monkeypatch, settings_kw=SHADOW)

    hybrid = _hybrid(calls)
    assert hybrid["guidedObservedAxis"] == "horizontal"
    assert hybrid["guidedObservedScore"] == pytest.approx(0.7372)
    # 관측치는 남되 권한 필드와 분리되어 있다.
    assert "sourcePeriodPx" not in hybrid
    assert np.isfinite(hybrid["guidedObservedPeriodPx"])
