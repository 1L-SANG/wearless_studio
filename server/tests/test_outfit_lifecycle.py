import asyncio
import contextlib
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

from app import repo, routes, public_routes
from app.services import outfit
from app.workers import analyze_job
from conftest import FakeConn, FakePool, FakeR2, auth_headers, fake_worker_app, make_settings, worker_job
from test_outfit import row
from test_outfit_routes import state


def test_explicit_set_application_survives_selection_patch(client, make_token, state):
    saved, _, items = state
    items.append(row('b2', 'bottom'))
    saved.update(outfitSelections={'version': 1, 'slots': {
        'bottom': {'itemId': 'bottom', 'origin': 'user'},
        'footwear': {'itemId': None, 'origin': 'user'}}, 'setIndex': 0,
        'optional': {'bag': False, 'accessory': False}},
        outfitRecommendation={'sets': [{'slots': {'bottom': 'bottom'}, 'reason': 'first'},
                                      {'slots': {'bottom': 'b2', 'footwear': None}, 'reason': 'second'}]})
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token), json={'outfitSetIndex': 1})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots']['bottom'] == {'itemId': 'b2', 'origin': 'ai'}
    assert saved['outfitSelections']['slots']['footwear'] == {'itemId': None, 'origin': 'user'}
    assert saved['matchIds'] == ['b2']
    assert 'outfitSetIndex' not in saved


def test_authenticated_analysis_commits_pending_before_start_and_preserves_late_selections(monkeypatch):
    outfit.clear_cache()
    async def run():
        saved, events = {}, []
        product = {'clothingType': 'top', 'colors': [{'images': [{'id': 'front', 'slot': 'Front'}], 'isBase': True}]}
        items = [row('b1', 'bottom'), row('b2', 'bottom'), row('bag', 'bag')]
        class Conn(FakeConn):
            async def commit(self): events.append('commit')
        class Pool:
            @contextlib.asynccontextmanager
            async def connection(self): yield Conn()
        app = fake_worker_app(make_settings(gemini_api_key='mock', outfit_recommendation_enabled=True))
        app.state.pool = Pool()
        async def finalize(conn, **kw):
            saved.update(deepcopy(kw['analysis_payload'])); events.append('save')
            return {'result': kw['result']}
        release = asyncio.Event()
        async def model(*a, **kw):
            assert events.index('save') < events.index('commit')
            events.append('model'); await release.wait()
            return {'sets': [{'slots': {'bottom': 'b2', 'footwear': None, 'bag': 'bag', 'accessory': None}, 'reason': 'new', 'concept': 'casual'}]}
        async def save(conn, pid, fields, **kwargs):
            saved.update(deepcopy(fields)); return deepcopy(saved)
        monkeypatch.setattr(repo, 'get_product', AsyncMock(side_effect=lambda *a: deepcopy(product)))
        monkeypatch.setattr(repo, 'get_analysis', AsyncMock(side_effect=lambda *a: deepcopy(saved)))
        monkeypatch.setattr(repo, 'get_analysis_for_update', AsyncMock(side_effect=lambda *a: deepcopy(saved)))
        monkeypatch.setattr(repo, 'list_active_matching_items', AsyncMock(return_value=items))
        monkeypatch.setattr(repo, 'get_asset_for_user', AsyncMock(return_value={'mime_type': 'image/png', 'r2_key': 'front'}))
        monkeypatch.setattr(repo, 'finalize_analyze_success', finalize)
        monkeypatch.setattr(repo, 'save_outfit_recommendation', save)
        monkeypatch.setattr(analyze_job, '_emit', AsyncMock())
        monkeypatch.setattr(analyze_job, 'analyze_image_bytes', AsyncMock(return_value={
            'analysis_payload': {}, 'result_data': {'clothingType': 'top'}, 'clothing_type': 'top',
            'provider': 'fake', 'feature_provider': 'fake', 'consistency': None}))
        call = AsyncMock(side_effect=model)
        monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
        await asyncio.wait_for(analyze_job.run_analyze_job(app, worker_job()), timeout=1)
        assert saved['outfitRecommendation']['status'] == 'pending'
        assert saved['outfitRecommendation']['fallbackReady'] is True
        saved['outfitSelections']['slots']['bottom'] = {'itemId': None, 'origin': 'user'}
        saved['outfitSelections']['optional']['bag'] = True
        remembered = deepcopy(saved['outfitSelections'])
        release.set()
        await asyncio.gather(*list(app.state.outfit_tasks.values()))
        assert saved['outfitRecommendation']['status'] == 'ready'
        assert saved['outfitRecommendation']['sets'][0]['concept'] == 'casual'
        assert saved['outfitSelections'] == remembered
        assert call.await_count == 1
        # Reanalysis of the same photo/context must persist a reused completed result.
        saved['outfitSelections']['slots']['bottom'] = {'itemId': 'b1', 'origin': 'ai'}
        saved['outfitSelections']['optional']['bag'] = False
        await analyze_job.run_analyze_job(app, worker_job())
        await asyncio.gather(*list(app.state.outfit_tasks.values()))
        assert saved['outfitRecommendation']['status'] == 'ready'
        assert call.await_count == 1
    asyncio.run(run())


