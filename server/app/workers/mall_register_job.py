"""Uncharged, per-mall registration using one immutable job input."""
from copy import deepcopy
import uuid

from .. import repo
from ..services.cafe24_client import ERROR_COPY, Cafe24Client, classify_error, normalize_mall_id
from ..services.mall_adapters.base import get_adapter
from ..services.mall_registration_contract import derive_registration_outcome, registration_view
from .mall_register_context import RegistrationJobContext, RegistrationStopped, recheck_registration


async def finish_registration(app, job: dict, *, code: str | None = None) -> None:
    if code == 'lease_lost':
        return
    metadata, payload = job['metadata'], job['payload']
    lookup_outcome = 'failed'
    if (metadata.get('create') or {}).get('sentAt'):
        lookup_outcome = 'duplicate_suspected' if (metadata.get('lookup') or {}).get('kind') == 'multiple' else 'unknown'
    # The public helper intentionally returns None while running. Evaluate the
    # proposed terminal row so finalization uses the same outcome priority.
    outcome = derive_registration_outcome({**job, 'status': 'done', 'result': {'outcome': lookup_outcome}})
    warnings = list(metadata.get('warnings') or [])
    if outcome == 'created' and code:
        warning = dict(code=code, message=ERROR_COPY.get(code, ERROR_COPY['unknown']))
        if warning not in warnings:
            warnings.append(warning)
    if outcome == 'created' and all(row['quantity'] == 0 for row in payload['snapshot']['variants']):
        warning = dict(code='all_sold_out', message='모든 옵션이 품절로 등록돼요')
        if warning not in warnings:
            warnings.append(warning)
    result = dict(outcome=outcome, code=code, warnings=warnings)
    if outcome == 'created' and payload['mall'] == 'cafe24':
        mall_id = normalize_mall_id(payload['shopId'])
        if mall_id == payload['shopId']:
            # Fixed, validated Cafe24 domains. Do not trust store.mall_url as a UI link.
            result.update(productUrl=f'https://{mall_id}.cafe24.com/product/detail.html?product_no={int(metadata["productId"])}',
                          adminUrl=f'https://{mall_id}.cafe24.com/disp/admin/shop1/product/productmodify?product_no={int(metadata["productId"])}')
    terminal = {**job, 'status': 'error' if outcome == 'failed' else 'done', 'result': result}
    view = registration_view(terminal)
    result.update({key: deepcopy(view[key]) for key in ('setupStatus', 'productId', 'stages', 'retryAction')})
    async with app.state.pool.connection() as conn:
        # Connection lock precedes the job row lock. No token refresh in this short transaction.
        async with conn.cursor() as cur:
            await cur.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))',
                              (f'mall:{job["user_id"]}:{payload["mall"]}',))
        if not await repo.renew_job_lease(conn, job['id'], job['lease_token']):
            return
        if warnings != (metadata.get('warnings') or []):
            if not await repo.set_job_metadata_patch(conn, job_id=job['id'], lease_token=job['lease_token'],
                                                     patch={'warnings': warnings}):
                return
            metadata['warnings'] = warnings
        if outcome == 'created' and view['setupStatus'] == 'complete':
            async with conn.cursor() as cur:
                await cur.execute("""update mall_connections set last_registered_at = now()
                    where id = %s and user_id = %s and status = 'connected'
                    and exists (select 1 from jobs where id = %s and locked_by = %s and status = 'running')""",
                    (payload['connectionId'], job['user_id'], job['id'], job['lease_token']))
        await repo.finalize_uncharged_job(conn, job_id=job['id'], lease_token=job['lease_token'],
            status=terminal['status'], result=result,
            error_message=ERROR_COPY.get(code or 'unknown', ERROR_COPY['unknown']) if outcome == 'failed' else None)
        await conn.commit()


async def run_mall_register_job(app, job: dict) -> None:
    job['metadata'] = deepcopy(job.get('metadata') or {})
    try:
        await recheck_registration(app, job)
        settings, payload = app.state.settings, job['payload']
        adapter = get_adapter(payload['mall'])
        client = Cafe24Client(payload['shopId'], client_id=settings.cafe24_client_id,
            client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)
        ctx = RegistrationJobContext(app, job, client=client, storage=app.state.r2)
        if not job['metadata'].get('externalCode'):
            await ctx.checkpoint(dict(externalCode='wl' + uuid.UUID(job['id']).hex[:16]))
        if not job['metadata'].get('productId'):
            if job['metadata'].get('rejectedAt'):
                await finish_registration(app, job, code='invalid_request')
                return
            if not (job['metadata'].get('create') or {}).get('sentAt'):
                await adapter.prepare(ctx)
                try:
                    await adapter.create(ctx)
                except RegistrationStopped:
                    raise
                except Exception:
                    if job['metadata'].get('rejectedAt') or not (job['metadata'].get('create') or {}).get('sentAt'):
                        raise
                    # A lost create response can only be recovered by exact-code lookup.
            if not job['metadata'].get('productId'):
                lookup = await adapter.lookup_created(ctx)
                await ctx.checkpoint(dict(lookup=lookup))
                if lookup['kind'] == 'found':
                    await ctx.checkpoint(dict(productId=lookup['productIds'][0]))
        if job['metadata'].get('productId'):
            await adapter.complete_setup(ctx)
        await finish_registration(app, job)
    except RegistrationStopped as exc:
        await finish_registration(app, job, code=exc.code)
    except Exception as exc:
        await finish_registration(app, job, code=classify_error(exc))
