"""Stage timing and immutable continuation use real application entrypoints."""
import asyncio
from dataclasses import asdict, replace
import importlib
import json
from types import SimpleNamespace

import pytest

from app.agents import cut_generator, wearshot_runtime as rt, vision_llm
from app.agents.gemini_image import GeminiImageClient
from conftest import make_settings
from test_wearshot_contract_v2 import packet
from test_wearshot_qc_v2 import observed, mark
from test_wearshot_runtime_v2 import canvas


def harness():
    assert importlib.util.find_spec('app.experiments.wearshot_stage_benchmark'), 'stage benchmark missing'
    return importlib.import_module('app.experiments.wearshot_stage_benchmark')


def test_generation_override_is_local_and_frozen():
    assert hasattr(rt, 'generation_settings'), 'v2 settings resolver missing'
    settings = make_settings(wearshot_generation_model='gpt-image-2.5-flare-2026-09-08')
    before = asdict(settings)
    local = rt.generation_settings(settings)
    assert cut_generator._resolve_generation_model(local, {}) == 'gpt-image-2.5-flare-2026-09-08'
    assert cut_generator._resolve_generation_model(settings, {}) == 'gemini-3-pro-image'
    assert asdict(settings) == before
    unset = replace(settings, wearshot_generation_model=None)
    assert rt.generation_settings(unset) is unset


@pytest.mark.parametrize('value,expected', [('', None), ('  gpt-image-2.5-flare-2026-09-08  ', 'gpt-image-2.5-flare-2026-09-08')])
def test_generation_environment_setting_only_changes_v2_consumer(monkeypatch, value, expected):
    from app import config
    monkeypatch.setattr(config.os, 'environ', {'WEARSHOT_GENERATION_MODEL': value})
    settings = config.load_settings()
    assert settings.wearshot_generation_model == expected
    assert settings.wearshot_repair_model is None
    assert cut_generator._resolve_generation_model(settings, {}) == 'gemini-3-pro-image'
    assert cut_generator._resolve_generation_model(rt.generation_settings(settings), {}) == (expected or 'gemini-3-pro-image')


@pytest.mark.parametrize('phase', ['generate-first', 'qc-first', 'conditional-repair', 'qc-final'])
def test_live_invocation_requires_single_explicit_case_and_arm(evidence, external, monkeypatch, phase):
    for kwargs in [dict(mode=phase), dict(mode=phase, case='sample-a'), dict(mode=phase, case='sample-a', arm='both')]:
        with pytest.raises(ValueError):
            asyncio.run(harness().run_manifest(evidence.path, evidence.out, **kwargs))
    assert evidence.calls == [] and not evidence.out.exists()


@pytest.mark.parametrize('hidden', [False, True])
def test_shared_qc_times_primary_and_focus_without_double_counting(monkeypatch, hidden):
    from app.agents.wearshot_contract import FrameLock
    contract = packet(frame_lock=FrameLock('medium', 'hidden', 'Head outside crop.')) if hidden else packet()
    clock = [0.0]
    assert hasattr(rt, 'perf_counter'), 'shared QC monotonic timing missing'
    monkeypatch.setattr(rt, 'perf_counter', lambda: clock[0])
    async def judge(settings, model, prompt, images, schema, timeout):
        assert timeout == 180
        if 'garments' in schema['properties']:
            clock[0] += 39
            return observed(contract)
        clock[0] += 12
        return dict(viewAdequate=True, selectedFaceRelation='clear_target', strongestTargetEvidence='Target eyes',
                    strongestSourceEvidence='Source absent', remainingAmbiguity='None')
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    result = asyncio.run(rt.review_candidate(make_settings(openai_api_key='test'), contract, canvas()))
    assert result['timing']['primaryMs'] == 39000
    assert result['timing']['focusedMs'] == (0 if hidden else 12000)
    assert result['timing']['totalMs'] == (39000 if hidden else 51000)
    assert result['timing']['focusedStatus'] == ('skipped_hidden_face' if hidden else 'completed')


