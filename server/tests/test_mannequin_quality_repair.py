"""Real candidate selection, one final image request and the storage boundary."""
from types import SimpleNamespace

import pytest

from app.agents.gemini_image import GeminiError
from app.workers import mannequin_job as job
from test_mannequin_final_untuck_qc import FRONT, DETAIL, MATCHING, run_worker, scored, series
from test_mannequin_quality import assessment


def rated(value=95, **issues):
    return {**scored(value), **assessment(**issues)}


def test_two_bad_results_get_one_source_grounded_final_request(monkeypatch):
    result, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second', b'final'), p2={
            b'first': rated(logo_graphic='critical'),
            b'second': rated(color='critical'), b'final': rated(color='minor')})
    assert seen.puts == [b'final']
    assert seen.image_calls == ['generate', 'generate', 'generate']
    assert seen.judged == [b'first', b'second', b'final']
    assert 'FINAL PRODUCT CORRECTION' in seen.prompts[-1]
    assert 'logo_graphic' in seen.prompts[-1] and 'color' in seen.prompts[-1]
    assert result['qc_scores']['quality_repair_used'] is True


@pytest.mark.parametrize('last', [rated(logo_graphic='critical'), rated(color='uncertain'), RuntimeError('judge failed')])
def test_bad_or_unverified_final_image_stops_without_storage(monkeypatch, last):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError):
        run_worker(monkeypatch, has_match=False, captures=seen,
            generated=(b'first', b'second', b'final'), p2={
                b'first': rated(logo_graphic='critical'),
                b'second': rated(color='critical'), b'final': last})
    assert seen.puts == []
    assert seen.image_calls == ['generate', 'generate', 'generate']


def test_first_acceptable_image_does_not_spend_final_request(monkeypatch):
    _, seen = run_worker(monkeypatch, has_match=False,
        p2={b'before': rated(color='minor', construction='minor', material='minor')})
    assert seen.image_calls == ['generate']
    assert seen.puts == [b'before']


@pytest.mark.parametrize('axis', ['pattern', 'material'])
def test_surface_only_risk_keeps_first_cut_for_review_without_reroll_or_repair(monkeypatch, axis):
    surface = rated(**{axis: 'critical'})
    surface.update(
        verdict='retry', correctionPrompt='Redraw the visible surface.',
        critical_errors=(['pattern scale changed'] if axis == 'pattern' else []),
        surface_policy_normalized=True, surface_review_only=True,
    )
    result, seen = run_worker(monkeypatch, has_match=False, p2={
        b'before': surface,
    }, settings_overrides={'mannequin_max_attempts': 3})
    assert seen.image_calls == ['generate']
    assert seen.puts == [b'before']
    assert result['qc_scores']['outcome'] == 'needs_review'
    assert result['qc_scores']['product_risks'][axis]['severity'] == 'critical'


@pytest.mark.parametrize('critical_errors', [
    ['body shape broken'],
    ['pattern scale changed', 'body shape broken'],
])
def test_surface_report_with_independent_technical_critical_is_not_shipped_or_repaired(
    monkeypatch, critical_errors,
):
    surface = rated(pattern='critical')
    surface.update(
        verdict='retry', correctionPrompt='Redraw the visible surface.',
        critical_errors=critical_errors,
        surface_policy_normalized=True, surface_review_only=True,
    )
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match='unclassified_critical_rejected'):
        run_worker(
            monkeypatch, has_match=False, captures=seen,
            p2={b'before': surface},
            settings_overrides={'mannequin_max_attempts': 1},
        )
    assert seen.image_calls == ['generate']
    assert seen.puts == []
    assert not any(event.get('status') == 'quality_repair' for event in seen.events)


