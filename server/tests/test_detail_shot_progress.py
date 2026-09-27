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
from app.workers.detail_shot_runtime import ProgressStore


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
            with pytest.raises(DetailShotRejected, match='attempt_budget_exhausted'):
                await two_quality.claim('quality-recipe')
            await conn.execute("update jobs set locked_by='next-lease' where id='j'")
            await conn.commit()
            with pytest.raises(DetailShotRejected, match='lease_lost'):
                await first.finish({'passed': True})

    asyncio.run(check(), loop_factory=asyncio.SelectorEventLoop)
