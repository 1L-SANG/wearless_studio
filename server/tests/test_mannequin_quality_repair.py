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


def test_matching_reject_survives_later_definite_provider_failure_for_final_repair(monkeypatch):
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
    _, seen = run_worker(monkeypatch, mode=mode, pants_mode='enforce',
        settings_overrides={'mannequin_untuck_pass': 'off'},
        generated=(b'first', b'second', b'final'), p2={
            b'first': {**rated(), 'matching_critical_errors': ['wrong trousers']},
            b'second': second, b'final': rated()})
    assert seen.puts == [b'final']
    assert seen.image_calls == ['generate', 'generate', 'generate']
    assert 'wrong trousers' in seen.prompts[-1]


def test_main_shadow_does_not_block_matching_repair_on_main_uncertainty(monkeypatch):
    _, seen = run_worker(monkeypatch, mode='shadow', pants_mode='enforce',
        generated=(b'first', b'second', b'final'), p2={
            b'first': {**rated(), 'matching_critical_errors': ['wrong trousers']},
            b'second': {**rated(), 'matching_critical_errors': ['wrong trousers']},
            b'final': rated(color='uncertain')})
    assert seen.puts == [b'final']
    assert seen.image_calls == ['generate', 'generate', 'generate']


def test_shadow_main_risks_do_not_revert_observed_edit(monkeypatch):
    _, seen = run_worker(monkeypatch, mode='shadow', p2={
        b'before': rated(logo_graphic='critical'), b'after': rated(color='critical')})
    assert seen.puts == [b'after']


def test_final_pool_tradeoff_preserves_existing_candidate(monkeypatch):
    _, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second'), p2={
            b'first': rated(logo_graphic='minor'), b'second': rated(color='minor')},
        series_results={b'first': series(60), b'second': series(60)})
    assert seen.puts == [b'first']


def test_final_repair_uses_original_inputs_not_failed_parent(monkeypatch):
    from app.agents.gemini_image import InlineImage
    _, seen = run_worker(monkeypatch, has_match=False,
        generated=(b'first', b'second', b'final'), p2={
            b'first': rated(logo_graphic='critical'), b'second': rated(color='critical'),
            b'final': rated()}, candidate_kwargs={
            'generation_path': 'edit', 'parent_cut_img': InlineImage('image/png', b'parent'),
            'adjust_directives': 'Adjust length to basic.'})
    assert b'parent' in [i.data for i in seen.requests[0]['images']]
    assert [i.data for i in seen.requests[-1]['images']] == [b'base', FRONT.data, DETAIL.data]


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
