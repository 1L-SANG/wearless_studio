"""Reading the source period from several crops, and watching the old one.

The scan already votes across windows inside a crop. It cannot see the crop
boundary, which comes from Vision landmarks — and on one real photo that boundary
moved enough that the scan failed on most probe crops, handing the answer to the
guided search, whose winner flipped between 15, 30 and 45.

This resolver runs the same scan over concentric insets of the production crop and
asks whether the readings agree. In P3 it decides nothing: production still uses
its own reading, and these tests pin that separation.
"""
from __future__ import annotations

import inspect
import random

import pytest

from app.services.hybrid_composite import source_texture_qa as qa
from app.services.hybrid_composite import source_texture_resolver as r
from app.services.hybrid_composite import stripe_model as sm


class _Model:
    """Stands in for a StripeModel — only the fields the resolver reads."""

    reason = None

    def __init__(self, period, confidence=0.8, axis="vertical"):
        self.period_px, self.confidence, self.axis = period, confidence, axis


class _Failure:
    period_px = None

    def __init__(self, reason="stripe_model_low_confidence"):
        self.reason = reason


def _family(n=4):
    """n distinct ROI specs. Built directly so resolver tests do not depend on how
    many members the production family happens to have."""
    return [r.RoiSpec(roi_id=f"roi-{i}", bounds=(0, 0, 1000 + i, 1000 + i),
                      construction="synthetic", role="test") for i in range(n)]


def _resolve(periods, family=None):
    """periods: list of float | None (None = scan failure), one per ROI."""
    fam = family or _family(len(periods))
    seq = list(periods)
    return r.resolve(fam, lambda spec: (_Model(seq[fam.index(spec)])
                                        if seq[fam.index(spec)] is not None
                                        else _Failure()))


# ---------------------------------------------------- A: deterministic family

def test_a_the_roi_family_is_a_pure_function_of_the_production_crop():
    a = r.build_roi_family((750, 1080, 2205, 2680), width=3000, height=4000)
    b = r.build_roi_family((750, 1080, 2205, 2680), width=3000, height=4000)
    assert [s.bounds for s in a] == [s.bounds for s in b]
    assert all(not s.adds_vision_dependency and not s.candidate_dependent for s in a)
    assert all(s.deterministic_given_source_context for s in a)


def test_a_no_member_is_smaller_than_the_crop_production_trusts():
    """The scan needs >=3 agreeing patches; a smaller crop loses that floor.

    Measured on the real photo: baseline and both outsets resolved, every inset
    failed with stripe_model_low_confidence.
    """
    base = (750, 1080, 2205, 2680)
    family = r.build_roi_family(base, width=3000, height=4000)
    base_area = (base[2] - base[0]) * (base[3] - base[1])
    for spec in family:
        x0, y0, x1, y1 = spec.bounds
        assert (x1 - x0) * (y1 - y0) >= base_area, spec
        assert x0 <= base[0] and y0 <= base[1] and x1 >= base[2] and y1 >= base[3], spec
    assert r.ROI_OUTSET_FRACTIONS[0] == 0.0, "the production crop itself is the baseline"
    assert family[0].roi_id == "baseline"


def test_a_members_stay_inside_the_image():
    for spec in r.build_roi_family((10, 10, 900, 900), width=1000, height=1000):
        x0, y0, x1, y1 = spec.bounds
        assert 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000, spec


def test_a_members_never_shrink_below_the_scan_multi_window_floor():
    for spec in r.build_roi_family((0, 0, 1100, 1100), width=2000, height=2000):
        w, h = spec.bounds[2] - spec.bounds[0], spec.bounds[3] - spec.bounds[1]
        assert min(w, h) >= r.MIN_SCAN_SIDE_PX


