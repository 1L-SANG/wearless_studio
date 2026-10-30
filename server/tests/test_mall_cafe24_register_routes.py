"""Cafe24 route regressions expressed through the common batch contract."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app import repo
from multimall_fakes import registration_env as env, confirmed, submit
from test_multimall_routes import PROJECT, PUBLICATION, ASSET, draft
from test_mall_connect import USER, OTHER

BASE='/v1/malls/registrations'


def assert_error(response,status,code):
    assert response.status_code==status,response.text
    assert response.json()['error']['code']==code
    assert response.headers['cache-control']=='no-store'


def blocked_code(response):
    assert_error(response,409,'no_ready_malls')
    return response.json()['error']['items'][0]['issues'][0]['code']


@pytest.mark.parametrize('case,code', [
    ('missing_project','project_not_found'),('foreign_project','project_not_found'),('deleted_project','project_not_found'),
    ('closed_account','account_closed'),('missing_publication','publication_not_found'),('foreign_publication','publication_not_found'),
    ('other_project_publication','publication_not_found'),('pending_publication','publication_required'),('invalid_files','invalid_files')])
def test_create_registration_requires_live_owned_project_and_ready_publication_match(env,case,code):
    client,store=env;value=confirmed(client)
    if case=='missing_project':store['source']['project']=None
    elif case=='foreign_project':store['source']['project']['user_id']=OTHER
    elif case=='deleted_project':store['source']['project']['deleted_at']='now'
    elif case=='closed_account':store['closed']=True
    elif case=='missing_publication':store['source']['publication']=None
    elif case=='foreign_publication':store['source']['publication']['user_id']=OTHER
    elif case=='other_project_publication':store['source']['publication']['project_id']=str(uuid.uuid4())
    elif case=='pending_publication':store['source']['publication']['status']='pending'
    else:store['source']['publication']['files'][0]['index']=2
    response=submit(client,value)
    if case in ('pending_publication','invalid_files'):assert blocked_code(response)==code
    else:assert_error(response,404,code)
    assert not store['jobs'] and not store['limits']


@pytest.mark.parametrize('change', [dict(basePrice=0),dict(basePrice=2147483648),dict(basePrice=1.5),dict(basePrice=True),dict(basePrice='39000'),
    dict(defaultQuantity=-1),dict(productName=''),dict(productName='가'*101),dict(productName='a\nb'),dict(mainAssetId='bad'),
    dict(priceOverrides={'cafe24':dict(amount=True,basis='selling_price')}),dict(salesOverrides={'listingMode':'T'}),dict(unknown='secret'),
    dict(categories={'cafe24':{'categoryId':True}}),dict(sizes=['M',' M ']),dict(variantOverrides=[{'variantKey':'x','quantity':None}])])
def test_registration_strictly_rejects_invalid_input(env,change):
    client,store=env
    assert_error(client.post(BASE+'/preflight',json={**draft(),**change}),422,'validation_error')
    assert not store['jobs'] and not store['limits']


def test_create_registration_blocks_real_model_project(env):
    client,store=env;value=confirmed(client);store['real']=True
    assert_error(submit(client,value),403,'real_model_not_allowed')
    assert not store['jobs']


@pytest.mark.parametrize('case',['foreign_project','foreign_owner','deleted','not_image'])
def test_create_registration_rejects_asset_from_other_project(env,case):
    client,store=env;value=confirmed(client);a=store['source']['assets'][0]
    if case=='foreign_project':a['project_id']=str(uuid.uuid4())
    elif case=='foreign_owner':a['user_id']=OTHER
    elif case=='deleted':a['deleted_at']='now'
    else:a['mime_type']='application/pdf'
    response=submit(client,value)
    if case=='not_image':assert blocked_code(response)=='image_format'
    else:assert_error(response,404,'asset_not_found')
    assert not store['jobs']


@pytest.mark.parametrize('case', ['expired','missing_expiry','not_allowed','missing','missing_scope'])
def test_create_registration_blocks_reconnect_required_or_not_allowed(env,case):
    client,store=env;value=confirmed(client);f=store['facts']['cafe24']
    if case=='expired':f['refresh_expires_at']=datetime.now(timezone.utc)-timedelta(days=1)
    elif case=='missing_expiry':f['refresh_expires_at']=None
    elif case=='not_allowed':client.app.state.settings=replace(client.app.state.settings,cafe24_allowed_mall_ids=frozenset({'other'}))
    elif case=='missing':f['connectionId']=None
    else:f['scopes']=[]
    assert blocked_code(submit(client,value)) in ('reconnect_required','connection_changed','registration_preparing','missing_permission','connection_required')
    assert not store['jobs']


@pytest.mark.parametrize('flag,value',[('multimall_register_enabled',False),('mall_connect_enabled',False),('detail_publish_enabled',False),
    ('cafe24_enabled',False),('cafe24_client_id',''),('cafe24_client_secret',''),('mall_keys_kek','')])
def test_create_registration_reports_preparing_when_any_required_flag_off(env,flag,value):
    client,store=env;value_draft=confirmed(client);client.app.state.settings=replace(client.app.state.settings,**{flag:value})
    assert blocked_code(submit(client,value_draft))=='registration_preparing'
    assert not store['jobs']


@pytest.mark.parametrize('status',['pending','running','done','error','cancelled'])
def test_same_request_retry_returns_original_receipt_using_actual_repo_projection(env,status):
    client,store=env;value=confirmed(client);batch=str(uuid.uuid4());first=submit(client,value,batch)
    next(iter(store['jobs'].values()))['status']=status
    store['facts']['cafe24']['connectionId']=str(uuid.uuid4())
    retry=submit(client,value,batch)
    assert retry.status_code==200 and retry.json()==first.json()
    assert len(store['jobs'])==1 and store['wakes']==1 and store['limits']['register']==1
    assert 'idempotency_key' not in repo._JOB_COLS


@pytest.mark.parametrize('change',['quota','publication','connection','project','mall','real_model','thumbnail','disabled'])
def test_accepted_request_replay_survives_changed_resources_without_new_quota(env,change):
    client,store=env;value=confirmed(client);batch=str(uuid.uuid4());first=submit(client,value,batch)
    store['limits']['register']=10
    if change=='publication':store['source']['publication']=None
    elif change=='connection':store['facts']['cafe24']['connectionId']=None
    elif change=='project':store['source']['project']=None
    elif change=='mall':store['facts']['cafe24']['shopId']='another'
    elif change=='real_model':store['real']=True
    elif change=='thumbnail':store['source']['assets']=[]
    elif change=='disabled':client.app.state.settings=replace(client.app.state.settings,multimall_register_enabled=False)
    replay=submit(client,value,batch)
    assert replay.status_code==200 and replay.json()==first.json()
    assert store['limits']['register']==10 and store['wakes']==1
    store['closed']=True
    assert_error(submit(client,value,batch),404,'account_closed')


@pytest.mark.parametrize('change',[dict(basePrice=40000),dict(productName='다른 상품'),dict(projectId=str(uuid.UUID(int=99))),
    dict(publicationId=str(uuid.UUID(int=99))),dict(defaultQuantity=1),dict(salesOverrides={'listingMode':'ON_SALE'}),
    dict(categories={'cafe24':{'categoryId':'27'}}),dict(mainAssetId=str(uuid.UUID(int=99)))])
def test_reused_key_changed_input_is_409_even_after_resources_removed_and_quota_full(env,change):
    client,store=env;value=confirmed(client);batch=str(uuid.uuid4());assert submit(client,value,batch).status_code==202
    store['source']['project']=None;store['source']['assets']=[];store['source']['publication']=None;store['limits']['register']=10
    assert_error(submit(client,{**value,**change},batch),409,'idempotency_key_reused')


@pytest.mark.parametrize('change',['owner','kind'])
def test_accepted_key_lookup_never_replays_other_owner_or_kind(env,change):
    client,store=env;value=confirmed(client);batch=str(uuid.uuid4());submit(client,value,batch)
    j=next(iter(store['jobs'].values()));j['user_id' if change=='owner' else 'kind']=OTHER if change=='owner' else 'export'
    store['source']['project']=None
    assert_error(submit(client,value,batch),404,'project_not_found')


def test_registration_in_progress_conflict_does_not_consume_quota_even_at_limit(env):
    client,store=env;value=confirmed(client);first=submit(client,value);store['limits']['register']=10
    response=submit(client,value)
    assert blocked_code(response)=='registration_unresolved'
    assert response.json()['error']['items'][0]['existingJobId']==first.json()['items'][0]['jobId']
    assert store['limits']['register']==10 and len(store['jobs'])==1


def test_create_registration_sets_external_code_from_job_id_before_dispatch(env):
    client,store=env;response=submit(client);job=next(iter(store['jobs'].values()))
    assert job['metadata']['externalCode']=='wl'+uuid.UUID(job['id']).hex[:16]
    assert set(job['payload'])=={'mall','batchId','requestHash','connectionId','shopId','publicationId','editorRevision','snapshot'}
    assert response.status_code==202 and store['wakes']==1


@pytest.mark.parametrize('status',['pending','running','done','error','cancelled'])
def test_registration_list_recovers_current_store_unresolved_beyond_latest_five(env,status):
    client,store=env;submit(client);original=next(iter(store['jobs'].values()))
    original.update(status=status,metadata={**original['metadata'],'create':{'sentAt':'now'}},created_at='2026-10-01')
    for i in range(6):
        jid=str(uuid.uuid4());store['jobs'][jid]={**copy.deepcopy(original),'id':jid,'status':'done','created_at':f'2026-10-{i+10}',
            'metadata':{'productId':str(i)},'result':{'setupStatus':'complete'}}
    response=client.get(f'/v1/projects/{PROJECT}/mall-registrations')
    assert response.status_code==200,response.text
    data=response.json();assert len(data['items'])==5 and data['recoveries']['cafe24'][0]['jobId']==original['id']
    assert original['id'] not in {j['jobId'] for j in data['items']} and data['recoveries']['zigzag']==[]
    store['facts']['cafe24']['connectionId']=None
    assert client.get(f'/v1/projects/{PROJECT}/mall-registrations').json()['recoveries']['cafe24']==[]


@pytest.mark.parametrize('metadata,result,expected', [({},None,0),({'productId':'42'},{'setupStatus':'complete'},0),
    ({'productId':'42'},{'setupStatus':'needs_attention'},1),({'rejectedAt':'later','create':{'sentAt':'now'}},None,0)])
def test_registration_list_recovers_only_unresolved_terminal_jobs(env,metadata,result,expected):
    client,store=env;submit(client);job=next(iter(store['jobs'].values()));job.update(status='error',metadata=metadata,result=result)
    data=client.get(f'/v1/projects/{PROJECT}/mall-registrations').json()
    assert len(data['recoveries']['cafe24'])==expected


def test_registration_get_and_list_are_owner_scoped_and_kind_checked(env):
    client,store=env;accepted=submit(client).json();jid=accepted['items'][0]['jobId']
    assert client.get(BASE+'/'+jid).json()['outcome'] is None
    store['source']['project']['user_id']=OTHER
    assert_error(client.get(f'/v1/projects/{PROJECT}/mall-registrations'),404,'project_not_found')
    client.app.dependency_overrides.clear()
    for path in (BASE+'/'+jid,f'/v1/projects/{PROJECT}/mall-registrations','/v1/malls/cafe24/registration-catalog'):
        assert client.get(path).status_code==401
    assert submit(client,draft()).status_code==401


def test_removed_registration_and_defaults_routes_do_not_accept_old_formats(env):
    client,_=env
    assert client.post('/v1/malls/cafe24/registrations',json={}).status_code==404
    assert client.put('/v1/malls/zigzag/defaults',json={}).status_code==404
    assert client.get('/v1/malls/cafe24/categories').status_code==404