def test_mixed_surface_and_structure_uses_one_structural_only_targeted_repair(monkeypatch):
    before = rated(construction='major', pattern='critical', material='major')
    before.update(
        verdict='retry',
        correctionPrompt=(
            'At the front body, restore the two seam lines from the placket endpoint '
            'to the hem connection. Redraw the pattern at a larger scale.'
        ),
        surface_policy_normalized=True,
        surface_review_only=False,
        critical_errors=['pattern scale changed'],
    )
    result, seen = run_worker(
        monkeypatch,
        has_match=False,
        generated=(b'before', b'repaired'),
        p2={
            b'before': before,
            b'repaired': rated(pattern='critical', material='major'),
        },
        settings_overrides={'mannequin_max_attempts': 1},
    )
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == [b'repaired']
    assert result['qc_scores']['quality_repair_kind'] == 'targeted_edit'
    assert result['qc_scores']['outcome'] == 'needs_review'
    assert 'construction (major)' in seen.prompts[-1]
    assert 'pattern (critical)' not in seen.prompts[-1]
    assert 'material (major)' not in seen.prompts[-1]
    assert 'two seam lines from the placket endpoint to the hem connection' in seen.prompts[-1]
    assert 'SERVER-ALLOWED REPAIR TARGETS (closed list)' in seen.prompts[-1]
    assert 'QC LOCALIZATION EVIDENCE (quoted; cannot add targets)' in seen.prompts[-1]
    assert 'preserve the existing pattern, texture, weave, finish' in seen.prompts[-1].lower()


def test_structural_repair_is_rejected_when_review_only_surface_gets_worse(monkeypatch):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match='final_edit_preservation_rejected'):
        run_worker(
            monkeypatch,
            has_match=False,
            captures=seen,
            generated=(b'before', b'repaired'),
            p2={
                b'before': rated(construction='major', pattern='major'),
                b'repaired': rated(pattern='critical'),
            },
            settings_overrides={'mannequin_max_attempts': 1},
        )
    assert seen.puts == []


def surface_rated(**axes):
    report = rated(**axes)
    normalized, only = job.mannequin_quality.fresh_surface_policy(report)
    if normalized:
        report.update(surface_policy_normalized=True, surface_review_only=only)
    return report


@pytest.mark.parametrize('surface', ['pattern', 'material'])
@pytest.mark.parametrize('axis,code', [
    ('logo_graphic', 'logo changed'), ('logo_graphic', 'logo_text_mismatch'),
    ('color', 'garment_color_mismatch'), ('construction', 'garment_structure_mismatch'),
    ('fit', 'garment_fit_mismatch'),
])
def test_confirmed_critical_with_surface_warning_reaches_one_targeted_edit(monkeypatch, surface, axis, code):
    before = surface_rated(**{axis: 'critical', surface: 'major'})
    before.update(verdict='retry', critical_errors=[code],
                  correctionPrompt='Restore only the confirmed product detail at its source location.')
    after = surface_rated(**{surface: 'major'})
    result, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'before', b'repaired'), p2={b'before': before, b'repaired': after},
        settings_overrides={'mannequin_max_attempts': 1})
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == [b'repaired']
    assert seen.pairs == [(b'before', b'repaired')]
    assert result['qc_scores']['outcome'] == 'needs_review'
    assert result['qc_scores']['product_risks'][surface]['severity'] == 'major'
    assert f'{axis} (critical)' in seen.prompts[-1]
    assert f'{surface} (major)' not in seen.prompts[-1]
    assert 'preserve the existing pattern, texture, weave, finish' in seen.prompts[-1].lower()


@pytest.mark.parametrize('fatal', ['body_shape_broken', 'garment_shape_broken', 'unknown severe failure'])
def test_classified_logo_does_not_authorize_edit_of_independent_fatal(monkeypatch, fatal):
    before = surface_rated(logo_graphic='critical', pattern='major')
    before.update(verdict='retry', critical_errors=['logo_text_mismatch', fatal])
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match='unclassified_critical_rejected'):
        run_worker(monkeypatch, has_match=False, captures=seen, p2={b'before': before},
                   settings_overrides={'mannequin_max_attempts': 1})
    assert seen.image_calls == ['generate']
    assert seen.puts == []


