"""Actual idempotency, promotion row locks and reclamation in the local CI test DB.

Uses the queue suite's isolated schema and localhost-only opt-in fixture. Providers
and object storage are in memory, and never contact paid services.
"""
import asyncio
import hashlib
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

from PIL import Image
from psycopg.types.json import Jsonb
import pytest

from app import repo
from app.services import draft_visuals as dv
from app.services.segmentation_types import SamViewResult
from test_product_tidy_queue_postgres import database as queue_database, connection


@pytest.fixture
def database(queue_database):
    async def setup():
        async with connection(queue_database) as conn:
            await conn.execute('''
                alter table assets add column id uuid primary key default gen_random_uuid(),
                  add column user_id uuid, add column project_id uuid, add column visibility text,
                  add column r2_bucket text, add column r2_key text unique, add column mime_type text,
                  add column byte_size bigint, add column original_filename text,
                  add column metadata jsonb not null default '{}', add column deleted_at timestamptz;
                create table draft_slots(user_id uuid primary key, payload jsonb,
                  active_token uuid, expires_at timestamptz, device_label text, photos_pending boolean default false,
                  updated_at timestamptz not null default now());
                create table projects(id uuid primary key, user_id uuid, cover_asset_id uuid, deleted_at timestamptz);
                create table products(id uuid default gen_random_uuid(), project_id uuid,
                  name text, clothing_type text, colors jsonb, measurements jsonb,
                  measurements_unknown boolean, upload_complete boolean);
                create table matching_items(id text primary key, owner_user_id uuid, project_id uuid,
                  image_asset_id uuid, thumbnail_asset_id uuid, is_active boolean default true);
                create table mannequin_cuts(asset_id uuid);
                create table wardrobe_images(asset_id uuid);
                create table exports(asset_id uuid);
                create table profiles(avatar_asset_id uuid);
            ''')
    asyncio.run(setup())
    return queue_database


class MemoryR2:
    def __init__(self):
        self.objects = {}
    def get_bytes(self, key):
        return self.objects[key]
    def put_bytes(self, key, data, mime):
        self.objects[key] = data
    def head(self, key):
        return {'size': len(self.objects[key])} if key in self.objects else None
    def public_url(self, key):
        return 'https://images.test/' + key


def png(color, alpha=255):
    stream = io.BytesIO()
    Image.new('RGBA', (32, 48), (*color, alpha)).save(stream, 'PNG')
    return stream.getvalue()


async def fixture(database, monkeypatch, role='product'):
    uid, aid, token, pid, dest = [str(uuid.uuid4()) for _ in range(5)]
    r2 = MemoryR2()
    original = png((180, 20, 30))
    raw_key, destination_key = f'raw-{uuid.uuid4()}', f'destination-{uuid.uuid4()}'
    r2.objects.update({raw_key: original, destination_key: original})
    @asynccontextmanager
    async def pooled():
        async with connection(database) as conn:
            yield conn
    app = SimpleNamespace(state=SimpleNamespace(r2=r2, pool=SimpleNamespace(connection=pooled),
        settings=SimpleNamespace(draft_visuals='on', draft_visuals_daily_budget_usd=5, r2_bucket='test')))
    async def project(conn, owner, project_id):
        return await (await conn.execute('select id::text as id from projects where id=%s and user_id=%s',
                                        (project_id, owner))).fetchone()
    monkeypatch.setattr(repo, 'get_project', project)
    calls = {'sam': 0, 'qwen': 0}
    cut = png((180, 20, 30), 200)
    from test_product_tidy import padded_tidy_png
    tidy = padded_tidy_png()
    async def segment(settings, storage, views):
        calls['sam'] += 1
        r2.objects['canonical/shared.png'] = cut
        return {'Front': SamViewResult(view='Front', ready=True, cutout_key='canonical/shared.png',
            source_hash=hashlib.sha256(original).hexdigest(), model_version='fal-ai/sam-3/image',
            algorithm_version='fal-sam3-v1', checksum='test', width=32, height=48, area_frac=.5,
            byte_size=len(cut), cached=False)}
    async def render(settings, data, *, transparent, on_generated):
        calls['qwen'] += 1
        await on_generated(tidy)
        return tidy
    monkeypatch.setattr(dv.fal_cutout, 'segment_garment', segment)
    monkeypatch.setattr(dv.qwen_flatlay, 'render', render)
    monkeypatch.setattr(dv.matching_cutout, 'cutout_color_agrees', lambda *_: (True, {}))
    payload = {'product': {'colors': [{'isBase': True, 'images': [{'slot': 'Front', 'id': aid}]}]},
               'customMatch': {'localId': 'local', 'sources': [{'id': aid}]}}
    async with connection(database) as conn:
        await conn.execute("insert into draft_slots(user_id,payload,active_token,expires_at) values(%s,%s,%s,now()+interval '7 days')", (uid, Jsonb(payload), token))
        await conn.execute('insert into projects(id,user_id) values(%s,%s)', (pid, uid))
        await conn.execute("insert into products(project_id,colors) values(%s,'[]')", (pid,))
        for ident, scope, key, purpose in [(aid, None, raw_key, 'draft_slot'), (dest, pid, destination_key, 'upload')]:
            await repo.create_asset(conn, asset_id=ident, user_id=uid, project_id=scope, source='upload',
                bucket='test', key=key, mime='image/png', size=len(original), original_filename=None,
                metadata={'purpose': purpose})
    async def prepare():
        async with connection(database) as conn:
            return await dv.prepare(app, conn, user_id=uid, token=token, role=role,
                source_asset_ids=[aid], local_id='local' if role == 'custom' else None)
    return SimpleNamespace(app=app, uid=uid, aid=aid, token=token, pid=pid, dest=dest, calls=calls,
                           prepare=prepare, r2=r2)


