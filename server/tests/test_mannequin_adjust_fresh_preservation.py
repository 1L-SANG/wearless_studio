"""Same user adjustment contract survives the fresh-render fallback after edit depth two."""
import dataclasses
import json
from types import SimpleNamespace

import pytest

from app.agents import mannequin_fit_qc
from app.services.qc import QcResult
from app.workers import mannequin_job as worker
from test_mannequin_adjust_edit_path import PROFILE, _parent, _run_worker, _PNG_1PX
from test_mannequin_quality_repair import rated


def _actual_candidate(monkeypatch, report, mode='enforce'):
    real = worker._run_candidate
    seen = SimpleNamespace(generation=[], qc=[], puts=[])

    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            seen.generation.append(dict(model=model, prompt=prompt, images=images, size=size))
            return SimpleNamespace(image=_PNG_1PX, mime='image/png')

    async def image_judge(settings, sources, current, **kwargs):
        seen.qc.append(dict(sources=sources, current=current, **kwargs))
        value = dict(report)
        if kwargs.get('before_image') is None:
            for key in ('target_resolved', 'protected_regions_unchanged', 'regression_reasons'):
                value.pop(key, None)  # Match the actual unpaired schema, not a fake paired answer.
        return value

    async def axis_judge(*args, **kwargs):
        return dict(identityPass=True, mismatches=[], axisPass=[{'axis': 'fit', 'target': 'slim',
                    'pass': True, 'visible': True, 'observedLandmark': 'declared fit observed'}])

    async def base_judge(*args, **kwargs):
        return {'poseFrameMatch': {'decision': 'pass'}}

    async def series_judge(*args, **kwargs):
        return None

    async def style_refs(*args, **kwargs):
        return [], []

    monkeypatch.setattr(worker.image_qc, 'verdict', image_judge)
    monkeypatch.setattr(mannequin_fit_qc, 'verdict', axis_judge)
    monkeypatch.setattr(worker, '_apply_base_fidelity_qc', base_judge)
    monkeypatch.setattr(worker, '_apply_series_qc', series_judge)
    monkeypatch.setattr(worker, '_load_style_refs', style_refs)
    monkeypatch.setattr(worker.qc, 'evaluate_mannequin_qc', lambda data: QcResult('pass', [], {}))
    monkeypatch.setattr(worker.qc, 'evaluate_canvas_alpha_qc', lambda data: QcResult('pass', [], {}))

    async def execute(**kwargs):
        app = kwargs['app']
        app.state.settings = dataclasses.replace(app.state.settings, image_qc=mode,
            mannequin_max_attempts=1, mannequin_axis_qc='enforce', mannequin_base_fidelity_qc='enforce',
            mannequin_specialist_qc='off', mannequin_untuck_pass='off', mannequin_bust_pass='off',
            mannequin_draft_reuse='off')
        app.state.gemini = Provider()
        app.state.r2.put_bytes = lambda key, data, mime, cache=None: seen.puts.append(data)
        return await real(**kwargs)

    return execute, seen


@pytest.mark.parametrize('mode', ['enforce', 'shadow'])
def test_depth_cap_fresh_keeps_parent_for_studio_and_existing_paired_qc(monkeypatch, mode):
    execute, seen = _actual_candidate(monkeypatch, rated(95), mode)
    calls, storage = _run_worker(monkeypatch, parent=_parent(editDepth=2),
        candidate_runner=execute, assert_success=False, settings_overrides={'image_qc': mode})
    assert len(calls['success']) == 1 and not calls['failure']
    assert calls['run'][0]['generation_path'] == 'fresh'
    assert calls['run'][0]['parent_cut_img'] is None
    assert len(seen.generation) == len(seen.qc) == 1
    assert [image.data for image in seen.generation[0]['images']] == [b'base', b'product', b'parent-cut']
    assert '3. BEFORE STUDIO' in seen.generation[0]['prompt']
    assert 'not garment identity or fit evidence' in seen.generation[0]['prompt']
    assert 'CURRENT CUT' not in seen.generation[0]['prompt']
    assert seen.qc[0]['before_image'].data == b'parent-cut'
    assert 'MAIN PRODUCT' in seen.qc[0]['edit_goal'] and 'fit:' in seen.qc[0]['edit_goal']
    assert seen.qc[0]['fit_profile'] == PROFILE
    metadata = calls['success'][0]['candidates'][0]['generation_metadata']
    assert metadata['generationPath'] == 'fresh' and metadata['editDepth'] == 0
    assert metadata['parentCutId'] is None
    assert seen.puts == [_PNG_1PX]