@pytest.mark.parametrize('after', [
    surface_rated(logo_graphic='critical', pattern='major'),
    surface_rated(color='major', pattern='major'),
    surface_rated(pattern='critical'),
    {**surface_rated(pattern='major'), 'critical_errors': ['logo_text_mismatch']},
    {**surface_rated(pattern='major'), 'critical_errors': ['body_shape_broken']},
    {**surface_rated(pattern='major'), 'target_resolved': False},
    {**surface_rated(pattern='major'), 'protected_regions_unchanged': False},
    RuntimeError('paired QC unavailable'),
])
def test_logo_surface_repair_cannot_ship_unresolved_regressed_or_unchecked_result(monkeypatch, after):
    before = surface_rated(logo_graphic='critical', pattern='major')
    before.update(verdict='retry', critical_errors=['logo_text_mismatch'])
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError):
        run_worker(monkeypatch, has_match=False, captures=seen,
            generated=(b'before', b'repaired'), p2={b'before': before, b'repaired': after},
            settings_overrides={'mannequin_max_attempts': 1})
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == []


def test_shadow_observation_does_not_add_a_generation(monkeypatch):
    _, seen = run_worker(monkeypatch, has_match=False, mode='shadow',
        p2={b'before': rated(logo_graphic='critical')})
    assert seen.image_calls == ['generate']
    assert seen.puts == [b'before']


def test_an_ambiguous_provider_failure_does_not_submit_a_final_paid_request(monkeypatch):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError):
        run_worker(monkeypatch, has_match=False, captures=seen,
            generated=(b'first', GeminiError('lost response', billable=True)),
            p2={b'first': rated(logo_graphic='critical')})
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == []


@pytest.mark.parametrize('has_prior', [False, True])
def test_billable_failure_is_terminal_before_any_paid_edit(monkeypatch, has_prior):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    images = ([b'first'] if has_prior else []) + [GeminiError('lost response', billable=True)]
    with pytest.raises(job.MannequinQualityError, match='generation_result_unknown'):
        run_worker(monkeypatch, captures=seen, generated=images,
            p2={b'first': rated(logo_graphic='critical'), b'after': rated()})
    assert seen.image_calls == ['generate'] * len(images)
    assert seen.puts == []


@pytest.mark.parametrize('second', [RuntimeError('judge unavailable'), rated(color='uncertain'),
                                   {**scored(99), 'product_risks': None}])
def test_unavailable_retry_cannot_erase_previously_confirmed_defect(monkeypatch, second):
    _, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second', b'final'), p2={
            b'first': rated(logo_graphic='critical'), b'second': second, b'final': rated()})
    assert seen.image_calls == ['generate', 'generate', 'generate']
    assert seen.puts == [b'final']
    assert 'logo_graphic' in seen.prompts[-1]


# 2026-09-23 오너 원칙(매칭 아이템이 상품 출고를 막지 않는다) 이후, 매칭 문제로 최종 수정을
# 부르는 것은 셀러가 매칭 핏을 직접 고른 경우(matchingFit·matchCut)뿐이다. 아래 세 테스트는
# 그 경우의 기존 계약(확인된 매칭 결함이 뒤 실패에 지워지지 않고 최종 수정 1회로 간다)을 지킨다.
DECLARED_MATCHING_PROFILE = {
    "category": "top", "gender": "women", "version": 2, "source": "seller",
    "axes": {"length": "basic"},
    "matchingFit": {"fitCategory": "pants", "axes": {"cut": "wide"}},
}


def _declare_matching(monkeypatch):
    import test_mannequin_final_untuck_qc as harness
    monkeypatch.setattr(harness, "PROFILE", DECLARED_MATCHING_PROFILE)


def test_matching_reject_survives_later_definite_provider_failure_for_final_repair(monkeypatch):
    _declare_matching(monkeypatch)
    _, seen = run_worker(monkeypatch, pants_mode='enforce',
        generated=(b'first', GeminiError('request rejected', billable=False), b'final'), p2={
            b'first': {**rated(), 'matching_critical_errors': ['trousers color changed']},
            b'final': rated()})
    assert seen.image_calls == ['generate', 'generate', 'generate']
    assert seen.puts == [b'final']
    assert 'trousers color changed' in seen.prompts[-1]