def test_public_pending_poll_uses_signed_capability_and_never_starts_provider(client, monkeypatch):
    outfit.clear_cache()
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key='mock',
        outfit_recommendation_enabled=True, r2_secret_access_key='outfit-secret-long-enough')
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=[row('b1', 'bottom')]))
    monkeypatch.setattr(public_routes, 'analyze_core_image_bytes', AsyncMock(return_value={
        'result_data': {'clothingType': 'top'}, 'analysis_payload': {}, 'clothing_type': 'top'}))
    async def delayed(*a, **kw):
        await asyncio.sleep(0.05)
        return {'sets': [{'slots': {'bottom': 'b1', 'footwear': None, 'bag': None, 'accessory': None}, 'reason': 'new', 'concept': 'casual'}]}
    call = AsyncMock(side_effect=delayed)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    response = client.post('/v1/public/analyze', files=[('images', ('f.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))],
                           data={'parts': 'core', 'runId': 'outfit-run-123'})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['outfitRecommendation']['status'] == 'pending'
    assert data['outfitRecommendation']['fallbackReady'] is True
    for _ in range(3):
        polled = client.post('/v1/public/outfit-recommendations/status', json={'handoff': data[outfit.HANDOFF_KEY]})
        assert polled.status_code == 200, polled.text
        assert len(polled.json()['data']['outfitRecommendation']['sets']) == 1
    assert call.await_count <= 1
    forged = deepcopy(data[outfit.HANDOFF_KEY]); forged['signature'] = '0' * 64
    assert client.post('/v1/public/outfit-recommendations/status', json={'handoff': forged}).status_code == 400


def test_authenticated_status_read_has_pending_fallback_and_no_model_call(client, make_token, state, monkeypatch):
    saved, product, items = state
    config = replace(client.app.state.settings, gemini_api_key='mock', outfit_recommendation_enabled=True)
    saved.update(outfit.prepare_recommendation(config, FakeR2(), product, saved, [(b'photo', 'image/png')], items))
    call = AsyncMock(); monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    for _ in range(2):
        response = client.get('/v1/projects/p1/analysis/outfit-recommendations', headers=auth_headers(make_token))
        assert response.status_code == 200, response.text
        assert response.json()['outfitRecommendation']['fallbackReady'] is True
    assert call.await_count == 0


def test_expired_pending_read_returns_fallback_without_restarting(client, make_token, state, monkeypatch):
    saved, product, items = state
    config = replace(client.app.state.settings, gemini_api_key='mock', outfit_recommendation_enabled=True)
    saved.update(outfit.prepare_recommendation(config, FakeR2(), product, saved, [(b'photo', 'image/png')], items))
    saved['outfitRecommendation']['pendingSince'] = 0
    call = AsyncMock(); monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    response = client.get('/v1/projects/p1/analysis/outfit-recommendations', headers=auth_headers(make_token))
    assert response.json()['outfitRecommendation']['status'] == 'fallback'
    assert response.json()['outfitRecommendation']['sets']
    assert call.await_count == 0


def test_server_optional_toggle_uses_current_set_then_remembers_choice(client, make_token, state, monkeypatch):
    saved, _, _ = state
    saved['outfitRecommendation'] = {'sets': [{'slots': {'bag': 'bag'}, 'reason': 'one'}]}
    saved['outfitSelections'] = {'version': 1, 'slots': {}, 'setIndex': 0, 'optional': {'bag': False, 'accessory': False}}
    call = AsyncMock(); monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    for enabled in [True, False, True]:
        response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token), json={
            'outfitSelections': {'version': 1, 'slots': {}, 'optional': {'bag': enabled}}})
        assert response.status_code == 200, response.text
        assert response.json()['outfitSelections']['slots']['bag']['itemId'] == 'bag'
        assert response.json()['outfitSelections']['optional']['bag'] is enabled
    assert call.await_count == 0


def test_promotion_keeps_explicit_set_chosen_after_public_judgement(client, make_token, state):
    saved, product, items = state
    items.append(row('b2', 'bottom'))
    prior = {'version': 1, 'slots': {'bottom': {'itemId': 'bottom', 'origin': 'user'}}}
    fields = asyncio.run(outfit.recommend(client.app.state.settings, client.app.state.r2, product,
        {**saved, 'outfitSelections': prior}, [(b'\x89PNG-bytes', 'image/png')], items, slots=['Front']))
    token = outfit.issue_handoff(fields, [(b'\x89PNG-bytes', 'image/png')], ['Front'], 'outfit-secret-long-enough')
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote', headers=auth_headers(make_token), json={
        'handoff': token, 'outfitSelections': {'version': 1, 'setIndex': 1, 'slots': {
            'bottom': {'itemId': 'b2', 'origin': 'ai'}}, 'optional': {'bag': False, 'accessory': False}}})
    assert response.status_code == 200, response.text
    assert response.json()['outfitSelections']['slots']['bottom']['itemId'] == 'b2'