@pytest.fixture
def evidence(tmp_path):
    contract = packet()
    paths = {}
    for ref in contract.references:
        path = tmp_path / f'{ref.key}.png'
        path.write_bytes(ref.image.data)
        paths[ref.key] = path.name
    manifest = dict(schemaVersion=1, models={
        'image2': dict(first='gpt-image-2', repair='gpt-image-2'),
        'flare_sunburst': dict(first='gpt-image-2.5-flare-2026-09-08', repair='gpt-image-2.5-sunburst-2026-09-08')},
        qcModel='gpt-6-astra', qcTimeoutSeconds=180, quality='medium', imageSize='2K',
        cases=[dict(id='sample-a', generationDeclared=True, contract=contract.to_dict(), referencePaths=paths, outputSize='1360x2048')])
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    return SimpleNamespace(path=path, out=tmp_path / 'results', manifest=manifest, contract=contract,
                           first=canvas('orange'), final=canvas('green'), clock=[0.0], calls=[], fail_first=True)


@pytest.fixture
def external(monkeypatch, evidence):
    e = evidence
    async def generate(self, model, prompt, images, image_size, **kwargs):
        e.calls.append(('generate', model, images, prompt))
        assert image_size == '2K'
        assert kwargs == dict(openai_preserve_input_bytes=True, openai_output_size='1360x2048')
        is_repair = images[0] == e.first
        assert images == ([e.first] if is_repair else []) + [r.image for r in e.contract.references]
        e.clock[0] += 35 if is_repair else 12
        image = e.final if is_repair else e.first
        return SimpleNamespace(image=image.data, mime=image.mime, usage={'total_tokens': 99}, latency_ms=11000)
    async def judge(settings, model, prompt, images, schema, timeout):
        e.calls.append(('qc', model, images))
        assert timeout == 180 and model == 'gpt-6-astra'
        if 'garments' in schema['properties']:
            e.clock[0] += 30 if images[-1] == e.final else 39
            raw = observed(e.contract)
            if e.fail_first and images[-1] == e.first:
                raw['globalChecks']['capture'] = mark('FAIL')
            raw['protectedChecks'] = {k: mark() for k in schema['properties']['protectedChecks']['properties']}
            return raw
        e.clock[0] += 12
        return dict(viewAdequate=True, selectedFaceRelation='clear_target', strongestTargetEvidence='Target eyes',
                    strongestSourceEvidence='Source absent', remainingAmbiguity='None')
    monkeypatch.setattr(GeminiImageClient, 'generate_content_image', generate)
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    monkeypatch.setattr(rt, 'perf_counter', lambda: e.clock[0], raising=False)
    return e


def run(e, monkeypatch, mode='dry-run', arm='flare_sunburst', case='sample-a'):
    h = harness()
    monkeypatch.setattr(h, 'perf_counter', lambda: e.clock[0])
    return asyncio.run(h.run_manifest(e.path, e.out, mode=mode,
        case=None if mode in {'dry-run', 'report'} else case,
        arm=None if mode in {'dry-run', 'report'} else arm,
        settings=make_settings(openai_api_key='test', gemini_api_key=None, vertex_project=None, vertex_location='global')))


def test_default_dry_run_never_calls_or_writes(evidence, external, monkeypatch):
    result = run(evidence, monkeypatch)
    assert result[0]['status'] == 'dry-run'
    assert evidence.calls == [] and not evidence.out.exists()


@pytest.mark.parametrize('needs_repair', [True, False])
def test_same_first_image_underpins_conditional_flow_and_measured_sum(evidence, external, monkeypatch, needs_repair):
    e = evidence
    e.fail_first = needs_repair
    first = run(e, monkeypatch, 'generate-first')[0]
    assert first['status'] == 'completed'
    assert first['reviewStatus'] == 'unreviewed'
    assert first['actionMs'] == 12000 and first['providerLatencyMs'] == 11000
    preview = next(r for r in run(e, monkeypatch, 'report')['cases'] if r['arm'] == 'flare_sunburst')
    assert preview['timing']['withQcDecisionTimeMs'] is None
    assert preview['timing']['acceptedResultTimeMs'] is None
    e.clock[0] += 9000  # controller pause must never enter measured critical path
    assert run(e, monkeypatch, 'qc-first')[0]['releaseAllowed'] is (not needs_repair)
    repair = run(e, monkeypatch, 'conditional-repair')[0]
    assert repair['decision'] == ('repair' if needs_repair else 'skip_pass')
    if needs_repair:
        assert run(e, monkeypatch, 'qc-final')[0]['releaseAllowed']
    result = run(e, monkeypatch, 'report')
    row = next(r for r in result['cases'] if r['arm'] == 'flare_sunburst')
    assert row['timing']['withoutQcMs'] == 12000
    assert row['timing']['withQcCriticalPathMs'] == (12000 + 39000 + 12000 + 35000 + 42000 if needs_repair else 63000)
    assert row['timing']['manualQueueTimeIncluded'] is False
    assert row['outcome'] == 'released'
    assert result['summary']['flare_sunburst']['withoutQcMs'] == dict(count=1, median=12000, range=[12000, 12000])
    assert result['summary']['flare_sunburst']['withQcDecisionTimeMs'] == dict(
        count=1, median=140000 if needs_repair else 63000, range=[140000, 140000] if needs_repair else [63000, 63000])
    assert (e.out / 'sample-a.generate-first.flare_sunburst.png').read_bytes() == e.first.data
    assert [c[1] for c in e.calls if c[0] == 'generate'] == (['gpt-image-2.5-flare-2026-09-08', 'gpt-image-2.5-sunburst-2026-09-08'] if needs_repair else ['gpt-image-2.5-flare-2026-09-08'])