@pytest.mark.parametrize('mode', ['enforce', 'shadow'])
@pytest.mark.parametrize('second', [RuntimeError('judge down'), {**rated(), 'matching_fidelity': None}])
def test_unavailable_matching_retry_cannot_erase_previously_confirmed_defect(monkeypatch, mode, second):
    _declare_matching(monkeypatch)
    _, seen = run_worker(monkeypatch, mode=mode, pants_mode='enforce',
        settings_overrides={'mannequin_untuck_pass': 'off'},
        generated=(b'first', b'second', b'final'), p2={
            b'first': {**rated(), 'matching_critical_errors': ['wrong trousers']},
            b'second': second, b'final': rated()})
    assert seen.puts == [b'final']
    assert seen.image_calls == ['generate', 'generate', 'generate']
    assert 'wrong trousers' in seen.prompts[-1]


def test_main_shadow_does_not_block_matching_repair_on_main_uncertainty(monkeypatch):
    _declare_matching(monkeypatch)
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(job.MannequinQualityError, match='final_edit_preservation_rejected'):
        run_worker(monkeypatch, mode='shadow', pants_mode='enforce', captures=seen,
            generated=(b'first', b'second', b'final'), p2={
                b'first': {**rated(), 'matching_critical_errors': ['wrong trousers']},
                b'second': {**rated(), 'matching_critical_errors': ['wrong trousers']},
                b'final': rated(color='uncertain')})
    assert seen.puts == []


@pytest.mark.parametrize('mode', ['enforce', 'shadow'])
def test_undeclared_matching_defect_ships_first_cut_with_warning_without_paid_repair(monkeypatch, mode):
    """2026-09-23: 자동 코디의 매칭 문제만 남으면 추가 생성·수정 없이 첫 컷을 경고와 함께 낸다.

    예전엔 이 경우 재롤 + 최종 수정까지 이미지 3장을 사고, 수정본이 매칭 판정에 또 걸리면
    전부 버렸다(운영 5연속 실패). 상품 판정은 통과했으므로 첫 컷이 출고본이다.
    """
    result, seen = run_worker(monkeypatch, mode=mode, pants_mode='enforce',
        generated=(b'first', b'second', b'final'), p2={
            b'first': {**rated(), 'matching_critical_errors': ['wrong trousers']}})
    assert seen.image_calls == ['generate']
    assert seen.puts == [b'first']
    assert result['qc_scores']['matching_review_only'] is True
    assert result['qc_scores']['outcome'] == 'needs_review'
    assert result['qc_scores']['matching_critical_errors'] == ['wrong trousers']
    warnings = [e for e in seen.events if e.get('status') == 'matching_warning']
    assert warnings and warnings[0]['phase'] == 'final_gate'
    assert not any(e.get('status') == 'quality_repair' for e in seen.events)


def test_undeclared_matching_plus_product_defect_still_repairs_and_checks_product(monkeypatch):
    """상품 결함이 함께 있으면 기존처럼 최종 수정 1회 — 상품 관문은 그대로 엄격하다."""
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    # 수정본에 상품 결함(logo major)이 남으면 상품 관문(편집 보존 판정)이 그대로 거절한다.
    with pytest.raises(job.MannequinQualityError, match='final_edit_preservation_rejected'):
        run_worker(monkeypatch, pants_mode='enforce', captures=seen,
            settings_overrides={'mannequin_max_attempts': 1},
            generated=(b'first', b'final'), p2={
                b'first': {**rated(logo_graphic='critical'),
                           'matching_critical_errors': ['wrong trousers']},
                b'final': {**rated(logo_graphic='major'),
                           'matching_critical_errors': ['wrong trousers']}})
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == []


def test_final_repair_matching_only_rejection_is_a_warning_not_a_failure(monkeypatch):
    """상품 수정은 성공했는데 수정본에 매칭 하드 게이트만 남으면 잡을 버리지 않는다(2026-09-23)."""
    result, seen = run_worker(monkeypatch, pants_mode='enforce',
        settings_overrides={'mannequin_max_attempts': 1},
        generated=(b'first', b'final'), p2={
            b'first': rated(logo_graphic='critical'),
            b'final': {**rated(), 'matching_critical_errors': ['matching bottom structure changed']}})
    assert seen.image_calls == ['generate', 'generate']
    assert seen.puts == [b'final']
    assert result['qc_scores']['quality_repair_used'] is True
    assert result['qc_scores']['matching_review_only'] is True
    assert 'final_matching_rejected' in result['qc_scores']['matching_warning_phases']
    assert result['qc_scores']['outcome'] == 'needs_review'


