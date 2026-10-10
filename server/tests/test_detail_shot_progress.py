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
from app import repo
from app.workers import detail_shot_runtime
from app.workers.detail_shot_runtime import ProgressStore


def test_third_claim_requires_two_failed_judgments():
    async def check(decisions):
        state = {'phase': 'judged', 'attempts': 2, 'fingerprint': 'same',
                 'qc': {'attempts': 2,
                        'judgments': [{'decision': decision} for decision in decisions]}}
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
        store, _ = await check(['FAIL'])  # 첫 호출이 미과금 일시 오류였던 경우
        assert await store.claim('same') == 3
        for decisions in (['FAIL', 'UNKNOWN'], ['FAIL', 'PASS'], []):
            store, _ = await check(decisions)
            with pytest.raises(DetailShotRejected):
                await store.claim('same')

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_best_effort_finish_accepts_second_provider_attempt_after_first_transient():
    store = ProgressStore.__new__(ProgressStore)
    state = {'phase': 'generating', 'attempts': 3,
             'candidate': {'attempt': 2, 'key': 'saved'},
             'candidates': [{'attempt': 2, 'key': 'saved'}]}

    async def change(mutate=None):
        nonlocal state
        if mutate:
            state = mutate(state)
        return state

    store._change = change

    async def verify():
        await store.finish({'passed': False, 'publishedBestEffort': True,
                            'attempts': 3, 'selectedAttempt': 2,
                            'judgments': [{'attempt': 2, 'decision': 'FAIL'}]},
                           selected_attempt=2)
        assert state['phase'] == 'accepted'
        assert state['candidate']['attempt'] == 2

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.parametrize('quality_options, expected_quality', [({}, 'medium'), ({'image_quality': 'high'}, 'high')])
def test_failed_three_candidates_remain_private_and_held(monkeypatch, quality_options, expected_quality):
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
            await store.save_candidate(InlineImage('image/png', data), 'same', attempt, **quality_options)
        assert (await store.load_candidate(attempt=1)).data == b'first'
        assert len(state['candidates']) == 3
        assert [candidate['imageQuality'] for candidate in state['candidates']] == [expected_quality] * 3
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
            retryable = ProgressStore(app, job, 'transient-cut')
            assert await retryable.claim('same-recipe') == 1
            await retryable.record_retryable_failure({'passed': False, 'attempts': 1})
            retryable_reopened = ProgressStore(app, job, 'transient-cut')
            assert await retryable_reopened.claim('same-recipe') == 2
            await retryable_reopened.record_retryable_failure({'passed': False, 'attempts': 2})
            assert await retryable.claim('same-recipe') == 3
            await retryable.record_retryable_failure({'passed': False, 'attempts': 3})
            with pytest.raises(DetailShotRejected, match='attempt_budget_exhausted'):
                await retryable_reopened.claim('same-recipe')
            mixed = ProgressStore(app, job, 'transient-then-quality')
            assert await mixed.claim('mixed-recipe') == 1
            await mixed.record_retryable_failure({'passed': False, 'attempts': 1})
            assert await mixed.claim('mixed-recipe') == 2
            await mixed._change(lambda s: {**s, 'phase': 'candidate'})
            await mixed.claim_judgment()
            await mixed.record_judgment({'passed': False, 'decision': 'FAIL',
                                         'attempts': 2, 'judgments': [{'decision': 'FAIL'}]})
            assert await mixed.claim('mixed-recipe') == 3
            two_quality = ProgressStore(app, job, 'two-quality-fails')
            assert await two_quality.claim('quality-recipe') == 1
            await two_quality._change(lambda s: {**s, 'phase': 'candidate'})
            await two_quality.claim_judgment()
            await two_quality.record_judgment({'passed': False, 'decision': 'FAIL',
                                               'attempts': 1, 'judgments': [{'decision': 'FAIL'}]})
            assert await two_quality.claim('quality-recipe') == 2
            await two_quality._change(lambda s: {**s, 'phase': 'candidate'})
            await two_quality.claim_judgment()
            await two_quality.record_judgment({'passed': False, 'decision': 'FAIL',
                                               'attempts': 2,
                                               'judgments': [{'decision': 'FAIL'}, {'decision': 'FAIL'}]})
            assert await two_quality.claim('quality-recipe') == 3
            with pytest.raises(DetailShotRejected, match='attempt_budget_exhausted'):
                await two_quality.claim('quality-recipe')
            await conn.execute("update jobs set locked_by='next-lease' where id='j'")
            await conn.commit()
            with pytest.raises(DetailShotRejected, match='lease_lost'):
                await first.finish({'passed': True})

    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


