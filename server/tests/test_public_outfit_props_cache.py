"""Public props survive the shorter recommendation-cache lifetime without paid retries."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import public_routes, repo
from app.services import outfit
from conftest import FakePool, FakeR2, auth_headers
from test_outfit import row
from test_outfit_props import accessories
from test_outfit_routes import state


def test_public_outfit_capabilities_are_read_only_without_auth(client, monkeypatch):
    class NoDatabase:
        def connection(self):
            pytest.fail('capability reads must not query the database')

    client.app.state.pool = NoDatabase()
    initial = AsyncMock(side_effect=AssertionError('no model'))
    props = AsyncMock(side_effect=AssertionError('no model'))
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', initial)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', props)
    response = client.get('/v1/public/outfit-capabilities')
    assert response.status_code == 200, response.text
    assert response.json() == {'props': True, 'maxAccessories': 3}
    initial.assert_not_awaited()
    props.assert_not_awaited()


def test_dedicated_public_props_route_never_runs_initial_recommendation(client, monkeypatch):
    outfit.clear_cache()
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key='outfit-secret-long-enough')
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=accessories()))
    initial = AsyncMock(side_effect=AssertionError('props must not start the initial provider'))
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', initial)
    files = [('images', ('front.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))]
    context = json.dumps({'clothingType': 'top'})
    first = client.post('/v1/public/outfit-recommendations', files=files, data={'analysisContext': context})
    handoff = first.json()['data'][outfit.HANDOFF_KEY]
    response = client.post('/v1/public/outfit-props-recommendations', files=files,
        data={'analysisContext': context, 'role': 'accessory', 'handoff': json.dumps(handoff)})
    assert response.status_code == 200, response.text
    assert response.json()['data']['outfitRecommendation']['props']['accessory'] == {
        'status': 'fallback', 'picks': ['0', '3', '4']}
    assert initial.await_count == 0


def test_dedicated_project_props_route_preserves_selections(client, make_token, state, monkeypatch):
    saved, _, items = state
    items.extend(accessories())
    saved['outfitSelections'] = {'version': 1, 'slots': {'accessory': {'itemId': '5', 'origin': 'user'}}}
    original = deepcopy(saved['outfitSelections'])
    monkeypatch.setattr(repo, 'get_analysis_for_update', AsyncMock(side_effect=lambda *args: deepcopy(saved)))
    initial = AsyncMock(side_effect=AssertionError('props must not start the initial provider'))
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', initial)
    response = client.post('/v1/projects/p1/analysis/outfit-props-recommendations',
        headers=auth_headers(make_token), json={'role': 'accessory'})
    assert response.status_code == 200, response.text
    assert response.json()['outfitRecommendation']['props']['accessory'] == {
        'status': 'fallback', 'picks': ['0', '3', '4']}
    assert saved['outfitSelections'] == original
    assert initial.await_count == 0


@pytest.mark.parametrize('role', [None, 'bottom'])
@pytest.mark.parametrize('public', [True, False], ids=['public', 'project'])
def test_dedicated_props_routes_require_a_prop_role_before_database(client, make_token, monkeypatch, role, public):
    class NoDatabase:
        def connection(self):
            pytest.fail('invalid props requests must not query the database')

    client.app.state.pool = NoDatabase()
    if public:
        data = {} if role is None else {'role': role}
        response = client.post('/v1/public/outfit-props-recommendations',
            files=[('images', ('front.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))], data=data)
    else:
        response = client.post('/v1/projects/p1/analysis/outfit-props-recommendations',
            headers=auth_headers(make_token), json={} if role is None else {'role': role})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'invalid_outfit_props_role'


@pytest.mark.parametrize('complete', [True, False], ids=['completed', 'pending-deadline'])
def test_public_props_rehydrate_expired_base_and_merge_same_handoff_requests(client, monkeypatch, complete):
    outfit.clear_cache()
    client.app.state.pool = FakePool()
    client.app.state.r2 = FakeR2()
    client.app.state.settings = replace(client.app.state.settings, gemini_api_key='mock',
        outfit_recommendation_enabled=True, r2_secret_access_key='outfit-secret-long-enough')
    items = [row('bottom', 'bottom'), row('bag', 'bag'), *accessories()]
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=items))
    monkeypatch.setattr(public_routes, '_analysis_semaphore', asyncio.Semaphore(4))
    initial_call = AsyncMock(return_value={'sets': [
        {'slots': {'bottom': 'bottom', 'footwear': None}, 'reason': 'base', 'concept': 'casual'}]})
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', initial_call)

    async def run():
        started = {role: asyncio.Event() for role in ('bag', 'accessory')}
        releases = {role: asyncio.Event() for role in started}
        calls = []

        async def model(settings, r2, source, pools, **kwargs):
            role = next(iter(pools))
            calls.append(role)
            started[role].set()
            await releases[role].wait()
            return {'picks': ['bag'] if role == 'bag' else ['3', '4', '5'], 'reason': '상품에 어울려요.'}

        monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', model)
        files = [('images', ('front.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))]
        context = {'clothingType': 'top', 'targetGenders': ['women']}
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as http:
            first = await http.post('/v1/public/outfit-recommendations', files=files,
                data={'analysisContext': json.dumps(context)})
            assert first.status_code == 200, first.text
            fields = first.json()['data']
            base_key = fields['outfitRecommendation']['contextKey']
            task = outfit._inflight.get(base_key)
            if task is not None:
                await task
            status = await http.post('/v1/public/outfit-recommendations/status',
                json={'handoff': fields[outfit.HANDOFF_KEY]})
            assert status.status_code == 200, status.text
            original_handoff = status.json()['data'][outfit.HANDOFF_KEY]
            assert original_handoff['contract']['outfitRecommendation']['status'] == 'ready'
            timestamp, cached = outfit._cache[base_key]
            outfit._cache[base_key] = (timestamp - outfit._CACHE_TTL - 1, cached)

            current = {'version': 1, 'slots': {'bottom': {'itemId': None, 'origin': 'user'},
                'accessory': {'itemId': '5', 'extraIds': ['3'], 'origin': 'user'}}, 'setIndex': 1,
                'optional': {'bag': True, 'accessory': True}}
            responses = await asyncio.gather(*(http.post('/v1/public/outfit-recommendations', files=files,
                data={'analysisContext': json.dumps({**context, 'outfitSelections': current}),
                    'role': role, 'handoff': json.dumps(original_handoff)}) for role in started))
            for response in responses:
                assert response.status_code == 200, response.text
                assert response.json()['data']['outfitSelections'] == current
            await asyncio.gather(*(event.wait() for event in started.values()))
            latest_handoff = responses[-1].json()['data'][outfit.HANDOFF_KEY]
            pending = await http.post('/v1/public/outfit-recommendations/status', json={'handoff': latest_handoff})
            assert pending.status_code == 200, pending.text
            data = pending.json()['data']
            assert data['outfitSelections'] == current
            assert {role: prop['status'] for role, prop in data['outfitRecommendation']['props'].items()} == {
                'bag': 'pending', 'accessory': 'pending'}
            deadlines = deepcopy(data['outfitRecommendation']['propsExpiresAt'])
            tasks = list(outfit._inflight.values())
            if complete:
                # A role completion must also restore an evicted, fully signable base.
                outfit._cache.pop(base_key)
                for release in releases.values():
                    release.set()
                await asyncio.gather(*tasks)
                expected = {'bag': {'status': 'ready', 'picks': ['bag']},
                    'accessory': {'status': 'ready', 'picks': ['3', '4', '5']}}
            else:
                # A worker restart/eviction retains the signed timeout and deterministic picks.
                outfit.clear_cache()
                now = max(deadlines.values()) + 1
                monkeypatch.setattr(outfit.time, 'time', lambda: now)
                expected = {'bag': {'status': 'fallback', 'picks': ['bag']},
                    'accessory': {'status': 'fallback', 'picks': ['0', '3', '4']}}
            final = await http.post('/v1/public/outfit-recommendations/status', json={'handoff': data[outfit.HANDOFF_KEY]})
            assert final.status_code == 200, final.text
            final_data = final.json()['data']
            assert final_data['outfitRecommendation']['props'] == expected
            assert final_data['outfitRecommendation']['propsExpiresAt'] == deadlines
            assert final_data['outfitSelections'] == current
            assert outfit.verify_handoff(final_data[outfit.HANDOFF_KEY],
                client.app.state.settings.r2_secret_access_key)['outfitSelections'] == current
            for role in started:
                retried = await http.post('/v1/public/outfit-recommendations', files=files,
                    data={'analysisContext': json.dumps({**context, 'outfitSelections': current}),
                        'role': role, 'handoff': json.dumps(final_data[outfit.HANDOFF_KEY])})
                assert retried.status_code == 200, retried.text
                assert retried.json()['data']['outfitRecommendation']['props'][role] == expected[role]
            assert sorted(calls) == ['accessory', 'bag']
            assert initial_call.await_count == 1
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        outfit.clear_cache()
