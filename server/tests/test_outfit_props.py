"""10/19 on-demand props: independent requests and authoritative selections."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.services import outfit
from conftest import FakeR2, make_settings, auth_headers
from test_outfit import row
from test_outfit_routes import state


def accessories():
    return [row(str(i), 'accessory', category=category, color_group=color, sort_order=i)
            for i, (category, color) in enumerate([('귀걸이', 'gold'), ('귀걸이', 'silver'),
                ('귀걸이', 'black'), ('목걸이', 'white'), ('반지', 'blue'), ('모자', 'brown')])]


def test_initial_recommendation_has_only_clothes_and_shoes():
    result = outfit.prepare_recommendation(make_settings(), FakeR2(), {'clothingType': 'top'}, {},
        [(b'front', 'image/png')], [row('bottom', 'bottom'), row('shoe', 'shoes'), row('bag', 'bag'), *accessories()])
    rec = result['outfitRecommendation']
    assert rec['sets'][0]['slots'] == {'bottom': 'bottom', 'footwear': 'shoe'}
    assert len(rec['candidates']['accessory']) == 6
    assert rec['candidates']['bag'][0]['id'] == 'bag'


def test_extra_ids_are_catalog_valid_distinct_categories_and_bounded():
    items = accessories() + [row('bag', 'bag'), row('off', 'accessory', category='시계', is_active=False)]
    value = {'version': 1, 'slots': {'accessory': {'itemId': '0', 'extraIds': ['0', '1', 'bag', 'off', '3', '4', '5'], 'origin': 'user'}}}
    selected = outfit.normalize_selections(value, 'top', allowed_items=items, strict=True)
    assert selected['slots']['accessory'] == {'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}
    value['slots']['accessory']['itemId'] = None
    assert outfit.normalize_selections(value, 'top', allowed_items=items, strict=True)['slots']['accessory'] == {'itemId': None, 'origin': 'user'}


def test_props_cycle_ignores_legacy_set_props_and_clears_extras():
    value = {'version': 1, 'setIndex': 0, 'optional': {'bag': True, 'accessory': True}, 'slots': {
        'accessory': {'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}, 'bag': {'itemId': None, 'origin': 'user'}}}
    rec = {'sets': [{'slots': {'bottom': 'bottom', 'bag': 'legacy', 'accessory': 'legacy'}}] * 3,
        'props': {'bag': {'status': 'fallback', 'picks': ['bag']}, 'accessory': {'status': 'ready', 'picks': ['3', '4']}}}
    result = outfit.apply_set(value, rec, 'top', 2)
    assert result['slots']['accessory'] == {'itemId': '3', 'origin': 'ai'}
    assert result['slots']['bag'] == {'itemId': None, 'origin': 'user'}
    assert outfit.toggle_optional({'version': 1, 'slots': {}, 'setIndex': 1}, rec, 'top', 'accessory', True)['slots']['accessory']['itemId'] == '4'


def test_props_off_is_immediate_and_key_ignores_all_seller_choices():
    settings = make_settings()
    product, analysis, source, items = {'clothingType': 'top'}, {}, [(b'front', 'image/png')], accessories()
    first = outfit.start_props(settings, FakeR2(), product, analysis, source, items, 'accessory')
    prop = first['outfitRecommendation']['props']['accessory']
    assert prop == {'status': 'fallback', 'picks': ['0', '3', '4']}
    changed = {'outfitSelections': {'version': 1, 'slots': {'accessory': {'itemId': '5', 'origin': 'user'}}}}
    assert outfit.props_key(settings, product, analysis, source, None, items, 'accessory') == outfit.props_key(settings, product, changed, source, None, items, 'accessory')
    pools = outfit.props_candidates(items, outfit.product_context(product, analysis), 'accessory', limit=6)
    assert sum(item['category'] == '귀걸이' for item in pools) == 2


@pytest.mark.parametrize('response,status', [({'picks': ['3', '4'], 'reason': '상품과 어울려요'}, 'ready'),
    ({'picks': ['forged'], 'reason': 'wrong'}, 'fallback'), ({'picks': ['0', '0'], 'reason': 'duplicate'}, 'fallback')])
def test_paid_props_one_call_reuses_persisted_result_after_cache_eviction(monkeypatch, response, status):
    outfit.clear_cache()
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', call, raising=False)
    async def run():
        settings = make_settings(gemini_api_key='mock', outfit_recommendation_enabled=True)
        args = (settings, FakeR2(), {'clothingType': 'top'}, {}, [(b'props', 'image/png')], accessories(), 'accessory')
        one = outfit.start_props(*args)
        two = outfit.start_props(*args)
        key = one['outfitRecommendation']['propsContextKeys']['accessory']
        assert two['outfitRecommendation']['props']['accessory']['status'] == 'pending'
        result = await outfit._inflight[key]
        assert result['outfitRecommendation']['props']['accessory']['status'] == status
        outfit.clear_cache()
        again = outfit.start_props(settings, FakeR2(), args[2], result, args[4], args[5], 'accessory')
        assert again['outfitRecommendation']['props']['accessory']['status'] == status
        assert call.await_count == 1
        assert set(call.call_args.args[3]) == {'accessory'}
        assert 'fixed' not in call.call_args.kwargs
    asyncio.run(run())


def test_props_timeout_returns_deterministic_picks(monkeypatch):
    outfit.clear_cache()
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend_props', slow, raising=False)
    async def run():
        settings = make_settings(gemini_api_key='mock', outfit_recommendation_enabled=True, outfit_props_timeout_seconds=.001)
        result = outfit.start_props(settings, FakeR2(), {'clothingType': 'top'}, {}, [(b'timeout', 'image/png')], accessories(), 'accessory')
        key = result['outfitRecommendation']['propsContextKeys']['accessory']
        done = await outfit._inflight[key]
        assert done['outfitRecommendation']['props']['accessory'] == {'status': 'fallback', 'picks': ['0', '3', '4']}
    asyncio.run(run())


def test_role_post_updates_only_recommendation_and_pending_snapshot_uses_fallback(client, make_token, state, monkeypatch):
    from app import repo
    monkeypatch.setattr(repo, "get_analysis_for_update", AsyncMock(side_effect=lambda *args: deepcopy(state[0])))
    outfit.clear_cache()
    saved, product, items = state
    items.extend(accessories())
    saved.update(outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved, [(b'x', 'image/png')], items))
    saved['outfitSelections']['optional']['accessory'] = True
    saved['outfitSelections']['slots']['bottom'] = {'itemId': None, 'origin': 'user'}
    original = deepcopy(saved['outfitSelections'])
    result = client.post('/v1/projects/p1/analysis/outfit-recommendations', headers=auth_headers(make_token), json={'role': 'accessory'})
    assert result.status_code == 200, result.text
    assert saved['outfitSelections'] == original
    assert result.json()['outfitRecommendation']['props']['accessory']['picks'] == ['0', '3', '4']
    saved['outfitRecommendation']['props']['accessory']['status'] = 'pending'
    frozen = outfit.generation_snapshot(saved, 'top', items)
    assert frozen['slots']['accessory']['itemId'] == '0'


def test_thumbnail_fetches_overlap_but_images_and_manifest_keep_order():
    from app.agents import outfit_recommender as agent
    from io import BytesIO
    from PIL import Image
    from threading import Event, Lock
    context = {'clothingType': 'top'}
    def png(color):
        data = BytesIO()
        Image.new('RGB', (8, 8), color).save(data, format='PNG')
        return data.getvalue()
    class Images(FakeR2):
        def __init__(self):
            self.started = 0
            self.lock = Lock()
            self.both = Event()
        def get_bytes(self, key):
            with self.lock:
                self.started += 1
                if self.started == 2:
                    self.both.set()
            assert self.both.wait(1), 'thumbnail requests must overlap'
            return png('red' if key.startswith('a-') else 'blue')
    request = asyncio.run(agent.build_request(Images(), [(png('white'), 'image/png')],
        {'bottom': [row('a', 'bottom'), row('b', 'bottom')]}, context=context, fixed={'slots': {}}))
    assert '2. CANDIDATE role=bottom, id=a\n3. CANDIDATE role=bottom, id=b' in request['prompt']
    decoded = [Image.open(BytesIO(image.data)).getpixel((0, 0)) for image in request['images']]
    assert decoded[1][0] > 240 and decoded[1][2] < 10
    assert decoded[2][2] > 240 and decoded[2][0] < 10


def test_public_props_status_and_promotion_revalidate_signed_ids(client, make_token, state, monkeypatch):
    import json
    from app import repo
    from conftest import FakePool
    outfit.clear_cache()
    saved, product, items = state
    items.extend(accessories())
    client.app.state.pool = FakePool()
    monkeypatch.setattr(repo, 'list_curated_matching_items', AsyncMock(return_value=items))
    photo = b'\x89PNG\r\n\x1a\nphoto'
    files = [('images', ('front.png', photo, 'image/png'))]
    context = json.dumps({'clothingType': 'top', 'subCategory': 'knit', 'targetGenders': ['women']})
    first = client.post('/v1/public/outfit-recommendations', files=files, data={'analysisContext': context})
    assert first.status_code == 200, first.text
    fields = first.json()['data']
    response = client.post('/v1/public/outfit-recommendations', files=files,
        data={'analysisContext': context, 'role': 'accessory', 'handoff': json.dumps(fields[outfit.HANDOFF_KEY])})
    assert response.status_code == 200, response.text
    fields = response.json()['data']
    assert fields['outfitRecommendation']['props']['accessory'] == {'status': 'fallback', 'picks': ['0', '3', '4']}
    status = client.post('/v1/public/outfit-recommendations/status', json={'handoff': fields[outfit.HANDOFF_KEY]})
    assert status.status_code == 200, status.text
    assert status.json()['data']['outfitRecommendation']['props']['accessory']['picks'] == ['0', '3', '4']
    monkeypatch.setattr(client.app.state.r2, 'get_bytes', lambda key: photo)
    promoted = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': fields[outfit.HANDOFF_KEY]})
    assert promoted.status_code == 200, promoted.text
    assert saved['outfitRecommendation']['props']['accessory']['picks'] == ['0', '3', '4']
    items[:] = [item for item in items if item['id'] != '3']
    rejected = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': fields[outfit.HANDOFF_KEY]})
    assert rejected.status_code == 400


def test_props_background_completion_preserves_latest_user_choice_and_other_prop(monkeypatch):
    from app import repo
    from app.services import outfit_jobs
    from conftest import fake_worker_app
    async def run():
        outfit.clear_cache()
        app = fake_worker_app(make_settings())
        product, items = {'clothingType': 'top'}, accessories() + [row('bag', 'bag')]
        fields = outfit.start_props(app.state.settings, FakeR2(), product, {}, [(b'front', 'image/png')], items, 'accessory')
        key = fields['outfitRecommendation']['propsContextKeys']['accessory']
        fields['outfitRecommendation']['props']['accessory']['status'] = 'pending'
        saved = deepcopy(fields)
        saved['outfitSelections'] = {'version': 1, 'slots': {'accessory': {'itemId': '5', 'origin': 'user'}}}
        model = asyncio.get_running_loop().create_future()
        outfit._inflight[key] = model
        monkeypatch.setattr(repo, 'get_analysis_for_update', AsyncMock(side_effect=lambda *args: deepcopy(saved)))
        monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value=product))
        monkeypatch.setattr(outfit, 'load_items', AsyncMock(return_value=items))
        async def save(conn, pid, patch, **kwargs):
            assert set(patch) == {'outfitRecommendation'}
            saved.update(deepcopy(patch))
        monkeypatch.setattr(repo, 'save_outfit_recommendation', AsyncMock(side_effect=save))
        try:
            outfit_jobs.follow_props(app, 'u1', 'p1', product, {}, fields, 'accessory')
            saved['outfitRecommendation'].setdefault('props', {})['bag'] = {'status': 'fallback', 'picks': ['bag']}
            result = deepcopy(fields)
            result['outfitRecommendation']['props']['accessory'] = {'status': 'ready', 'picks': ['4', '5']}
            model.set_result(result)
            await asyncio.gather(*app.state.outfit_tasks.values())
            assert repo.save_outfit_recommendation.await_count == 1
            assert saved['outfitSelections']['slots']['accessory'] == {'itemId': '5', 'origin': 'user'}
            assert saved['outfitRecommendation']['props']['bag']['picks'] == ['bag']
            assert saved['outfitRecommendation']['props']['accessory']['picks'] == ['4', '5']
        finally:
            outfit._inflight.pop(key, None)
    asyncio.run(run())


@pytest.mark.parametrize('value,expected', [(None, 10), ('0', 1), ('99', 30), ('bad', 10)])
def test_props_timeout_setting_is_bounded(monkeypatch, value, expected):
    from app.config import load_settings
    monkeypatch.delenv('OUTFIT_PROPS_TIMEOUT_SECONDS', raising=False)
    if value is not None:
        monkeypatch.setenv('OUTFIT_PROPS_TIMEOUT_SECONDS', value)
    assert load_settings().outfit_props_timeout_seconds == expected


def test_server_matches_shared_frontend_extra_ids_and_set_contract():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/outfit-props-contract.json').read_text())
    for case in fixture['normalize']:
        actual = outfit.normalize_selections(case['input'], 'top', allowed_items=fixture['items'], strict=True)
        assert actual['slots']['accessory'] == case['accessory'], case['name']
    case = fixture['apply']
    assert outfit.apply_set(case['input'], case['recommendation'], 'top', case['index']) == case['expected']


def test_concurrent_role_requests_merge_in_base_cache_and_ignore_stale_pending():
    outfit.clear_cache()
    settings, r2, product, source = make_settings(), FakeR2(), {'clothingType': 'top'}, [(b'both-props', 'image/png')]
    items = [row('bottom', 'bottom'), row('bag', 'bag'), *accessories()]
    original = outfit.start_recommendation(settings, r2, product, {}, source, items)
    first = outfit.start_props(settings, r2, product, original, source, items, 'bag')
    second = outfit.start_props(settings, r2, product, original, source, items, 'accessory')
    assert set(second['outfitRecommendation']['props']) == {'bag', 'accessory'}
    key = original['outfitRecommendation']['contextKey']
    outfit._cache_result(key, original)  # An initial completion captured before props started.
    cached = outfit.read_recommendation(key)
    assert set(cached['outfitRecommendation']['props']) == {'bag', 'accessory'}
    assert first['outfitRecommendation']['props']['bag']['picks'] == ['bag']


def test_enabling_an_ai_slot_uses_current_prop_pick_but_user_memory_survives():
    rec = {'props': {'bag': {'status': 'fallback', 'picks': ['first', 'second']}}}
    value = {'version': 1, 'setIndex': 1, 'slots': {'bag': {'itemId': 'old-ai', 'origin': 'ai'}}}
    assert outfit.toggle_optional(value, rec, 'top', 'bag', True)['slots']['bag']['itemId'] == 'second'
    value['slots']['bag']['origin'] = 'user'
    assert outfit.toggle_optional(value, rec, 'top', 'bag', True)['slots']['bag']['itemId'] == 'old-ai'


def test_promotion_subscribes_to_pending_props_after_initial_recommendation_finished(client, make_token, state, monkeypatch):
    from app.services import outfit_jobs
    from unittest.mock import Mock
    saved, product, items = state
    items.extend(accessories())
    photo = b'\x89PNG-public-pending'
    monkeypatch.setattr(client.app.state.r2, 'get_bytes', lambda key: photo)
    fields = outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved, [(photo, 'image/png')], items)
    fields.update(outfit.start_props(client.app.state.settings, FakeR2(), product, fields, [(photo, 'image/png')], items, 'accessory'))
    fields['outfitRecommendation']['props']['accessory']['status'] = 'pending'
    handoff = outfit.issue_handoff(fields, [(photo, 'image/png')], ['Front'], client.app.state.settings.r2_secret_access_key,
        context=outfit.product_context(product, saved))
    outfit.clear_cache()
    follow = Mock()
    monkeypatch.setattr(outfit_jobs, 'follow_props', follow)
    response = client.post('/v1/projects/p1/analysis/outfit-recommendations:promote',
        headers=auth_headers(make_token), json={'handoff': handoff})
    assert response.status_code == 200, response.text
    assert follow.call_count == 1
    assert follow.call_args.args[-1] == 'accessory'


def test_explicit_set_command_saves_displayed_props_even_after_late_server_ready(client, make_token, state):
    saved, product, items = state
    items.extend(accessories())
    saved.update(outfit.prepare_recommendation(client.app.state.settings, FakeR2(), product, saved, [(b'set', 'image/png')], items))
    saved['outfitSelections']['optional']['accessory'] = True
    saved['outfitSelections']['slots']['accessory'] = {'itemId': '0', 'extraIds': ['3', '4'], 'origin': 'user'}
    saved['outfitSelections']['slots']['bag'] = {'itemId': None, 'origin': 'user'}
    saved['outfitRecommendation']['sets'] *= 3
    saved['outfitRecommendation']['props'] = {'accessory': {'status': 'ready', 'picks': ['5']}}
    displayed = deepcopy(saved['outfitSelections'])
    displayed['setIndex'] = 1
    displayed['slots']['accessory'] = {'itemId': '3', 'origin': 'ai'}
    response = client.patch('/v1/projects/p1/analysis?merge=1', headers=auth_headers(make_token),
        json={'outfitSetIndex': 1, 'outfitSelections': displayed})
    assert response.status_code == 200, response.text
    assert saved['outfitSelections']['slots']['accessory'] == {'itemId': '3', 'origin': 'ai'}
    assert saved['outfitSelections']['slots']['bag'] == {'itemId': None, 'origin': 'user'}
    assert saved['outfitSelections']['setIndex'] == 1


def test_confirmation_before_props_post_persists_still_freezes_deterministic_pick():
    analysis = {'outfitSelections': {'version': 1, 'setIndex': 1, 'optional': {'accessory': True},
        'slots': {'accessory': {'itemId': None, 'origin': 'ai'}}}}
    frozen = outfit.generation_snapshot(analysis, 'top', accessories())
    assert frozen['slots']['accessory'] == {'itemId': '3', 'origin': 'ai'}
    analysis['outfitSelections']['slots']['accessory'] = {'itemId': '5', 'origin': 'user'}
    assert outfit.generation_snapshot(analysis, 'top', accessories())['slots']['accessory']['itemId'] == '5'


def test_pending_confirmation_uses_fallback_not_old_ai_memory():
    analysis = {'outfitRecommendation': {'props': {'accessory': {'status': 'pending', 'picks': ['0', '3', '4']}}},
        'outfitSelections': {'version': 1, 'setIndex': 2, 'optional': {'accessory': True},
            'slots': {'accessory': {'itemId': '5', 'origin': 'ai'}}}}
    assert outfit.generation_snapshot(analysis, 'top', accessories())['slots']['accessory'] == {'itemId': '4', 'origin': 'ai'}
