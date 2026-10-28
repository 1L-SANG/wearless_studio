"""Owner-scoped single registration contract, admission and recovery APIs."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, StrictInt

from . import repo
from .auth import require_user
from .db import get_conn
from .detail_publications import _assert_account_open
from .routes import _wake_dispatcher
from .services.cafe24_client import Cafe24Client, classify_error
from .services.cafe24_tokens import call_with_token
from .services.mall_catalog import (
    registration_capability, load_mall_facts,
)
from .services.mall_preflight import evaluate_preflight, issue
from .services.mall_registration_contract import (
    MALLS, StrictModel, RegisterDraftInput, normalize_register_draft, registration_request_hash,
    freeze_registration, registration_view, derive_registration_outcome,
)
from .services.mall_sales_defaults import sales_defaults_view

router = APIRouter(prefix='/v1/malls', tags=['Mall registrations'])
project_router = APIRouter(prefix='/v1/projects', tags=['Mall registrations'])


class PreflightRequest(RegisterDraftInput):
    pass


class BatchRegistrationRequest(StrictModel):
    batchId: UUID
    draft: RegisterDraftInput


class ResumeRegistrationRequest(StrictModel):
    requestKey: UUID


class SalesDefaultsRequest(StrictModel):
    baseRevision: StrictInt = Field(ge=0)
    shipping: dict | None = None
    returns: dict | None = None
    disclosure: dict | None = None
    taxType: str | None = None
    productOrigin: str | None = None
    shippingDays: StrictInt | None = None
    listingMode: str | None = None


class MallDefaultsRequest(StrictModel):
    baseRevision: StrictInt = Field(ge=0)
    addresses: dict[str, str] = Field(default_factory=dict)


def _error(code, message, status=400, **detail):
    return HTTPException(status_code=status, detail=dict(code=code, message=message, **detail))


def _uuid(value, code='registration_not_found'):
    try:
        return str(UUID(value))
    except ValueError:
        raise _error(code, '등록 내역을 찾을 수 없어요', 404) from None


def _mall(value):
    if value not in MALLS:
        raise _error('mall_not_found', '쇼핑몰을 찾을 수 없어요', 404)
    return value


@asynccontextmanager
async def _conn(request):
    try:
        async with get_conn(request) as conn:
            yield conn
    except repo.CreditError as exc:
        extra = {'items': exc.items} if hasattr(exc, 'items') else {}
        raise _error(exc.code, exc.message, exc.status, **extra) from None
    except repo.MallAccountClosed:
        raise _error('account_closed', '계정 삭제가 완료되어 사용할 수 없습니다.', 404) from None


async def _locks(conn, keys):
    async with conn.cursor() as cur:
        for key in keys:
            await cur.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))', (key,))


async def _source(conn, draft, user_id, facts_by_mall, *, lock=False, settings):
    source = await repo.load_registration_source(conn, user_id=user_id, project_id=draft['projectId'],
        publication_id=draft['publicationId'], asset_ids=[i for i in [draft['mainAssetId'], *draft['additionalAssetIds']] if i], lock=lock)
    if not source.get('project'):
        raise _error('project_not_found', '프로젝트를 찾을 수 없어요', 404)
    if draft['publicationId'] and not source.get('publication'):
        raise _error('publication_not_found', '게시한 상세페이지를 찾을 수 없어요', 404)
    if {a['id'] for a in source['assets']} != set(i for i in [draft['mainAssetId'], *draft['additionalAssetIds']] if i):
        raise _error('asset_not_found', '이 프로젝트의 이미지를 찾을 수 없어요', 404)
    if await repo.project_uses_real_model(conn, user_id=user_id, project_id=draft['projectId']):
        raise _error('real_model_not_allowed', '실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요', 403)
    source['salesDefaults'] = await repo.get_sales_defaults(conn, user_id=user_id)
    source['mallSpecificDefaults'], source['recoveries'] = {}, {}
    for mall in draft['selectedMalls']:
        source['mallSpecificDefaults'][mall] = await repo.get_mall_defaults(conn, user_id=user_id, mall=mall)
        connection = await repo.get_mall_connection(conn, user_id=user_id, mall=mall)
        if connection:
            source['recoveries'][mall] = await repo.find_mall_registration_recoveries(conn, user_id=user_id,
                project_id=draft['projectId'], mall=mall, shop_id=connection['shop_id']) if mall == 'cafe24' else []
        if mall in facts_by_mall:
            facts = facts_by_mall[mall]
            capability = registration_capability(settings, mall, connection)
            if facts.get('connectionId') != (connection or {}).get('id'):
                facts.update(status='blocked', reasonCode='connection_changed')
            elif capability['status'] != 'available':
                facts.update(capability)
    return source


def _draft(body):
    return normalize_register_draft(body.model_dump(mode='json', exclude_unset=True))


@router.get('/sales-defaults')
async def get_sales_defaults(request: Request, user_id: str = Depends(require_user)):
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        return sales_defaults_view(await repo.get_sales_defaults(conn, user_id=user_id))


@router.put('/sales-defaults')
async def put_sales_defaults(request: Request, body: SalesDefaultsRequest, user_id: str = Depends(require_user)):
    async with _conn(request) as conn:
        await _locks(conn, [f'mall-account:{user_id}'])
        await _assert_account_open(conn, user_id)
        try:
            row = await repo.save_sales_defaults(conn, user_id=user_id, base_revision=body.baseRevision,
                patch=body.model_dump(exclude_unset=True, exclude={'baseRevision'}))
        except ValueError:
            raise _error('invalid_defaults', '판매 기본 정보를 다시 확인해 주세요', 422) from None
        return sales_defaults_view(row)


@router.get('/{mall}/registration-defaults')
async def get_registration_defaults(request: Request, mall: str, user_id: str = Depends(require_user)):
    _mall(mall)
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await repo.get_mall_defaults(conn, user_id=user_id, mall=mall) or {}
    return dict(revision=row.get('revision', 0), addresses=row.get('addresses') or {})


@router.put('/{mall}/registration-defaults')
async def put_registration_defaults(request: Request, mall: str, body: MallDefaultsRequest, user_id: str = Depends(require_user)):
    _mall(mall)
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        connection = await repo.get_mall_connection(conn, user_id=user_id, mall=mall)
    if not connection:
        raise _error('connection_required', '쇼핑몰을 먼저 연결해 주세요', 409)
    addresses = body.addresses
    if addresses.keys() - {'shippingAddressId', 'returnAddressId'}:
        raise _error('invalid_defaults', '주소 선택을 다시 확인해 주세요', 422)
    if any(addresses.values()):
        if mall != 'zigzag':
            raise _error('invalid_defaults', '이 쇼핑몰은 주소 선택을 지원하지 않아요', 422)
        from .mall_connect import list_zigzag_addresses
        rows = await list_zigzag_addresses(request, user_id)
        if any(value.strip() and value.strip() not in {row['id'] for row in rows} for value in addresses.values()):
            raise _error('address_not_found', '현재 스토어의 주소를 골라 주세요', 422)
    async with _conn(request) as conn:
        await _locks(conn, [f'mall-account:{user_id}', f'mall:{user_id}:{mall}'])
        await _assert_account_open(conn, user_id)
        current = await repo.get_mall_connection(conn, user_id=user_id, mall=mall)
        if not current or current['id'] != connection['id']:
            raise _error('connection_changed', '연결이 바뀌었어요. 다시 확인해 주세요', 409)
        row = await repo.save_mall_specific_defaults(conn, user_id=user_id, mall=mall, base_revision=body.baseRevision, patch={'addresses': addresses})
    return dict(revision=row['revision'], addresses=row['addresses'])


@router.get('/{mall}/registration-catalog')
async def get_registration_catalog(request: Request, mall: str, user_id: str = Depends(require_user)):
    _mall(mall)
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
    facts = await load_mall_facts(request.app, user_id=user_id, mall=mall)
    return {key: facts.get(key) for key in ('mall', 'status', 'catalogVersion', 'complete', 'categories', 'priceBasis', 'imageRatio', 'capabilities', 'issues')}


@router.post('/registrations/preflight')
async def preflight_registration(request: Request, body: PreflightRequest, user_id: str = Depends(require_user)) -> dict:
    draft = _draft(body)
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _source(conn, draft, user_id, {}, settings=request.app.state.settings)
    facts = {mall: await load_mall_facts(request.app, user_id=user_id, mall=mall) for mall in draft['selectedMalls']}
    async with _conn(request) as conn:
        source = await _source(conn, draft, user_id, facts, settings=request.app.state.settings)
    return evaluate_preflight(draft, source=source, facts_by_mall=facts)


def _receipt(receipt):
    return {key: deepcopy(receipt[key]) for key in ('batchId', 'acceptedAt', 'items')} | {
        'readyCount': sum(i['status'] == 'accepted' for i in receipt['items'])}


@router.post('/registration-batches', status_code=202)
async def create_registration_batch(request: Request, response: Response, body: BatchRegistrationRequest, user_id: str = Depends(require_user)) -> dict:
    draft, batch_id = _draft(body.draft), str(body.batchId)
    request_hash = registration_request_hash(draft)
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        previous = await repo.get_mall_batch_jobs(conn, user_id=user_id, batch_id=batch_id)
        if previous:
            receipt = previous[0]['metadata']['batchReceipt']
            if receipt['requestHash'] != request_hash:
                raise repo.IdempotencyKeyReused()
            response.status_code = 200
            return _receipt(receipt)
    facts = {mall: await load_mall_facts(request.app, user_id=user_id, mall=mall) for mall in draft['selectedMalls']}
    async with _conn(request) as conn:
        await _locks(conn, [f'mall-account:{user_id}', *[f'mall:{user_id}:{m}' for m in draft['selectedMalls']],
            f'mall-sales-defaults:{user_id}', *[f'mall-defaults:{user_id}:{m}' for m in draft['selectedMalls']],
            f'mall-batch:{user_id}:{batch_id}', f'mall-register:{user_id}:{draft["projectId"]}'])
        await _assert_account_open(conn, user_id)
        previous = await repo.get_mall_batch_jobs(conn, user_id=user_id, batch_id=batch_id)
        if previous:
            receipt = previous[0]['metadata']['batchReceipt']
            if receipt['requestHash'] != request_hash:
                raise repo.IdempotencyKeyReused()
            response.status_code = 200
            return _receipt(receipt)
        source = await _source(conn, draft, user_id, facts, lock=True, settings=request.app.state.settings)
        result = evaluate_preflight(draft, source=source, facts_by_mall=facts)
        payloads, blocked = {}, []
        for item in result['items']:
            mall = item['mall']
            if item['status'] == 'ready':
                confirmed = draft['confirmedSourcesByMall'].get(mall)
                current = item['sourceVersions']
                code = None
                if not confirmed: code = 'preflight_required'
                elif confirmed['connectionVersion'] != current['connectionVersion']: code = 'connection_changed'
                elif any(confirmed[k] != current[k] for k in ('salesDefaultsRevision', 'mallDefaultsRevision')): code = 'defaults_changed'
                elif confirmed['catalogVersion'] != current['catalogVersion']: code = 'catalog_changed'
                elif confirmed['editorRevision'] != current['editorRevision']: code = 'editor_revision_changed'
                if code:
                    item.update(status='blocked', issues=[issue(code, 'confirmedSourcesByMall.' + mall, '확인한 정보가 바뀌었어요. 다시 점검해 주세요', 'open_defaults')])
                else:
                    payloads[mall] = dict(mall=mall, batchId=batch_id, requestHash=request_hash,
                        connectionId=facts[mall]['connectionId'], shopId=facts[mall]['shopId'],
                        publicationId=draft['publicationId'], editorRevision=draft['editorRevision'],
                        snapshot=freeze_registration(draft, source=source, facts=facts[mall]))
            if item['status'] != 'ready':
                blocked.append({key: item[key] for key in ('mall', 'issues', 'existingJobId') if key in item} | {'status': 'blocked'})
        receipt = await repo.enqueue_mall_registration_batch(conn, user_id=user_id, project_id=draft['projectId'],
            batch_id=batch_id, request_hash=request_hash, payloads=payloads, blocked=blocked)
        await conn.commit()
    _wake_dispatcher(request)
    return _receipt(receipt)


async def _job(conn, user_id, job_id, *, lock=False):
    async with conn.cursor() as cur:
        await cur.execute(f"select {repo._JOB_COLS} from jobs where id = %s and user_id = %s "
            "and kind = 'mall_register' and payload->>'mall' in ('cafe24', 'zigzag', 'ably')" + (' for update' if lock else ''), (job_id, user_id))
        job = await cur.fetchone()
    if not job:
        raise _error('registration_not_found', '등록 내역을 찾을 수 없어요', 404)
    return job


@router.get('/registration-batches/{batch_id}')
async def get_registration_batch(request: Request, batch_id: str, user_id: str = Depends(require_user)) -> dict:
    async with _conn(request) as conn:
        jobs = await repo.get_mall_batch_jobs(conn, user_id=user_id, batch_id=_uuid(batch_id))
    if not jobs:
        raise _error('registration_not_found', '접수 기록을 찾을 수 없어요', 404)
    receipt = _receipt(jobs[0]['metadata']['batchReceipt'])
    views = {j['id']: registration_view(j) for j in jobs}
    receipt['items'] = [dict(item, registration=views[item['jobId']]) if item['status'] == 'accepted' else item for item in receipt['items']]
    return receipt


@router.get('/registrations/{job_id}')
async def get_registration(request: Request, job_id: str, user_id: str = Depends(require_user)) -> dict:
    async with _conn(request) as conn:
        return registration_view(await _job(conn, user_id, _uuid(job_id)))


async def _connection(conn, settings, user_id, job):
    payload = job['payload']
    connection = await repo.get_mall_connection(conn, user_id=user_id, mall=payload['mall'])
    if not connection or connection['id'] != payload['connectionId'] or connection['shop_id'] != payload['shopId']:
        raise _error('connection_changed', '연결이 바뀌었어요. 기존 쇼핑몰 관리자에서 확인해 주세요', 409)
    capability = registration_capability(settings, payload['mall'], connection)
    if capability['status'] != 'available':
        raise _error(capability.get('reasonCode', 'registration_preparing'), '쇼핑몰 연결과 등록 준비 상태를 확인해 주세요', 409)


async def _limit(conn, user_id, operation):
    key = f'mall-{operation}:{user_id}'
    await _locks(conn, [key])
    async with conn.cursor() as cur:
        await cur.execute("select coalesce(sum(request_count), 0) as count from client_event_rate_limits "
            "where scope = 'user' and key = %s and window_start > clock_timestamp() - interval '1 minute'", (key,))
        if (await cur.fetchone())['count'] >= 6:
            raise _error('rate_limited', '1분에 6번까지 확인할 수 있어요', 429)
        await cur.execute("insert into client_event_rate_limits (scope, key, window_start, request_count) values ('user', %s, clock_timestamp(), 1)", (key,))


@router.post('/registrations/{job_id}/verify')
async def verify_registration(request: Request, job_id: str, user_id: str = Depends(require_user)) -> dict:
    job_id = _uuid(job_id)
    settings = request.app.state.settings
    async with _conn(request) as conn:
        await _assert_account_open(conn, user_id)
        job = await _job(conn, user_id, job_id)
        if derive_registration_outcome(job) not in ('unknown', 'duplicate_suspected'):
            raise _error('not_verifiable', '등록 여부를 다시 확인할 수 없는 상태예요', 409)
        await _connection(conn, settings, user_id, job)
        await _limit(conn, user_id, 'verify')
        await conn.commit()
    payload = job['payload']
    client = Cafe24Client(payload['shopId'], client_id=settings.cafe24_client_id,
                          client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)
    try:
        products = await call_with_token(request.app, user_id=user_id, connection_id=payload['connectionId'],
            fn=lambda token: client.find_products_by_custom_code(token, job['metadata']['externalCode']))
    except Exception as exc:
        raise _error(classify_error(exc), '등록 여부를 확인하지 못했어요. 쇼핑몰 연결을 확인해 주세요', 409) from None
    async with _conn(request) as conn:
        await _locks(conn, [f'mall-account:{user_id}', f'mall:{user_id}:{payload["mall"]}'])
        await _assert_account_open(conn, user_id)
        await _connection(conn, settings, user_id, job)
        job = await _job(conn, user_id, job_id, lock=True)
        outcome = derive_registration_outcome(job)
        if outcome == 'created':
            return registration_view(job)
        if outcome not in ('unknown', 'duplicate_suspected'):
            raise _error('not_verifiable', '등록 여부를 다시 확인할 수 없는 상태예요', 409)
        if len(products) == 1:
            await repo.set_job_metadata_patch(conn, job_id=job_id, user_id=user_id,
                patch={'productId': str(products[0])}, expect_status=('done', 'error', 'cancelled'))
            await repo.set_job_result_patch(conn, job_id=job_id, user_id=user_id,
                patch=dict(outcome='created', setupStatus='needs_attention', code=None,
                    productUrl=f'https://{payload["shopId"]}.cafe24.com/product/detail.html?product_no={products[0]}'),
                expect_status=('done', 'error', 'cancelled'))
        elif len(products) > 1:
            await repo.set_job_result_patch(conn, job_id=job_id, user_id=user_id,
                patch={'outcome': 'duplicate_suspected'}, expect_status=('done', 'error', 'cancelled'))
        return registration_view(await _job(conn, user_id, job_id))


@router.post('/registrations/{job_id}/resume')
async def resume_registration(request: Request, job_id: str, body: ResumeRegistrationRequest, user_id: str = Depends(require_user)) -> dict:
    job_id, request_key = _uuid(job_id), str(body.requestKey)
    async with _conn(request) as conn:
        job = await _job(conn, user_id, job_id)
        mall = job['payload']['mall']
        await _locks(conn, [f'mall-account:{user_id}', f'mall:{user_id}:{mall}', f'mall-register:{user_id}:{job["project_id"]}'])
        await _assert_account_open(conn, user_id)
        job = await _job(conn, user_id, job_id, lock=True)
        if request_key in job['metadata'].get('resumeRequestKeys', []):
            return registration_view(job)
        await _connection(conn, request.app.state.settings, user_id, job)
        if registration_view(job)['retryAction'] != 'resume':
            raise _error('registration_not_resumable', '이 작업은 설정을 이어 할 수 없어요', 409)
        await _limit(conn, user_id, 'resume')
        row, changed = await repo.requeue_mall_registration(conn, user_id=user_id, job_id=job_id, request_key=request_key)
        await conn.commit()
    if changed:
        _wake_dispatcher(request)
    return registration_view(row)


@project_router.get('/{project_id}/mall-registrations')
async def list_mall_registrations(request: Request, project_id: str, user_id: str = Depends(require_user)):
    project_id = _uuid(project_id, 'project_not_found')
    async with _conn(request) as conn:
        if not await repo.get_project(conn, user_id, project_id):
            raise _error('project_not_found', '프로젝트를 찾을 수 없어요', 404)
        rows = await repo.list_project_jobs_by_kind(conn, user_id=user_id, project_id=project_id, kind='mall_register', limit=5)
        recoveries = {'cafe24': [], 'zigzag': []}
        connection = await repo.get_mall_connection(conn, user_id=user_id, mall='cafe24')
        if connection:
            recoveries['cafe24'] = [registration_view(job) for job in await repo.find_mall_registration_recoveries(
                conn, user_id=user_id, project_id=project_id, mall='cafe24', shop_id=connection['shop_id'])]
    return dict(items=[registration_view(row) for row in rows if row['payload'].get('mall') in MALLS], recoveries=recoveries)
