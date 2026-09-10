"""Selection follows product impact, never the number of defect descriptions."""
import pytest

from app.agents import mannequin_quality as quality


def assessment(**severities):
    axes = ('logo_graphic', 'color', 'construction', 'pattern', 'material', 'fit')
    return {
        'product_risks': {
            axis: {'severity': severities.get(axis, 'none'), 'evidence': f'{axis} photo comparison'}
            for axis in axes
        },
        'critical_errors': [],
    }


def test_missing_logo_loses_to_multiple_minor_differences():
    missing_logo = assessment(logo_graphic='critical')
    small_differences = assessment(color='minor', construction='minor', material='minor')
    assert quality.compare_risks(small_differences, missing_logo) == 'better'
    assert quality.compare_risks(missing_logo, small_differences) == 'worse'


def test_duplicate_prose_never_changes_selection():
    old, new = assessment(color='major'), assessment(color='minor')
    old['critical_errors'] = ['colour shifted'] * 10
    assert quality.compare_risks(new, old) == 'better'
    old['critical_errors'] = ['colour shifted']
    assert quality.compare_risks(new, old) == 'better'


def test_same_severity_tradeoff_is_not_resolved_by_count_or_axis_order():
    old = assessment(logo_graphic='minor')
    new = assessment(color='minor', construction='minor', material='minor')
    assert quality.compare_risks(new, old) == 'tradeoff'
    assert quality.compare_risks(old, new) == 'tradeoff'


def test_improvement_without_any_worse_axis_wins():
    old = assessment(color='major', construction='minor')
    new = assessment(color='major')
    assert quality.compare_risks(new, old) == 'better'


def test_same_findings_with_different_words_are_equivalent():
    old, new = assessment(color='major'), assessment(color='major')
    new['product_risks']['color']['evidence'] = 'Different wording of the same observation'
    assert quality.compare_risks(new, old) == 'equivalent'


@pytest.mark.parametrize('axis', ['color', 'construction', 'pattern'])
def test_existing_logo_critical_cannot_hide_a_new_major_problem(axis):
    old = assessment(logo_graphic='critical')
    new = assessment(logo_graphic='critical', **{axis: 'major'})
    assert quality.edit_risk_reason(old, new) == f'worse_{axis}'


def test_fixing_logo_does_not_buy_permission_to_break_color():
    assert quality.edit_risk_reason(
        assessment(logo_graphic='critical'), assessment(color='critical')) == 'worse_color'


def test_minor_difference_is_allowed_when_fixing_an_important_problem():
    assert quality.edit_risk_reason(
        assessment(logo_graphic='major'), assessment(color='minor')) is None


def test_losing_a_confirmed_assessment_keeps_before():
    assert quality.edit_risk_reason(assessment(), assessment(color='uncertain')) == 'review_unavailable'
    assert quality.edit_risk_reason(assessment(), {}) == 'review_unavailable'


def test_unclassified_existing_critical_is_not_compared_as_free_text():
    before = {'critical_errors': ['logo changed']}
    after = {'critical_errors': ['logo changed', 'color changed']}
    assert quality.edit_risk_reason(before, after) == 'unclassified_critical'


@pytest.mark.parametrize('broken', [None, {}, {'color': {'severity': 'none'}}, []])
def test_incomplete_risk_report_is_not_a_clean_result(broken):
    assert quality.validate_risks(broken) is None


def test_missing_evidence_cannot_support_a_critical_judgment():
    report = assessment(color='critical')['product_risks']
    report['color']['evidence'] = ''
    assert quality.validate_risks(report) is None


def test_uncertainty_is_not_rated_as_zero_defects():
    assert quality.compare_risks(assessment(color='uncertain'), assessment(color='minor')) == 'unavailable'


def test_repair_feedback_prioritizes_source_proven_failures_and_preserves_correct_parts():
    report = assessment(logo_graphic='critical', color='minor', construction='major')
    prompt = quality.repair_feedback(report)
    assert prompt.index('logo_graphic') < prompt.index('construction')
    assert 'logo_graphic photo comparison' in prompt
    assert 'minor' not in prompt
    assert 'product photos' in prompt
    assert 'already-correct' in prompt