def test_retry_claim_fences_candidate_identity_concurrency_and_new_store():
    import copy
    state = {'phase': 'judged', 'attempts': 1, 'fingerprint': 'recipe',
             'candidate': {'attempt': 1, 'sha256': 'candidate'},
             'qc': {'fingerprint': 'recipe', 'attempts': 1,
                    'judgments': [{'attempt': 1, 'decision': 'UNKNOWN', 'unknownCause': 'timeout'}]}}
    original = copy.deepcopy(state)
    async def check():
        nonlocal state
        lock = asyncio.Lock()
        async def change(mutate=None):
            nonlocal state
            async with lock:
                if mutate:
                    state = copy.deepcopy(mutate(copy.deepcopy(state)))
                return copy.deepcopy(state)
        first, reopened = ProgressStore.__new__(ProgressStore), ProgressStore.__new__(ProgressStore)
        for store in (first, reopened):
            store._change = change
            store.job = {'lease_token': 'lease'}
        for fp, attempt, candidate_hash in [('changed', 1, 'candidate'),
                                            ('recipe', 2, 'candidate'), ('recipe', 1, 'changed')]:
            with pytest.raises(DetailShotRejected):
                await first.claim_judgment_retry(fp, attempt, candidate_hash)
            assert state == original
        results = await asyncio.gather(
            first.claim_judgment_retry('recipe', 1, 'candidate'),
            reopened.claim_judgment_retry('recipe', 1, 'candidate'), return_exceptions=True)
        assert sum(isinstance(r, DetailShotRejected) for r in results) == 1
        assert len(state['judgmentRetries']) == 1
        assert state['phase'] == 'rejudging'
        with pytest.raises(DetailShotRejected):
            await reopened.claim('recipe')
        with pytest.raises(DetailShotRejected):
            await reopened.record_judgment(original['qc'])
        with pytest.raises(DetailShotRejected):
            await reopened.recover_judgment_retry()
        assert state['phase'] == 'rejudging'
        reopened.job = {'lease_token': 'new-lease'}
        recovered = await reopened.recover_judgment_retry()
        assert recovered['phase'] == 'judged'
        assert recovered['judgmentRetries'][0]['status'] == 'INTERRUPTED'
        assert len(recovered['qc']['judgments']) == 1
        with pytest.raises(DetailShotRejected):
            await reopened.claim_judgment_retry('recipe', 1, 'candidate')
    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


