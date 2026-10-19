"""ADR-0022 10/17: candidate-only sets, remembered optional slots, no paid reads."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import outfit, outfit_generation
from conftest import FakeR2, make_settings
from test_outfit import row


def args():
    return (make_settings(gemini_api_key='mock', outfit_recommendation_enabled=True), FakeR2(),
            {'clothingType': 'top'}, {}, [(b'new-contract', 'image/png')],
            [row('b1', 'bottom'), row('b2', 'bottom'), row('s1', 'shoes'), row('s2', 'shoes'), row('bag', 'bag')])


def sets():
    return [{'slots': {'bottom': 'b1', 'footwear': 's1'}, 'reason': '첫 코디', 'concept': 'tonal'},
            {'slots': {'bottom': 'b2', 'footwear': 's2'}, 'reason': '다른 코디', 'concept': 'contrast'}]


def test_accepts_two_distinct_sets_marks_membership_and_calls_model_once(monkeypatch):
    outfit.clear_cache()
    model = AsyncMock(return_value={'sets': sets(), 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}})
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', model)
    result = asyncio.run(outfit.recommend(*args(), slots=['Front']))
    recommendation = result['outfitRecommendation']
    assert recommendation['status'] == 'ready'
    assert recommendation['sets'] == sets()
    assert all(item['inSets'] for item in recommendation['candidates']['bottom'])
    assert recommendation['roleStatus']['accessory'] == 'unavailable'
    assert 'reason' not in recommendation
    assert model.await_count == 1


@pytest.mark.parametrize('bad', [
    [{'slots': {**sets()[0]['slots'], 'bottom': 'fabricated'}, 'reason': 'bad'}],
    [{'slots': {**sets()[0]['slots'], 'bottom': 's1'}, 'reason': 'bad'}],
    [{'slots': {**sets()[0]['slots'], 'inner': None}, 'reason': 'bad'}],
    [], sets() + sets(),
])
def test_invalid_sets_fall_back_without_partial_acceptance(monkeypatch, bad):
    outfit.clear_cache()
    model = AsyncMock(return_value={'sets': bad})
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', model)
    actual = asyncio.run(outfit.recommend(*args(), slots=['Front']))['outfitRecommendation']
    assert actual['status'] == 'fallback'
    assert len(actual['sets']) == 3
    assert model.await_count == 1
    assert {s['slots']['bottom'] for s in actual['sets']} <= {'b1', 'b2'}


def test_fallback_sets_are_diverse_and_order_independent():
    context = {'clothingType': 'top'}
    items = [row('b1', 'bottom'), row('b2', 'bottom'), row('s1', 'shoes'), row('s2', 'shoes')]
    first = outfit.fallback_sets(outfit.candidate_pools(items, context))
    second = outfit.fallback_sets(outfit.candidate_pools(list(reversed(items)), context))
    assert first == second
    assert [s['slots'] for s in first] == [
        {'bottom': 'b1', 'footwear': 's1'},
        {'bottom': 'b2', 'footwear': 's2'},
        {'bottom': 'b1', 'footwear': 's2'}]


def test_apply_set_preserves_user_empty_replaces_direct_pick_and_keeps_disabled_memory():
    current = {'version': 1, 'setIndex': 0, 'optional': {'bag': False, 'accessory': True}, 'slots': {
        'bottom': {'itemId': 'mine', 'origin': 'user'}, 'footwear': {'itemId': None, 'origin': 'user'},
        'bag': {'itemId': 'remembered', 'origin': 'user'}, 'accessory': {'itemId': 'old', 'origin': 'user'}}}
    actual = outfit.apply_set(current, {'sets': sets(), 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}}, 'top', 1)
    assert actual['setIndex'] == 1
    assert actual['slots']['bottom'] == {'itemId': 'b2', 'origin': 'ai'}
    assert actual['slots']['footwear'] == {'itemId': None, 'origin': 'user'}
    assert actual['slots']['bag'] == {'itemId': 'remembered', 'origin': 'user'}
    assert actual['slots']['accessory']['itemId'] is None
    assert actual['optional'] == {'bag': False, 'accessory': True}


def test_optional_defaults_toggle_memory_and_snapshot_exclusion():
    selected = outfit.normalize_selections(None, 'top')
    assert selected['optional'] == {'bag': False, 'accessory': False}
    enabled = outfit.toggle_optional(selected, {'sets': sets(), 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}}, 'top', 'bag', True)
    assert enabled['slots']['bag']['itemId'] == 'bag'
    disabled = outfit.toggle_optional(enabled, {'sets': sets(), 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}}, 'top', 'bag', False)
    assert disabled['slots']['bag']['itemId'] == 'bag'
    assert outfit.toggle_optional(disabled, {'sets': sets()[::-1], 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}}, 'top', 'bag', True)['slots']['bag']['itemId'] == 'bag'
    snapshot = outfit.generation_snapshot({'outfitSelections': disabled}, 'top', [])
    assert 'bag' not in snapshot['slots']
    assert 'bag' not in outfit_generation.from_payload({}, {'outfitSelections': disabled}, 'top')['slots']
    assert outfit.generation_snapshot({'outfitSelections': enabled}, 'top', [row('bag', 'bag')])['slots']['bag']['itemId'] == 'bag'


def test_pending_is_immediately_usable_and_repeated_start_joins_one_model(monkeypatch):
    outfit.clear_cache()
    async def run():
        release = asyncio.Event()
        async def model(*a, **kw):
            await release.wait()
            return {'sets': sets(), 'props': {'bag': {'status': 'fallback', 'picks': ['bag']}}}
        call = AsyncMock(side_effect=model)
        monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
        one = outfit.start_recommendation(*args(), slots=['Front'])
        two = outfit.start_recommendation(*args(), slots=['Front'])
        key = one['outfitRecommendation']['contextKey']
        assert one == two
        assert one['outfitRecommendation']['status'] == 'pending'
        assert one['outfitRecommendation']['fallbackReady'] is True
        assert len(one['outfitRecommendation']['sets']) == 3
        for _ in range(3):
            assert outfit.read_recommendation(key)['outfitRecommendation']['status'] == 'pending'
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*list(outfit._inflight.values()))
        assert outfit.read_recommendation(key)['outfitRecommendation']['status'] == 'ready'
        assert call.await_count == 1
    asyncio.run(run())


def test_queue_wait_is_bounded_and_does_not_call_model_after_timeout(monkeypatch):
    outfit.clear_cache()
    async def run():
        call = AsyncMock()
        monkeypatch.setattr(outfit.outfit_recommender, 'recommend', call)
        values = list(args())
        from dataclasses import replace
        values[0] = replace(values[0], outfit_timeout_seconds=0.01)
        blocked = asyncio.Semaphore(0)
        pending = outfit.start_recommendation(*values, slots=['Front'], semaphore=blocked)
        task = outfit._inflight[pending['outfitRecommendation']['contextKey']]
        try:
            await asyncio.wait_for(asyncio.shield(task), 0.1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        ready = outfit.read_recommendation(pending['outfitRecommendation']['contextKey'])
        assert ready['outfitRecommendation']['status'] == 'fallback'
        assert call.await_count == 0
    asyncio.run(run())


def test_single_set_does_not_mark_unselected_candidate_as_in_sets(monkeypatch):
    outfit.clear_cache()
    monkeypatch.setattr(outfit.outfit_recommender, 'recommend', AsyncMock(return_value={'sets': sets()[:1]}))
    result = asyncio.run(outfit.recommend(*args(), slots=['Front']))
    assert len(result['outfitRecommendation']['sets']) == 1
    assert [(item['id'], item['inSets']) for item in result['outfitRecommendation']['candidates']['bottom']] == [('b1', True), ('b2', False)]


@pytest.mark.parametrize('changed', [{'fit': 'wide'}, {'length': 'long'}, {'name': 'new description'}])
def test_catalog_prompt_metadata_changes_reuse_key(changed):
    values = list(args())
    values[5][0].update(fit='slim', length='regular')
    before = outfit.prepare_recommendation(*values, slots=['Front'])['outfitRecommendation']['contextKey']
    values[5][0].update(changed)
    after = outfit.prepare_recommendation(*values, slots=['Front'])['outfitRecommendation']['contextKey']
    assert before != after


def test_new_context_resets_index_before_optional_current_set_lookup():
    values = list(args())
    values[3] = {'outfitSelections': {'version': 1, 'setIndex': 2, 'slots': {
        'bottom': {'itemId': 'old-bottom', 'origin': 'ai'}}, 'optional': {'bag': False, 'accessory': False}}}
    values[5] = [row('b1', 'bottom'), row('bag', 'bag')]
    fields = outfit.prepare_recommendation(*values, slots=['Front'])
    assert fields['outfitSelections']['setIndex'] == 0
    toggled = outfit.toggle_optional(fields['outfitSelections'], fields['outfitRecommendation'], 'top', 'bag', True)
    assert toggled['slots']['bag']['itemId'] is None  # Starts demand-only props separately.


def test_new_context_cache_hit_does_not_restore_previous_context_index():
    outfit.clear_cache()
    from dataclasses import replace
    values = list(args())
    values[0] = replace(values[0], outfit_recommendation_enabled=False)
    values[3] = {'outfitRecommendation': {'contextKey': 'old-context'}, 'outfitSelections': {
        'version': 1, 'setIndex': 2, 'slots': {'bottom': {'itemId': 'old-bottom', 'origin': 'ai'}},
        'optional': {'bag': False, 'accessory': False}}}
    values[5] = [row('b1', 'bottom'), row('bag', 'bag')]
    first = outfit.start_recommendation(*values, slots=['Front'])
    cached = outfit.start_recommendation(*values, slots=['Front'])
    assert first['outfitSelections']['setIndex'] == 0
    assert cached['outfitSelections']['setIndex'] == 0
    assert cached['outfitSelections']['slots']['bottom']['itemId'] == 'b1'
    assert outfit.toggle_optional(cached['outfitSelections'], cached['outfitRecommendation'], 'top', 'bag', True)['slots']['bag']['itemId'] is None
