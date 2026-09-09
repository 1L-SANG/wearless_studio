import asyncio
from types import SimpleNamespace
from copy import deepcopy

import pytest
from app.agents import wearshot_runtime as rt, vision_llm
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job
from test_wearshot_contract_v2 import packet, png
from test_wearshot_qc_v2 import observed, mark
from test_cut_output_qc_wiring import _RecordingR2
from test_wearshot_runtime_v2 import canvas


@pytest.mark.parametrize('failure', ['none', 'fixed', 'length', 'detail', 'variation', 'protected', 'face',
                                    'stage1_wrong_size', 'stage1_malformed', 'stage2_wrong_size', 'stage2_malformed'])
def test_worker_full_v2_release_before_upload_with_one_exact_repair(monkeypatch, failure):
    contract, base, final = packet(), canvas('orange'), canvas('green')
    generations, judgments, events = [], [], []
    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            generations.append(images)
            image = base if len(generations) == 1 else final
            if failure.startswith(f'stage{len(generations)}_'):
                image = png('green') if failure.endswith('wrong_size') else SimpleNamespace(data=b'bad', mime='image/png')
            return SimpleNamespace(image=image.data, mime=image.mime)
    async def judge(settings, model, prompt, images, schema, timeout):
        if 'garments' not in schema['properties']:
            return dict(viewAdequate=True, selectedFaceRelation='mixed' if failure == 'face' else 'clear_target',
                        strongestTargetEvidence='Visible eye shape follows target.', strongestSourceEvidence='Source nose differs.',
                        remainingAmbiguity='No hidden facial claims.')
        judgments.append(images)
        raw = observed(contract)
        if failure in {'length', 'protected', 'fixed', 'stage2_wrong_size', 'stage2_malformed'}:
            raw['garments'][1]['checks']['length'] = mark('FAIL' if len(judgments) == 1 or failure == 'length' else 'PASS')
        if failure == 'detail': raw['garments'][0]['details'][0]['status'] = 'FAIL'
        if failure == 'variation': raw['variation']['poseChanged'] = False
        raw['protectedChecks'] = {key: mark('FAIL' if failure == 'protected' and key == 'garment:top:color' else 'PASS')
                                 for key in schema['properties']['protectedChecks']['properties']}
        return raw
    async def emit(*args): events.append(args[-1])
    async def intent(*args, **kw): return 'intent'
    async def forbidden(*args, **kw): raise AssertionError('legacy seller-only best_of called')
    monkeypatch.setattr(dpj.image_qc, 'best_of', forbidden)
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    monkeypatch.setattr(dpj, '_emit', emit)
    monkeypatch.setattr(dpj.repo, 'create_ai_output_cleanup_intent', intent)
    r2 = _RecordingR2(events)
    settings = make_settings(openai_api_key='test', model_detail_cut='gpt-image-2', cut_output_qc_mode='off',
                             cut_output_release_policy='off', r2_bucket='test', wearshot_repair_model='gpt-image-2-repair')
    app = fake_worker_app(settings, r2=r2, gemini=Provider())
    refs = [ref.image for ref in contract.references]
    prepared = ({'id': 'b1', 'cutType': 'styling', 'shot': 'medium'}, refs, '', False, [], None, False,
                None, None, False, refs, contract)
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), [prepared], {'clothing_type': 'top'}, {}))
    assert len(r2.saved) == (1 if failure in {'none', 'fixed'} else 0)
    if failure.startswith('stage'):
        stage = int(failure[5])
        assert len(generations) == stage
        assert len(judgments) == stage - 1
        return
    assert len(generations) == (1 if failure == 'none' else 2)
    assert generations[0] == refs
    assert judgments[0] == [*refs, base]
    if failure != 'none':
        assert generations[1] == [base, *refs]
        assert judgments[1] == [base, *refs, final]
    else:
        assert result[1][0]['metadata']['wearshotContract']['fingerprint'] == contract.fingerprint