def test_a_the_placket_split_was_measured_and_dropped():
    """Accepted for call-site compatibility, deliberately unused."""
    with_box = r.build_roi_family((750, 1080, 2205, 2680), width=3000, height=4000,
                                  placket_box=[[0.47, 0.23], [0.53, 0.23],
                                               [0.54, 0.62], [0.48, 0.62]])
    without = r.build_roi_family((750, 1080, 2205, 2680), width=3000, height=4000)
    assert [s.bounds for s in with_box] == [s.bounds for s in without]


def test_a_a_crop_too_small_to_scan_yields_no_family():
    assert r.build_roi_family((0, 0, 400, 400), width=1000, height=1000) == []
    assert r.build_roi_family((10, 10, 10, 10), width=100, height=100) == []


def test_a_the_family_needs_no_vision_call():
    """Parsed, not grepped — the module explains the landmark jitter it exists for."""
    import ast

    tree = ast.parse(inspect.getsource(r))
    called = {(n.func.attr if isinstance(n.func, ast.Attribute)
               else getattr(n.func, "id", ""))
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not any(isinstance(n, ast.Await) for n in ast.walk(tree))
    for name in called:
        for forbidden in ("extract_geometry", "gemini", "generate", "vision"):
            assert forbidden not in name.lower(), f"{name} looks like {forbidden}"
    imported = {(n.module or "").split(".")[-1] for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)}
    assert "hybrid_landmarks" not in imported and "gemini_image" not in imported


# ------------------------------------------- B/C: determinism and ROI ordering

def test_b_the_same_inputs_resolve_identically_five_times():
    runs = [_resolve([19.7, 20.1, 20.4, 20.6]) for _ in range(5)]
    assert len({x.status for x in runs}) == 1
    assert len({x.selected_period_px for x in runs}) == 1
    assert len({x.period_cluster for x in runs}) == 1
    assert len({tuple(rr.roi_id for rr in x.roi_results) for x in runs}) == 1


def test_c_shuffling_the_roi_order_does_not_change_the_answer():
    fam = _family(4)
    readings = dict(zip([s.roi_id for s in fam], [19.7, 20.1, 20.4, 20.6]))
    baseline = r.resolve(fam, lambda s: _Model(readings[s.roi_id]))
    rng = random.Random(7)
    for _ in range(5):
        shuffled = fam[:]
        rng.shuffle(shuffled)
        got = r.resolve(shuffled, lambda s: _Model(readings[s.roi_id]))
        assert got.selected_period_px == baseline.selected_period_px
        assert got.status == baseline.status
        assert sorted(got.period_cluster) == sorted(baseline.period_cluster)


def test_c_the_answer_is_not_first_success_wins():
    fam = _family(3)
    forward = r.resolve(fam, lambda s: _Model({fam[0].roi_id: 19.7, fam[1].roi_id: 20.1,
                                               fam[2].roi_id: 20.6}[s.roi_id]))
    assert forward.selected_period_px != 19.7, "a median, not the first reading"
    assert forward.selected_period_px == 20.1


# ------------------------------------------------------- D: compatible cluster

def test_d_compatible_readings_form_one_reliable_cluster():
    got = _resolve([19.7, 20.1, 20.4])
    assert got.status == r.STATUS_RELIABLE
    assert got.uncertainty_reason is None
    assert len(got.period_cluster) == 3
    assert 19.7 <= got.selected_period_px <= 20.4
    assert got.consensus_method == "median_of_compatible_scans"


def test_d_the_observed_scan_spread_from_the_real_photo_is_one_cluster():
    """19.555 / 19.669 / 20.127 / 20.649 were the successful scans on one source."""
    got = _resolve([19.555, 19.669, 20.127, 20.649])
    assert got.status == r.STATUS_RELIABLE
    assert len(got.period_cluster) == 4


def test_d_failures_mixed_with_agreeing_successes_still_resolve():
    got = _resolve([None, 19.7, None, 20.1])
    assert got.status == r.STATUS_RELIABLE
    assert got.successful_scan_count == 2
    assert got.attempted_roi_count == 4
    assert [x.failure_reason for x in got.roi_results if not x.success] == \
        ["stripe_model_low_confidence"] * 2


