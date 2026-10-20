"""Delivery consumers and project fallback, with storage and SQL boundaries offline."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.services import draft_visuals as dv
from app.workers.sam_retry_pusher import SamRetryPusher
from test_draft_visuals import UID, SOURCE, TOKEN, app


@pytest.fixture
def recovery(monkeypatch):
    product = {'id': 'product', 'projectId': 'project', 'name': '셔츠',
               'colors': [{'isBase': True, 'images': [{'id': SOURCE, 'slot': 'Front'}]}]}
    digest = hashlib.sha256(b'original').hexdigest()
    job = {'id': TOKEN, 'user_id': UID, 'status': 'done', 'metadata': {},
           'payload': {'mode': dv.MODE, 'version': dv.VERSION, 'role': 'product',
                       'sources': [{'id': SOURCE, 'key': 'raw', 'hash': digest}]},
           'result': {'state': 'ready',
                      'cutout': {'assetId': 'cutout', 'key': 'cutout', 'metadata': {}},
                      'tidy': {'assetId': 'tidy', 'key': 'tidy', 'metadata': {}}}}
    assets = {part: {'id': part, 'user_id': UID, 'source': 'derived', 'r2_key': part,
                     'created_at': datetime.now(timezone.utc) - timedelta(days=8)}
              for part in ('cutout', 'tidy')}
    objects = {'raw': b'original', 'cutout': b'cutout', 'tidy': b'tidy'}
    state = SimpleNamespace(app=app('off'), job=job, assets=assets, product=product,
                            objects=objects, queued=[], copy_attempts=0, fail_copy=False,
                            abort_lookup=False, broken=False, after_commit=lambda: None)

    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, query, params):
            assert 'deleted_at is null' in query and 'user_id = %s' in query
            aid, owner = params
            row = assets.get(aid)
            self.row = deepcopy(row) if row and row['user_id'] == owner and not row.get('deleted_at') else None
        async def fetchone(self): return self.row

    class Conn:
        def cursor(self): return Cursor()
        async def commit(self): state.after_commit()
        async def rollback(self): state.broken = False
        @asynccontextmanager
        async def transaction(self):
            before = deepcopy(job), deepcopy(assets)
            try:
                yield self
            except Exception:
                job.clear(); job.update(before[0])
                assets.clear(); assets.update(before[1])
                state.broken = False
                raise

    state.conn = Conn()
    @asynccontextmanager
    async def connection(): yield state.conn
    state.app.state.pool = SimpleNamespace(connection=connection)
    def put(key, data, mime):
        state.copy_attempts += 1
        if state.fail_copy:
            raise RuntimeError('temporary copy failure')
        objects[key] = data
    state.app.state.r2.get_bytes = objects.__getitem__
    state.app.state.r2.put_bytes = put
    async def find(conn, owner, key):
        if state.abort_lookup:
            state.broken = True
            raise RuntimeError('lookup transaction aborted')
        assert owner == UID and key == dv.idempotency_key(UID, 'product', [digest])
        return deepcopy(job)
    async def lock(*args): return deepcopy(job)
    async def update(conn, jid, *, metadata): job['metadata'].update(deepcopy(metadata))
    async def create_asset(conn, **kw):
        assets[kw['asset_id']] = {'id': kw['asset_id'], 'user_id': kw['user_id'],
                                 'source': kw['source'], 'r2_key': kw['key']}
    async def create_job(conn, **kw):
        assert not state.broken, 'the failed optional lookup must release its transaction'
        state.queued.append(kw)
        return {'id': 'normal-preprocess'}, True
    monkeypatch.setattr(repo, 'get_project', AsyncMock(return_value={'id': 'project'}))
    monkeypatch.setattr(repo, 'get_product', AsyncMock(side_effect=lambda *args: deepcopy(product)))
    monkeypatch.setattr(repo, 'get_uploaded_assets_for_project', AsyncMock(return_value=[{'id': SOURCE, 'r2_key': 'raw'}]))
    monkeypatch.setattr(repo, 'has_draft_visual_work', AsyncMock(return_value=True))
    monkeypatch.setattr(repo, 'find_draft_visual_job', find)
    monkeypatch.setattr(repo, 'lock_draft_visual_job', lock)
    monkeypatch.setattr(repo, 'update_draft_visual_job', update)
    monkeypatch.setattr(repo, 'create_asset', create_asset)
    monkeypatch.setattr(repo, 'create_job', create_job)
    monkeypatch.setattr(repo, 'pending_draft_visual_deliveries', AsyncMock(side_effect=lambda *a, **kw: [deepcopy(job)]))
    monkeypatch.setattr(repo, 'list_retryable_sam_jobs', AsyncMock(return_value=[]))
    state.provider = AsyncMock(side_effect=AssertionError('delivery must not call paid providers'))
    for module, name in ((dv.fal_cutout, 'segment_garment'), (dv.qwen_flatlay, 'render'),
                         (dv.fal_api, 'run_model')):
        monkeypatch.setattr(module, name, state.provider)
    return state


def consume_once(state):
    pusher = SamRetryPusher(state.app)
    state.after_commit = pusher._stop.set
    asyncio.run(pusher._run())


def test_periodic_consumer_delivers_ready_output_after_copy_failure_without_paid_calls(recovery):
    f = recovery
    f.fail_copy = True
    asyncio.run(dv.promote(f.app, f.conn, user_id=UID, job_id=TOKEN,
                           project_id='project', source_asset_ids=[SOURCE]))
    assert not f.job['metadata']['promotions'][0].get('applied')
    f.fail_copy = False
    consume_once(f)
    assert f.job['metadata']['promotions'][0]['applied'] is True
    assert f.job['metadata']['promotions'][0]['deliveryState'] == 'applied'
    assert any('/projects/project/' in key for key in f.objects)
    assert f.queued == []
    f.provider.assert_not_awaited()


def test_periodic_delivery_failures_stop_at_the_persisted_attempt_limit(recovery):
    f = recovery
    f.fail_copy = True
    asyncio.run(dv.promote(f.app, f.conn, user_id=UID, job_id=TOKEN,
                           project_id='project', source_asset_ids=[SOURCE]))
    for _ in range(10): consume_once(f)
    assert 1 < f.copy_attempts <= 4
    attempts = f.copy_attempts
    for _ in range(3): consume_once(f)
    assert f.copy_attempts == attempts
    assert not f.job['metadata']['promotions'][0].get('applied')
    f.provider.assert_not_awaited()


def test_delivery_lookup_failure_preserves_the_regular_pusher_commit(recovery, monkeypatch):
    f = recovery
    pusher = SamRetryPusher(f.app)
    commits = []
    f.after_commit = lambda: commits.append(True)
    async def unavailable(*args, **kwargs):
        pusher._stop.set()
        raise RuntimeError('delivery lookup unavailable')
    monkeypatch.setattr(repo, 'pending_draft_visual_deliveries', unavailable)
    asyncio.run(pusher._run())
    assert commits == [True]


@pytest.mark.parametrize('invalid', ['reclaimed_after_seven_days', 'foreign_owner', 'upload', 'wrong_key'])
def test_dead_ready_result_queues_one_normal_project_preprocess(recovery, invalid):
    f = recovery
    if invalid == 'reclaimed_after_seven_days':
        f.assets['tidy']['deleted_at'] = datetime.now(timezone.utc)
        del f.objects['tidy']
    elif invalid == 'foreign_owner': f.assets['tidy']['user_id'] = 'another-user'
    elif invalid == 'upload': f.assets['tidy']['source'] = 'upload'
    else: f.assets['tidy']['r2_key'] = 'replaced-key'
    assert asyncio.run(routes._enqueue_canonical_cutout(f.conn, user_id=UID, project_id='project',
        product=f.product, tidy_enabled=True, app=f.app)) is True
    assert len(f.queued) == 1
    assert f.queued[0]['kind'] == 'sam_preprocess'
    assert f.queued[0]['payload'] == {'mode': 'canonical_cutout', 'productTidyRequired': True}
    assert not f.job['metadata'].get('promotions')
    f.provider.assert_not_awaited()


@pytest.mark.parametrize('role,applied', [('product', False), ('product', True), ('custom', False), ('custom', True)])
def test_promotion_coverage_rejects_reclaimed_result_assets(recovery, role, applied):
    f = recovery
    f.job['payload']['role'] = role
    item_id = 'custom' if role == 'custom' else None
    if item_id:
        f.job['result']['board'] = f.job['result'].pop('tidy')
    f.assets['tidy']['deleted_at'] = datetime.now(timezone.utc)
    f.job['metadata']['promotions'] = [{'projectId': 'project', 'sourceAssetIds': [SOURCE],
        'applied': applied, 'deliveryState': 'pending', **({'matchingItemId': item_id} if item_id else {})}]
    assert asyncio.run(dv.promotion_covers(f.conn, f.job, 'project', [SOURCE], item_id)) is False


def test_product_board_is_copied_on_recovered_delivery(recovery):
    f = recovery
    f.job['result']['board'] = {'assetId': 'board', 'key': 'board',
        'metadata': {'canonicalType': 'product_tidy_board', 'width': 341, 'height': 512}}
    f.assets['board'] = {'id': 'board', 'user_id': UID, 'source': 'derived', 'r2_key': 'board'}
    f.objects['board'] = b'board-png'
    f.fail_copy = True
    asyncio.run(dv.promote(f.app, f.conn, user_id=UID, job_id=TOKEN,
                         project_id='project', source_asset_ids=[SOURCE]))
    f.fail_copy = False
    consume_once(f)
    assert f.job['metadata']['promotions'][0]['applied'] is True
    assert sorted(data for key, data in f.objects.items() if '/projects/project/' in key) == [
        b'board-png', b'cutout', b'tidy']
    f.provider.assert_not_awaited()


def test_reclaimed_product_board_is_not_live_and_get_falls_back_to_full_tidy(recovery):
    f = recovery
    f.job['result']['tidy'].update(width=768, height=1024)
    f.job['result']['board'] = {'assetId': 'board', 'key': 'board', 'width': 341, 'height': 512}
    assert asyncio.run(dv.result_assets_live(f.conn, f.job)) is False
    result = asyncio.run(dv.current_presentation(f.app, f.conn, f.job,
        user_id=UID, role='product', source_asset_ids=[SOURCE]))
    assert result['front']['tidyUrl'] == 'https://asset.test/tidy'
    assert (result['front']['width'], result['front']['height']) == (768, 1024)
    assert 'board' in f.job['result'], 'GET must not mutate the durable result'


@pytest.mark.parametrize('failure', ['r2_read', 'job_lookup'])
def test_join_failure_keeps_product_saved_and_queues_one_preprocess(recovery, client, make_token, monkeypatch, failure):
    f = recovery
    @asynccontextmanager
    async def connection(request): yield f.conn
    monkeypatch.setattr(routes, 'get_conn', connection)
    saved = []
    async def save(conn, pid, owner, fields):
        saved.append(deepcopy(fields))
        return {**f.product, **fields}
    monkeypatch.setattr(repo, 'save_product', save)
    monkeypatch.setattr(repo, 'get_analysis', AsyncMock(return_value={}))
    if failure == 'r2_read':
        def unavailable(key): raise RuntimeError('R2 read unavailable')
        f.app.state.r2.get_bytes = unavailable
    else: f.abort_lookup = True
    client.app.state.r2 = f.app.state.r2
    client.app.state.settings = replace(client.app.state.settings, product_tidy='on', fal_key='offline-key')
    response = client.patch('/v1/projects/project/product', json={'name': '저장된 셔츠'},
                            headers={'Authorization': f'Bearer {make_token(sub=UID)}'})
    assert response.status_code == 200
    assert response.json()['name'] == '저장된 셔츠' and saved == [{'name': '저장된 셔츠'}]
    assert len(f.queued) == 1
    assert f.queued[0]['payload'] == {'mode': 'canonical_cutout', 'productTidyRequired': True}
    f.provider.assert_not_awaited()
