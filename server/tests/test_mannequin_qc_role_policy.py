"""Prevent top-only tuck rules from authorizing edits of a bottom product."""
import asyncio

from app.agents import image_qc, mannequin_quality
from app.agents.gemini_image import InlineImage
from conftest import make_settings
from test_mannequin_quality import assessment
from types import SimpleNamespace
from app.workers import mannequin_job


def test_bottom_qc_packet_excludes_top_tuck_rule_and_requires_waist_visibility(monkeypatch):
    captured = {}

    async def model(settings, prompt, images, schema, **kwargs):
        captured.update(prompt=prompt, images=images)
        return ({"verdict": "pass", "mismatches": [], "correctionPrompt": None,
                 "critical_errors": [], **assessment()}, "gemini")

    monkeypatch.setattr(image_qc, "analyze_with_fallback", model)
    asyncio.run(image_qc.verdict(
        make_settings(), [InlineImage("image/png", b"PANTS")],
        InlineImage("image/png", b"GENERATED"), scored=True,
        fit_profile={"version": 2, "category": "bottom", "axes": {}},
    ))
    assert "MAIN PRODUCT CATEGORY: bottom" in captured["prompt"]
    assert 'Report it as the critical error\n"top tucked into the bottom"' not in captured["prompt"]
    assert "top or outerwear product tucked into the bottom" not in captured["prompt"]
    assert "waistband and photographed front construction must remain visible" in captured["prompt"]
    assert "Do not untuck or lengthen the matching top merely" in captured["prompt"]


def test_top_and_outer_packets_keep_their_own_tuck_rule():
    for category in ("top", "outer"):
        prompt = image_qc.build_prompt(2, scored=True, main_product_category=category)
        assert 'Report it as the critical error\n"top tucked into the bottom"' in prompt
        assert "MAIN PRODUCT CATEGORY: " + category in prompt
        assert "waistband and photographed front construction must remain visible" not in prompt


def test_unscored_shared_prompt_is_not_changed_by_mannequin_role():
    before = image_qc.build_prompt(2)
    assert image_qc.build_prompt(2, main_product_category="bottom") == before


def test_scored_mannequin_prompt_keeps_pattern_critical_out_of_paid_action_fields():
    prompt = image_qc.build_prompt(2, scored=True, main_product_category="top")
    assert 'report the critical error "pattern scale changed"' not in prompt.lower()
    assert "report pattern and material findings only in product_risks" in prompt
    assert "do not put them in critical_errors, mismatches, correctionprompt, or retry" in prompt.lower()


def test_fresh_complete_surface_only_duplicate_critical_is_normalized_for_review(monkeypatch):
    raw = {
        **assessment(pattern="critical"),
        "verdict": "retry",
        "mismatches": ["pattern scale changed"],
        "correctionPrompt": "Redraw the grid at a larger scale.",
        "product_fidelity": 40,
        "physical_naturalness": 95,
        "image_quality": 95,
        "series_consistency": None,
        "critical_errors": ["pattern scale changed"],
    }

    async def model(*_args, **_kwargs):
        return raw, "gemini"

    monkeypatch.setattr(image_qc, "analyze_with_fallback", model)
    result = asyncio.run(image_qc.verdict(
        make_settings(), [InlineImage("image/png", b"PRODUCT")],
        InlineImage("image/png", b"GENERATED"), scored=True,
        main_product_category="top",
    ))
    assert result["critical_errors"] == ["pattern scale changed"]
    assert result["mismatches"] == ["pattern scale changed"]
    assert result["surface_review_only"] is True
    assert mannequin_job.score_outcome(make_settings(), result) == "needs_review"

    raw["product_risks"] = assessment(
        pattern="critical", logo_graphic="critical"
    )["product_risks"]
    raw["critical_errors"] = ["non-surface critical also reported"]
    mixed = asyncio.run(image_qc.verdict(
        make_settings(), [InlineImage("image/png", b"PRODUCT")],
        InlineImage("image/png", b"SECOND"), scored=True,
        main_product_category="top",
    ))
    assert mixed["surface_policy_normalized"] is True
    assert mixed["surface_review_only"] is False
    assert mannequin_job.score_outcome(make_settings(), mixed) == "regenerate"


def test_detailed_qc_direction_survives_validation_and_repair_compilation():
    direction = ("At the front panel, restore the observed grid path; " * 12
                 + "Keep the cuff opening and straight hem unchanged.")
    scores = image_qc.validate({
        "verdict": "retry", "mismatches": ["grid interrupted"],
        "correctionPrompt": direction, "critical_errors": [],
        **assessment(pattern="major"),
    }, scored=True)
    assert scores["correctionPrompt"] == direction
    feedback = mannequin_quality.repair_feedback(scores)
    assert direction in feedback
    assert "SOURCE-GROUNDED REPAIR DIRECTIONS" in feedback