# --------------------------------------------------- E/F: uncertainty, no guess

def test_e_no_successful_scan_is_uncertain_not_an_exception():
    got = _resolve([None, None, None])
    assert got.status == r.STATUS_UNCERTAIN
    assert got.uncertainty_reason == r.REASON_NO_SUCCESSFUL_SCAN
    assert got.selected_period_px is None


def test_e_a_single_reading_is_not_a_consensus():
    got = _resolve([None, 20.649, None])
    assert got.status == r.STATUS_UNCERTAIN
    assert got.uncertainty_reason == r.REASON_SINGLE_ROI_NOT_A_CONSENSUS
    assert got.selected_period_px is None
    assert got.period_cluster == (20.649,), "the reading is still reported"


def test_f_incompatible_readings_are_uncertain_rather_than_arbitrated():
    got = _resolve([15.0, 20.6, 30.0, 45.0])
    assert got.status == r.STATUS_UNCERTAIN
    assert got.uncertainty_reason == r.REASON_CONFLICTING_CLUSTERS
    assert got.selected_period_px is None


def test_f_a_harmonic_outlier_cannot_capture_a_stable_cluster():
    """One 2x reading among agreeing ones must not become the answer."""
    got = _resolve([19.7, 20.1, 20.4, 40.4])
    assert got.status == r.STATUS_UNCERTAIN
    assert got.selected_period_px is None, "no silent majority rule"


def test_e_uncertainty_never_raises():
    for periods in ([None], [None, None], [15.0, 45.0], []):
        fam = _family(max(1, len(periods)))[:len(periods)]
        got = r.resolve(fam, lambda s: _Failure())
        assert got.status in (r.STATUS_RELIABLE, r.STATUS_UNCERTAIN)


# ------------------------------------------------------- G: no new thresholds

def test_g_the_agreement_tolerance_is_the_scan_s_own():
    assert r.PATCH_PERIOD_AGREEMENT_TOL is sm.PATCH_PERIOD_AGREEMENT_TOL
    assert sm.PATCH_PERIOD_AGREEMENT_TOL == 0.15
    src = inspect.getsource(r)
    assert "0.15" not in src, "the tolerance must be imported, not restated"


def test_g_the_production_support_count_is_left_tbd():
    assert r.MINIMUM_SUPPORTING_ROIS_FOR_PRODUCTION is None
    d = r.MultiRoiResolution(r.STATUS_RELIABLE, 20.0, "vertical", 0.8, "m", 2, 2).as_dict()
    assert d["minimumSupportingRoisForProduction"] is None
    # what THIS module requires is stated, not hidden behind the TBD above
    assert d["shadowConsensusMinRois"] == 2
    assert "not a production readiness claim" in d["statusMeaning"]


def test_g_shadow_reliable_needs_two_readings_not_one():
    assert r.SHADOW_CONSENSUS_MIN_ROIS == 2
    one = _resolve([None, 20.6, None])
    assert one.status == r.STATUS_UNCERTAIN
    assert one.uncertainty_reason == r.REASON_SINGLE_ROI_NOT_A_CONSENSUS
    two = _resolve([20.6, 20.7, None])
    assert two.status == r.STATUS_RELIABLE


def test_g_the_family_is_not_claimed_to_be_vision_free():
    """It samples around a Vision-derived boundary; it does not remove it."""
    for spec in r.build_roi_family((750, 1080, 2205, 2680), width=3000, height=4000):
        d = spec.as_dict()
        assert d["addsVisionDependency"] is False
        assert d["upstreamVisionDependent"] is True
        assert d["candidateDependent"] is False