@pytest.mark.parametrize('mode', ['enforce', 'shadow'])
@pytest.mark.parametrize('kind', ['source_uncertain', 'target_unresolved', 'preservation_unknown', 'pair_missing'])
def test_depth_cap_cannot_auto_replace_with_unverified_high_score(monkeypatch, kind, mode):
    report = rated(95, **({'construction': 'uncertain'} if kind == 'source_uncertain' else {}))
    if kind == 'target_unresolved':
        report['target_resolved'] = False
    elif kind == 'preservation_unknown':
        report['protected_regions_unchanged'] = False
    elif kind == 'pair_missing':
        report.pop('target_resolved', None)
    execute, seen = _actual_candidate(monkeypatch, report, mode)
    calls, _ = _run_worker(monkeypatch, parent=_parent(editDepth=2),
        candidate_runner=execute, assert_success=False, settings_overrides={'image_qc': mode})
    assert not calls['success'] and len(calls['failure']) == 1
    assert calls['failure'][0]['code'] == 'mannequin_quality_failed'
    assert calls['failure'][0]['metadata']['reason'] == 'adjustment_preservation_rejected'
    assert len(seen.generation) == len(seen.qc) == 1
    assert seen.qc[0]['before_image'].data == b'parent-cut'
    assert seen.puts == []


def test_depth_cap_missing_before_stops_before_generation_qc_or_storage(monkeypatch):
    execute, seen = _actual_candidate(monkeypatch, rated(95))
    calls, _ = _run_worker(monkeypatch,
        parent={**_parent(editDepth=2), 'r2_key': 'private-source-token.png'},
        candidate_runner=execute, assert_success=False, settings_overrides={'image_qc': 'enforce'})
    assert not calls['run'] and not calls['success'] and len(calls['failure']) == 1
    assert calls['failure'][0]['metadata']['reason'] == 'adjustment_preservation_unavailable'
    assert 'private-source-token' not in json.dumps(calls['failure'])
    assert seen.generation == seen.qc == seen.puts == []


@pytest.mark.parametrize('parent,match_id', [
    (_parent(editDepth=2, profileCategory='pants'), None),
    (_parent(editDepth=2, profileGender='men'), None),
    (_parent(editDepth=2, matchItemId='different-match'), 'match-1'),
    ({**_parent(), 'generation_metadata': {}}, None),
])
def test_actual_incompatible_inputs_keep_fresh_without_claiming_preservation(monkeypatch, parent, match_id):
    calls, storage = _run_worker(monkeypatch, parent=parent, current_match_id=match_id)
    run = calls['run'][0]
    assert run['generation_path'] == 'fresh' and run['parent_cut_img'] is None
    assert run.get('preservation_before_image') is None
    assert 'parent.png' not in storage.reads


@pytest.mark.parametrize('missing', [False, True])
def test_depth_cap_off_keeps_zero_product_qc_and_no_additional_before_read(monkeypatch, missing):
    execute, seen = _actual_candidate(monkeypatch, rated(95, construction='uncertain'), 'off')
    parent = _parent(editDepth=2)
    if missing:
        parent['r2_key'] = 'private-source-token.png'
    calls, storage = _run_worker(monkeypatch, parent=parent, candidate_runner=execute,
        assert_success=False, settings_overrides={'image_qc': 'off'})
    assert len(calls['success']) == 1 and not calls['failure']
    assert calls['run'][0]['generation_path'] == 'fresh'
    assert calls['run'][0]['parent_cut_img'] is None
    assert calls['run'][0].get('preservation_before_image') is None
    assert parent['r2_key'] not in storage.reads
    assert len(seen.generation) == 1 and seen.qc == []
    assert [image.data for image in seen.generation[0]['images']] == [b'base', b'product']
    assert seen.puts == [_PNG_1PX]
