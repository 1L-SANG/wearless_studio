"""Matching-only warnings preserve the first native cut, never a paid redraw."""
import pytest

from app.agents import mannequin_quality
from app.workers import mannequin_job as job
import test_mannequin_final_untuck_qc as harness
from test_mannequin_quality_repair import rated


def matching_only():
    return {**rated(), 'matching_critical_errors': ['matching bottom leg width changed'],
            'matching_fidelity': 30, 'verdict': 'retry'}


@pytest.mark.parametrize('problem', ['matching bottom leg width changed', 'matching top length changed'])
@pytest.mark.parametrize('attempts', [1, 3])
def test_matching_only_keeps_first_with_warning_without_postpass(monkeypatch, problem, attempts):
    monkeypatch.setattr(harness, 'PROFILE', {
        'version': 2, 'category': 'top', 'gender': 'women', 'source': 'auto', 'axes': {}})
    assessment = {**matching_only(), 'matching_critical_errors': [problem]}
    result, seen = harness.run_worker(monkeypatch, pants_mode='enforce',
        settings_overrides={'mannequin_max_attempts': attempts}, p2={b'before': assessment})
    assert seen.image_calls == ['generate']
    assert seen.puts == [b'before']
    assert result['qc_scores']['outcome'] == 'needs_review'
    assert result['qc_scores']['matching_critical_errors'] == [problem]
    assert result['qc_scores']['matching_review_only'] is True
    assert not any(e.get('status') in ('quality_repair', 'untuck_pass') for e in seen.events)


@pytest.mark.parametrize('change', [
    {'critical_errors': ['body shape broken']},
    {'product_risks': None},
    {'image_quality': 20},
    {'physical_naturalness': 20},
    {'bottom_waistband_visible': 'covered'},
    {'bottom_waistband_visible': 'uncertain'},
])
def test_warning_cannot_hide_main_technical_or_visibility_problem(change):
    assert not mannequin_quality.matching_only_review({**matching_only(), **change})


def test_main_structure_failure_is_not_matching_only():
    report = {**rated(construction='major'), 'matching_critical_errors': ['wrong matching width']}
    assert not mannequin_quality.matching_only_review(report)


def test_matching_review_does_not_erase_unseen_surface_risk():
    report = {**rated(pattern='major'), 'matching_critical_errors': ['wrong matching width']}
    assert mannequin_quality.matching_only_review(report)
    assert report['product_risks']['pattern']['severity'] == 'major'


def test_warning_does_not_override_seller_edit(monkeypatch):
    report = matching_only()
    calls = []
    original = mannequin_quality.matching_only_review
    monkeypatch.setattr(mannequin_quality, 'matching_only_review', lambda value: calls.append(value) or original(value))
    # Existing seller-axis path remains governed by its original checks, not early retention.
    with pytest.raises((job.MannequinQualityError, IndexError)):
        harness.run_worker(monkeypatch, pants_mode='enforce',
            settings_overrides={'mannequin_max_attempts': 1}, p2={b'before': report},
            candidate_kwargs={'generation_path': 'edit', 'parent_cut_img': harness.FRONT,
                              'adjust_directives': 'Use the seller selected basic length.'})
    assert calls == []


def test_matching_warning_marker_cannot_erase_a_main_error():
    from conftest import make_settings
    report = {**matching_only(), **rated(construction='major'), 'matching_review_only': True}
    assert job.score_outcome(make_settings(), report) == 'regenerate'


@pytest.mark.parametrize('declared', [
    {'matchingFit': {'fitCategory': 'pants', 'axes': {'cut': 'wide'}}},
    {'matchCut': 'wide'},
])
def test_explicit_matching_choice_is_not_automatic_retention(monkeypatch, declared):
    from types import SimpleNamespace
    monkeypatch.setattr(harness, 'PROFILE', {
        'version': 2, 'category': 'top', 'gender': 'women', 'source': 'seller', 'axes': {}, **declared})
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError):
        harness.run_worker(monkeypatch, pants_mode='enforce', captures=seen,
            settings_overrides={'mannequin_max_attempts': 1}, p2={b'before': matching_only()})
    assert not any(e.get('status') == 'matching_review_only' for e in seen.events)
    assert seen.puts == []
