"""Producer gates keep multi-accessory drafts away from older rolling API workers."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.config import load_settings
from app.services import outfit
from conftest import FakeR2, auth_headers, make_settings
from test_outfit_props import accessories
from test_outfit_routes import state


def selected(*identifiers, enabled=True):
    return {'version': 1, 'slots': {'accessory': {'itemId': identifiers[0],
        **({'extraIds': list(identifiers[1:])} if len(identifiers) > 1 else {}), 'origin': 'user'}},
        'optional': {'accessory': enabled, 'bag': False}, 'setIndex': 0}


@pytest.mark.parametrize('value,expected', [(None, False), ('off', False), ('on', True), ('typo', False)])
def test_release_gate_requires_an_explicit_on_environment_value(monkeypatch, value, expected):
    monkeypatch.delenv('OUTFIT_PROPS_READY', raising=False)
    if value is not None:
        monkeypatch.setenv('OUTFIT_PROPS_READY', value)
    assert load_settings().outfit_props_ready is expected
    assert make_settings().outfit_props_ready is False


def test_release_gate_off_capability_is_read_only_and_keeps_legacy_limit(client, monkeypatch):
    class NoDatabase:
        def connection(self):
            pytest.fail('capabilities must not query the database')

    client.app.state.pool = NoDatabase()
    model = AsyncMock(side_effect=AssertionError('capabilities must not call a provider'))
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', model)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', model)
    response = client.get('/v1/public/outfit-capabilities')
    assert response.status_code == 200, response.text
    assert response.json() == {'props': False, 'maxAccessories': 1}
    assert response.headers.get('cache-control') == 'no-store'
    model.assert_not_awaited()


@pytest.mark.parametrize('public', [True, False], ids=['public', 'project'])
def test_release_gate_off_dedicated_props_reject_before_database_or_provider(client, make_token, monkeypatch, public):
    class NoDatabase:
        def connection(self):
            pytest.fail('warming props requests must not query the database')

    client.app.state.pool = NoDatabase()
    model = AsyncMock(side_effect=AssertionError('warming props requests must not call a provider'))
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', model)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', model)
    if public:
        response = client.post('/v1/public/outfit-props-recommendations',
            files=[('images', ('front.png', b'\x89PNG\r\n\x1a\nphoto', 'image/png'))], data={'role': 'accessory'})
    else:
        response = client.post('/v1/projects/p1/analysis/outfit-props-recommendations',
            headers=auth_headers(make_token), json={'role': 'accessory'})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'outfit_props_not_ready'
    model.assert_not_awaited()


@pytest.mark.parametrize('enabled', [True, False], ids=['enabled', 'remembered-disabled'])
def test_release_gate_off_multi_accessory_save_never_persists(client, make_token, state, monkeypatch, enabled):
    saved, _, items = state
    items.extend(accessories())
    before = deepcopy(saved)
    save = AsyncMock(side_effect=AssertionError('warming multi-selection must not persist'))
    monkeypatch.setattr(repo, 'save_analysis', save)
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token),
        json={'outfitSelections': selected('0', '3', '4', enabled=enabled)})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'outfit_props_not_ready'
    assert saved == before
    save.assert_not_awaited()


@pytest.mark.parametrize('override', [False, True], ids=['signed-selection', 'local-selection'])
def test_release_gate_off_multi_accessory_promotion_never_persists(client, make_token, state, monkeypatch, override):
    saved, product, items = state
    items.extend(accessories())
    fields = outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved,
        [(b'\x89PNG-bytes', 'image/png')], items, slots=['Front'])
    fields['outfitSelections'] = selected('0') if override else selected('0', '3', '4')
    token = outfit.issue_handoff(fields, [(b'\x89PNG-bytes', 'image/png')], ['Front'],
        client.app.state.settings.r2_secret_access_key, context=outfit.product_context(product, saved))
    save = AsyncMock(side_effect=AssertionError('warming promotion must not persist'))
    monkeypatch.setattr(repo, 'save_outfit_recommendation', save)
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': token,
            **({'outfitSelections': selected('0', '3', '4')} if override else {})})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'outfit_props_not_ready'
    assert 'outfitSelections' not in saved
    save.assert_not_awaited()


def test_release_gate_off_generation_rejects_multi_before_job_or_credit_reservation(client, make_token, state, monkeypatch):
    saved, _, items = state
    items.extend(accessories())
    saved['outfitSelections'] = selected('0', '3', '4')
    create = AsyncMock(side_effect=AssertionError('warming multi-selection must not create a job'))
    reserve = AsyncMock(side_effect=AssertionError('warming multi-selection must not reserve credits'))
    monkeypatch.setattr(repo, 'create_job', create)
    monkeypatch.setattr(repo, 'reserve_credits', reserve)
    response = client.post('/v1/projects/p1/mannequins:generate', headers=auth_headers(make_token), json={})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'outfit_props_not_ready'
    create.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.parametrize('ready,identifiers,enabled,expected', [
    (False, ('0',), True, {'itemId': '0', 'origin': 'user'}),
    (False, ('0', '3', '4'), False, None),
    (True, ('0', '3', '4'), True, {'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}),
])
def test_release_gate_preserves_single_disabled_and_ready_generation_snapshots(state, ready, identifiers, enabled, expected):
    saved, product, items = state
    items.extend(accessories())
    saved['outfitSelections'] = selected(*identifiers, enabled=enabled)
    settings = make_settings(outfit_props_ready=ready)
    actual = asyncio.run(routes._outfit_generation_snapshot(None, 'user-1', 'p1',
        analysis=saved, product=product, settings=settings))
    assert actual['slots'].get('accessory') == expected


def test_release_gate_on_saves_three_accessories_without_turning_on_paid_recommendations(client, make_token, state):
    saved, _, items = state
    items.extend(accessories())
    client.app.state.settings = replace(client.app.state.settings, outfit_props_ready=True)
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token),
        json={'outfitSelections': selected('0', '3', '4')})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots']['accessory'] == {
        'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}
    assert client.app.state.settings.outfit_recommendation_enabled is False


def test_release_gate_on_promotes_three_signed_accessories(client, make_token, state):
    saved, product, items = state
    items.extend(accessories())
    client.app.state.settings = replace(client.app.state.settings, outfit_props_ready=True)
    fields = outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved,
        [(b'\x89PNG-bytes', 'image/png')], items, slots=['Front'])
    fields['outfitSelections'] = selected('0', '3', '4')
    token = outfit.issue_handoff(fields, [(b'\x89PNG-bytes', 'image/png')], ['Front'],
        client.app.state.settings.r2_secret_access_key, context=outfit.product_context(product, saved))
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': token})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots']['accessory'] == {
        'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}


def test_release_gate_off_generic_refresh_never_persists_multi_selection(client, make_token, state, monkeypatch):
    _, _, items = state
    items.extend(accessories())
    save = AsyncMock(side_effect=AssertionError('warming refresh must not persist'))
    monkeypatch.setattr(repo, 'save_outfit_recommendation', save)
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations',
        headers=auth_headers(make_token), json={'outfitSelections': selected('0', '3', '4')})
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'outfit_props_not_ready'
    save.assert_not_awaited()


@pytest.mark.parametrize('ready', [False, True], ids=['warming', 'ready'])
def test_release_gate_restored_editor_retry_before_job_or_charge(client, make_token, state, monkeypatch, ready):
    _, _, items = state
    items.extend(accessories())
    client.app.state.settings = replace(client.app.state.settings, outfit_props_ready=ready)
    frozen = {'version': 1, 'slots': selected('0', '3', '4')['slots']}
    block = {'id': 'failed', 'source': 'ai', 'cutType': 'styling', 'shot': 'full',
        'outfitSelectionsSnapshot': frozen}
    monkeypatch.setattr(repo, 'get_storyboard', AsyncMock(return_value=[block]))
    monkeypatch.setattr(repo, 'get_editor_blocks', AsyncMock(return_value=[{'elements': [
        {'type': 'image', 'src': None, 'sourceBlockId': 'failed'}]}]))
    monkeypatch.setattr(repo, 'get_failed_cut_outfit_recipe', AsyncMock(return_value={
        'outfitSelectionsSnapshot': frozen}))
    create = AsyncMock(return_value=({'id': 'retry-job'}, True))
    reserve = AsyncMock(return_value=100)
    monkeypatch.setattr(repo, 'create_job', create)
    monkeypatch.setattr(repo, 'reserve_credits', reserve)
    response = client.post('/v1/projects/p1/editor:generate-image', headers=auth_headers(make_token),
        json={'mode': 'new', 'retryBlockId': 'failed', 'cutType': 'styling'})
    assert response.status_code == (202 if ready else 409), response.text
    if ready:
        assert create.await_args.kwargs['payload']['outfitSelectionsSnapshot'] == frozen
        reserve.assert_awaited_once()
    else:
        assert response.json()['error']['code'] == 'outfit_props_not_ready'
        create.assert_not_awaited()
        reserve.assert_not_awaited()


@pytest.mark.parametrize('ready', [False, True], ids=['warming', 'ready'])
def test_release_gate_editor_similar_uses_original_source_recipe_before_job_or_charge(client, make_token, monkeypatch, ready):
    from app.agents import asset_recipe
    from test_editor_vary_contract import ASSET, _route_setup
    frozen = {'version': 1, 'slots': selected('0', '3', '4')['slots']}
    recipe = {'cutType': 'styling', 'outfitSelectionsSnapshot': frozen}
    seen = _route_setup(monkeypatch, {'cut_type': 'styling',
        'generation': {'version': 1, 'origin': 'editor_new', 'recipe': recipe}})
    monkeypatch.setattr(asset_recipe, 'resolve_asset_recipes', AsyncMock(return_value={
        ASSET: {'recipe': recipe, 'cutType': 'styling', 'upload': False}}))
    client.app.state.settings = replace(client.app.state.settings, outfit_props_ready=ready)
    response = client.post('/v1/projects/p1/editor:generate-image', headers=auth_headers(make_token), json={
        'mode': 'vary', 'variationMode': 'similar',
        'source': {'src': f'/v1/assets/{ASSET}/file', 'cutType': 'styling'}})
    assert response.status_code == (202 if ready else 409), response.text
    if ready:
        assert 'job' in seen and seen['reserved'] is True
    else:
        assert response.json()['error']['code'] == 'outfit_props_not_ready'
        assert 'job' not in seen and 'reserved' not in seen


@pytest.mark.parametrize('ready', [False, True], ids=['warming', 'ready'])
def test_release_gate_editor_fill_inspects_the_inherited_source_outfit(client, make_token, monkeypatch, ready):
    from app.agents import asset_recipe
    from test_context_fill_routes import ASSET, EMPTY, post_fill, setup_fill
    blocks = [{**EMPTY, 'elements': [{'id': 'anchor', 'type': 'image',
        'src': f'/v1/assets/{ASSET}/file', 'x': 0, 'y': 0, 'w': 500, 'h': 600}, *EMPTY['elements']]}]
    seen = setup_fill(monkeypatch, blocks=blocks)
    frozen = {'version': 1, 'slots': selected('0', '3', '4')['slots']}
    recipe = {'sectionRole': 'styling', 'cutType': 'styling', 'colorId': 'col1',
        'shot': 'full', 'outfitSelectionsSnapshot': frozen}
    monkeypatch.setattr(asset_recipe, 'resolve_asset_recipes', AsyncMock(return_value={ASSET: {
        'origin': 'editor_new', 'recipe': recipe, 'cutType': 'styling', 'colorId': 'col1',
        'upload': False, 'realDerived': False, 'sourceAssetId': None, 'recipeFrom': 'self'}}))
    client.app.state.settings = replace(client.app.state.settings, outfit_props_ready=ready)
    response = post_fill(client, make_token)
    assert response.status_code == (202 if ready else 409), response.text
    if ready:
        assert seen['created'][0]['payload']['mode'] == 'vary'
        assert seen['reserved'] == [19]
    else:
        assert response.json()['error']['code'] == 'outfit_props_not_ready'
        assert seen['created'] == [] and seen['reserved'] == []
