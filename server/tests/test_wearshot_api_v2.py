from copy import deepcopy
import asyncio

import pytest
from app import routes
from app.agents import wearshot_runtime as rt
from conftest import auth_headers, patch_route_db
from test_confirmed_gpt_runtime import _contract
from test_wearshot_contract_v2 import png

TARGET = '11111111-1111-4111-8111-111111111111'
TONE = '22222222-2222-4222-8222-222222222222'
MATCH = '33333333-3333-4333-8333-333333333333'
EXAMPLE = 'ex_styling_women_top_full_snapshot_03'


def arrange(monkeypatch, *, existing=False, reuse=None, missing=False):
    state = dict(project={'id': 'p1', 'selected_mannequin_id': 'A-1'},
        analysis={'selectedModelId': 'mE', 'confirmedGptProductEvidence': _contract(png('white').data)},
        product={'clothing_type': 'top', 'colors': [{'id': 'base', 'isBase': True, 'images': [{'slot': 'Front', 'id': 'seller-target'}]}]},
        storyboard=[{'id': 'b1', 'source': 'ai', 'cutType': 'styling', 'shot': 'full',
                     'direction': 'front', 'refScope': 'all', 'exampleId': EXAMPLE, 'matchIds': ['m1']}],
        create=[], reserves=[])
    async def project(*a): return state['project']
    async def analysis(*a): return state['analysis']
    async def product(*a): return state['product']
    async def storyboard(*a): return state['storyboard']
    async def cuts(*a): return [{'candidate': 'A', 'version': 1, 'asset_id': TARGET, 'active_asset_id': TONE}]
    async def owned(conn, uid, aid):
        if missing: return None
        return {'id': aid, 'project_id': 'p1' if aid != MATCH else 'p2', 'r2_key': aid,
            'mime_type': 'image/png', 'metadata': {'type': 'mannequinToneAdjusted', 'sourceCutId': 'A-1',
                'sourceAssetId': TARGET, 'sourceHash': 'a' * 64} if aid == TONE else {},
            'source_asset_id': TARGET if aid == TONE else aid, 'source_cut_id': 'A-1',
            'source_r2_key': TARGET if aid == TONE else aid, 'source_mime_type': 'image/png',
            'source_metadata': {'matchItemId': 'm1'}}
    async def match_asset(*a): return 'seller-match'
    async def match_meta(*a): return {'clothing_type': 'bottom', 'category': '팬츠', 'length': 'full', 'owner_user_id': None}
    async def asset(conn, uid, aid): return {'id': aid, 'r2_key': aid, 'mime_type': 'image/png'}
    async def editor(*a): return [{'id': 'out'}] if existing else []
    async def create(conn, **kwargs):
        state['create'].append(kwargs)
        return ({'id': 'job', 'payload': reuse} if reuse is not None else {'id': 'job'}), reuse is None
    async def reserve(*a): state['reserves'].append(a[-1]); return 100
    for name, func in [('get_project', project), ('get_analysis', analysis), ('get_product', product),
        ('get_storyboard', storyboard), ('list_mannequin_cuts', cuts), ('get_owned_mannequin_asset', owned),
        ('get_matching_item_asset', match_asset), ('get_matching_item_metadata', match_meta),
        ('get_asset_for_user', asset), ('get_editor_blocks', editor), ('create_job', create), ('reserve_credits', reserve)]:
        monkeypatch.setattr(routes.repo, name, func, raising=False)
    patch_route_db(monkeypatch, routes)
    return state


def body():
    return {'contractVersion': 'approved_mannequin_v2', 'matchingMannequinAssets': {'m1': MATCH}, 'variationAxis': 'pose', 'captureProfile': 'soft'}


@pytest.mark.parametrize('change', ['extra', 'uuid', 'version', 'axis', 'profile'])
def test_typed_v2_request_rejects_untrusted_fields(client, make_token, change):
    data = body()
    if change == 'extra': data['instructions'] = 'Make it looser'
    if change == 'uuid': data['matchingMannequinAssets']['m1'] = 'anything'
    if change == 'version': data['contractVersion'] = 'generic'
    if change == 'axis': data['variationAxis'] = 'color'
    if change == 'profile': data['captureProfile'] = 'cinematic'
    response = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert response.status_code == 422


def test_route_snapshots_active_tone_and_explicit_matching_before_reservation(client, make_token, monkeypatch):
    state = arrange(monkeypatch)
    response = client.post('/v1/projects/p1/detail-page:generate', json=body(), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    payload = state['create'][0]['payload']['wearshotV2']
    assert payload['target']['id'] == TONE
    assert payload['target']['source_asset_id'] == TARGET
    assert payload['matching']['m1']['id'] == MATCH
    assert payload['request'] == body()
    assert len(payload['selectionFingerprint']) == 64
    assert state['reserves'] == [1]


@pytest.mark.parametrize('scenario', ['unused', 'missing', 'existing', 'reuse'])
def test_invalid_or_unprovable_v2_cannot_reserve_or_return_other_output(client, make_token, monkeypatch, scenario):
    state = arrange(monkeypatch, existing=scenario == 'existing', reuse={'mode': 'generate'} if scenario == 'reuse' else None,
                    missing=scenario == 'missing')
    data = body()
    if scenario == 'unused': data['matchingMannequinAssets']['unselected'] = TARGET
    response = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert response.status_code == 409, response.text
    assert state['reserves'] == []
    if scenario != 'reuse': assert state['create'] == []


def test_worker_snapshot_verifies_pinned_tone_without_rereading_latest(monkeypatch):
    state = arrange(monkeypatch)
    assert hasattr(rt, 'snapshot_request'), 'v2 queue snapshot missing'
    snap = asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], body()))
    async def latest(*a): raise AssertionError('worker reread active selection')
    monkeypatch.setattr(routes.repo, 'list_mannequin_cuts', latest)
    asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snap))
    state['analysis']['selectedModelId'] = 'mB'
    with pytest.raises(ValueError):
        asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snap))