def test_important_issues_are_blocking_but_minor_and_uncertain_are_not_claimed_critical():
    assert quality.blocking_issues(assessment(color='minor')) == []
    assert quality.blocking_issues(assessment(color='uncertain')) == []
    assert quality.blocking_issues(assessment(logo_graphic='critical'))
    assert quality.blocking_issues({'critical_errors': ['legacy defect']}) == ['legacy defect']


def test_technical_critical_is_not_erased_by_clean_product_dimensions():
    old = assessment()
    old['critical_errors'] = ['mannequin body broken']
    assert quality.compare_risks(assessment(color='major'), old) == 'better'


def test_worker_uses_risk_priority_before_numeric_quality():
    from app.workers import mannequin_job as job
    from conftest import make_settings
    old = {**assessment(logo_graphic='critical'), 'product_fidelity': 99}
    new = {**assessment(color='minor', construction='minor'), 'product_fidelity': 70}
    assert job._is_better_candidate(make_settings(image_qc='enforce'), new, old)
    assert job.score_outcome(make_settings(), old) == 'regenerate'


def test_worker_preserves_report_when_combining_and_rejects_worsening_with_same_grade():
    from app.workers import mannequin_job as job
    from conftest import make_settings
    old = {**assessment(logo_graphic='critical'), 'product_fidelity': 40}
    new = {**assessment(logo_graphic='critical', color='critical'), 'product_fidelity': 10}
    assert job.merge_qc_scores(old, None)['product_risks'] == old['product_risks']
    assert job.edit_regressed(make_settings(image_qc='enforce'), old, new)


def test_scored_provider_request_binds_assessment_to_actual_image(monkeypatch):
    import asyncio
    import hashlib
    from app.agents import image_qc
    from app.agents.gemini_image import InlineImage
    from conftest import make_settings

    async def provider(settings, prompt, images, schema):
        assert 'product_risks' in schema['properties']
        assert set(schema['properties']['product_risks']['required']) == set(assessment()['product_risks'])
        assert images[-1].data == b'candidate'
        return {'verdict': 'pass', **assessment(color='minor')}, 'gemini'

    monkeypatch.setattr(image_qc, 'analyze_with_fallback', provider)
    result = asyncio.run(image_qc.verdict(make_settings(), [InlineImage('image/png', b'original')],
        InlineImage('image/png', b'candidate'), scored=True))
    assert result['image_hash'] == hashlib.sha256(b'candidate').hexdigest()
    assert result['product_risks']['color']['severity'] == 'minor'


@pytest.mark.parametrize('critical', [[], ['logo missing']])
def test_scored_provider_missing_report_preserves_known_signals_without_claiming_pass(monkeypatch, critical):
    import asyncio
    from app.agents import image_qc
    from app.agents.gemini_image import InlineImage
    from app.workers import mannequin_job as job
    from conftest import make_settings

    async def provider(*args):
        return {'verdict': 'pass', 'product_fidelity': 99, 'critical_errors': critical}, 'gemini'

    monkeypatch.setattr(image_qc, 'analyze_with_fallback', provider)
    result = asyncio.run(image_qc.verdict(make_settings(), [], InlineImage('image/png', b'candidate'), scored=True))
    assert result['critical_errors'] == critical
    assert result['product_fidelity'] == 99
    assert not quality.review_complete(result)
    assert job.score_outcome(make_settings(), result) == ('regenerate' if critical else 'needs_review')


def test_partial_report_keeps_confirmed_logo_failure_when_another_axis_is_missing():
    from app.agents import image_qc
    from app.workers import mannequin_job as job
    from conftest import make_settings

    partial = assessment(logo_graphic='critical')
    del partial['product_risks']['material']
    result = image_qc.validate({'verdict': 'pass', **partial}, scored=True)
    assert not quality.review_complete(result)
    assert result['product_risks']['material']['severity'] == 'uncertain'
    assert job.score_outcome(make_settings(), result) == 'regenerate'
    assert 'logo_graphic' in quality.repair_feedback(result)


def test_shared_unscored_request_does_not_use_mannequin_policy():
    from app.agents import image_qc
    assert set(image_qc.qc_schema()['properties']) == {'verdict', 'mismatches', 'correctionPrompt'}
    assert 'PRODUCT IMPACT' not in image_qc.build_prompt(1)