async def claim(database):
    async with connection(database) as conn:
        return await repo.claim_next_job(conn, ('sam_preprocess', 'matching_cutout'), 'draft-test')


def test_concurrent_duplicate_prepare_and_recovered_lease_spend_once(database, monkeypatch):
    async def scenario():
        f = await fixture(database, monkeypatch)
        first, second = await asyncio.gather(f.prepare(), f.prepare())
        assert first['jobId'] == second['jobId']
        job = await claim(database)
        await dv.run(f.app, job)
        assert f.calls == {'sam': 1, 'qwen': 1}
        async with connection(database) as conn:
            await conn.execute("update jobs set status='pending' where id=%s", (job['id'],))
        recovered = await claim(database)
        await dv.run(f.app, recovered)
        assert f.calls == {'sam': 1, 'qwen': 1}
    asyncio.run(scenario())


@pytest.mark.parametrize('complete_first', [False, True])
@pytest.mark.parametrize('role', ['product', 'custom'])
def test_completed_and_running_promotions_reuse_paid_outputs(database, monkeypatch, complete_first, role):
    async def scenario():
        f = await fixture(database, monkeypatch, role)
        prepared = await f.prepare()
        job = await claim(database)
        item_id = 'custom-one' if role == 'custom' else None
        if item_id:
            async with connection(database) as conn:
                await conn.execute('insert into matching_items(id,owner_user_id,project_id,image_asset_id,thumbnail_asset_id) '
                    'values(%s,%s,%s,%s,%s)', (item_id,f.uid,f.pid,f.dest,f.dest))
        if complete_first:
            await dv.run(f.app, job)
        async with connection(database) as conn:
            assert await dv.promote(f.app, conn, user_id=f.uid, job_id=prepared['jobId'], project_id=f.pid,
                source_asset_ids=[f.dest], matching_item_id=item_id) == {'joined': True, 'jobId': prepared['jobId']}
        if not complete_first:
            await dv.run(f.app, job)
        # Replaying the link, including after completion, performs no model calls.
        async with connection(database) as conn:
            await dv.promote(f.app, conn, user_id=f.uid, job_id=prepared['jobId'], project_id=f.pid,
                source_asset_ids=[f.dest], matching_item_id=item_id)
            assets = await (await conn.execute('select * from assets where project_id=%s and source=\'derived\'', (f.pid,))).fetchall()
            assert len(assets) == 3
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            assert stored['metadata']['promotions'][0]['applied'] is True
            if role == 'product':
                by_kind = {a['metadata']['canonicalType']: a for a in assets}
                tidy, board = by_kind['product_tidy'], by_kind['product_tidy_board']
                assert tidy['metadata']['boardAssetId'] == str(board['id'])
                assert board['metadata']['tidyAssetId'] == str(tidy['id'])
                for part, row in [('tidy', tidy), ('board', board)]:
                    assert f.r2.objects[row['r2_key']] == f.r2.objects[stored['result'][part]['key']]
                assert (board['metadata']['width'], board['metadata']['height']) == (341, 512)
                from app.services import product_tidy
                f.app.state.settings.product_tidy = 'on'
                front = (await product_tidy.visuals(conn, f.r2, f.app.state.settings,
                    user_id=f.uid, project_id=f.pid,
                    product={'colors': [{'images': [{'id': f.dest, 'slot': 'Front'}]}]}))['front']
                assert front['tidyUrl'] == f.r2.public_url(board['r2_key'])
                assert (front['width'], front['height']) == (341, 512)
        assert f.calls == {'sam': 1, 'qwen': 1}
    asyncio.run(scenario())