def test_runtime_binds_seller_facts_and_tone_hash_and_typed_scope(monkeypatch):
    assert hasattr(rt, 'build_contract'), 'v2 runtime binder missing'
    from test_confirmed_gpt_runtime import _contract
    from app.agents.wearshot_contract import image_sha256
    seller, target, face, example = png('white'), png('blue'), png('yellow'), png('gray')
    scope = {'allSha256': image_sha256(example), 'faceVisibility': 'hidden',
             'garmentScopes': {'top': [], 'bottom': ['length']}}
    metadata = SimpleNamespace(shot='medium', requested_framing='Neck through complete top hem and upper trousers.',
                               face_exposure='Head outside frame.')
    kwargs = dict(target_id='target', target_asset_id='tone', target_image=target,
        seller_images=(('Front', seller, 'seller-id'),), evidence_contract=_contract(seller.data),
        matching=(('m1', 'bottom', target, 'matching-id', png('black'), 'matching-seller'),),
        face_image=face, body_image=None, model_id='mE', example_image=example,
        directing=metadata, scope=scope, clothing_type='top', variation_axis='pose')
    contract = rt.build_contract(**kwargs)
    assert contract.target.essentials[0].code == 'front_shape'
    assert contract.target.essentials[0].evidence_keys == ('target-seller-1',)
    assert contract.matching[0].out_of_frame_axes == ('length',)
    assert contract.frame_lock.face_visibility == 'hidden'
    assert contract.references[0].image == target
    for change in ({'example_image': png('black')}, {'clothing_type': 'dress'}, {'seller_images': (('Front', png('black'), 'seller-id'),)}):
        with pytest.raises(ValueError): rt.build_contract(**(kwargs | change))
    metadata.direction_description = 'Front-family leaning seated posture.'
    metadata.pose_semantics = {'action': 'leaning against a railing', 'gaze': 'off camera'}
    changed = rt.build_contract(**kwargs)
    assert changed.fingerprint != contract.fingerprint
    assert 'leaning against a railing' in changed.frame_lock.description