@pytest.mark.parametrize('change', ['reference', 'code', 'deadline', 'model', 'size', 'quality', 'first_image', 'first_receipt', 'first_started'])
def test_changed_provenance_blocks_next_phase_before_calls(evidence, external, monkeypatch, change):
    e = evidence
    run(e, monkeypatch, 'generate-first')
    if change == 'reference':
        (e.path.parent / 'face.png').write_bytes(canvas('pink').data)
    elif change == 'code':
        monkeypatch.setattr(harness(), '_code_versions', lambda: {'changed': '0' * 64})
    elif change in ('first_image', 'first_receipt', 'first_started'):
        suffix = {'first_image': '.png', 'first_receipt': '.receipt.json', 'first_started': '.started.json'}[change]
        path = e.out / ('sample-a.generate-first.flare_sunburst' + suffix)
        if change == 'first_image':
            path.write_bytes(e.final.data)
        else:
            value = json.loads(path.read_text())
            value['request']['outputSize'] = '2048x2048'
            path.write_text(json.dumps(value))
    else:
        if change == 'deadline': e.manifest['qcTimeoutSeconds'] = 60
        if change == 'model': e.manifest['models']['flare_sunburst']['first'] = 'gpt-image-other'
        if change == 'size': e.manifest['cases'][0]['outputSize'] = '2048x2048'
        if change == 'quality': e.manifest['quality'] = 'high'
        e.path.write_text(json.dumps(e.manifest))
    count = len(e.calls)
    with pytest.raises(ValueError): run(e, monkeypatch, 'qc-first')
    assert len(e.calls) == count
    assert not (e.out / 'sample-a.qc-first.flare_sunburst.started.json').exists()


@pytest.mark.parametrize('failure', ['provider', 'dimensions', 'cancel'])
def test_failed_or_cancelled_generation_has_terminal_receipt_and_no_resubmission(evidence, external, monkeypatch, failure):
    e = evidence
    attempts = []
    async def bad(self, *args, **kwargs):
        attempts.append(1)
        if failure == 'cancel': raise asyncio.CancelledError()
        if failure == 'provider': raise RuntimeError('SECRET provider failure')
        from test_wearshot_contract_v2 import png
        return SimpleNamespace(image=png().data, mime='image/png', usage={}, latency_ms=1)
    monkeypatch.setattr(GeminiImageClient, 'generate_content_image', bad)
    if failure == 'cancel':
        with pytest.raises(asyncio.CancelledError): run(e, monkeypatch, 'generate-first')
    else:
        assert run(e, monkeypatch, 'generate-first')[0]['status'] == 'failed'
    receipt = json.loads((e.out / 'sample-a.generate-first.flare_sunburst.receipt.json').read_text())
    assert receipt['status'] == ('cancelled' if failure == 'cancel' else 'failed')
    assert 'SECRET' not in json.dumps(receipt)
    with pytest.raises(ValueError): run(e, monkeypatch, 'generate-first')
    assert attempts == [1]
    assert not (e.out / 'sample-a.generate-first.flare_sunburst.png').exists()