def test_completion_and_promotion_serialize_on_same_job_row(database, monkeypatch):
    async def scenario():
        f = await fixture(database, monkeypatch)
        prepared = await f.prepare()
        job = await claim(database)
        locked, release = asyncio.Event(), asyncio.Event()
        # 완료 처리는 잡 행을 잠근 채 deliver 를 부른다. 그 안에서 멈춰 두고 승격을 겹친다.
        original = dv.deliver
        async def paused(app, conn, current, **kwargs):
            if not locked.is_set():
                locked.set()
                await release.wait()
            return await original(app, conn, current, **kwargs)
        monkeypatch.setattr(dv, 'deliver', paused)
        worker = asyncio.create_task(dv.run(f.app, job))
        await asyncio.wait_for(locked.wait(), 3)
        async def link():
            async with connection(database) as conn:
                return await dv.promote(f.app, conn, user_id=f.uid, job_id=prepared['jobId'], project_id=f.pid,
                    source_asset_ids=[f.dest])
        linking = asyncio.create_task(link())
        release.set()
        await asyncio.wait_for(asyncio.gather(worker, linking), 5)
        async with connection(database) as conn:
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            assert stored['metadata']['promotions'][0]['applied'] is True
        assert f.calls == {'sam': 1, 'qwen': 1}
    asyncio.run(scenario())


def test_reclaimer_preserves_projects_active_jobs_and_global_canonical(database, monkeypatch):
    async def scenario():
        f = await fixture(database, monkeypatch)
        await f.prepare()
        orphan = str(uuid.uuid4())
        async with connection(database) as conn:
            await repo.create_asset(conn, asset_id=orphan, user_id=f.uid, project_id=None, source='derived',
                bucket='test', key='orphan', mime='image/png', size=1, original_filename=None,
                metadata={'purpose': 'draft_visuals'})
            global_id = str(uuid.uuid4())
            await repo.create_asset(conn, asset_id=global_id, user_id=f.uid, project_id=None, source='derived',
                bucket='test', key='canonical/global', mime='image/png', size=1, original_filename=None,
                metadata={'canonicalType': 'canonical_cutout'})
            await conn.execute("update assets set created_at=now()-interval '8 days'")
            await conn.execute('delete from draft_slots')
            reclaimed = await repo.reclaim_stale_unreferenced_draft_assets(conn, older_than_minutes=10080)
            assert [row['id'] for row in reclaimed] == [orphan]
    asyncio.run(scenario())


def test_product_board_follows_draft_resume_and_reclamation_lifetime(database, monkeypatch):
    async def scenario():
        f = await fixture(database, monkeypatch)
        await f.prepare()
        job = await claim(database)
        await dv.run(f.app, job)
        async with connection(database) as conn:
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            board_id = stored['result']['board']['assetId']
            assert await dv.result_assets_live(conn, stored)
            await conn.execute("update assets set created_at=now()-interval '8 days'")
            assert await repo.soft_delete_unreferenced_draft_assets(conn, f.uid, [board_id]) == []
            assert await repo.reclaim_stale_unreferenced_draft_assets(conn, older_than_minutes=10080) == []
            resumed = await dv.visuals(f.app, conn, user_id=f.uid, token=f.token)
            assert resumed['product']['front']['tidyUrl'] == f.r2.public_url(stored['result']['board']['key'])
            assert (resumed['product']['front']['width'], resumed['product']['front']['height']) == (341, 512)
            await conn.execute('delete from draft_slots where user_id=%s', (f.uid,))
            reclaimed = await repo.reclaim_stale_unreferenced_draft_assets(conn, older_than_minutes=10080)
            assert board_id in {a['id'] for a in reclaimed}
            assert not await dv.result_assets_live(conn, stored)
    asyncio.run(scenario())


