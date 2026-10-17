import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from app import public_routes, repo, routes
from app.services import outfit, outfit_jobs
from conftest import FakePool, FakeR2, auth_headers, fake_worker_app, make_settings
from test_outfit import row
from test_outfit_routes import state


@pytest.mark.parametrize('before,after', [('top', 'bottom'), ('bottom', 'top'), ('outer', 'top')])
@pytest.mark.parametrize('explicit_selections', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_type_reconciliation_leaves_complement_open_for_three_fallback_sets(
        client, make_token, state, monkeypatch, before, after, explicit_selections, enabled):
    saved, product, items = state
    client.app.state.settings = replace(client.app.state.settings,
        gemini_api_key='mock', outfit_recommendation_enabled=enabled)
    outfit.clear_cache()
    items[:] = [row(f'{kind}-{i}', kind, color_group=f'color-{i}')
                for kind in ('top', 'bottom', 'inner') for i in range(3)]
    product['clothingType'] = before
    saved['clothingType'] = before
    saved.update(outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product,
        saved, [(b'photo', 'image/png')], items, slots=['Front']))
    old_key = saved['outfitRecommendation']['contextKey']
    async def save_product(conn, pid, uid, patch):
        product.update(id='product', projectId=pid, name='상품', clothingType=patch['clothing_type'])
        return deepcopy(product)
    monkeypatch.setattr(repo, 'save_product', save_product)
    monkeypatch.setattr(routes, '_enqueue_canonical_cutout', AsyncMock(return_value=False))
    call = AsyncMock()
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    start = Mock()
    monkeypatch.setattr(outfit_jobs, 'start_project', start)
    # The input page persists the product first, then the analysis chip patch.
    response = client.patch('/v1/projects/p1/product', headers=auth_headers(make_token),
                            json={'clothingType': after})
    assert response.status_code == 200, response.text
    patch = {'clothingType': after, 'matchClothing': [
        {**item, 'selected': item['selected'] and item['clothingType'] == outfit.complementary_slot(after)}
        for item in saved['matchClothing']]}
    if explicit_selections:
        patch['outfitSelections'] = outfit.normalize_selections(saved['outfitSelections'], after)
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token), json=patch)
    assert response.status_code == 200, response.text
    role = outfit.complementary_slot(after)
    assert saved['outfitSelections']['slots'].get(role, {}).get('origin') != 'user'
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations',
                           headers=auth_headers(make_token), json={'clothingType': after})
    assert response.status_code == 200, response.text
    fields = response.json()
    assert fields['outfitRecommendation']['contextKey'] != old_key
    assert fields['outfitRecommendation']['status'] == ('pending' if enabled else 'fallback')
    assert start.call_count == int(enabled)
    if enabled:
        assert fields['outfitRecommendation']['fallbackReady'] is True
    assert len(fields['outfitRecommendation']['sets']) == 3
    assert all(entry['slots'][role] for entry in fields['outfitRecommendation']['sets'])
    assert fields['outfitSelections']['slots'][role]['origin'] == 'ai'
    call.assert_not_called()


@pytest.mark.parametrize('choice', [None, 'bottom'])
def test_type_reconciliation_preserves_still_eligible_user_choice(client, make_token, state, choice):
    saved, product, _ = state
    saved.update(clothingType='outer', outfitSelections={'version': 1, 'slots': {
        'bottom': {'itemId': choice, 'origin': 'user'},
        'inner': {'itemId': None, 'origin': 'user'}}})
    product['clothingType'] = 'top'
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token),
                            json={'clothingType': 'top', 'matchClothing': []})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots'] == {'bottom': {'itemId': choice, 'origin': 'user'}}
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations',
                           headers=auth_headers(make_token), json={})
    assert response.status_code == 200, response.text
    assert response.json()['outfitSelections']['slots']['bottom'] == {'itemId': choice, 'origin': 'user'}


def test_explicit_legacy_deselection_is_still_user_null(client, make_token, state):
    saved, _, _ = state
    saved['outfitSelections'] = {'version': 1, 'slots': {'bottom': {'itemId': 'bottom', 'origin': 'ai'}}}
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token),
                            json={'matchClothing': [{'id': 'bottom', 'selected': False}]})
    assert response.status_code == 200, response.text
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations',
                           headers=auth_headers(make_token), json={})
    assert response.status_code == 200, response.text
    assert response.json()['outfitSelections']['slots']['bottom'] == {'itemId': None, 'origin': 'user'}
    assert all(entry['slots']['bottom'] is None for entry in response.json()['outfitRecommendation']['sets'])