def test_retry_completion_replaces_only_its_candidate_and_cannot_be_written_twice():
    import copy
    state = {'phase': 'judged', 'attempts': 2, 'fingerprint': 'recipe',
             'candidate': {'attempt': 2, 'sha256': 'candidate'},
             'qc': {'fingerprint': 'recipe', 'attempts': 2,
                    'judgments': [{'attempt': 1, 'decision': 'FAIL'},
                                  {'attempt': 2, 'decision': 'UNKNOWN', 'unknownCause': 'timeout'}]}}
    store = ProgressStore.__new__(ProgressStore)
    store.job = {'lease_token': 'lease'}
    async def change(mutate=None):
        nonlocal state
        if mutate:
            state = copy.deepcopy(mutate(copy.deepcopy(state)))
        return copy.deepcopy(state)
    store._change = change
    async def check():
        await store.claim_judgment_retry('recipe', 2, 'candidate')
        final = {'fingerprint': 'recipe', 'attempts': 2,
                 'judgments': [{'attempt': 1, 'decision': 'FAIL'}, {'attempt': 2, 'decision': 'PASS', 'passed': True}]}
        invalid = copy.deepcopy(final)
        invalid['judgments'].append({'attempt': 2, 'decision': 'PASS'})
        with pytest.raises(DetailShotRejected):
            await store.record_judgment_retry(invalid)
        assert state['phase'] == 'rejudging'
        report = await store.record_judgment_retry(final)
        assert len(state['qc']['judgments']) == 2
        assert state['qc']['judgments'][0] == {'attempt': 1, 'decision': 'FAIL'}
        assert state['qc']['judgments'][1]['decision'] == 'PASS'
        assert report['judgmentRetries'][0]['status'] == 'COMPLETE'
        with pytest.raises(DetailShotRejected):
            await store.record_judgment_retry(final)
        with pytest.raises(DetailShotRejected):
            await store.claim_judgment_retry('recipe', 2, 'candidate')
    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


def test_retry_claim_is_atomic_between_postgres_connections_and_survives_lease_recovery():
    import uuid
    from psycopg import sql
    # Dedicated throwaway schema on the local test database only.
    url = 'postgresql://postgres:postgres@127.0.0.1:54322/postgres'
    async def check():
        try:
            admin = await psycopg.AsyncConnection.connect(url, row_factory=dict_row, connect_timeout=2)
        except psycopg.OperationalError:
            if os.getenv('CI'):
                raise
            pytest.skip('Local test Postgres is not running')
        schema = 'detail_retry_test_' + uuid.uuid4().hex
        async with admin:
            await admin.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
            await admin.execute(sql.SQL('create table {}.jobs (id text, user_id text, project_id text, '
                                        "status text, locked_by text, metadata jsonb default '{{}}'::jsonb)").format(sql.Identifier(schema)))
            await admin.execute(sql.SQL("insert into {}.jobs values ('j','u','p','running','lease','{{}}')").format(sql.Identifier(schema)))
            await admin.commit()
            try:
                @asynccontextmanager
                async def connection():
                    async with await psycopg.AsyncConnection.connect(
                            url, row_factory=dict_row, connect_timeout=2,
                            options=f'-c search_path={schema}') as conn:
                        yield conn
                app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection)))
                job = {'id': 'j', 'user_id': 'u', 'project_id': 'p', 'lease_token': 'lease'}
                first, other = ProgressStore(app, job, 'cut'), ProgressStore(app, job, 'cut')
                initial = {'attempt': 1, 'decision': 'UNKNOWN', 'unknownCause': 'timeout'}
                await first._change(lambda _: {'phase': 'judged', 'fingerprint': 'recipe', 'attempts': 1,
                                               'candidate': {'attempt': 1, 'sha256': 'candidate'},
                                               'qc': {'fingerprint': 'recipe', 'attempts': 1, 'judgments': [initial]}})
                results = await asyncio.gather(
                    first.claim_judgment_retry('recipe', 1, 'candidate'),
                    other.claim_judgment_retry('recipe', 1, 'candidate'), return_exceptions=True)
                assert sum(isinstance(result, DetailShotRejected) for result in results) == 1
                reopened = ProgressStore(app, job, 'cut')
                state = await reopened.read()
                assert state['phase'] == 'rejudging'
                assert len(state['judgmentRetries']) == 1
                with pytest.raises(DetailShotRejected):
                    await reopened.claim_judgment_retry('recipe', 1, 'candidate')
                await admin.execute(sql.SQL("update {}.jobs set locked_by='new-lease' where id='j'").format(sql.Identifier(schema)))
                await admin.commit()
                recovered = ProgressStore(app, {**job, 'lease_token': 'new-lease'}, 'cut')
                state = await recovered.recover_judgment_retry()
                assert state['phase'] == 'judged'
                assert state['qc']['judgments'] == [initial]
                assert state['judgmentRetries'][0]['status'] == 'INTERRUPTED'
                with pytest.raises(DetailShotRejected, match='lease_lost'):
                    await first.record_judgment_retry({'fingerprint': 'recipe', 'attempts': 1,
                                                       'judgments': [{'attempt': 1, 'decision': 'PASS'}]})
                with pytest.raises(DetailShotRejected):
                    await recovered.claim_judgment_retry('recipe', 1, 'candidate')
            finally:
                await admin.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))
                await admin.commit()
    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)