@pytest.mark.parametrize('complete_first', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_lost_prepare_response_project_queue_joins_by_owned_hash(database, monkeypatch, complete_first, enabled):
    from app import routes
    async def scenario():
        f = await fixture(database, monkeypatch)
        await f.prepare()  # Response and every browser binding are lost.
        job = await claim(database)
        if complete_first:
            await dv.run(f.app, job)
        before = f.calls['qwen']
        f.app.state.settings.draft_visuals = 'on' if enabled else 'off'
        product = {'colors': [{'isBase': True, 'images': [{'id': f.dest, 'slot': 'Front'}]}]}
        async with connection(database) as conn:
            await conn.execute('update products set colors=%s where project_id=%s', (Jsonb(product['colors']), f.pid))
            assert await routes._enqueue_canonical_cutout(conn, user_id=f.uid, project_id=f.pid,
                product=product, tidy_enabled=True, draft_visuals_enabled=enabled, app=f.app)
            assert f.calls['qwen'] == before
            assert (await (await conn.execute('select count(*) as n from jobs')).fetchone())['n'] == 1
        if not complete_first:
            f.app.state.settings.draft_visuals = 'on'
            await dv.run(f.app, job)
        assert f.calls['qwen'] == 1
    asyncio.run(scenario())


def test_yesterday_pending_job_is_declined_when_execution_day_budget_is_full(database, monkeypatch):
    async def scenario():
        f = await fixture(database, monkeypatch)
        await f.prepare()
        async with connection(database) as conn:
            await conn.execute("update jobs set created_at=now()-interval '1 day'")
            await conn.execute("insert into jobs(user_id,kind,status,payload,metadata) values(%s,'sam_preprocess','done',%s,%s)",
                (str(uuid.uuid4()), Jsonb({'mode': dv.MODE}), Jsonb({'paidStarted': True, 'reservedUsd': 5})))
        job = await claim(database)
        await dv.run(f.app, job)
        async with connection(database) as conn:
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            assert stored['result'] == {'state': 'failed', 'reason': 'daily_budget'}
            assert not stored['metadata'].get('paidStarted')
        assert f.calls == {'sam': 0, 'qwen': 0}
    asyncio.run(scenario())


def test_concurrent_yesterday_jobs_reserve_execution_day_under_same_budget_lock(database, monkeypatch):
    async def scenario():
        first = await fixture(database, monkeypatch)
        await first.prepare()
        async with connection(database) as conn:
            await conn.execute("update jobs set created_at=now()-interval '1 day'")
        second = await fixture(database, monkeypatch)
        await second.prepare()
        async with connection(database) as conn:
            await conn.execute("update jobs set created_at=now()-interval '1 day'")
        first.app.state.settings.draft_visuals_daily_budget_usd = .05
        second.app.state.settings.draft_visuals_daily_budget_usd = .05
        # fixture's model stubs close over their own storage. Use one shared fake
        # provider/storage here while retaining two independent DB transactions.
        second.r2.objects.update(first.r2.objects)
        first.app.state.r2 = second.r2
        jobs = [await claim(database), await claim(database)]
        owners = {first.uid: first, second.uid: second}
        await asyncio.gather(*(dv.run(owners[job['user_id']].app, job) for job in jobs))
        async with connection(database) as conn:
            rows = await (await conn.execute('select result,metadata from jobs')).fetchall()
            assert sum(row['result'].get('state') == 'ready' for row in rows) == 1
            assert sum(row['result'].get('reason') == 'daily_budget' for row in rows) == 1
            usage = await repo.draft_visual_usage(conn, first.uid)
            assert float(usage['reserved']) == .05
            paid = next(row for row in rows if row['metadata'].get('paidStarted'))
            assert paid['metadata']['budgetDate'] == usage['budget_day']
        assert second.calls['qwen'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('role', ['product', 'custom'])
@pytest.mark.parametrize('failure', ['r2', 'db'])
def test_failed_delivery_preserves_ready_and_project_read_retries_without_models(database, monkeypatch, role, failure):
    async def scenario():
        f = await fixture(database, monkeypatch, role)
        prepared = await f.prepare()
        job = await claim(database)
        item_id = 'recover-custom' if role == 'custom' else None
        product = {'colors': [{'images': [{'id': f.dest, 'slot': 'Front'}]}]}
        async with connection(database) as conn:
            await conn.execute('update products set colors=%s where project_id=%s', (Jsonb(product['colors']), f.pid))
            if item_id:
                await conn.execute('insert into matching_items(id,owner_user_id,project_id,image_asset_id,thumbnail_asset_id) '
                    'values(%s,%s,%s,%s,%s)', (item_id, f.uid, f.pid, f.dest, f.dest))
            await dv.promote(f.app, conn, user_id=f.uid, job_id=prepared['jobId'], project_id=f.pid,
                source_asset_ids=[f.dest], matching_item_id=item_id)
        original_store = dv.store
        failed = False
        async def flaky_store(app, conn, current, part, data, metadata, **kwargs):
            nonlocal failed
            if kwargs.get('project_id') and part == 'board' and not failed:
                failed = True
                if failure == 'db':
                    await conn.execute('select missing_delivery_column from jobs')
                raise RuntimeError('temporary R2 copy failure')
            return await original_store(app, conn, current, part, data, metadata, **kwargs)
        monkeypatch.setattr(dv, 'store', flaky_store)
        await dv.run(f.app, job)
        async with connection(database) as conn:
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            assert stored['result']['state'] == 'ready'
            assert not stored['metadata']['promotions'][0].get('applied')
            await dv.retry_project_deliveries(f.app, conn, user_id=f.uid, project_id=f.pid)
            stored = await repo.lock_draft_visual_job(conn, f.uid, job['id'])
            assert stored['metadata']['promotions'][0]['applied'] is True
            assets = await (await conn.execute("select id from assets where project_id=%s and source='derived'", (f.pid,))).fetchall()
            assert len(assets) == 3
        assert f.calls == {'sam': 1, 'qwen': 1}
    asyncio.run(scenario())