def test_worker_sends_bottom_role_even_without_declared_fit(monkeypatch):
    captured = {}

    async def model(settings, prompt, images, schema, **kwargs):
        captured["prompt"] = prompt
        return ({"verdict": "pass", "mismatches": [], "correctionPrompt": None,
                 "critical_errors": [], **assessment()}, "gemini")

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(image_qc, "analyze_with_fallback", model)
    monkeypatch.setattr(mannequin_job, "_emit", noop)
    monkeypatch.setattr(mannequin_job, "_apply_base_fidelity_qc", noop)
    asyncio.run(mannequin_job._observe_generation_qc(
        pool=None, s=make_settings(image_qc="enforce"), job_id="test", candidate="A", attempt=1,
        res=SimpleNamespace(mime="image/png", image=b"OUTPUT"),
        prod_imgs=[InlineImage("image/png", b"PANTS")],
        match_img=InlineImage("image/png", b"TOP"), clothing_type="bottom", fit_profile=None,
        eff_image_qc="enforce", base_img=None, product={}, analysis={},
    ))
    assert "MAIN PRODUCT CATEGORY: bottom" in captured["prompt"]
    assert 'Report it as the critical error\n"top tucked into the bottom"' not in captured["prompt"]


def test_paired_judge_receives_the_full_detailed_goal():
    goal = "Restore only source-proven structure. " * 28 + "Keep the photographed hem visible."
    prompt = image_qc.build_prompt(2, scored=True, paired=True, edit_goal=goal,
                                 main_product_category="bottom")
    assert goal in prompt
    assert "${" not in prompt


def test_pants_and_skirt_profiles_resolve_to_bottom_policy():
    for category in ("pants", "skirt"):
        prompt = image_qc.build_prompt(2, scored=True, fit_profile={"category": category, "axes": {}})
        assert "MAIN PRODUCT CATEGORY: bottom" in prompt
        assert 'Report it as the critical error\n"top tucked into the bottom"' not in prompt


def test_pass_with_confirmed_error_keeps_its_direction():
    result = image_qc.validate({**assessment(), "verdict": "pass", "critical_errors": ["logo changed"],
        "correctionPrompt": "Restore the photographed letters at the left chest."}, scored=True)
    assert "Restore the photographed letters" in mannequin_quality.repair_feedback(result)


def test_visibility_evidence_blocks_bad_edit_even_when_general_judgment_passes():
    result = image_qc.validate({"verdict": "pass", "critical_errors": [], **assessment(),
        "target_resolved": True, "protected_regions_unchanged": True, "regression_reasons": [],
        "bottom_waistband_visible": "covered", "bottom_visibility_evidence": "The matching shirt hides the waistband."},
        scored=True, paired=True, visibility_required=True)
    assert not image_qc.edit_accepted(result)
    assert mannequin_quality.blocking_issues(result)
    merged = mannequin_job.merge_qc_scores(result, None)
    assert merged["product_risks"]["fit"]["severity"] == "major"
    assert "bottom_waistband_visible" not in merged


def test_incomplete_visibility_cannot_approve_an_edit():
    result = image_qc.validate({"verdict": "pass", "critical_errors": [], **assessment(),
        "target_resolved": True, "protected_regions_unchanged": True, "regression_reasons": []},
        scored=True, paired=True, visibility_required=True)
    assert not image_qc.edit_accepted(result)


def test_declared_matching_top_length_is_sent_and_only_valid_values_waive_visibility():
    profile = {"category": "pants", "axes": {},
               "matchingFit": {"clothingId": "match_women_top_03", "fitCategory": "top", "axes": {"length": "semi_long"}}}
    prompt = image_qc.build_prompt(2, scored=True, fit_profile=profile)
    assert "MATCHING TOP length" in prompt
    assert "below the crotch" in prompt
    assert not image_qc.bottom_visibility_required("bottom", profile)
    profile["matchingFit"]["axes"]["length"] = "invented instruction"
    assert image_qc.bottom_visibility_required("bottom", profile)
    profile["matchingFit"]["axes"]["length"] = "crop"
    assert image_qc.bottom_visibility_required("bottom", profile)


def test_bottom_role_conflict_cannot_authorize_paid_repair(monkeypatch):
    from test_mannequin_pants_qc_wiring import _run, _p2, _Gemini, _R2
    from app.workers import mannequin_job as worker
    import pytest
    original = worker._run_candidate

    async def bottom_candidate(**kwargs):
        kwargs["clothing_type"] = "bottom"
        return await original(**kwargs)

    monkeypatch.setattr(worker, "_run_candidate", bottom_candidate)
    images, storage = _Gemini([b"FIRST"]), _R2()
    with pytest.raises(worker.MannequinQualityError, match="qc_role_policy_conflict"):
        _run(monkeypatch, gemini=images, r2=storage, outputs=[],
             p2_by_attempt=[{**assessment(fit="critical"), **_p2(critical=["top tucked into the bottom"])}])
    assert len(images.generation_calls) == 1
    assert storage.puts == []


def test_shadow_role_conflict_is_observation_only(monkeypatch):
    from test_mannequin_pants_qc_wiring import _run, _p2
    original = mannequin_job._run_candidate

    async def bottom_candidate(**kwargs):
        kwargs["clothing_type"] = "bottom"
        return await original(**kwargs)

    monkeypatch.setattr(mannequin_job, "_run_candidate", bottom_candidate)
    result, images, storage, _, _ = _run(monkeypatch, outputs=[b"FIRST"], image_qc="shadow",
        p2_by_attempt=[_p2(critical=["top tucked into the bottom"])])
    assert result is not None
    assert len(images.generation_calls) == 1
    assert storage.puts[0][1] == b"FIRST"