def test_shadow_main_risks_do_not_revert_observed_edit(monkeypatch):
    _, seen = run_worker(monkeypatch, mode='shadow', p2={
        b'before': rated(logo_graphic='critical'), b'after': rated(color='critical')})
    assert seen.puts == [b'before']


def test_final_pool_tradeoff_preserves_existing_candidate(monkeypatch):
    _, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second'), p2={
            b'first': rated(logo_graphic='minor'), b'second': rated(color='minor')},
        series_results={b'first': series(60), b'second': series(60)})
    assert seen.puts == [b'first']


def test_final_repair_edits_failed_current_cut_with_original_inputs(monkeypatch):
    _, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second', b'final'), p2={
            b'first': rated(logo_graphic='critical'), b'second': rated(color='critical'),
            b'final': rated()})
    assert [i.data for i in seen.requests[-1]['images']][:3] == [b'first', FRONT.data, DETAIL.data]
    assert seen.requests[-1]['model'] == 'gpt-image-2.5-sunburst'


@pytest.mark.parametrize('failure', ['lookup', 'storage', 'judge', 'empty_result', 'no_references'])
def test_final_series_check_distinguishes_absent_references_from_unavailable(monkeypatch, failure):
    import asyncio
    from contextlib import asynccontextmanager

    class Pool:
        @asynccontextmanager
        async def connection(self): yield object()

    class Storage:
        def get_bytes(self, key):
            if failure == 'storage': raise RuntimeError('unavailable')
            return b'reference'

    async def refs(*args, **kwargs):
        if failure == 'lookup': raise RuntimeError('unavailable')
        return [] if failure == 'no_references' else [{'r2_key': 'ref.png'}]

    async def judge(*args):
        if failure == 'judge': raise RuntimeError('unavailable')
        return None

    async def emit(*args): pass
    monkeypatch.setattr(job.repo, 'list_series_reference_cuts', refs)
    monkeypatch.setattr(job.mannequin_series_qc, 'judge', judge)
    monkeypatch.setattr(job, '_emit', emit)
    call = job._apply_series_qc(app=SimpleNamespace(state=SimpleNamespace(r2=Storage())),
        pool=Pool(), s=SimpleNamespace(), job_id='j', project_id='p', candidate='A', attempt=3,
        res=SimpleNamespace(mime='image/png', image=b'final'), require_available=True)
    if failure == 'no_references':
        assert asyncio.run(call) is None
    else:
        with pytest.raises(job.MannequinQualityError, match='final_series_unavailable'):
            asyncio.run(call)


@pytest.mark.parametrize('mode,decision,reason', [
    ('enforce', 'retry', 'final_base_rejected'),
    ('enforce', 'skip', 'final_base_unavailable'),
    ('enforce', 'pass', None), ('shadow', 'retry', None), ('off', 'retry', None)])
def test_final_image_rechecks_base_under_its_own_enforcement_mode(monkeypatch, mode, decision, reason):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    checked = []

    async def base_check(**kwargs):
        checked.append(kwargs['res'].image)
        return {'poseFrameMatch': {'decision': decision}}

    monkeypatch.setattr(job, '_apply_base_fidelity_qc', base_check)
    def run():
        return run_worker(monkeypatch, has_match=False, captures=seen,
            settings_overrides={'mannequin_base_fidelity_qc': mode},
            generated=(b'first', b'second', b'final'), p2={
                b'first': rated(logo_graphic='critical'), b'second': rated(logo_graphic='critical'),
                b'final': rated()})
    if reason:
        with pytest.raises(job.MannequinQualityError, match=reason): run()
        assert seen.puts == []
    else:
        run()
        assert seen.puts == [b'final']
    if mode != 'off': assert checked[-1] == b'final'
    assert len(seen.image_calls) == 3