def test_context_refresh_drops_ineligible_ai_gender_choice_and_recommends_new_pool(client, make_token, state):
    saved, _, items = state
    items.append(row('men-bottom', 'bottom', gender='men'))
    saved['outfitSelections'] = {'version': 1, 'slots': {
        'bottom': {'itemId': 'bottom', 'origin': 'ai'}, 'footwear': {'itemId': None, 'origin': 'user'}}}
    saved['matchClothing'] = [{'id': 'bottom', 'selected': True}]
    saved['targetGenders'] = ['men']
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations', headers=auth_headers(make_token),
                           json={'targetGenders': ['men']})
    assert response.status_code == 200, response.text
    assert response.json()['outfitSelections']['slots']['bottom']['itemId'] == 'men-bottom'
    assert response.json()['outfitSelections']['slots']['footwear'] == {'itemId': None, 'origin': 'user'}
    assert [item['id'] for item in response.json()['outfitRecommendation']['candidates']['bottom']] == ['men-bottom']


def test_outfit_selection_save_keeps_main_cutout_processing_and_tidy_thumbnail(client, make_token, state, monkeypatch):
    saved, _, items = state
    items[0].update(is_custom=True, thumb_key='tidy-current.webp', image_meta={})
    monkeypatch.setattr(repo, 'has_active_matching_cutout_job', AsyncMock(return_value=True))
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token), json={
        'outfitSelections': {'version': 1, 'slots': {'bottom': {'itemId': 'bottom', 'origin': 'user'}}}})
    assert response.status_code == 200, response.text
    actual = response.json()['matchClothing'][0]
    assert actual['cutoutStatus'] == 'processing'
    assert actual['thumb'] == 'https://r2.test/tidy-current.webp'
    assert actual['imageUrl'] == 'https://r2.test/bottom.png'


def test_stored_same_context_recommendation_does_not_restart_after_cache_eviction(client, make_token, state, monkeypatch):
    saved, product, items = state
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key='', outfit_recommendation_enabled=True)
    saved.update(asyncio.run(outfit.recommend(client.app.state.settings, client.app.state.r2,
        product, saved, [(b'\x89PNG-bytes', 'image/png')], items, slots=['Front'])))
    outfit.clear_cache()
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key='mock', outfit_recommendation_enabled=True)
    call = AsyncMock(); monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations', headers=auth_headers(make_token), json={})
    assert response.status_code == 200, response.text
    assert response.json()['outfitRecommendation']['status'] == 'fallback'
    assert call.await_count == 0


def test_public_refresh_rejects_manual_item_outside_gender_pool(client, monkeypatch):
    import json
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=[
        row('women-bottom', 'bottom', gender='women'), row('men-bottom', 'bottom', gender='men')]))
    call = AsyncMock(); monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
    response = client.post('/v1/public/outfit-recommendations',
        files=[('images', ('f.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))], data={'analysisContext': json.dumps({
            'clothingType': 'top', 'targetGenders': ['men'], 'outfitSelections': {'version': 1,
                'slots': {'bottom': {'itemId': 'women-bottom', 'origin': 'user'}}}})})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'outfit_item_unavailable'
    assert call.await_count == 0


def test_public_refresh_recomputes_old_ai_choice_and_preserves_user_null(client, monkeypatch):
    import json
    outfit.clear_cache()
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=[
        row('women-bottom', 'bottom', gender='women'), row('men-bottom', 'bottom', gender='men')]))
    response = client.post('/v1/public/outfit-recommendations',
        files=[('images', ('f.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))], data={'analysisContext': json.dumps({
            'clothingType': 'top', 'targetGenders': ['men'], 'outfitSelections': {'version': 1, 'slots': {
                'bottom': {'itemId': 'women-bottom', 'origin': 'ai'},
                'footwear': {'itemId': None, 'origin': 'user'}}}})})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['outfitSelections']['slots']['bottom']['itemId'] == 'men-bottom'
    assert data['outfitSelections']['slots']['footwear'] == {'itemId': None, 'origin': 'user'}
    assert [item['id'] for item in data['outfitRecommendation']['candidates']['bottom']] == ['men-bottom']