def test_g_no_period_specific_heuristic_exists():
    """Code only — the comments quote measured periods like "20.649 / 20.267"."""
    import ast

    tree = ast.parse(inspect.getsource(r))
    for node in ast.walk(tree):          # drop docstrings
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    code = ast.unparse(tree)
    for banned in ("== 15", "== 30", "== 45", "harmonic"):
        assert banned not in code, banned
    numbers = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))}
    for banned in (15, 30, 45, 20.649):
        assert banned not in numbers, f"{banned} must not appear as a literal"


# ------------------------------------------- H/I/J: guided semantics + provenance

class _Profile:
    """Only what the guided scorer reads from a model: the folded period profile.

    Built directly rather than extracted, so the candidate-provenance assertions
    below actually run instead of skipping on a synthetic image the extractor
    declines.
    """

    def __init__(self, period=20):
        import numpy as np
        k = period
        prof = np.zeros((k, 3), np.float32)
        prof[:, 0] = [80.0 if i < k // 2 else 55.0 for i in range(k)]
        prof[:, 1] = [4.0 if i < k // 2 else -6.0 for i in range(k)]
        prof[:, 2] = [-3.0 if i < k // 2 else 9.0 for i in range(k)]
        self.period_profile_lab = prof


def _guided_probe(collect=None, period=20):
    import numpy as np
    rng = np.random.default_rng(0)
    h = w = 640
    img = np.zeros((h, w, 3), np.uint8)
    for x in range(w):
        img[:, x] = (205, 200, 190) if (x % period) < period // 2 else (110, 125, 165)
    img = np.clip(img + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    return sm.find_period_guided(img, _Profile(period), collect=collect), None


def test_h_collecting_diagnostics_does_not_change_the_winner():
    without, _ = _guided_probe(collect=None)
    collected: list = []
    with_, _ = _guided_probe(collect=collected)
    assert without == with_, "the selection must be untouched by observing it"


def test_i_every_scored_candidate_is_reported_with_its_provenance():
    collected: list = []
    _guided_probe(collect=collected)
    if not collected:
        pytest.skip("synthetic image produced no guided candidates")
    for c in collected:
        assert set(c) >= {"axis", "periodPx", "score", "autocorrelationPeak",
                          "basePeakPx", "multiplier"}
        assert isinstance(c["score"], float)
        if c["multiplier"] is not None:
            assert c["multiplier"] in (1, 2, 3)
            assert c["periodPx"] == pytest.approx(c["basePeakPx"] * c["multiplier"], abs=0.6)


def test_i_the_peak_flag_means_exactly_this_period_is_itself_a_peak():
    """The 15/30/45 question is whether a winner was a peak or only a multiple.

    A clean synthetic signal really does peak at 2x and 3x, so the flag is not
    "was it generated by a multiplier" — it is "is this lag a peak in its own
    right". On the real fabric ac[30] was 0.08 and ac[45] negative, and those
    would be recorded False.
    """
    collected: list = []
    _guided_probe(collect=collected)
    assert collected, "the guided search must have scored candidates"
    for c in collected:
        expected = any(o["multiplier"] == 1 for o in c["origins"])
        assert c["autocorrelationPeak"] is expected, c


def test_i_the_reported_base_peak_is_the_smallest_one_that_explains_it():
    collected: list = []
    _guided_probe(collect=collected)
    for c in collected:
        bases = [o["basePeakPx"] for o in c["origins"]]
        assert c["basePeakPx"] == min(bases), c
        assert c["multiplier"] == max(o["multiplier"] for o in c["origins"]
                                      if o["basePeakPx"] == c["basePeakPx"])


def test_i_a_multiple_of_a_peak_is_recorded_with_that_peak():
    collected: list = []
    _guided_probe(collect=collected, period=20)
    multiples = [c for c in collected if c["multiplier"] in (2, 3)]
    assert multiples, "the x2/x3 expansion must appear in the record"
    for c in multiples:
        assert c["periodPx"] == pytest.approx(c["basePeakPx"] * c["multiplier"], abs=0.6)


def test_i_the_15_30_45_family_shape_is_representable():
    """A base peak at 15 must be recorded as 15x1, 30x2, 45x3 — not three peaks."""
    src = inspect.getsource(sm.find_period_guided)
    assert 'origins.setdefault(round(float(c) * m, 1), []).append(' in src
    assert '"basePeakPx": float(c), "multiplier": m' in src
    assert 'for m in (1, 2, 3)' in src, "candidate generation unchanged"


def test_j_the_guided_search_still_runs_exactly_once():
    """Diagnostics come out of the one search, not a second one."""
    src = inspect.getsource(sm.find_period_guided)
    assert src.count("np.correlate(p, p, mode=\"full\")") == 1
    assert src.count("collect.append(") == 1
    # the collector sits inside the existing scoring loop, after the score exists
    body = src.split("score = float(corr.max()", 1)[1]
    assert "collect.append(" in body.split("if best is None", 1)[0]


def test_h_the_scoring_and_selection_expressions_are_unchanged():
    src = inspect.getsource(sm.find_period_guided)
    assert "score = float(corr.max() / (na * nb))" in src
    assert "if best is None or score > best[2]:" in src
    assert "if best is None or best[2] < 0.5:" in src


# --------------------------------------- K/L: artifact extension and comparison

def test_k_real_candidates_replace_the_missing_reason():
    cands = [{"axis": "vertical", "periodPx": 15.0, "score": 0.44,
              "autocorrelationPeak": True, "basePeakPx": 15.0, "multiplier": 1},
             {"axis": "vertical", "periodPx": 30.0, "score": 0.53,
              "autocorrelationPeak": False, "basePeakPx": 15.0, "multiplier": 2}]
    p = qa.provenance_payload(job_id="j", candidate="A", attempt=1,
                              guided={"attempted": True, "candidates": cands})
    assert p["guided"]["candidates"] == cands
    assert p["guided"].get("candidatesMissingReason") is None


def test_k_an_unrun_search_is_distinguished_from_an_empty_result():
    p = qa.provenance_payload(job_id="j", candidate="A", attempt=1,
                              guided={"attempted": False, "candidates": []})
    assert p["guided"]["candidatesMissingReason"] == qa.GUIDED_NOT_ATTEMPTED


def test_l_the_shadow_reading_is_recorded_beside_legacy_never_over_it():
    res = _resolve([19.7, 20.1, 20.4])
    p = qa.provenance_payload(
        job_id="j", candidate="A", attempt=1,
        source_period_px=30.0, source_period_source="guided",
        shadow_multi_roi=res)
    assert p["sourcePeriodPx"] == 30.0, "legacy value untouched"
    assert p["legacyVsShadow"]["authority"] == "legacy"
    assert p["shadowMultiRoi"]["status"] == r.STATUS_RELIABLE
    cmp = p["legacyVsShadow"]["comparison"]
    assert cmp["bothAvailable"] is True
    assert cmp["relativeDifference"] > 0.15
    assert cmp["agreementWithinExistingTolerance"] is False
    assert cmp["tolerance"] == sm.PATCH_PERIOD_AGREEMENT_TOL


def test_l_agreement_is_reported_when_the_two_readings_match():
    res = _resolve([19.7, 20.1, 20.4])
    p = qa.provenance_payload(job_id="j", candidate="A", attempt=1,
                              source_period_px=20.649, source_period_source="front_scan",
                              shadow_multi_roi=res)
    assert p["legacyVsShadow"]["comparison"]["agreementWithinExistingTolerance"] is True


def test_l_a_missing_shadow_reading_is_null_not_invented():
    p = qa.provenance_payload(job_id="j", candidate="A", attempt=1,
                              source_period_px=20.649, source_period_source="front_scan")
    assert p["shadowMultiRoi"] is None
    assert p["legacyVsShadow"]["comparison"]["bothAvailable"] is False
    assert p["legacyVsShadow"]["shadow"]["periodPx"] is None