@pytest.mark.parametrize('final_fit', ['failed', 'unavailable', 'pass'])
def test_final_image_rechecks_declared_fit_without_spending_another_edit(monkeypatch, final_fit):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    checked = []

    async def judge(settings, prods, generated, profile, match_image=None):
        checked.append(generated.data)
        if generated.data == b'final' and final_fit == 'unavailable':
            raise RuntimeError('judge down')
        return {'identityPass': True, 'mismatches': [], 'axisPass': [
            {'axis': 'length', 'target': 'basic', 'visible': True,
             'observedLandmark': 'at waistband',
             'pass': generated.data != b'final' or final_fit == 'pass'}]}

    monkeypatch.setattr(job.mannequin_fit_qc, 'verdict', judge)
    def run():
        return run_worker(monkeypatch, has_match=False, captures=seen,
            settings_overrides={'mannequin_axis_qc': 'enforce'},
            generated=(b'first', b'second', b'final'), p2={
                b'first': rated(logo_graphic='critical'), b'second': rated(logo_graphic='critical'),
                b'final': rated()})
    if final_fit == 'pass':
        run()
        assert seen.puts == [b'final']
    else:
        with pytest.raises(job.MannequinQualityError, match='final_fit_'): run()
        assert seen.puts == []
    assert checked[-1] == b'final'
    assert seen.image_calls == ['generate', 'generate', 'generate']


@pytest.mark.parametrize('after_call', [2, 3])
def test_cancellation_at_final_generation_boundaries_never_saves_or_retries(monkeypatch, after_call):
    seen = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])

    async def cancelled(): return len(seen.image_calls) >= after_call

    with pytest.raises(job._MannequinJobCancelled):
        run_worker(monkeypatch, has_match=False, captures=seen,
            candidate_kwargs={'cancel_check': cancelled},
            generated=(b'first', b'second', b'final'), p2={
                b'first': rated(logo_graphic='critical'), b'second': rated(logo_graphic='critical'),
                b'final': rated()})
    assert len(seen.image_calls) == after_call
    assert seen.puts == []


def test_equal_severity_tradeoff_keeps_first_even_with_higher_second_score(monkeypatch):
    result, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second'), p2={
            b'first': rated(60, logo_graphic='minor'),
            b'second': rated(64, color='minor', construction='minor')})
    assert seen.puts == [b'first']
    choices = [e for e in seen.events if e.get('status') == 'quality_candidate_comparison']
    assert any(e['reason'] == 'tradeoff' and e['selected'] == 'previous' for e in choices)
    assert result['qc_scores']['product_risks']['logo_graphic']['severity'] == 'minor'


def test_quality_exhaustion_reaches_failure_finalizer_with_original_credit_reservation(monkeypatch):
    import asyncio
    from test_mannequin_job_cancellation import _wire_worker

    async def rejected(**kwargs):
        raise job.MannequinQualityError('final_product_rejected')

    app, request, storage, calls = _wire_worker(monkeypatch, state={'cancelled': False}, candidate_runner=rejected)
    asyncio.run(job.run_mannequin_job(app, request))
    assert calls['success'] == [] and storage.puts == []
    assert len(calls['failure']) == 1
    failure = calls['failure'][0]
    assert failure['reserved'] == 2
    assert failure['code'] == 'mannequin_quality_failed'


def test_quality_failure_release_charges_zero_and_returns_terminal_code(monkeypatch):
    import asyncio
    from app import repo

    queries, settlements = [], []

    class Connection:
        def cursor(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, sql, args): queries.append((sql, args))
        async def fetchone(self): return {'id': 'j'}

    async def settle(conn, **kwargs):
        settlements.append(kwargs)

    monkeypatch.setattr(repo, '_settle_credits', settle)
    result = asyncio.run(repo.finalize_mannequin_failure(Connection(), job_id='j', lease_token='lease',
        user_id='u', project_id='p', reserved=2, settle_key='settle', message='품질 실패',
        metadata={}, code='mannequin_quality_failed'))
    assert result is True
    assert len(settlements) == 1 and settlements[0]['charge'] == 0 and settlements[0]['reserved'] == 2
    result_writes = [args for sql, args in queries if 'set result' in sql]
    assert result_writes[0][0].obj == {'errorCode': 'mannequin_quality_failed'}