def test_detail_failure_persists_interrupted_retry_and_preserves_other_metadata(monkeypatch):
    from unittest.mock import AsyncMock
    settle = AsyncMock()
    monkeypatch.setattr(repo, '_settle_credits', settle)
    async def check():
        try:
            conn = await psycopg.AsyncConnection.connect(
                'postgresql://postgres:postgres@127.0.0.1:54322/postgres',
                row_factory=dict_row, connect_timeout=2)
        except psycopg.OperationalError:
            if os.getenv('CI'):
                raise
            pytest.skip('Local test Postgres is not running')
        async with conn:
            await conn.execute('create temp table jobs (id text, user_id text, project_id text, '
                               'status text, locked_by text, locked_at timestamptz, '
                               "finished_at timestamptz, error_message text, result jsonb, metadata jsonb default '{}'::jsonb)")
            await conn.execute('create temp table job_events (job_id text, event_type text, payload jsonb)')
            await conn.execute("insert into jobs (id,user_id,project_id,status,locked_by,metadata) "
                               "values ('j','u','p','running','lease','{\"other\":\"keep\"}')")
            await conn.commit()
            @asynccontextmanager
            async def connection():
                yield conn
            app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection)))
            job = {'id': 'j', 'user_id': 'u', 'project_id': 'p', 'lease_token': 'lease'}
            store = ProgressStore(app, job, 'cut')
            initial = {'attempt': 1, 'decision': 'UNKNOWN', 'unknownCause': 'provider_error',
                       'retryAfterSeconds': 2}
            await store._change(lambda _: {
                'phase': 'judged', 'fingerprint': 'recipe', 'attempts': 1,
                'candidate': {'attempt': 1, 'sha256': 'candidate'},
                'qc': {'fingerprint': 'recipe', 'attempts': 1, 'judgments': [initial]}})
            await store.claim_judgment_retry('recipe', 1, 'candidate')
            kwargs = dict(job_id='j', lease_token='lease', user_id='u', project_id='p', reserved=38,
                          settle_key='k', message='interrupted', metadata={'error': 'worker_shutdown'},
                          code='worker_shutdown', result={'errorCode': 'worker_shutdown'},
                          job_metadata={'retainedCutKeys': {'b': 'private'}})
            assert await repo.finalize_detail_page_failure(conn, **kwargs) is True
            await conn.commit()
            row = await (await conn.execute("select * from jobs where id='j'")).fetchone()
            assert row['status'] == 'error' and row['error_message'] == 'interrupted'
            assert row['result'] == {'errorCode': 'worker_shutdown'}
            assert row['metadata']['other'] == 'keep'
            assert row['metadata']['retainedCutKeys'] == {'b': 'private'}
            state = row['metadata']['detailShots'][store.key]
            assert state['phase'] == 'held' and state['judgmentRetryLease'] is None
            assert state['qc']['judgments'] == [initial]
            assert state['judgmentRetries'][-1]['status'] == 'INTERRUPTED'
            assert state['qc']['judgmentRetries'] == state['judgmentRetries']
            event = await (await conn.execute('select * from job_events')).fetchone()
            assert event['payload'] == {'code': 'worker_shutdown', 'message': 'interrupted'}
            assert await repo.finalize_detail_page_failure(conn, **kwargs) is False
            assert settle.await_count == 1
            assert settle.await_args.kwargs['reserved'] == 38
            assert settle.await_args.kwargs['charge'] == 0
    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)
