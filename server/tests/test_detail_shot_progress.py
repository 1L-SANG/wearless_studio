"""Postgres checks for the durable image-attempt fence (CI's local database)."""
import asyncio
from contextlib import asynccontextmanager
import os
from types import SimpleNamespace
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
import pytest

from app.agents.detail_shot_pipeline import DetailShotRejected
from app.agents.gemini_image import InlineImage
from app.workers import detail_shot_runtime
from app.workers.detail_shot_runtime import ProgressStore


def test_third_claim_requires_two_failed_judgments():
    async def check(decisions):
        state = {'phase': 'judged', 'attempts': 2, 'fingerprint': 'same',
                 'qc': {'judgments': [{'decision': decision} for decision in decisions]}}
        store = ProgressStore.__new__(ProgressStore)

        async def change(mutate=None):
            nonlocal state
            if mutate:
                state = mutate(state)
            return state

        store._change = change
        return store, state

    async def verify():
        store, _ = await check(['FAIL', 'FAIL'])
        assert await store.claim('same') == 3
        with pytest.raises(DetailShotRejected):
            await store.claim('same')
        for decisions in (['FAIL', 'UNKNOWN'], ['FAIL', 'PASS'], ['FAIL']):
            store, _ = await check(decisions)
            with pytest.raises(DetailShotRejected):
                await store.claim('same')

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_failed_three_candidates_remain_private_and_held(monkeypatch):
    class R2:
        def __init__(self):
            self.objects = {}

        def put_bytes(self, key, data, mime, **kwargs):
            self.objects[key] = data

        def get_bytes(self, key):
            return self.objects[key]

    class Connection:
        async def commit(self):
            pass

    @asynccontextmanager
    async def connection():
        yield Connection()

    async def checkpoint_intent(*args, **kwargs):
        return True

    monkeypatch.setattr(detail_shot_runtime.repo, 'create_ai_checkpoint_intent', checkpoint_intent)
    app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection), r2=R2()))
    job = {'id': 'job', 'user_id': 'user', 'project_id': 'project', 'lease_token': 'lease'}
    store = ProgressStore(app, job, 'detail')
    state = {'fingerprint': 'same', 'attempts': 1, 'phase': 'generating'}

    async def change(mutate=None):
        nonlocal state
        if mutate:
            state = mutate(state)
        return state

    store._change = change

    async def verify():
        for attempt, data in enumerate((b'first', b'second', b'third'), 1):
            if attempt > 1:
                await store._change(lambda previous: {**previous, 'attempts': attempt,
                                                       'phase': 'generating'})
            await store.save_candidate(InlineImage('image/png', data), 'same', attempt)
        assert (await store.load_candidate(attempt=1)).data == b'first'
        assert len(state['candidates']) == 3
        await store.finish({'passed': False, 'publishedBestEffort': True})
        assert state['phase'] == 'held' and state['candidate']['attempt'] == 3
        assert (await store.load_candidate()).data == b'third'

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_best_effort_finish_selects_the_judged_candidate_bytes(monkeypatch):
    class R2:
        objects = {}

        def put_bytes(self, key, data, mime, **kwargs):
            self.objects[key] = data

        def get_bytes(self, key):
            return self.objects[key]

    class Connection:
        async def commit(self):
            pass

    @asynccontextmanager
    async def connection():
        yield Connection()

    async def checkpoint_intent(*args, **kwargs):
        return True

    monkeypatch.setattr(detail_shot_runtime.repo, 'create_ai_checkpoint_intent', checkpoint_intent)
    app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection), r2=R2()))
    job = {'id': 'job', 'user_id': 'user', 'project_id': 'project', 'lease_token': 'lease'}
    store = ProgressStore(app, job, 'detail')
    state = {'fingerprint': 'same', 'attempts': 1, 'phase': 'generating'}

    async def change(mutate=None):
        nonlocal state
        if mutate:
            state = mutate(state)
        return state

    store._change = change

    async def verify():
        for attempt, data in enumerate((b'best', b'logo-wrong', b'color-wrong'), 1):
            if attempt > 1:
                await store._change(lambda previous: {**previous, 'attempts': attempt, 'phase': 'generating'})
            await store.save_candidate(InlineImage('image/png', data), 'same', attempt)
        await store.finish({'passed': False, 'publishedBestEffort': True, 'attempts': 3,
                            'selectedAttempt': 1,
                            'judgments': [{'decision': 'FAIL'}] * 3}, selected_attempt=1)
        assert state['phase'] == 'accepted'
        assert state['candidate']['attempt'] == 1
        assert (await store.load_candidate()).data == b'best'

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_progress_survives_new_store_and_lost_lease():
    url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:54322/postgres')
    if urlparse(url).hostname not in {'localhost', '127.0.0.1', 'postgres'}:
        pytest.skip('Only an isolated local/CI Postgres may run this test')

    async def check():
        try:
            conn = await psycopg.AsyncConnection.connect(url, row_factory=dict_row, connect_timeout=2)
        except psycopg.OperationalError:
            if os.getenv('CI'):
                raise
            pytest.skip('Local test Postgres is not running')
        async with conn:
            # TEMP shadows the real jobs relation only on this connection. No user
            # data or migrations are touched, and closing the connection removes it.
            await conn.execute('create temp table jobs (id text, user_id text, project_id text, '
                               "status text, locked_by text, metadata jsonb default '{}'::jsonb)")
            await conn.execute("insert into jobs values ('j','u','p','running','lease', '{}')")
            await conn.commit()

            @asynccontextmanager
            async def connection():
                try:
                    yield conn
                except Exception:
                    await conn.rollback()
                    raise

            app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection)))
            job = {'id': 'j', 'user_id': 'u', 'project_id': 'p', 'lease_token': 'lease'}
            first = ProgressStore(app, job, 'cut1')
            assert await first.claim('recipe') == 1
            reopened = ProgressStore(app, job, 'cut1')
            assert (await reopened.read())['attempts'] == 1
            with pytest.raises(DetailShotRejected):
                await reopened.claim('recipe')  # in-flight cannot be submitted twice
            # Another cut has its own budget; updating it retains the first ledger.
            second = ProgressStore(app, job, 'cut2')
            assert await second.claim('other') == 1
            assert (await first.read())['attempts'] == 1
            await first._change(lambda s: {**s, 'phase': 'candidate'})
            await first.claim_judgment()
            with pytest.raises(DetailShotRejected):
                await reopened.claim_judgment()
            await first.record_judgment({'passed': False, 'decision': 'FAIL'})
            assert await reopened.claim('recipe') == 2
            await first._change(lambda s: {**s, 'phase': 'judged'})
            with pytest.raises(DetailShotRejected):
                await reopened.claim('recipe')
            await conn.execute("update jobs set locked_by='next-lease' where id='j'")
            await conn.commit()
            with pytest.raises(DetailShotRejected, match='lease_lost'):
                await first.finish({'passed': True})

    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)