@pytest.mark.parametrize('failure', ['qc_unavailable', 'qc_unjudgeable', 'repair_provider', 'final_fail'])
def test_failed_continuation_holds_without_fallback_and_preserves_first(evidence, external, monkeypatch, failure):
    e = evidence
    run(e, monkeypatch, 'generate-first')
    if failure.startswith('qc_'):
        original = vision_llm._call_gpt
        async def bad(*args):
            if failure == 'qc_unavailable': raise RuntimeError('SECRET')
            value = await original(*args)
            if 'garments' in value:
                value['globalChecks']['capture'] = mark('UNJUDGEABLE')
            return value
        monkeypatch.setattr(vision_llm, '_call_gpt', bad)
    qc = run(e, monkeypatch, 'qc-first')[0]
    if failure == 'qc_unavailable':
        assert qc['status'] == 'failed' and not qc['releaseAllowed']
        with pytest.raises(ValueError): run(e, monkeypatch, 'conditional-repair')
    elif failure == 'qc_unjudgeable':
        assert run(e, monkeypatch, 'conditional-repair')[0]['decision'] == 'hold'
    else:
        if failure == 'repair_provider':
            async def bad_generation(*args, **kwargs): raise RuntimeError('SECRET')
            monkeypatch.setattr(GeminiImageClient, 'generate_content_image', bad_generation)
        repair = run(e, monkeypatch, 'conditional-repair')[0]
        if failure == 'repair_provider':
            assert repair['status'] == 'failed'
            with pytest.raises(ValueError): run(e, monkeypatch, 'qc-final')
        else:
            original = vision_llm._call_gpt
            async def bad_final(*args):
                value = await original(*args)
                if 'garments' in value: value['globalChecks']['capture'] = mark('FAIL')
                return value
            monkeypatch.setattr(vision_llm, '_call_gpt', bad_final)
            assert not run(e, monkeypatch, 'qc-final')[0]['releaseAllowed']
        with pytest.raises(ValueError): run(e, monkeypatch, 'conditional-repair')
    assert (e.out / 'sample-a.generate-first.flare_sunburst.png').read_bytes() == e.first.data
    report = run(e, monkeypatch, 'report')
    row = next(r for r in report['cases'] if r['arm'] == 'flare_sunburst')
    assert row['outcome'] in ('held', 'held_or_unreconciled')
    assert row['timing']['acceptedResultTimeMs'] is None


def test_cli_default_does_not_load_credentials(evidence, external, monkeypatch, capsys):
    def forbidden(): pytest.fail('read-only CLI loaded live settings')
    monkeypatch.setattr(harness(), 'load_settings', forbidden)
    assert harness().main(['--manifest', str(evidence.path), '--output-dir', str(evidence.out)]) == 0
    assert json.loads(capsys.readouterr().out)[0]['status'] == 'dry-run'
    assert not evidence.out.exists() and evidence.calls == []


@pytest.mark.parametrize('arm,first_model,repair_model', [
    ('image2', 'gpt-image-2', 'gpt-image-2'),
    ('flare_sunburst', 'gpt-image-2.5-flare-2026-09-08', 'gpt-image-2.5-sunburst-2026-09-08'),
])
def test_real_generator_and_adapter_submit_frozen_native_transport(evidence, monkeypatch, arm, first_model, repair_model):
    import base64
    import hashlib
    import httpx
    e = evidence
    submissions = []
    async def post(self, url, **kwargs):
        assert url == 'https://api.openai.com/v1/images/edits'
        submissions.append(kwargs)
        image = e.first if len(submissions) == 1 else e.final
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image.data).decode()}], 'usage': {'total_tokens': 99}})
    async def judge(settings, model, prompt, images, schema, timeout):
        if 'garments' not in schema['properties']:
            return dict(viewAdequate=True, selectedFaceRelation='clear_target', strongestTargetEvidence='Target eyes',
                        strongestSourceEvidence='Source absent', remainingAmbiguity='None')
        raw = observed(e.contract)
        if images[-1] == e.first: raw['globalChecks']['capture'] = mark('FAIL')
        raw['protectedChecks'] = {k: mark() for k in schema['properties']['protectedChecks']['properties']}
        return raw
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    first = run(e, monkeypatch, 'generate-first', arm)[0]
    run(e, monkeypatch, 'qc-first', arm)
    repair = run(e, monkeypatch, 'conditional-repair', arm)[0]
    assert first['status'] == repair['status'] == 'completed'
    assert len(submissions) == 2
    for call, receipt, model, refs in [(submissions[0], first, first_model, [r.image for r in e.contract.references]),
                                     (submissions[1], repair, repair_model, [e.first, *[r.image for r in e.contract.references]])]:
        assert call['data'] == dict(model=model, prompt=call['data']['prompt'], size='1360x2048', quality='medium', output_format='png', n='1')
        assert [(file[1][1], file[1][2]) for file in call['files']] == [(r.data, r.mime) for r in refs]
        assert hashlib.sha256(call['data']['prompt'].encode()).hexdigest() == receipt['actualRequest']['promptSha256']
        assert receipt['actualRequest']['model'] == model
        assert receipt['usage'] == {'total_tokens': 99}
        assert receipt['providerLatencyMs'] >= 0


