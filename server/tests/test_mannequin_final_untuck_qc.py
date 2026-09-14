"""The final saved image and its scores must refer to the same candidate.

The worker, score validation, gate/rollback decisions and save path are real.
Provider, vision and storage responses are controlled, and unrelated pixel
checks are isolated. These are dataflow tests, not visual model evaluations.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents import image_qc
from app.agents.gemini_image import GeminiError, InlineImage
from app.agents.product_reference import ProductReference
from app.services.qc import QcResult
from app.workers import mannequin_job as job
from conftest import make_settings


PROFILE = {"category": "top", "gender": "women", "version": 2,
           "source": "seller", "axes": {"length": "basic"}}
FRONT = InlineImage("image/png", b"seller-front")
DETAIL = InlineImage("image/png", b"seller-detail")
MATCHING = InlineImage("image/png", b"matching-bottom")


def scored(value, *, critical=(), matching_critical=(), target=True, protected=True,
           regressions=()):
    return image_qc.validate({
        "verdict": "pass", "mismatches": [], "correctionPrompt": None,
        "product_fidelity": value, "physical_naturalness": value,
        "image_quality": value, "series_consistency": None,
        "critical_errors": list(critical), "matching_fidelity": 99,
        "matching_critical_errors": list(matching_critical),
        "target_resolved": target, "protected_regions_unchanged": protected,
        "regression_reasons": list(regressions),
    }, scored=True, matching=True, paired=True)


def series(value):
    return {"consistency": value, "inconsistencies": []}


def run_worker(monkeypatch, *, p2, generated=(b"before",), after=b"after",
               series_results=None, mode="enforce", has_match=True,
               pants_mode="off", ref_images=(), bust_pass="off", captures=None,
               settings_overrides=None, candidate_kwargs=None, analysis_override=None):
    pending = list(generated)
    captures = captures or SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    captures.requests = []
    captures.pairs = []
    captures.pair_options = []

    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            bust = "confirmed under-rendering" in prompt
            untuck = not bust and "unbroken visible line" in prompt
            captures.image_calls.append("bust" if bust else "untuck" if untuck else "generate")
            captures.prompts.append(prompt)
            captures.requests.append({"images": images, "model": model, "size": size})
            result = after if bust or untuck else pending.pop(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(image=result, mime="image/png")

    class Storage:
        def put_bytes(self, key, data, mime, cache=None):
            captures.puts.append(data)

    async def judge(settings, source_images, candidate, **kwargs):
        assert source_images == [FRONT, DETAIL]
        assert kwargs["scored"] is True
        assert kwargs["fit_profile"] == PROFILE
        expected_match = MATCHING if has_match and pants_mode != "off" else None
        assert kwargs["match_image"] == expected_match
        if kwargs.get("before_image") is not None:
            assert kwargs["edit_goal"]
            captures.pairs.append((kwargs["before_image"].data, candidate.data))
            captures.pair_options.append({
                "source_mirrored": kwargs.get("source_mirrored"),
                "match_is_custom": kwargs.get("match_is_custom"),
            })
        captures.judged.append(candidate.data)
        response = p2[candidate.data]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def judge_series(**kwargs):
        image = kwargs["res"].image
        captures.series.append(image)
        return deepcopy((series_results or {}).get(image))

    async def emit(_pool, _id, _kind, payload):
        captures.events.append(deepcopy(payload))

    monkeypatch.setattr(job.image_qc, "verdict", judge)
    async def untuck_gate(*args, **kwargs):
        return {"verdict": "tucked", "confidence": 0.95}
    async def bust_gate(*args, **kwargs):
        return {"verdict": "insufficient", "confidence": 0.95}
    monkeypatch.setattr(job.mannequin_untuck, "judge_gate", untuck_gate)
    monkeypatch.setattr(job.mannequin_bust, "judge_gate", bust_gate)
    monkeypatch.setattr(job, "_apply_series_qc", judge_series)
    monkeypatch.setattr(job, "_emit", emit)
    monkeypatch.setattr(job.qc, "evaluate_mannequin_qc", lambda _: QcResult("pass", [], {}))
    monkeypatch.setattr(job.qc, "evaluate_canvas_alpha_qc", lambda _: QcResult("pass", [], {}))
    monkeypatch.setattr(job.qc, "compare_pants_region", lambda *a, **k: QcResult("same", [], {}))
    settings = make_settings(**{
        **dict(
        r2_bucket="bucket", image_qc=mode, mannequin_axis_qc="off",
        mannequin_max_attempts=2, mannequin_bust_pass=bust_pass, mannequin_bust_gate="on", mannequin_fabric_pass="off",
        mannequin_untuck_pass="on", mannequin_untuck_gate="on", mannequin_pants_qc=pants_mode,
        qc_edit_regression_margin=10),
        **(settings_overrides or {}),
    })
    app = SimpleNamespace(state=SimpleNamespace(
        settings=settings, pool=object(), r2=Storage(), gemini=Provider()))
    result = asyncio.run(job._run_candidate(
        app=app, job={"id": "j", "user_id": "u", "project_id": "p", "payload": {}},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", b"base"), prod_imgs=[FRONT, DETAIL],
        match_img=MATCHING if has_match else None, product_count=2,
        template="${baseGender} ${clothingType} ${imageManifest}",
        product={}, analysis=analysis_override or {}, clothing_type="top", fit_profile=deepcopy(PROFILE),
        product_refs=[ProductReference("Front", "front", FRONT), ProductReference("Detail", "detail", DETAIL)],
        ref_imgs=ref_images,
        **(candidate_kwargs or {}),
    ))
    return result, captures


def test_saved_final_image_gets_its_own_main_and_series_scores(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(91), b"after": scored(96)},
                             series_results={b"before": series(87), b"after": series(93)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"]["product_fidelity"] == 96
    assert result["qc_scores"]["series_consistency"] == 93
    assert seen.judged == [b"before", b"after"]
    assert seen.series == [b"before", b"after"]
    assert seen.image_calls == ["generate", "untuck"]


@pytest.mark.parametrize("post", [scored(35), scored(95, critical=["invented panel seam"])])
def test_bad_final_edit_rolls_back_image_and_scores_together(monkeypatch, post):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(92), b"after": post})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 92
    assert result["qc_scores"]["critical_errors"] == []
    assert seen.image_calls == ["generate", "untuck"]


@pytest.mark.parametrize("post", [
    scored(99, target=False),
    scored(99, protected=False, regressions=("rib scale changed",)),
])
def test_higher_score_cannot_override_unresolved_or_regressed_pair(monkeypatch, post):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(80), b"after": post})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 80


def test_untuck_request_includes_current_cut_original_sources_and_matching(monkeypatch):
    _, seen = run_worker(
        monkeypatch, p2={b"before": scored(90), b"after": scored(92)},
        pants_mode="shadow",
        candidate_kwargs={"match_is_custom": True, "source_mirrored": True})
    request = seen.requests[-1]
    assert request["model"] == "gpt-image-2.5-sunburst"
    assert [image.data for image in request["images"]][:4] == [
        b"before", FRONT.data, DETAIL.data, MATCHING.data]
    assert "2x2 contact sheet" in seen.prompts[-1]
    assert "MIRRORED SOURCE PHOTOS" in seen.prompts[-1]
    assert seen.pair_options[-1] == {
        "source_mirrored": True, "match_is_custom": True}


def test_small_score_drop_keeps_successful_edit(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(80), b"after": scored(76)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"]["product_fidelity"] == 76
    assert result["qc_scores"]["outcome"] == "needs_review"


def test_failed_postcheck_keeps_previous_image_without_another_generation(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(92), b"after": RuntimeError("judge unavailable")})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 92
    assert seen.image_calls == ["generate", "untuck"]


def test_intermediate_edit_check_failure_keeps_checked_image_and_scores(monkeypatch):
    result, seen = run_worker(
        monkeypatch, p2={b"before": scored(92), b"after": RuntimeError("rescore unavailable")},
        has_match=False, bust_pass="on",
    )
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 92
    assert seen.judged == [b"before", b"after"]
    assert seen.image_calls == ["generate", "bust"]


@pytest.mark.parametrize("has_match,after,expected_calls", [(False, b"after", ["generate"]), (True, b"before", ["generate", "untuck"])])
def test_unchanged_image_needs_no_additional_checks(monkeypatch, has_match, after, expected_calls):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(92)}, has_match=has_match, after=after,
                             series_results={b"before": series(89)})
    assert seen.puts == [b"before"]
    assert seen.judged == seen.series == [b"before"]
    assert seen.image_calls == expected_calls
    assert result["qc_scores"]["series_consistency"] == 89


def test_disabled_main_qc_is_not_enabled_by_final_edit(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"after": scored(95)}, mode="off")
    assert seen.puts == [b"after"]
    assert seen.judged == [b"after"]
    assert result["qc_scores"] is None


def test_effective_shadow_qc_with_style_reference_keeps_score_binding(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(91), b"after": scored(96)}, mode="off",
                             ref_images=[InlineImage("image/png", b"style-reference")])
    assert seen.puts == [b"after"]
    assert seen.judged == [b"before", b"after"]
    assert result["qc_scores"]["product_fidelity"] == 96


def test_missing_initial_judgment_can_be_replaced_by_final_judgment(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": RuntimeError("initial unavailable"), b"after": scored(91)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"] is not None
    assert result["qc_scores"]["product_fidelity"] == 91


@pytest.mark.parametrize("after_scores", [scored(95, critical=["invented panel seam"]), scored(30)])
def test_new_final_judgment_cannot_bypass_enforce_when_initial_check_failed(monkeypatch, after_scores):
    result, seen = run_worker(monkeypatch, p2={
        b"before": RuntimeError("initial unavailable"), b"after": after_scores})
    assert seen.puts == [b"before"]
    assert result["qc_scores"] is None
    assert seen.image_calls == ["generate", "untuck"]


def test_new_final_series_failure_cannot_bypass_enforce(monkeypatch):
    result, seen = run_worker(monkeypatch,
                             p2={b"before": scored(95), b"after": scored(95)},
                             series_results={b"after": series(30)})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 95
    assert result["qc_scores"]["series_consistency"] is None


def test_new_final_judgment_stays_observational_in_shadow(monkeypatch):
    result, seen = run_worker(monkeypatch, mode="shadow", p2={
        b"before": RuntimeError("initial unavailable"),
        b"after": scored(95, critical=["invented panel seam"])})
    assert seen.puts == [b"before"]
    assert result["qc_scores"] is None


def test_new_final_series_pass_is_not_rejected_for_missing_baseline(monkeypatch):
    result, seen = run_worker(monkeypatch,
                             p2={b"before": scored(95), b"after": scored(95)},
                             series_results={b"after": series(90)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"]["series_consistency"] == 90


def test_final_edit_cannot_bypass_existing_matching_identity_gate(monkeypatch):
    result, seen = run_worker(monkeypatch, pants_mode="enforce",
                             p2={b"before": scored(92), b"after": scored(95, matching_critical=["matching trousers changed"])})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["product_fidelity"] == 92


def test_final_series_regression_rolls_back_without_new_image_request(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(95), b"after": scored(95)},
                             series_results={b"before": series(94), b"after": series(30)})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["series_consistency"] == 94
    assert seen.image_calls == ["generate", "untuck"]


def test_unavailable_final_series_does_not_reuse_old_score_on_new_pixels(monkeypatch):
    result, seen = run_worker(monkeypatch, p2={b"before": scored(95), b"after": scored(95)},
                             series_results={b"before": series(94), b"after": None})
    assert seen.puts == [b"before"]
    assert result["qc_scores"]["series_consistency"] == 94


def test_budget_salvage_also_checks_final_image(monkeypatch):
    result, seen = run_worker(monkeypatch, generated=(b"first", b"second"),
                             p2={b"first": scored(55), b"second": scored(50), b"after": scored(88)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"]["product_fidelity"] == 88
    assert result["qc_scores"]["salvaged"] is True
    assert seen.image_calls == ["generate", "generate", "untuck"]


def test_loop_exhausted_salvage_also_checks_final_image(monkeypatch):
    result, seen = run_worker(monkeypatch, generated=(b"before", GeminiError("generation unavailable")),
                             p2={b"before": scored(91), b"after": scored(94)},
                             series_results={b"before": series(30), b"after": series(92)})
    assert seen.puts == [b"after"]
    assert result["qc_scores"]["product_fidelity"] == 94
    assert result["qc_scores"]["series_consistency"] == 92
    assert result["qc_scores"]["salvaged"] is True


def test_selected_earlier_candidate_supplies_the_rollback_baseline(monkeypatch):
    result, seen = run_worker(monkeypatch, generated=(b"first", b"second"),
                             p2={b"first": scored(95), b"second": scored(70), b"after": scored(75)},
                             series_results={b"first": series(55), b"second": series(40), b"after": series(95)})
    assert seen.puts == [b"first"]
    assert result["qc_scores"]["product_fidelity"] == 95
    assert result["qc_scores"]["series_consistency"] == 55
    assert result["qc_scores"]["salvaged"] is True
