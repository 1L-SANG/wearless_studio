"""Real PostgreSQL lease loss with a delayed MockTransport response, no external services."""
import asyncio
import copy
from datetime import datetime, timezone

import httpx
import pytest

from app import repo
from app.services import cafe24_tokens
from app.services.cafe24_client import Cafe24Client
from app.services.mall_adapters.cafe24 import Cafe24Adapter, build_cafe24_product_request, _hash
from app.workers import mall_register_job as worker
from app.workers.mall_register_context import RegistrationJobContext, RegistrationStopped
from test_mall_connect_postgres import mall_pg
from test_mall_cafe24_worker_postgres import worker_pg
from test_mall_cafe24_worker import CONNECTION, JOB, LEASE, USER


def test_stale_lease_cannot_patch_or_start_next_remote_call(worker_pg, monkeypatch):
    app, admin, pool = worker_pg
    calls = []
    app.state.r2 = object()

    async def token(app, *, user_id, connection_id):
        assert (user_id, connection_id) == (USER, CONNECTION)
        return 'fixture-token', datetime.now(timezone.utc)

    async def prepare(adapter, ctx):
        from app.services.cafe24_client import build_description_html
        urls = ['https://myshop.cafe24.com/copied.jpg']
        body = build_cafe24_product_request(ctx.snapshot,
            description=build_description_html(urls, ctx.snapshot['productName'], ctx.snapshot['publication']['width']),
            price_basis='S', external_code=ctx.job['metadata']['externalCode'])
        await ctx.checkpoint(dict(checkpoints={'uploading_images': dict(status='complete', urls=urls),
            'price_setting': dict(status='complete', basis='S', requestHash=_hash(body))}))

    monkeypatch.setattr(cafe24_tokens, 'ensure_cafe24_access_token', token)
    monkeypatch.setattr(Cafe24Adapter, 'prepare', prepare)

    async def scenario():
        sent, respond = asyncio.Event(), asyncio.Event()

        async def handler(request):
            calls.append(request.method)
            assert request.method == 'POST' and request.url.path == '/api/v2/admin/products'
            committed = admin.execute('select metadata from jobs where id = %s', (JOB,)).fetchone()['metadata']
            assert committed['create']['sentAt'] and committed['create']['leaseToken'] == LEASE
            sent.set()
            await respond.wait()
            return httpx.Response(201, json={'product': {'product_no': 123}})

        monkeypatch.setattr(worker, 'Cafe24Client', lambda *args, **kwargs:
            Cafe24Client(*args, **kwargs, transport=httpx.MockTransport(handler)))
        async with pool.connection() as conn:
            stale = await repo.get_job(conn, USER, JOB)
            stale['lease_token'] = LEASE
            await conn.commit()
        task = asyncio.create_task(worker.run_mall_register_job(app, stale))
        try:
            await asyncio.wait_for(sent.wait(), timeout=5)
            async with pool.connection() as conn:
                await conn.execute("update jobs set locked_at = now() - interval '1 hour' where id = %s", (JOB,))
                assert (await repo.recover_stale_leases(conn, 60, ('mall_register',)))[0]['status'] == 'pending'
                await conn.commit()
                claimed = await repo.claim_next_job(conn, ('mall_register',), 'new-worker')
                await conn.commit()
                assert claimed['lease_token'] != LEASE
            before = copy.deepcopy(admin.execute('select * from jobs where id = %s', (JOB,)).fetchone())
            respond.set()
            await asyncio.wait_for(task, timeout=5)
            ctx = RegistrationJobContext(app, stale, client=worker.Cafe24Client('myshop',
                client_id='id', client_secret='secret', api_version='2026-09-01'), storage=app.state.r2)
            with pytest.raises(RegistrationStopped, match='lease_lost'):
                await ctx.checkpoint({'productId': 'stale'})
            with pytest.raises(RegistrationStopped, match='lease_lost'):
                await ctx.call(lambda token: ctx.client.product_state(token, 123), effect='read')
            await worker.finish_registration(app, stale, code='unknown')
            async with pool.connection() as conn:
                assert not await repo.set_job_progress_fenced(conn, job_id=JOB, lease_token=LEASE, progress=99)
                await conn.commit()
            after = admin.execute('select * from jobs where id = %s', (JOB,)).fetchone()
            assert after == before and calls == ['POST']
            assert 'productId' not in after['metadata']
            assert admin.execute('select count(*) as n from job_events').fetchone()['n'] == 0
            assert admin.execute('select last_registered_at from mall_connections').fetchone()['last_registered_at'] is None
        finally:
            respond.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