def _assert_worker_override_and_repair(monkeypatch, repair_override):
    from app.workers import detail_page_job as dpj
    from conftest import fake_worker_app, worker_job
    from test_cut_output_qc_wiring import _RecordingR2
    contract, first, final = packet(), canvas('orange'), canvas('green')
    models, events = [], []
    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            models.append(model)
            image = first if len(models) == 1 else final
            return SimpleNamespace(image=image.data, mime=image.mime)
    async def judge(settings, model, prompt, images, schema, timeout):
        if 'garments' not in schema['properties']:
            return dict(viewAdequate=True, selectedFaceRelation='clear_target', strongestTargetEvidence='Target eyes',
                        strongestSourceEvidence='Source absent', remainingAmbiguity='None')
        raw = observed(contract)
        if images[-1] == first: raw['globalChecks']['capture'] = mark('FAIL')
        raw['protectedChecks'] = {k: mark() for k in schema['properties']['protectedChecks']['properties']}
        return raw
    async def emit(*args): events.append(args[-1])
    async def intent(*args, **kwargs): return 'intent'
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    monkeypatch.setattr(dpj, '_emit', emit)
    monkeypatch.setattr(dpj.repo, 'create_ai_output_cleanup_intent', intent)
    settings = make_settings(openai_api_key='test', model_detail_cut='gpt-image-2', r2_bucket='test',
        wearshot_generation_model='gpt-image-2.5-flare-2026-09-08', wearshot_repair_model=repair_override)
    original = asdict(settings)
    r2 = _RecordingR2(events)
    app = fake_worker_app(settings, r2=r2, gemini=Provider())
    refs = [r.image for r in contract.references]
    item = ({'id': 'b1', 'cutType': 'styling', 'shot': 'medium'}, refs, '', False, [], None, False,
            None, None, False, refs, contract)
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), [item], {'clothing_type': 'top'}, {}))
    assert len(r2.saved) == 1
    assert models == ['gpt-image-2.5-flare-2026-09-08', repair_override or 'gpt-image-2.5-flare-2026-09-08']
    assert asdict(settings) == original
    assert result[1][0]['metadata']['wearshotContract']['fingerprint'] == contract.fingerprint


@pytest.mark.parametrize('repair_override', [None, 'gpt-image-2.5-sunburst-2026-09-08'])
def test_worker_v2_routes_override_and_one_repair_without_changing_legacy_settings(monkeypatch, repair_override):
    _assert_worker_override_and_repair(monkeypatch, repair_override)


@pytest.mark.parametrize('kind', ['unknown_only', 'mixed', 'focus_unknown', 'focus_fail', 'focus_wrong_hash'])
def test_opt_in_repair_plan_uses_only_known_failures_and_keeps_unknown_unprotected(monkeypatch, kind):
    from app.agents import wearshot_qc
    from app.agents.wearshot_contract import image_sha256
    contract, image = packet(), canvas()
    raw = observed(contract)
    if kind in ('unknown_only', 'mixed'):
        raw['globalChecks']['capture'] = mark('UNJUDGEABLE')
    if kind in ('mixed', 'focus_unknown', 'focus_wrong_hash'):
        raw['globalChecks']['light'] = mark('FAIL')
    result = wearshot_qc.validate(raw, contract, image)
    result['identityReview'] = dict(status='UNJUDGEABLE' if kind == 'focus_unknown' else 'FAIL' if kind in ('focus_fail', 'focus_wrong_hash') else 'PASS',
        candidateSha256='0' * 64 if kind == 'focus_wrong_hash' else image_sha256(image))
    if kind == 'unknown_only':
        with pytest.raises(ValueError): rt.derive_repair_plan(contract, image, result, known_failures_only=True)
        assert rt.derive_repair_plan(contract, image, result).failed_axes == ('capture',)
        return
    plan = rt.derive_repair_plan(contract, image, result, known_failures_only=True)
    assert plan.failed_axes == (('identity',) if kind == 'focus_fail' else ('light',))
    if kind == 'mixed': assert 'capture' not in plan.approved_axes
    if kind.startswith('focus_'): assert 'identity' not in plan.approved_axes
    if kind in ('focus_unknown', 'focus_wrong_hash'):
        assert 'identity' in rt.derive_repair_plan(contract, image, result).failed_axes


