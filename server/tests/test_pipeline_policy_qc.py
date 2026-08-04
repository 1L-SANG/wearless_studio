"""Phase 5-7: deterministic garment policy and quantitative QC contracts."""

import asyncio
import json

import cv2
import numpy as np

from app.agents.gemini_image import GeminiImageResult, InlineImage
from app.services.color_fidelity_qc import compare_color
from app.services.garment_profile import build_garment_profile, select_pipeline_policy
from app.services.pattern_fidelity_qc import compare_pattern
from app.services.qc_decision import decide
from app.services.qc_result import assemble_qc_result
from app.services.garment_roi import foreground_mask, mannequin_difference_mask
from app.services.quantitative_fidelity_qc import run as run_quantitative_qc
from app.workers.mannequin_job import (
    _apply_structured_outcome,
    _generation_pipeline_policy,
    _policy_image_size,
    _select_policy_candidate,
    _save_cut,
)
from conftest import make_settings


def _vertical_stripes(*, period=20, shift=0, stripe_width=None, h=240, w=180):
    image = np.full((h, w, 3), (220, 220, 220), np.uint8)
    stripe_width = stripe_width or period // 3
    for x in range(shift, w, period):
        image[:, max(0, x):min(w, x + stripe_width)] = (40, 70, 150)
    return image


def test_plain_tshirt_uses_fast_lane_and_common_checks_only():
    profile = build_garment_profile({"category": "tshirt", "patternSpec": {"type": "solid"}})
    policy = select_pipeline_policy(profile)
    assert policy["lane"] == "FAST"
    assert policy["modules"] == ["composition", "image_quality", "color_fidelity", "style_consistency"]
    assert policy["candidateCount"] == 1


def test_logo_and_check_force_guarded_protected_and_pattern_checks():
    profile = build_garment_profile({
        "category": "shirt", "patternSpec": {"type": "check"},
        "protectedAssets": {"logos": [{"assetId": "logo-1"}]},
    })
    policy = select_pipeline_policy(profile)
    assert policy["lane"] == "GUARDED"
    assert {"pattern_fidelity", "protected_detail"} <= set(policy["modules"])
    assert policy["candidateCount"] == 2


def test_lace_is_manual_and_never_auto_approves():
    profile = build_garment_profile({"category": "blouse", "garmentSpec": {"materialTraits": ["lace"]}})
    policy = select_pipeline_policy(profile)
    assert policy["lane"] == "MANUAL"
    assert policy["autoApproval"] is False
    assert {"advanced_structure", "material"} <= set(policy["modules"])

    decision = decide([], policy_version="qc-v1", auto_approval=policy["autoApproval"])
    assert decision["overallDecision"] == "review"
    assert decision["warnings"] == ["manual_review_required"]


def test_db_shaped_truth_reads_nested_category_and_protected_details():
    profile = build_garment_profile({
        "garmentSpec": {"category": "jacket"},
        "patternSpec": {"type": "solid"},
        "protectedDetails": {"logo": True, "textPrint": False, "embroidery": False},
    })
    assert profile["category"] == "jacket"
    assert profile["protectedDetailCount"] == 1
    assert profile["riskScore"] == 40


def test_generation_policy_is_flag_and_truth_gated():
    truth = {"garmentSpec": {"category": "shirt"}, "patternSpec": {"type": "check"}}
    assert _generation_pipeline_policy(
        make_settings(mannequin_structured_qc="off"), truth) is None
    assert _generation_pipeline_policy(
        make_settings(mannequin_structured_qc="shadow"), None) is None
    policy = _generation_pipeline_policy(
        make_settings(mannequin_structured_qc="shadow"), truth)
    assert policy["lane"] == "GUARDED"
    assert policy["candidateCount"] == 2
    assert policy["resolution"] == "2K"


def test_policy_resolution_applies_but_does_not_downgrade_fine_pattern_override():
    fast = {"resolution": "1K"}
    guarded = {"resolution": "2K"}
    assert _policy_image_size("2K", fast, fine_pattern=False) == "1K"
    assert _policy_image_size("4K", guarded, fine_pattern=True) == "4K"
    assert _policy_image_size("2K", None, fine_pattern=False) == "2K"
    assert _policy_image_size("1K", guarded, fine_pattern=False) == "1K"


def test_qa_image_size_cap_wins_over_policy_and_fine_pattern_upgrades():
    guarded = {"resolution": "2K"}
    assert _policy_image_size(
        "4K", guarded, fine_pattern=True, cap="1K") == "1K"
    assert _policy_image_size(
        "4K", None, fine_pattern=True, cap="2K") == "2K"
    assert _policy_image_size(
        "1K", guarded, fine_pattern=False, cap="off") == "1K"