@pytest.mark.parametrize('before,after', [('outer', 'top'), ('top', 'outer'), ('top', 'bottom')])
def test_type_change_migrates_only_compatible_legacy_selection(client, make_token, state, before, after):
    saved, product, _ = state
    saved.update(clothingType=before, matchClothing=[{'id': 'bottom', 'clothingType': 'bottom', 'selected': True}])
    assert 'outfitSelections' not in saved
    product['clothingType'] = after
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token), json={
        'clothingType': after, 'matchClothing': [{'id': 'bottom', 'clothingType': 'bottom', 'selected': after != 'bottom'}]})
    assert response.status_code == 200, response.text
    if after == 'bottom':
        assert 'bottom' not in saved['outfitSelections']['slots']
        assert saved['outfitSelections']['slots'].get('top', {}).get('origin') != 'user'
    else:
        assert saved['outfitSelections']['slots']['bottom'] == {'itemId': 'bottom', 'origin': 'user'}
        assert saved['matchIds'] == ['bottom']


@pytest.mark.parametrize('before,after', [('top', 'bottom'), ('bottom', 'top'), ('outer', 'top')])
def test_public_type_refresh_and_promotion_preserve_ai_fillable_role(
        client, make_token, state, monkeypatch, before, after):
    saved, product, items = state
    outfit.clear_cache()
    items[:] = [row(f'{kind}-{i}', kind, color_group=f'color-{i}')
                for kind in ('top', 'bottom', 'inner') for i in range(3)]
    previous = outfit.prepare_recommendation(client.app.state.settings, FakeR2(),
        {**product, 'clothingType': before}, saved, [(b'photo', 'image/png')], items)
    client.app.state.pool = FakePool()
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=items))
    photo = b'\x89PNG\r\n\x1a\nphoto'
    response = client.post('/v1/public/outfit-recommendations',
        files=[('images', ('front.png', photo, 'image/png'))], data={'analysisContext': json.dumps({
            'clothingType': after, 'subCategory': saved['subCategory'], 'targetGenders': ['women'],
            'outfitSelections': previous['outfitSelections']})})
    assert response.status_code == 200, response.text
    fields = response.json()['data']
    role = outfit.complementary_slot(after)
    assert fields['outfitSelections']['slots'][role]['origin'] == 'ai'
    assert len(fields['outfitRecommendation']['sets']) == 3
    assert all(entry['slots'][role] for entry in fields['outfitRecommendation']['sets'])
    product['clothingType'] = after
    monkeypatch.setattr(client.app.state.r2, 'get_bytes', lambda key: photo)
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': fields[outfit.HANDOFF_KEY],
            'outfitSelections': fields['outfitSelections']})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots'][role]['origin'] == 'ai'


def test_background_completion_persists_only_recommendation_over_latest_custom_selection(monkeypatch):
    async def run():
        outfit.clear_cache()
        product = {'clothingType': 'top', 'colors': []}
        items = [row('bottom', 'bottom'), row('custom', 'bottom', is_custom=True)]
        app = fake_worker_app(make_settings(gemini_api_key='mock', outfit_recommendation_enabled=True))
        fields = outfit.prepare_recommendation(app.state.settings, FakeR2(), product, {}, [(b'photo', 'image/png')], items)
        saved = deepcopy(fields)
        model = asyncio.get_running_loop().create_future()
        key = fields['outfitRecommendation']['contextKey']
        outfit._inflight[key] = model
        monkeypatch.setattr(repo, 'get_analysis_for_update', AsyncMock(side_effect=lambda *a: deepcopy(saved)))
        monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value=product))
        monkeypatch.setattr(outfit, 'load_items', AsyncMock(return_value=items))
        async def save(conn, pid, patch, *, expected_analysis):
            assert set(patch) == {'outfitRecommendation'}
            assert expected_analysis == saved
            saved.update(deepcopy(patch))
        monkeypatch.setattr(repo, 'save_outfit_recommendation', AsyncMock(side_effect=save))
        try:
            outfit_jobs.follow_existing(app, 'u1', 'p1', product, fields, fields)
            selections = deepcopy(fields['outfitSelections'])
            selections['slots']['bottom'] = {'itemId': 'custom', 'origin': 'user'}
            saved.update(outfit.sync_legacy(saved, selections, items, app.state.r2))
            saved['mdComment'] = '추천 대기 중 편집'
            expected = deepcopy(saved)
            result = deepcopy(fields)
            result['outfitRecommendation']['status'] = 'ready'
            model.set_result(result)
            await asyncio.gather(*app.state.outfit_tasks.values())
            assert repo.save_outfit_recommendation.await_count == 1
            assert saved['outfitRecommendation']['status'] == 'ready'
            for key in ('outfitSelections', 'matchClothing', 'matchIds', 'mdComment'):
                assert saved[key] == expected[key]
            assert outfit.generation_snapshot(saved, 'top', items)['slots']['bottom']['itemId'] == 'custom'
        finally:
            outfit._inflight.pop(fields['outfitRecommendation']['contextKey'], None)
    asyncio.run(run())