@pytest.mark.parametrize('drift', ['model_catalog', 'scope', 'matching_category', 'missing_evidence', 'unsupported_cut'])
def test_generation_relevant_metadata_is_preflight_bound(monkeypatch, drift):
    state = arrange(monkeypatch)
    if drift == 'missing_evidence':
        state['analysis'].pop('confirmedGptProductEvidence')
    if drift == 'unsupported_cut':
        state['storyboard'][0]['cutType'] = 'mirror'
    if drift in {'missing_evidence', 'unsupported_cut'}:
        with pytest.raises(ValueError):
            asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], body()))
        return
    snap = asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], body()))
    if drift == 'matching_category':
        async def changed(*args): return {'clothing_type': 'dress'}
        monkeypatch.setattr(routes.repo, 'get_matching_item_metadata', changed)
    else:
        assert hasattr(rt, 'catalog_projection'), 'v2 scope/model catalogue snapshot missing'
        original = rt.catalog_projection
        def changed(*args):
            value = deepcopy(original(*args))
            value['drift'] = drift
            return value
        monkeypatch.setattr(rt, 'catalog_projection', changed)
    with pytest.raises(ValueError):
        asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snap))


def test_legacy_cannot_join_v2_job_and_clean_profile_is_bound(client, make_token, monkeypatch):
    state = arrange(monkeypatch)
    data = body() | {'captureProfile': 'clean'}
    first = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert first.status_code == 202
    payload = state['create'][0]['payload']
    assert payload['wearshotV2']['request']['captureProfile'] == 'clean'
    async def reuse(*a, **kw): return {'id': 'job', 'payload': payload}, False
    monkeypatch.setattr(routes.repo, 'create_job', reuse)
    same = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert same.status_code == 202
    legacy = client.post('/v1/projects/p1/detail-page:generate', headers=auth_headers(make_token))
    assert legacy.status_code == 409
    assert len(state['reserves']) == 1


def test_missing_or_changed_target_seller_metadata_is_preflight_hold(monkeypatch):
    state = arrange(monkeypatch)
    original = routes.repo.get_asset_for_user
    assert hasattr(rt, '_seller_catalog'), 'target seller snapshot missing'
    snap = asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], body()))
    async def changed(conn, uid, aid):
        row = await original(conn, uid, aid)
        return (row | {'r2_key': 'replacement'}) if aid == 'seller-target' else row
    monkeypatch.setattr(routes.repo, 'get_asset_for_user', changed)
    with pytest.raises(ValueError):
        asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snap))
    async def missing(conn, uid, aid): return None if aid == 'seller-target' else await original(conn, uid, aid)
    monkeypatch.setattr(routes.repo, 'get_asset_for_user', missing)
    with pytest.raises(ValueError):
        asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], body()))


def test_duplicate_matching_selection_is_rejected_before_reservation(client, make_token, monkeypatch):
    state = arrange(monkeypatch)
    state['storyboard'][0]['matchIds'] = ['m1', 'm1']
    response = client.post('/v1/projects/p1/detail-page:generate', json=body(), headers=auth_headers(make_token))
    assert response.status_code == 409
    assert state['reserves'] == []


@pytest.mark.parametrize('lineage', ['sourceAssetId', 'sourceCutId', 'sourceHash', 'project'])
def test_tone_with_wrong_lineage_cannot_reserve(client, make_token, monkeypatch, lineage):
    state = arrange(monkeypatch)
    original = routes.repo.get_owned_mannequin_asset
    async def malformed(*args):
        row = await original(*args)
        if row['id'] == TONE:
            if lineage == 'project': row['project_id'] = 'other-project'
            else: row['metadata'][lineage] = None
        return row
    monkeypatch.setattr(routes.repo, 'get_owned_mannequin_asset', malformed)
    response = client.post('/v1/projects/p1/detail-page:generate', json=body(), headers=auth_headers(make_token))
    assert response.status_code == 409
    assert state['reserves'] == []


def test_only_exact_original_match_id_can_authorize_composite_fallback(monkeypatch):
    state = arrange(monkeypatch)
    data = body() | {'matchingMannequinAssets': {}}
    snap = asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], data))
    assert snap['matching']['m1']['id'] == TONE
    original = routes.repo.get_owned_mannequin_asset
    async def different(*args):
        row = await original(*args)
        row['source_metadata']['matchItemId'] = 'other'
        return row
    monkeypatch.setattr(routes.repo, 'get_owned_mannequin_asset', different)
    with pytest.raises(ValueError):
        asyncio.run(rt.snapshot_request(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], data))
