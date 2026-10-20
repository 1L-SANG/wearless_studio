"""Draft preparation uses no real storage, database or model provider."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app import repo
from app.services import draft_visuals as dv
from conftest import make_settings

UID = '11111111-1111-1111-1111-111111111111'
SOURCE = '22222222-2222-2222-2222-222222222222'
TOKEN = '33333333-3333-3333-3333-333333333333'

def slot():
    return {'active_token': TOKEN, 'expires_at': datetime.now(timezone.utc) + timedelta(days=7),
        'payload': {'product': {'colors': [{'images': [{'id': 'wrong', 'slot': 'Front'}]},
            {'isBase': True, 'images': [{'id': SOURCE, 'slot': 'Front'}]}]},
            'customMatch': {'localId': 'local', 'sources': [{'id': SOURCE}]}}}

def app(enabled='on'):
    return SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(draft_visuals=enabled,
        draft_visuals_daily_budget_usd=5, r2_bucket='test'),
        r2=SimpleNamespace(get_bytes=lambda key: b'original', public_url=lambda key: f'https://asset.test/{key}')))

def test_settings_default_off_and_on_requires_fal_key():
    assert make_settings().draft_visuals == 'off'
    with pytest.raises(ValueError, match='FAL_KEY'):
        make_settings(draft_visuals='on', fal_key=None)
    assert make_settings(draft_visuals='on', fal_key='test').draft_visuals == 'on'

def test_current_source_uses_server_base_color_and_custom_order():
    assert dv.current_sources(slot(), 'product') == [SOURCE]
    assert dv.current_sources(slot(), 'custom', 'local') == [SOURCE]
    assert dv.current_sources(slot(), 'custom', 'other') == []

def test_disabled_prepare_has_no_db_or_storage_side_effects():
    assert asyncio.run(dv.prepare(app('off'), None, user_id=UID, token=TOKEN,
        role='product', source_asset_ids=[SOURCE])) == {'state': 'off', 'jobId': None, 'sourceAssetIds': [SOURCE]}

@pytest.mark.parametrize('bad_token,ids', [('bad', [SOURCE]), (TOKEN, ['wrong'])])
def test_prepare_rejects_wrong_token_or_replaced_source(monkeypatch, bad_token, ids):
    monkeypatch.setattr(repo, 'lock_draft_slot', AsyncMock(return_value=slot()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dv.prepare(app(), None, user_id=UID, token=bad_token, role='product', source_asset_ids=ids))
    assert exc.value.status_code in (404, 409)

def patch_prepare(monkeypatch):
    monkeypatch.setattr(repo, 'lock_draft_slot', AsyncMock(return_value=slot()))
    monkeypatch.setattr(repo, 'get_asset_for_user', AsyncMock(return_value={
        'id': SOURCE, 'source': 'upload', 'project_id': None, 'r2_key': 'raw', 'metadata': {'purpose': 'draft_slot'}}))
    monkeypatch.setattr(repo, 'lock_draft_visual_budget', AsyncMock())
    monkeypatch.setattr(repo, 'bind_draft_visual_reference', AsyncMock())

def test_prepare_joins_identical_hash_before_counting_limits(monkeypatch):
    patch_prepare(monkeypatch)
    existing = {'id': 'job', 'status': 'running', 'payload': {'mode': 'draft_visuals'}, 'result': None}
    monkeypatch.setattr(repo, 'find_draft_visual_job', AsyncMock(return_value=existing))
    monkeypatch.setattr(repo, 'draft_visual_usage', AsyncMock(side_effect=AssertionError('cache must not spend quota')))
    result = asyncio.run(dv.prepare(app(), None, user_id=UID, token=TOKEN, role='product', source_asset_ids=[SOURCE]))
    assert result['jobId'] == 'job' and result['state'] == 'processing'

@pytest.mark.parametrize('usage', [dict(daily=20, active=0, reserved=0), dict(daily=0, active=2, reserved=0),
                                   dict(daily=0, active=0, reserved=5)])
def test_quota_declines_only_optional_visuals(monkeypatch, usage):
    patch_prepare(monkeypatch)
    monkeypatch.setattr(repo, 'find_draft_visual_job', AsyncMock(return_value=None))
    monkeypatch.setattr(repo, 'draft_visual_usage', AsyncMock(return_value=usage))
    monkeypatch.setattr(repo, 'create_job', AsyncMock(side_effect=AssertionError('quota cannot enqueue')))
    result = asyncio.run(dv.prepare(app(), None, user_id=UID, token=TOKEN, role='product', source_asset_ids=[SOURCE]))
    assert result['state'] == 'off' and result['reason'] == 'limit'

def test_visual_response_uses_existing_front_contract():
    result = dv.presentation(app(), {'id': 'job', 'status': 'done', 'result': {'state': 'ready',
        'tidy': {'key': 'tidy', 'width': 768, 'height': 1024}}}, role='product', source_asset_ids=[SOURCE])
    assert result['front']['tidyUrl'] == 'https://asset.test/tidy'
    assert (result['front']['width'], result['front']['height']) == (768, 1024)
    assert result['sourceAssetIds'] == [SOURCE]


def test_product_visual_response_prefers_board_url_and_its_dimensions():
    result = dv.presentation(app(), {'id': 'job', 'status': 'done', 'result': {'state': 'ready',
        'tidy': {'key': 'tidy', 'width': 768, 'height': 1024},
        'board': {'key': 'board', 'width': 341, 'height': 512}}}, role='product', source_asset_ids=[SOURCE])
    assert result['front']['tidyUrl'] == 'https://asset.test/board'
    assert (result['front']['width'], result['front']['height']) == (341, 512)

def test_retry_pusher_never_reissues_draft_paid_work():
    from app.workers.sam_retry_pusher import SamRetryPusher
    job = {'payload': {'mode': 'draft_visuals'}, 'result': {'state': 'unavailable'}}
    assert asyncio.run(SamRetryPusher(app())._push_one(None, None, job)) is False


def test_draft_provider_does_not_retry_ambiguous_5xx(monkeypatch):
    import httpx
    from test_fal_api import _transport
    from app.services import fal_api
    from app.services.segmentation_types import SamUnavailable
    posts = []
    _transport(monkeypatch, lambda request: (posts.append(request) or httpx.Response(503)))
    async def scenario():
        async def current():
            pass
        with fal_api.draft_request_scope(current):
            with pytest.raises(SamUnavailable):
                await fal_api.run_model(SimpleNamespace(fal_key='test'), fal_api.SAM_MODEL, {})
    asyncio.run(scenario())
    assert len(posts) == 1


def test_linked_front_enqueues_only_back_and_no_product_tidy(monkeypatch):
    from app import routes
    captured = []
    monkeypatch.setattr(repo, 'get_draft_visual_promotion', AsyncMock(return_value={'id': 'draft', 'status': 'running',
        'metadata': {'promotions': [{'projectId': 'project', 'sourceAssetIds': [SOURCE]}]}}))
    async def create(conn, **kw):
        captured.append(kw)
        return {'payload': kw['payload']}, True
    monkeypatch.setattr(repo, 'create_job', create)
    product = {'colors': [{'isBase': True, 'images': [{'id': SOURCE, 'slot': 'Front'}, {'id': TOKEN, 'slot': 'Back'}]}]}
    asyncio.run(routes._enqueue_canonical_cutout(None, user_id=UID, project_id='project',
        product=product, tidy_enabled=True, draft_visuals_enabled=True))
    assert len(captured) == 1
    assert captured[0]['payload'].get('skipDraftFront') is True
    assert captured[0]['payload'].get('productTidyRequired') is not True


def test_promotion_rejects_foreign_job_without_reading_storage(monkeypatch):
    monkeypatch.setattr(repo, 'lock_draft_visual_job', AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dv.promote(app(), None, user_id=UID, job_id=TOKEN,
                              project_id=SOURCE, source_asset_ids=[SOURCE]))
    assert exc.value.status_code == 404


def test_authenticated_routes_reject_missing_jwt(client):
    assert client.get('/v1/draft-slot/visuals').status_code == 401
    assert client.post('/v1/draft-slot/visuals:prepare', json={'role': 'product', 'sourceAssetIds': [SOURCE]}).status_code == 401


def test_prepare_route_budget_storage_failure_is_optional(client, make_token, monkeypatch):
    from contextlib import asynccontextmanager
    from app import routes
    class Conn:
        async def rollback(self): pass
    @asynccontextmanager
    async def conn(request): yield Conn()
    monkeypatch.setattr(routes, 'get_conn', conn)
    monkeypatch.setattr(dv, 'prepare', AsyncMock(side_effect=RuntimeError('quota store unavailable')))
    response = client.post('/v1/draft-slot/visuals:prepare', json={'role': 'product', 'sourceAssetIds': [SOURCE]},
                           headers={'Authorization': f'Bearer {make_token()}', 'X-Draft-Token': TOKEN})
    assert response.status_code == 200
    assert response.json()['state'] == 'off'
    assert response.json()['reason'] == 'unavailable'


@pytest.mark.parametrize('fail_provider', [False, True])
def test_worker_and_lease_recovery_never_repeat_paid_calls(monkeypatch, fail_provider):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    import hashlib
    import io
    from PIL import Image
    from app.services.segmentation_types import SamViewResult
    stream = io.BytesIO()
    Image.new('RGBA', (30, 40), (140, 20, 30, 200)).save(stream, 'PNG')
    data = stream.getvalue()
    from test_product_tidy import padded_tidy_png
    tidy = padded_tidy_png()
    storage = {'raw': data, 'canonical': data}
    source = {'id': SOURCE, 'key': 'raw', 'hash': hashlib.sha256(data).hexdigest()}
    state = {'id': TOKEN, 'user_id': UID, 'project_id': None, 'lease_token': 'lease-1', 'status': 'running',
             'payload': {'mode': dv.MODE, 'version': dv.VERSION, 'role': 'product', 'sources': [source]}, 'metadata': {}}
    class Conn:
        async def commit(self): pass
        @asynccontextmanager
        async def transaction(self): yield self
    @asynccontextmanager
    async def conn(): yield Conn()
    instance = app()
    instance.state.pool = SimpleNamespace(connection=conn)
    instance.state.r2 = SimpleNamespace(get_bytes=storage.__getitem__,
        put_bytes=lambda key, image, mime: storage.__setitem__(key, image))
    async def lock(*args): return deepcopy(state)
    async def update(conn, job_id, *, payload=None, metadata=None):
        state['metadata'].update(metadata or {})
    async def finalize(conn, **kw):
        state.update(status=kw['status'], result=kw['result'])
        return True
    monkeypatch.setattr(repo, 'lock_draft_visual_job', lock)
    monkeypatch.setattr(repo, 'update_draft_visual_job', update)
    monkeypatch.setattr(repo, 'finalize_uncharged_job', finalize)
    monkeypatch.setattr(repo, 'draft_visual_is_current', AsyncMock(return_value=True))
    monkeypatch.setattr(repo, 'lock_draft_visual_budget', AsyncMock())
    monkeypatch.setattr(repo, 'draft_visual_usage', AsyncMock(return_value={
        'reserved': 0, 'budget_day': '2026-10-20'}))
    monkeypatch.setattr(repo, 'create_asset', AsyncMock(return_value={'id': SOURCE}))
    calls = {'sam': 0, 'qwen': 0}
    async def segment(*args):
        calls['sam'] += 1
        return {'Front': SamViewResult(view='Front', ready=True, cutout_key='canonical', source_hash=source['hash'],
            model_version='fal-ai/sam-3/image', algorithm_version='fal-sam3-v1', checksum='test',
            width=30, height=40, byte_size=len(data), area_frac=.5, cached=False)}
    async def render(settings, cut, *, transparent, on_generated):
        calls['qwen'] += 1
        if fail_provider: raise RuntimeError('ambiguous provider response')
        await on_generated(tidy)
        return tidy
    monkeypatch.setattr(dv.fal_cutout, 'segment_garment', segment)
    monkeypatch.setattr(dv.qwen_flatlay, 'render', render)
    monkeypatch.setattr(dv.matching_cutout, 'cutout_color_agrees', lambda *_: (True, {}))
    asyncio.run(dv.run(instance, deepcopy(state)))
    assert state['result']['state'] == ('failed' if fail_provider else 'ready')
    if not fail_provider:
        result = state['result']
        assert storage[result['tidy']['key']] == tidy
        assert (result['tidy']['width'], result['tidy']['height']) == (768, 1024)
        with Image.open(io.BytesIO(storage[result['board']['key']])) as board:
            assert board.size == (341, 512)
            assert board.getchannel('A').getbbox() == (0, 0, 341, 512)
        assert (result['board']['width'], result['board']['height']) == (341, 512)
        assert result['board']['metadata']['purpose'] == 'draft_visuals'
        assert result['board']['metadata']['draftVisualJobId'] == TOKEN
    state.update(status='running', lease_token='lease-2')
    asyncio.run(dv.run(instance, deepcopy(state)))
    assert calls == {'sam': 1, 'qwen': 1}


def test_reclaimed_ready_cache_falls_back_to_original_without_new_paid_work(monkeypatch):
    patch_prepare(monkeypatch)
    async def asset(conn, user_id, aid):
        return {'id': SOURCE, 'source': 'upload', 'r2_key': 'raw', 'metadata': {'purpose': 'draft_slot'}} if aid == SOURCE else None
    monkeypatch.setattr(repo, 'get_asset_for_user', asset)
    job = {'id': 'job', 'status': 'done', 'payload': {'mode': dv.MODE}, 'result': {'state': 'ready',
        'tidy': {'assetId': 'reclaimed', 'key': 'deleted/tidy', 'width': 768, 'height': 1024}}}
    monkeypatch.setattr(repo, 'find_draft_visual_job', AsyncMock(return_value=job))
    monkeypatch.setattr(repo, 'create_job', AsyncMock(side_effect=AssertionError('must not reissue expired paid job')))
    result = asyncio.run(dv.prepare(app(), None, user_id=UID, token=TOKEN,
                                   role='product', source_asset_ids=[SOURCE]))
    assert result['state'] == 'failed'
    assert result['front']['tidyUrl'] is None


@pytest.mark.parametrize('has_client_id', [False, True])
def test_existing_custom_promotion_still_joins_when_preparation_switch_is_off(monkeypatch, has_client_id):
    from app import routes
    from test_matching_cutout_enqueue import _Conn
    monkeypatch.setattr(repo, 'get_draft_visual_promotion', AsyncMock(return_value={'id': 'existing', 'status': 'running',
        'metadata': {'promotions': [{'projectId': TOKEN, 'sourceAssetIds': [SOURCE], 'matchingItemId': 'custom'}]}}))
    monkeypatch.setattr(repo, 'has_draft_visual_work', AsyncMock(return_value=True))
    monkeypatch.setattr(dv, 'join_project', AsyncMock(return_value={'id': 'existing', 'status': 'running',
        'metadata': {'promotions': [{'projectId': TOKEN, 'sourceAssetIds': [SOURCE], 'matchingItemId': 'custom'}]}}))
    monkeypatch.setattr(repo, 'create_job', AsyncMock(side_effect=AssertionError('cannot charge again')))
    assert asyncio.run(routes._enqueue_matching_cutout(_Conn(), settings=SimpleNamespace(
        draft_visuals='off', matching_cutout='on'), user_id=UID, project_id=TOKEN,
        matching_item_id='custom', source_asset_ids=[SOURCE], source_keys=['raw'],
        draft_visual_job_id=SOURCE if has_client_id else None, app=None if has_client_id else app())) is True


def test_custom_join_lookup_failure_keeps_the_project_fallback_available(monkeypatch):
    from app import routes
    from test_matching_cutout_enqueue import _Conn
    monkeypatch.setattr(repo, 'get_draft_visual_promotion', AsyncMock(side_effect=RuntimeError('lookup unavailable')))
    monkeypatch.setattr(repo, 'create_job', AsyncMock(return_value=({'id': 'fallback'}, True)))
    conn = _Conn()
    assert asyncio.run(routes._enqueue_matching_cutout(conn, settings=SimpleNamespace(
        draft_visuals='on', matching_cutout='on'), user_id=UID, project_id=TOKEN,
        matching_item_id='custom', source_asset_ids=[SOURCE], source_keys=['raw'])) is True
    assert conn.rollbacks == 1 and conn.commits == 1


@pytest.mark.parametrize('enabled', [False, True])
def test_account_exposes_draft_visual_capability(client, make_token, monkeypatch, enabled):
    from dataclasses import replace
    from app import routes
    from conftest import patch_route_db
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, 'get_account', AsyncMock(return_value={
        'name': 'seller', 'avatar': '', 'credits': 0, 'plan': 'free'}))
    client.app.state.settings = replace(client.app.state.settings,
        draft_visuals='on' if enabled else 'off', fal_key='offline-test-key')
    response = client.get('/v1/me/account', headers={'Authorization': f'Bearer {make_token()}'})
    assert response.status_code == 200
    assert response.json()['draftVisualsEnabled'] is enabled


@pytest.mark.parametrize('reserved,expected_state', [(5, 'failed'), (0, 'ready_to_start')])
def test_worker_reserves_execution_day_budget_before_paid_calls(monkeypatch, reserved, expected_state):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    state = {'id': TOKEN, 'user_id': UID, 'lease_token': 'lease', 'status': 'running',
             'payload': {'mode': dv.MODE, 'version': dv.VERSION, 'role': 'product', 'sources': []},
             'metadata': {'reservedUsd': .05}, 'created_at': datetime(2026, 10, 19, tzinfo=timezone.utc)}
    events = []
    class Conn:
        async def commit(self): events.append('commit')
        @asynccontextmanager
        async def transaction(self): yield self
    @asynccontextmanager
    async def connection(): yield Conn()
    instance = app()
    instance.state.pool = SimpleNamespace(connection=connection)
    async def lock_budget(conn): events.append('budget_lock')
    async def lock_job(*args): events.append('job_lock'); return deepcopy(state)
    async def usage(conn, uid, *, exclude_job_id):
        assert exclude_job_id == TOKEN
        events.append('budget_check')
        return {'reserved': reserved, 'budget_day': '2026-10-20'}
    async def update(conn, jid, *, metadata): state['metadata'].update(metadata)
    async def finalize(conn, **kw): state.update(status=kw['status'], result=kw['result'])
    async def stop_before_provider(conn, current):
        assert state['metadata']['budgetDate'] == '2026-10-20'
        assert events.index('budget_check') < events.index('commit')
        raise RuntimeError('test stops before provider')
    monkeypatch.setattr(repo, 'lock_draft_visual_budget', lock_budget)
    monkeypatch.setattr(repo, 'lock_draft_visual_job', lock_job)
    monkeypatch.setattr(repo, 'draft_visual_usage', usage)
    monkeypatch.setattr(repo, 'update_draft_visual_job', update)
    monkeypatch.setattr(repo, 'finalize_uncharged_job', finalize)
    monkeypatch.setattr(repo, 'draft_visual_is_current', stop_before_provider)
    provider = AsyncMock(side_effect=AssertionError('budget test must never call a paid provider'))
    monkeypatch.setattr(dv.fal_cutout, 'segment_garment', provider)
    monkeypatch.setattr(dv.qwen_flatlay, 'render', provider)
    asyncio.run(dv.run(instance, deepcopy(state)))
    assert events[:3] == ['budget_lock', 'job_lock', 'budget_check']
    if expected_state == 'failed':
        assert state['result'] == {'state': 'failed', 'reason': 'daily_budget'}
        assert not state['metadata'].get('paidStarted')
    else:
        assert state['metadata']['paidStarted'] is True
        assert state['metadata']['budgetDate'] == '2026-10-20'
    provider.assert_not_awaited()


@pytest.mark.parametrize('state', ['failed', 'ready'])
def test_unapplied_record_alone_does_not_suppress_project_front(monkeypatch, state):
    from app import routes
    captured = []
    job = {'id': 'draft', 'status': 'done', 'result': {'state': state},
           'metadata': {'promotions': [{'projectId': 'project', 'sourceAssetIds': [SOURCE], 'applied': False}]}}
    monkeypatch.setattr(repo, 'get_draft_visual_promotion', AsyncMock(return_value=job))
    async def create(conn, **kw): captured.append(kw); return {}, True
    monkeypatch.setattr(repo, 'create_job', create)
    product = {'colors': [{'images': [{'id': SOURCE, 'slot': 'Front'}]}]}
    asyncio.run(routes._enqueue_canonical_cutout(None, user_id=UID, project_id='project',
        product=product, tidy_enabled=True, draft_visuals_enabled=True))
    assert captured[0]['payload']['productTidyRequired'] is True
    assert not captured[0]['payload'].get('skipDraftFront')


@pytest.mark.parametrize('failure', [None, 'r2', 'db'])
@pytest.mark.parametrize('enabled', [False, True])
def test_lost_handoff_discovers_server_job_and_retries_delivery_without_paid_work(monkeypatch, failure, enabled):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    import hashlib
    from app import routes
    instance = app()
    instance.state.settings.draft_visuals = 'on' if enabled else 'off'
    monkeypatch.setattr(repo, 'has_draft_visual_work', AsyncMock(return_value=True))
    digest = hashlib.sha256(b'original').hexdigest()
    state = {'id': TOKEN, 'user_id': UID, 'status': 'done', 'metadata': {},
        'payload': {'mode': dv.MODE, 'version': dv.VERSION, 'role': 'product',
                    'sources': [{'id': SOURCE, 'key': 'raw', 'hash': digest}]},
        'result': {'state': 'ready', 'cutout': {'assetId': 'cutout', 'key': 'cutout', 'metadata': {}},
                   'tidy': {'assetId': 'tidy', 'key': 'tidy', 'metadata': {}}}}
    copied = []; failed = False
    class Conn:
        async def commit(self): pass
        @asynccontextmanager
        async def transaction(self):
            before = deepcopy(state)
            try: yield self
            except Exception:
                state.clear(); state.update(before)
                raise
    def put(key, data, mime):
        nonlocal failed
        if failure == 'r2' and not failed:
            failed = True
            raise RuntimeError('temporary object copy failure')
        copied.append(key)
    instance.state.r2.put_bytes = put
    monkeypatch.setattr(repo, 'get_project', AsyncMock(return_value={'id': 'project'}))
    monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value={'colors': [{'images': [{'id': SOURCE, 'slot': 'Front'}]}]}))
    monkeypatch.setattr(repo, 'get_uploaded_assets_for_project', AsyncMock(return_value=[{'id': SOURCE, 'r2_key': 'raw'}]))
    monkeypatch.setattr(repo, 'get_asset_for_user', AsyncMock(side_effect=lambda conn, uid, aid:
        {'id': aid, 'source': 'derived', 'r2_key': aid} if uid == UID and aid in ('cutout', 'tidy') else None))
    async def find(conn, owner, key):
        assert owner == UID
        assert key == dv.idempotency_key(UID, 'product', [digest])
        return deepcopy(state)
    async def lock(*args): return deepcopy(state)
    async def update(conn, jid, *, metadata): state['metadata'].update(deepcopy(metadata))
    async def asset(*args, **kwargs):
        nonlocal failed
        if failure == 'db' and not failed:
            failed = True
            raise RuntimeError('temporary asset insert failure')
    monkeypatch.setattr(repo, 'find_draft_visual_job', find)
    monkeypatch.setattr(repo, 'lock_draft_visual_job', lock)
    monkeypatch.setattr(repo, 'update_draft_visual_job', update)
    monkeypatch.setattr(repo, 'create_asset', asset)
    monkeypatch.setattr(repo, 'pending_draft_visual_deliveries', AsyncMock(side_effect=lambda *args, **kw: [deepcopy(state)]))
    monkeypatch.setattr(repo, 'create_job', AsyncMock(side_effect=AssertionError('must reuse prepared front')))
    provider = AsyncMock(side_effect=AssertionError('must not call Qwen again'))
    monkeypatch.setattr(dv.qwen_flatlay, 'render', provider)
    async def scenario():
        conn = Conn()
        product = await repo.get_product(conn, 'project')
        # No client job ID or handoff is supplied to the project queue.
        assert await routes._enqueue_canonical_cutout(conn, user_id=UID, project_id='project', product=product,
            tidy_enabled=True, draft_visuals_enabled=enabled, app=instance)
        assert state['result']['state'] == 'ready'
        target = state['metadata']['promotions'][0]
        assert target.get('applied', False) is (failure is None)
        assert await dv.promotion_covers(conn, state, 'project', [SOURCE])
        await dv.retry_project_deliveries(instance, conn, user_id=UID, project_id='project')
        assert state['metadata']['promotions'][0]['applied'] is True
        assert state['metadata']['promotions'][0]['deliveryState'] == 'applied'
    asyncio.run(scenario())
    provider.assert_not_awaited()
    assert len(copied) >= 2