def test_image_size_cap_is_loaded_fail_safe(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE_CAP", "1k")
    assert load_settings().mannequin_image_size_cap == "1K"
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE_CAP", "unexpected")
    assert load_settings().mannequin_image_size_cap == "off"


def test_policy_candidate_selection_prefers_decision_then_deterministic_score():
    settings = make_settings()
    review = {"candidate": "A", "qc_scores": {
        "structuredQC": {"overallDecision": "review"}, "outcome": "needs_review",
        "product_fidelity": 95, "physical_naturalness": 95, "image_quality": 95,
    }}
    passed_low = {"candidate": "B", "qc_scores": {
        "structuredQC": {"overallDecision": "pass"}, "outcome": "auto_pass",
        "product_fidelity": 75, "physical_naturalness": 75, "image_quality": 75,
    }}
    assert _select_policy_candidate(settings, [review, passed_low]) is passed_low

    passed_high = {"candidate": "C", "qc_scores": {
        "structuredQC": {"overallDecision": "pass"}, "outcome": "auto_pass",
        "product_fidelity": 90, "physical_naturalness": 90, "image_quality": 90,
    }}
    assert _select_policy_candidate(settings, [passed_low, passed_high]) is passed_high


def test_decision_engine_is_policy_only_and_qc_unavailable_reviews():
    result = decide([
        {"check": "composition", "status": "pass", "score": 0.98},
        {"check": "color_fidelity", "status": "unavailable", "score": None},
    ], policy_version="qc-v1")
    assert result["overallDecision"] == "review"
    assert "qc_unavailable:color_fidelity" in result["warnings"]


def test_critical_error_rejects_even_when_other_scores_are_high():
    result = decide([
        {"check": "composition", "status": "pass", "score": 0.99},
        {"check": "garment_structure", "status": "fail", "score": 0.95,
         "criticalErrors": ["logo_changed"]},
    ], policy_version="qc-v1")
    assert result["overallDecision"] == "reject"
    assert result["criticalErrors"] == ["logo_changed"]


def test_color_qc_uses_mask_and_separates_shadow_from_midtones():
    src = np.full((100, 100, 3), (80, 120, 180), np.uint8)
    out = src.copy()
    out[:35] = (45, 70, 105)  # 같은 계열의 그림자 영역
    mask = np.ones((100, 100), np.uint8) * 255
    result = compare_color(src, out, source_mask=mask, output_mask=mask)
    assert result["check"] == "color_fidelity"
    assert result["metrics"]["midtoneDeltaE00"] < result["metrics"]["shadowDeltaE00"]
    assert 0 <= result["score"] <= 1


def test_low_mask_confidence_never_passes_color_qc():
    image = np.full((40, 40, 3), 120, np.uint8)
    tiny = np.zeros((40, 40), np.uint8)
    tiny[0:2, 0:2] = 255
    result = compare_color(image, image, source_mask=tiny, output_mask=tiny)
    assert result["status"] == "unavailable"


def test_pattern_qc_detects_period_change_and_reports_overlay():
    src = _vertical_stripes(period=20)
    out = _vertical_stripes(period=30)
    mask = np.ones(src.shape[:2], np.uint8) * 255
    result = compare_pattern(src, out, pattern_type="stripe", source_mask=mask, output_mask=mask)
    assert result["status"] == "fail"
    assert result["metrics"]["periodRelativeError"] > 0.20
    assert result["metrics"]["sourceHough"]["dominant"] == "vertical"
    assert result["metrics"]["sourceFftPeriodPx"] is not None
    assert result["debugOverlayPng"] is not None
    assert cv2.imdecode(np.frombuffer(result["debugOverlayPng"], np.uint8), cv2.IMREAD_COLOR) is not None


def test_pattern_qc_detects_stripe_thickness_change_at_same_period():
    src = _vertical_stripes(period=24, stripe_width=4)
    out = _vertical_stripes(period=24, stripe_width=12)
    mask = np.ones(src.shape[:2], np.uint8) * 255
    result = compare_pattern(src, out, pattern_type="stripe", source_mask=mask, output_mask=mask)
    assert result["status"] == "fail"
    assert result["metrics"]["stripeWidthMatch"] is False
    assert result["metrics"]["stripeWidthRelativeError"] > 0.35


def test_solid_product_skips_pattern_without_cv_cost():
    image = _vertical_stripes()
    result = compare_pattern(image, image, pattern_type="solid")
    assert result["status"] == "not_applicable"
    assert result["metrics"] == {}


def test_qc_result_keeps_compat_scores_and_structured_checks():
    checks = [{"check": "composition", "status": "pass", "score": 0.9}]
    decision = decide(checks, policy_version="qc-v1")
    result = assemble_qc_result(
        generation_output_id="out-1", truth_package_id="truth-1",
        checks=checks, decision=decision, policy_version="qc-v1",
    )
    assert result["overallDecision"] == "pass"
    assert result["scores"]["composition"] == 90
    assert result["checks"] == checks
    assert result["truthPackageId"] == "truth-1"


def test_structured_review_cannot_be_overwritten_by_legacy_auto_pass():
    scores = {"outcome": "auto_pass", "structuredQC": {"overallDecision": "review"}}
    _apply_structured_outcome(scores)
    assert scores["outcome"] == "needs_review"


def _png(image):
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_roi_extractors_fail_closed_and_find_changed_garment():
    source = np.full((160, 120, 3), 245, np.uint8)
    source[30:140, 25:95] = (60, 90, 170)
    source_mask, source_conf = foreground_mask(source)
    assert source_conf > 0.25
    assert source_mask[80, 60] == 255

    base = np.full((160, 120, 3), 230, np.uint8)
    output = base.copy()
    output[35:135, 30:90] = (60, 90, 170)
    output_mask, output_conf = mannequin_difference_mask(base, output)
    assert output_conf > 0.25
    assert output_mask[80, 60] == 255


def test_quantitative_runner_returns_json_safe_checks():
    source = np.full((160, 120, 3), 245, np.uint8)
    source[30:140, 25:95] = (60, 90, 170)
    base = np.full((160, 120, 3), 230, np.uint8)
    output = base.copy()
    output[35:135, 30:90] = (60, 90, 170)
    checks = run_quantitative_qc(
        source_bytes=_png(source), base_bytes=_png(base), output_bytes=_png(output),
        pattern_type="solid")
    assert [c["check"] for c in checks] == ["color_fidelity", "pattern_fidelity"]
    assert checks[1]["status"] == "not_applicable"
    import json
    json.dumps(checks)


def test_quantitative_runner_can_return_overlay_bytes_only_for_storage_boundary():
    source = np.full((180, 140, 3), 245, np.uint8)
    output = np.full_like(source, 230)
    base = output.copy()
    source_panel = _vertical_stripes(period=20, h=120, w=80)
    source_panel[np.all(source_panel == 220, axis=2)] = (160, 160, 160)
    output_panel = _vertical_stripes(period=30, h=110, w=70)
    output_panel[np.all(output_panel == 220, axis=2)] = (160, 160, 160)
    source[30:150, 30:110] = source_panel
    output[35:145, 35:105] = output_panel
    checks = run_quantitative_qc(
        source_bytes=_png(source), base_bytes=_png(base), output_bytes=_png(output),
        pattern_type="stripe", include_debug_bytes=True)
    pattern = next(check for check in checks if check["check"] == "pattern_fidelity")
    assert isinstance(pattern["_debugOverlayPng"], bytes)
    assert pattern["debugOverlaySha256"]


def test_save_cut_persists_qc_overlay_as_owned_debug_asset():
    source = np.full((180, 140, 3), 245, np.uint8)
    output = np.full_like(source, 230)
    base = output.copy()
    source_panel = _vertical_stripes(period=20, h=120, w=80)
    source_panel[np.all(source_panel == 220, axis=2)] = (160, 160, 160)
    output_panel = _vertical_stripes(period=30, h=110, w=70)
    output_panel[np.all(output_panel == 220, axis=2)] = (160, 160, 160)
    source[30:150, 30:110] = source_panel
    output[35:145, 35:105] = output_panel

    class R2:
        def __init__(self):
            self.puts = []

        def put_bytes(self, key, data, mime, cache=None):
            self.puts.append((key, data, mime, cache))

    r2 = R2()
    cut = asyncio.run(_save_cut(
        s=make_settings(mannequin_structured_qc="shadow", r2_bucket="bucket"),
        r2=r2, user_id="u1", project_id="p1", job_id="j1", candidate="A",
        base_fit="regular",
        res=GeminiImageResult(_png(output), "image/png", 1, None),
        qc_scores={"product_fidelity": 90, "physical_naturalness": 90,
                   "image_quality": 90, "critical_errors": []},
        product_truth={"id": "truth-1", "garmentSpec": {"category": "shirt"},
                       "patternSpec": {"type": "stripe"}, "protectedDetails": {}},
        source_image=InlineImage("image/png", _png(source)),
        base_image=InlineImage("image/png", _png(base)),
    ))

    assert len(r2.puts) == 2
    assert r2.puts[1][2] == "image/png"
    assert len(cut["qc_debug_assets"]) == 1
    debug = cut["qc_scores"]["structuredQC"]["debugAssets"][0]
    assert debug["assetId"] == cut["qc_debug_assets"][0]["asset_id"]
    assert debug["src"] == f"/v1/assets/{debug['assetId']}/file"
    json.dumps(cut["qc_scores"])