def test_source_locked_worker_holds_unknown_only_without_second_image(evidence, external, monkeypatch):
    from app.workers import detail_page_job as dpj
    from conftest import fake_worker_app, worker_job
    from test_cut_output_qc_wiring import _RecordingR2
    e = evidence
    refs = e.contract.references
    e.contract = packet(references=tuple(sorted(refs, key=lambda r: r.key != 'example')), directing_mode='source_locked_v1')
    original = vision_llm._call_gpt
    async def unknown(*args):
        raw = await original(*args)
        if 'garments' in raw: raw['globalChecks']['capture'] = mark('UNJUDGEABLE')
        return raw
    events = []
    async def emit(*args): events.append(args[-1])
    monkeypatch.setattr(vision_llm, '_call_gpt', unknown)
    monkeypatch.setattr(dpj, '_emit', emit)
    settings = make_settings(openai_api_key='test', r2_bucket='test',
        wearshot_generation_model='gpt-image-2.5-flare-2026-09-08', detail_cut_image_size='2K')
    r2 = _RecordingR2(events)
    app = fake_worker_app(settings, r2=r2, gemini=GeminiImageClient(settings))
    images = [r.image for r in e.contract.references]
    item = ({'id': 'b1', 'cutType': 'styling', 'shot': 'medium'}, images, '', False, [], None, False,
            None, None, False, images, e.contract)
    assert asyncio.run(dpj._gen_wearshot_cut(app, worker_job(), item, {}, settings)) is None
    assert len([c for c in e.calls if c[0] == 'generate']) == 1
    assert r2.saved == []
    assert events[-1]['status'] == 'cut_failed'


def test_fast_held_decisions_do_not_improve_accepted_result_summary(evidence, external, monkeypatch):
    from copy import deepcopy
    e = evidence
    second = deepcopy(e.manifest['cases'][0])
    second['id'] = 'sample-b'
    e.manifest['cases'].append(second)
    e.path.write_text(json.dumps(e.manifest))
    for mode in ('generate-first', 'qc-first', 'conditional-repair', 'qc-final'):
        run(e, monkeypatch, mode)
    original = vision_llm._call_gpt
    async def unknown(*args):
        raw = await original(*args)
        if 'garments' in raw: raw['globalChecks']['capture'] = mark('UNJUDGEABLE')
        return raw
    monkeypatch.setattr(vision_llm, '_call_gpt', unknown)
    for mode in ('generate-first', 'qc-first', 'conditional-repair'):
        run(e, monkeypatch, mode, case='sample-b')
    report = run(e, monkeypatch, 'report')
    rows = {r['caseId']: r for r in report['cases'] if r['arm'] == 'flare_sunburst'}
    assert rows['sample-a']['timing']['withQcDecisionTimeMs'] == 140000
    assert rows['sample-a']['timing']['acceptedResultTimeMs'] == 140000
    assert rows['sample-b']['outcome'] == 'held'
    assert rows['sample-b']['timing']['withQcDecisionTimeMs'] == 63000
    assert rows['sample-b']['timing']['acceptedResultTimeMs'] is None
    summary = report['summary']['flare_sunburst']
    assert summary['outcomeCounts'] == {'released': 1, 'held': 1}
    assert summary['withQcDecisionTimeMs'] == dict(count=2, median=101500, range=[63000, 140000])
    assert summary['acceptedResultTimeMs'] == dict(count=1, median=140000, range=[140000, 140000])
    assert report['summary']['image2']['outcomeCounts'] == {'not_started': 2}
    assert report['summary']['image2']['acceptedResultTimeMs'] == dict(count=0, median=None, range=None)


def test_older_profile_retains_strict_three_argument_repair_callable(monkeypatch):
    original = rt.derive_repair_plan
    calls = []
    def older_planner(contract, candidate, result):
        calls.append(contract.directing_mode)
        return original(contract, candidate, result)
    monkeypatch.setattr(rt, 'derive_repair_plan', older_planner)
    _assert_worker_override_and_repair(monkeypatch, None)
    assert calls == [None]
