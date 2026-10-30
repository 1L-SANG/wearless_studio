"""Common verify and resume routes never repeat an uncertain creation."""
import copy
from datetime import datetime, timezone
import uuid

import httpx
import pytest

from app import mall_registration
from app.services.cafe24_client import Cafe24Client, ConnectionChanged, ReconnectRequired
from multimall_fakes import registration_env, submit
from test_mall_cafe24_register_routes import assert_error, BASE
from test_mall_connect import OTHER, USER


@pytest.fixture
def env(registration_env,monkeypatch):
    client,store=registration_env;assert submit(client).status_code==202
    job=next(iter(store['jobs'].values()))
    job.update(status='done',progress=100,result={'outcome':'unknown','code':'unreachable'})
    job['metadata']['create']={'sentAt':'now','attempt':1}
    store.update(matches=[],remote=[],during_lookup=None)
    def respond(request):
        store['remote'].append((request.method,request.url.path))
        assert request.method=='GET' and request.url.path.endswith('/admin/products')
        assert request.headers['Authorization']=='Bearer fixture-token'
        hook=store.pop('during_lookup',None)
        if hook:hook()
        products=[dict(product_no=n,custom_product_code=job['metadata']['externalCode']) for n in store['matches']]
        products.append(dict(product_no=999,custom_product_code=job['metadata']['externalCode']+'extra'))
        return httpx.Response(200,json={'products':products})
    async def token(app,*,user_id,connection_id,fn):return await fn('fixture-token')
    monkeypatch.setattr(mall_registration,'call_with_token',token)
    monkeypatch.setattr(mall_registration,'Cafe24Client',lambda *a,**kw:Cafe24Client(*a,**kw,transport=httpx.MockTransport(respond)))
    return client,store,job


def verify(client,jid):return client.post(f'{BASE}/{jid}/verify')
def resume(client,jid,key=None):return client.post(f'{BASE}/{jid}/resume',json={'requestKey':key or str(uuid.uuid4())})


@pytest.mark.parametrize('case,status,code',[
    ('foreign',404,'registration_not_found'),('missing',404,'registration_not_found'),('invalid_id',404,'registration_not_found'),
    ('kind',404,'registration_not_found'),('mall',404,'registration_not_found'),('pending',409,'not_verifiable'),
    ('running',409,'not_verifiable'),('created',409,'not_verifiable'),('failed',409,'not_verifiable'),
    ('rejected',409,'not_verifiable'),('closed_account',404,'account_closed'),('unauthenticated',401,None)])
def test_verify_requires_owner_and_verifiable_outcome(env,case,status,code):
    client,store,job=env;jid=job['id']
    if case=='foreign':job['user_id']=OTHER
    elif case=='missing':jid=str(uuid.uuid4())
    elif case=='invalid_id':jid='invalid'
    elif case=='kind':job['kind']='export'
    elif case=='mall':job['payload']['mall']='smartstore'
    elif case in ('pending','running'):job['status']=case
    elif case=='created':job['metadata']['productId']='42'
    elif case=='failed':job['metadata'].pop('create');job['result']={}
    elif case=='rejected':job['metadata']['rejectedAt']='later'
    elif case=='closed_account':store['closed']=True
    elif case=='unauthenticated':client.app.dependency_overrides.clear()
    before=copy.deepcopy(job);response=verify(client,jid)
    if code:assert_error(response,status,code)
    else:assert response.status_code==status
    assert store['jobs'][job['id']]==before and not store['remote']


@pytest.mark.parametrize('status',['done','error','cancelled'])
@pytest.mark.parametrize('outcome',['unknown','duplicate_suspected'])
def test_verify_found_one_confirms_product_and_leaves_setup_for_resume(env,status,outcome):
    client,store,job=env;job.update(status=status,result={'outcome':outcome});store['matches']=[42]
    before=copy.deepcopy(job['payload']);response=verify(client,job['id'])
    assert response.status_code==200,response.text
    view=response.json();assert (view['status'],view['outcome'],view['productId'])==(status,'created','42')
    assert view['setupStatus']=='needs_attention' and view['retryAction']=='resume'
    assert job['payload']==before and job['metadata']['productId']=='42'
    assert view['productUrl']=='https://myshop.cafe24.com/product/detail.html?product_no=42'
    assert_error(verify(client,job['id']),409,'not_verifiable')
    assert len(store['remote'])==1 and store['limits']['register']==1


@pytest.mark.parametrize('already_created',[False,True])
def test_verify_found_two_sets_duplicate_suspected_or_keeps_confirmed_product(env,already_created):
    client,store,job=env;store['matches']=[42,43]
    if already_created:store['during_lookup']=lambda:job['metadata'].update(productId='41')
    response=verify(client,job['id']);assert response.status_code==200,response.text
    assert response.json()['outcome']==('created' if already_created else 'duplicate_suspected')
    assert response.json()['productId']==('41' if already_created else None)


@pytest.mark.parametrize('outcome',['unknown','duplicate_suspected'])
def test_verify_found_none_keeps_uncertainty_and_never_creates(env,outcome):
    client,store,job=env;job['result']['outcome']=outcome;before=copy.deepcopy(job)
    response=verify(client,job['id']);assert response.status_code==200,response.text
    assert response.json()['outcome']==outcome and store['jobs'][job['id']]==before
    assert len(store['remote'])==1
    assert_error(resume(client,job['id']),409,'registration_not_resumable')


@pytest.mark.parametrize('change',['disconnected','replaced','expired','during_lookup','wrong_shop','token_changed','token_reconnect'])
def test_verify_connection_changed_409(env,monkeypatch,change):
    client,store,job=env;f=store['facts']['cafe24'];store['matches']=[42]
    if change=='disconnected':f['connectionId']=None
    elif change=='replaced':f['connectionId']=str(uuid.uuid4())
    elif change=='expired':f['refresh_expires_at']=datetime.now(timezone.utc)
    elif change=='wrong_shop':job['payload']['shopId']='other'
    elif change=='during_lookup':store['during_lookup']=lambda:f.update(connectionId=None)
    else:
        async def error(*a,**kw):raise ConnectionChanged() if change=='token_changed' else ReconnectRequired()
        monkeypatch.setattr(mall_registration,'call_with_token',error)
    before=copy.deepcopy(job);response=verify(client,job['id'])
    assert response.status_code==409,response.text
    assert response.json()['error']['code'] in ('connection_changed','reconnect_required')
    assert store['jobs'][job['id']]==before


def test_verify_rate_limited_independently_from_registration(env):
    client,store,job=env
    for _ in range(6):assert verify(client,job['id']).status_code==200
    assert_error(verify(client,job['id']),429,'rate_limited')
    assert len(store['remote'])==6 and store['limits']['register']==1


def test_resume_preserves_snapshot_checkpoints_and_replays_same_request_key(env):
    client,store,job=env;job['metadata'].update(productId='42',checkpoints={'mapping_variants':{'status':'complete'}})
    before=copy.deepcopy(job);key=str(uuid.uuid4())
    first=resume(client,job['id'],key);assert first.status_code==200,first.text
    assert first.json()['status']=='pending' and first.json()['outcome'] is None
    assert job['payload']==before['payload'] and job['metadata']['checkpoints']==before['metadata']['checkpoints']
    assert resume(client,job['id'],key).status_code==200
    assert_error(resume(client,job['id']),409,'registration_not_resumable')
    assert store['limits'][f'mall-resume:{USER}']==1 and store['limits']['register']==1 and store['wakes']==2
    assert not store['remote']