@pytest.mark.parametrize('drift', ['none', 'analysis', 'source_hash', 'duplicate'])
def test_real_route_to_worker_binds_selected_tone_matching_and_no_fallback(client, make_token, monkeypatch, drift):
    from test_wearshot_api_v2 import arrange, body, TONE, TARGET, MATCH
    from app import routes
    from app.agents.wearshot_contract import image_sha256
    from conftest import auth_headers
    state = arrange(monkeypatch)
    if drift == 'duplicate':
        state['storyboard'].append({**state['storyboard'][0], 'id': 'b2'})
    image_map = {TONE: png('blue'), TARGET: png('red'), MATCH: png('black'), 'seller-target': png('white'),
                 'seller-match': png('gray'), 'model-face': png('yellow'), 'model-body': png('green')}
    original_owned = routes.repo.get_owned_mannequin_asset
    original_hash = image_sha256(image_map[TARGET])
    async def owned(*args):
        row = await original_owned(*args)
        if row['id'] == TONE: row['metadata']['sourceHash'] = original_hash
        return row
    async def asset(conn, uid, aid): return {'id': aid, 'r2_key': aid, 'mime_type': 'image/png'}
    def model_sheets(*args):
        return tuple({'key': key, 'mime': image_map[key].mime, 'bucket': 'public',
                      'byteLength': len(image_map[key].data), 'sha256': image_sha256(image_map[key])}
                     for key in ('model-face', 'model-body'))
    original_catalog = rt.catalog_projection
    example = png('purple')
    def catalog(*args):
        value = original_catalog(*args)
        for row in value.values():
            row['scope']['allSha256'] = image_sha256(example)
            row['directing']['all_sha256'] = image_sha256(example)
        return value
    async def load_example(*args, **kw): return example
    captured = {'provider': [], 'failed': [], 'success': []}
    original_build = rt.build_contract
    def build(**kw):
        contract = original_build(**kw)
        captured['contract'] = contract
        return contract
    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kw):
            captured['provider'].append(images)
            return SimpleNamespace(image=canvas('orange').data, mime='image/png')
    class Storage(_RecordingR2):
        def get_bytes(self, key): return image_map[key].data
    async def judgment(settings, model, prompt, images, schema, timeout):
        if 'garments' in schema['properties']:
            return observed(captured['contract'])
        return dict(viewAdequate=True, selectedFaceRelation='clear_target', strongestTargetEvidence='Target eye shape.',
                    strongestSourceEvidence='Source mouth differs.', remainingAmbiguity='No ambiguity.')
    async def finalize(conn, **kw): captured['success'].append(kw); return {'editor_blocks': [], 'available': 99}
    async def failure(conn, **kw): captured['failed'].append(kw)
    async def intent(*a, **kw): return 'intent'
    async def emit(*a): pass
    async def latest(*a): raise AssertionError('worker changed active tone')
    def assemble(storyboard, cut_results, *args, **kwargs):
        captured['assembled_cuts'] = cut_results
        return []
    monkeypatch.setattr(routes.repo, 'get_owned_mannequin_asset', owned)
    monkeypatch.setattr(routes.repo, 'get_asset_for_user', asset)
    monkeypatch.setattr(dpj.cut_generator, 'resolve_confirmed_gpt_direction_sheets', model_sheets)
    monkeypatch.setattr(rt, 'catalog_projection', catalog)
    monkeypatch.setattr(rt, 'build_contract', build)
    monkeypatch.setattr(dpj.cut_generator, 'load_example_image', load_example)
    monkeypatch.setattr(vision_llm, '_call_gpt', judgment)
    monkeypatch.setattr(dpj.repo, 'finalize_detail_page_success', finalize)
    monkeypatch.setattr(dpj.repo, 'finalize_detail_page_failure', failure)
    monkeypatch.setattr(dpj.repo, 'create_ai_output_cleanup_intent', intent)
    monkeypatch.setattr(dpj, '_emit', emit)
    monkeypatch.setattr(dpj.page_assembler, 'assemble', assemble)
    response = client.post('/v1/projects/p1/detail-page:generate', json=body(), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    payload = state['create'][0]['payload']
    monkeypatch.setattr(dpj.repo, 'list_mannequin_cuts', latest)
    if drift == 'analysis': state['analysis']['selectedModelId'] = 'mB'
    if drift == 'source_hash': image_map[TARGET] = png('white')
    r2 = Storage([])
    app = fake_worker_app(make_settings(openai_api_key='test', model_detail_cut='gpt-image-2', r2_bucket='test'), r2=r2, gemini=Provider())
    asyncio.run(dpj.run_detail_page_job(app, worker_job(payload)))
    if drift in {'analysis', 'source_hash'}:
        assert captured['provider'] == []
        assert captured['failed']
        assert captured['failed'][0]['code'] == 'wearshot_v2_preflight_hold'
        assert r2.saved == []
    else:
        assert captured['failed'] == []
        assert len(captured['success']) == 1
        refs = captured['provider'][0]
        assert refs[0] == image_map[TONE]
        assert image_map[MATCH] in refs
        assert image_map[TARGET] not in refs
        assert captured['contract'].capture_profile == 'soft'
        assert len(r2.saved) == 1
        if drift == 'duplicate':
            assert state['reserves'] == [1]
            assert len(captured['provider']) == 1
            assert len(captured['success'][0]['cut_assets']) == 1
            assert len(captured['assembled_cuts']) == 2
            assert {cut['blockId'] for cut in captured['assembled_cuts']} == {'b1', 'b2'}
            assert len({cut['imageUrl'] for cut in captured['assembled_cuts']}) == 1
