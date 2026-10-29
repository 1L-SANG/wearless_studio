"""Single registration contract and HTTP boundary checks, no external network."""
import copy
import importlib
import uuid

import pytest

PROJECT, PUBLICATION, ASSET = [str(uuid.UUID(int=i)) for i in (10, 11, 12)]


def contract():
    return importlib.import_module('app.services.mall_registration_contract')


def draft(**changes):
    return dict(projectId=PROJECT, publicationId=PUBLICATION, editorRevision=3,
                selectedMalls=['cafe24', 'zigzag'], productName=' 데일리  니트 ', basePrice=39000,
                defaultQuantity=0, sizes=['FREE'], variantOverrides=[], mainAssetId=ASSET,
                additionalAssetIds=[], categories={}) | changes


def test_common_notice_preserves_editor_values_and_requires_real_measurements():
    c = contract()
    blocks = [{'infoType': 'required_notice', 'info': {'fields': [
        {'key': 'name', 'value': '셀러 품명'}, {'key': 'materials', 'value': '정보 입력 필요'},
        {'key': 'sizes', 'value': '\u00a0'}]}},
        {'kind': 'size', 'info': {'rows': [{'label': 'FREE', 'values': {}}]}}]
    result = c.resolve_notice(blocks, {'name': '기본 품명', 'colors': [{'id': 'a', 'name': '검정'}],
                              'materials': [{'name': '면', 'ratio': 100}]},
                              {'disclosure': {'manufacturer': '공장', 'asManager': '담당', 'phone': '010-1234'}})
    assert result['fields']['name'] == '셀러 품명'
    assert result['fields']['materials'] == '면 100%'
    assert result['fields']['sizes'] == '' and result['fields']['madeAt'] == ''
    assert result['fields']['contact'] == '담당 010-1234'
    duplicate = copy.deepcopy(blocks[0]); duplicate['info']['fields'][0]['value'] = '다른 품명'
    assert 'name' in c.resolve_notice([*blocks, duplicate], {}, {})['conflicts']


def test_variant_keys_quantities_and_prices_are_unambiguous():
    c = contract()
    product = {'colors': [{'id': 'a|b', 'name': '검정'}, {'id': 'a', 'name': '흰색'}]}
    rows = c.build_variants(product, ['M', 'b|M'], 0, [])
    assert len({r['variantKey'] for r in rows}) == 4
    assert all(r['quantity'] == 0 for r in rows)
    for sizes, quantity, overrides in [([' M ', 'M'], 1, []), (['M'], True, []),
            (['M'], -1, []), (['M'], 0, [{'variantKey': rows[0]['variantKey'], 'quantity': 0.5}])]:
        with pytest.raises(ValueError): c.build_variants(product, sizes, quantity, overrides)
    for changes in ({'basePrice': True}, {'unknown': 'secret'}, {'basePrice': 0}):
        with pytest.raises(ValueError): c.normalize_register_draft({**draft(), **changes})


def test_batch_hash_preserves_semantic_order_and_frozen_input():
    c = contract()
    a = c.normalize_register_draft(draft())
    b = c.normalize_register_draft({**draft(), 'selectedMalls': ['zigzag', 'cafe24']})
    assert c.registration_request_hash(a) == c.registration_request_hash(b)
    a['additionalAssetIds'] = [str(uuid.UUID(int=20)), str(uuid.UUID(int=21))]
    b['additionalAssetIds'] = list(reversed(a['additionalAssetIds']))
    assert c.registration_request_hash(a) != c.registration_request_hash(b)
    assert a['productName'] == '데일리 니트'


@pytest.mark.parametrize('status,metadata,result,outcome,setup', [
    ('running', {'productId': '42'}, {'outcome': 'failed'}, None, 'pending'),
    ('error', {'productId': '42'}, {'outcome': 'failed'}, 'created', 'needs_attention'),
    ('done', {'create': {'sentAt': 'now'}}, {}, 'unknown', 'pending'),
    ('done', {'create': {'sentAt': 'now'}, 'rejectedAt': 'later'}, {}, 'failed', 'not_applicable'),
    ('done', {'productId': '42'}, {'setupStatus': 'complete'}, 'created', 'complete'),
])
def test_registration_outcome_uses_metadata_and_hides_stale_results(status, metadata, result, outcome, setup):
    c = contract()
    job = dict(id=ASSET, status=status, metadata=metadata, result=result, payload={'mall': 'cafe24'})
    view = c.registration_view(job)
    assert view['outcome'] == outcome and view['setupStatus'] == setup
    assert view['productId'] == metadata.get('productId')


def source():
    return dict(project=dict(id=PROJECT, editor_revision=3, editor_blocks=[]),
        publication=dict(id=PUBLICATION, user_id=str(uuid.UUID(int=1)), project_id=PROJECT, status='ready',
                         editor_revision=3, width=860, files=[dict(index=1, bytes=100, width=860, height=100)]),
        product=dict(name='니트', colors=[dict(id='black', name='검정')]), analysis={},
        assets=[dict(id=ASSET, r2_key='owned/main.png', mime_type='image/png', width=800, height=1000)],
        mallSpecificDefaults={}, recoveries={})


def facts(mall='cafe24', **changes):
    return dict(mall=mall, status='available', connectionId=str(uuid.UUID(int=30)), connectionVersion='v1',
                shopId='myshop', catalogVersion='cat1', complete=True, categories=[], priceBasis='S',
                imageRatio=dict(width=1, height=1), capabilities={}) | changes


def test_preflight_is_read_only_and_blocks_only_affected_mall():
    from app.services.mall_preflight import evaluate_preflight
    c = contract(); d = c.normalize_register_draft(draft())
    s = source(); before = copy.deepcopy(s)
    result = evaluate_preflight(d, source=s, facts_by_mall={'cafe24': facts(), 'zigzag': facts('zigzag', **{'status': 'preparing'})})
    assert result['readyMalls'] == ['cafe24'] and result['blockedMalls'] == ['zigzag']
    assert result['items'][0]['sourceVersions']['editorRevision'] == 3
    assert s == before
    snapshot = c.freeze_registration(d, source=s, facts=facts())
    s['mallSpecificDefaults']['cafe24'] = {'listing_mode': 'ON_SALE'}
    assert snapshot['sales']['listingMode'] == 'HIDDEN'
    d['variantOverrides'] = [{'variantKey': '["black","FREE"]', 'additionalAmount': 2147483647}]
    with pytest.raises(ValueError): c.freeze_registration(d, source=s, facts=facts())


def test_snapshot_and_notice_use_only_selected_mall_defaults():
    from app.services.mall_preflight import evaluate_preflight
    from app.services.mall_adapters.cafe24 import build_cafe24_product_request
    c = contract(); d = c.normalize_register_draft(draft()); s = source()
    s['mallSpecificDefaults'] = {
        'cafe24': {'revision': 4, 'tax_type': 'FREE', 'listing_mode': 'ON_SALE',
                   'disclosure': {'country': 'KR'}},
        'zigzag': {'revision': 9, 'tax_type': 'TAX', 'listing_mode': 'HIDDEN',
                   'shipping': {'baseFee': 9000}, 'addresses': {'returnAddressId': 'zig-return'},
                   'disclosure': {'country': 'CN', 'manufacturer': '지그재그 공장'}},
    }
    checked = evaluate_preflight(d, source=s, facts_by_mall={
        'cafe24': facts(), 'zigzag': facts('zigzag', status='preparing')})
    assert checked['readyMalls'] == ['cafe24']
    assert checked['items'][0]['sourceVersions'] == dict(connectionVersion='v1', mallDefaultsRevision=4,
        catalogVersion='cat1', editorRevision=3)
    snapshot = c.freeze_registration(d, source=s, facts=facts())
    assert snapshot['sales']['taxType'] == 'FREE' and snapshot['sales']['listingMode'] == 'ON_SALE'
    assert snapshot['notice']['fields']['origin'] == 'KR'
    assert snapshot['notice']['fields']['maker'] == ''
    assert 'shipping' not in snapshot['sales'] and 'addresses' not in snapshot['mallDefaults']
    request = build_cafe24_product_request(snapshot, description='상세페이지', price_basis='S', external_code='wl-fixture')['request']
    assert request['made_in_code'] == 'KR' and request['tax_type'] == 'B'
    assert request['shipping_fee_by_product'] == 'F'
    zigzag = c.freeze_registration(d, source=s, facts=facts('zigzag'))
    assert zigzag['sales']['shipping']['baseFee'] == 9000
    assert zigzag['notice']['fields']['maker'] == '지그재그 공장'
    s['mallSpecificDefaults']['cafe24']['disclosure']['country'] = 'US'
    s['mallSpecificDefaults']['zigzag']['shipping']['baseFee'] = 1
    assert snapshot['notice']['fields']['origin'] == 'KR' and zigzag['sales']['shipping']['baseFee'] == 9000
    s['project']['editor_blocks'] = [{'infoType': 'required_notice', 'info': {'fields': [{'key': 'origin', 'value': 'JP'}]}}]
    assert c.freeze_registration(d, source=s, facts=facts())['notice']['fields']['origin'] == 'JP'


def test_another_mall_defaults_change_does_not_invalidate_cafe24_admission(env):
    from multimall_fakes import confirmed, submit
    client, store = env
    value = confirmed(client)
    store['source']['mallSpecificDefaults']['zigzag'] = {'revision': 99, 'shipping': {'baseFee': 9000}}
    result = submit(client, value)
    assert result.status_code == 202, result.text
    snapshot = next(iter(store['jobs'].values()))['payload']['snapshot']
    assert 'shipping' not in snapshot['sales']
    assert all('mall-sales-defaults' not in str(params) for _, params in store['sql'])


def test_category_pagination_price_basis_and_image_settings_fail_closed():
    import asyncio
    async def scenario():
        import httpx
        from app.services.cafe24_client import Cafe24Client, Cafe24ApiError
        calls = []
        def respond(request):
            calls.append(request)
            if request.url.path.endswith('/images/setting'):
                return httpx.Response(200, json={'images_setting': {'detail_image_width': 800, 'detail_image_height': 1000}})
            if request.url.path.endswith('/products/42'):
                return httpx.Response(200, json={'product': {'product_no': 42, 'display': 'F', 'selling': 'F'}})
            return httpx.Response(200, json={'variants': [{'variant_code': 'V1', 'quantity': 0}]})
        client = Cafe24Client('myshop', client_id='public', client_secret='private', api_version='2026-09-01', transport=httpx.MockTransport(respond))
        assert await client.image_setting('secret') == {'width': 800, 'height': 1000}
        assert (await client.product_state('secret', 42))['productId'] == '42'
        assert (await client.variants('secret', 42))[0]['variantCode'] == 'V1'
        assert all(r.method == 'GET' for r in calls)
        client.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'images_setting': {}}))
        with pytest.raises(Cafe24ApiError): await client.image_setting('secret')
    asyncio.run(scenario())

# Import the fixture without importing this module from its fixture module at collection time.
@pytest.fixture
def env(monkeypatch):
    from multimall_fakes import registration_env
    return registration_env.__wrapped__(monkeypatch)


def test_ready_mall_is_accepted_when_blocked_mall_has_no_source_versions(env):
    from multimall_fakes import confirmed, submit
    client, store = env
    value=confirmed(client)
    assert set(value['confirmedSourcesByMall']) == {'cafe24'}
    response=submit(client,value)
    assert response.status_code==202,response.text
    assert [i['status'] for i in response.json()['items']]==['accepted','blocked']
    job=next(iter(store['jobs'].values()))
    assert set(job['payload'])=={'mall','batchId','requestHash','connectionId','shopId','publicationId','editorRevision','snapshot'}
    assert store['wakes']==1 and store['limits']['register']==1


@pytest.mark.parametrize('changed,code', [('connectionVersion','connection_changed'),('mallDefaults','defaults_changed'),('catalogVersion','catalog_changed')])
def test_admission_rechecks_connection_defaults_and_catalog_versions(env,changed,code):
    from multimall_fakes import confirmed, submit
    client,store=env;value=confirmed(client)
    if changed=='mallDefaults':store['source']['mallSpecificDefaults']={'cafe24':{'revision':1}}
    else:store['facts']['cafe24'][changed]='changed'
    r=submit(client,value)
    assert r.status_code==409,r.text
    assert r.json()['error']['items'][0]['issues'][0]['code']==code
    assert not store['jobs'] and not store['limits']


def test_replay_returns_original_receipt_before_current_resource_checks(env):
    from multimall_fakes import confirmed,submit
    client,store=env;value=confirmed(client);batch=str(uuid.uuid4())
    first=submit(client,value,batch);assert first.status_code==202
    store['source']['project']=None;store['facts']['cafe24']['status']='preparing';store['limits']['register']=10
    reads=store['reads'];replay=submit(client,value,batch)
    assert replay.status_code==200 and replay.json()==first.json()
    assert store['reads']==reads and store['wakes']==1
    conflict=submit(client,{**value,'productName':'다른 이름'},batch)
    assert conflict.status_code==409 and conflict.json()['error']['code']=='idempotency_key_reused'


@pytest.mark.parametrize('case', ['no_ready','quota'])
def test_no_ready_or_quota_failure_creates_no_partial_receipt(env,case):
    from multimall_fakes import confirmed,submit
    client,store=env;value=confirmed(client)
    if case=='no_ready':value['confirmedSourcesByMall']={}
    else:store['limits']['register']=10
    r=submit(client,value)
    assert r.status_code==(409 if case=='no_ready' else 429),r.text
    assert not store['jobs'] and store['wakes']==0


@pytest.mark.parametrize('case', ['project','publication','asset','job','kind','mall'])
def test_registration_routes_enforce_project_publication_asset_and_job_ownership(env,case):
    from multimall_fakes import confirmed,submit
    client,store=env;value=confirmed(client)
    if case in ('project','publication','asset'):
        key={'project':'projectId','publication':'publicationId','asset':'mainAssetId'}[case]
        value[key]=str(uuid.uuid4());r=submit(client,value)
    else:
        from test_mall_connect import OTHER
        accepted=submit(client,value).json();jid=accepted['items'][0]['jobId'];job=store['jobs'][jid]
        if case=='job':job['user_id']=OTHER
        elif case=='kind':job['kind']='export'
        else:job['payload']['mall']='smartstore'
        r=client.get('/v1/malls/registrations/'+jid)
    assert r.status_code==404,r.text


def test_verify_and_resume_never_recreate_unknown_product(env,monkeypatch):
    from app import mall_registration
    from multimall_fakes import submit
    client,store=env;accepted=submit(client).json();jid=accepted['items'][0]['jobId'];job=store['jobs'][jid]
    job.update(status='done',result={'outcome':'unknown'});job['metadata']['create']={'sentAt':'now'}
    calls=[]
    class ReadOnlyClient:
        def __init__(self,*a,**kw):pass
        async def find_products_by_custom_code(self,token,code):calls.append(('read',code));return []
    async def token(app,*,user_id,connection_id,fn):return await fn('secret-token')
    monkeypatch.setattr(mall_registration,'Cafe24Client',ReadOnlyClient)
    monkeypatch.setattr(mall_registration,'call_with_token',token)
    r=client.post(f'/v1/malls/registrations/{jid}/verify')
    assert r.status_code==200 and r.json()['outcome']=='unknown'
    assert client.post(f'/v1/malls/registrations/{jid}/resume',json={'requestKey':str(uuid.uuid4())}).status_code==409
    job=store['jobs'][jid]
    job['metadata']['productId']='42'
    job['metadata']['checkpoints']={'uploading_additional_images':{'requests':[{'sentAt':'now','status':'unknown'}]}}
    assert client.get(f'/v1/malls/registrations/{jid}').json()['retryAction']=='open_admin'
    assert client.post(f'/v1/malls/registrations/{jid}/resume',json={'requestKey':str(uuid.uuid4())}).status_code==409
    job=store['jobs'][jid]
    job['metadata']['checkpoints']={}
    r=client.post(f'/v1/malls/registrations/{jid}/resume',json={'requestKey':str(uuid.uuid4())})
    assert r.status_code==200 and r.json()['outcome'] is None and len(calls)==1
    assert len(store['jobs'])==1 and store['limits']['register']==1


def test_registration_responses_and_events_redact_secrets(env,monkeypatch,caplog):
    from app import mall_registration
    from multimall_fakes import submit
    client,store=env;accepted=submit(client);jid=accepted.json()['items'][0]['jobId'];job=store['jobs'][jid]
    job.update(status='done',result={'outcome':'unknown'});job['metadata']['create']={'sentAt':'now'}
    job['metadata']['private']='Authorization Bearer secret-token'
    class FailingClient:
        def __init__(self,*a,**kw):pass
        async def find_products_by_custom_code(self,*a):raise RuntimeError('Authorization Bearer secret-token upstream-body')
    async def token(app,*,user_id,connection_id,fn):return await fn('secret-token')
    monkeypatch.setattr(mall_registration,'Cafe24Client',FailingClient)
    monkeypatch.setattr(mall_registration,'call_with_token',token)
    responses=[accepted,client.get(f'/v1/malls/registrations/{jid}'),client.get('/v1/malls/registration-batches/'+job['payload']['batchId']),
        client.post(f'/v1/malls/registrations/{jid}/verify'),client.post('/v1/malls/registration-batches',json={'batchId':'bad','draft':{'Authorization':'secret-token'}})]
    assert responses[-2].status_code==409 and responses[-1].status_code==422
    for r in responses:
        assert r.headers['cache-control']=='no-store'
        assert not any(s in r.text for s in ('secret-token','Authorization','upstream-body','private'))
    assert not any(s in caplog.text for s in ('secret-token','upstream-body'))


def test_catalog_reads_every_page_caches_only_complete_facts_and_preserves_parents(env,monkeypatch):
    import asyncio
    import httpx
    from app.services import mall_catalog
    from app.services.cafe24_client import Cafe24Client
    client,store=env;calls=[];cutoff=[False]
    def respond(request):
        path=request.url.path
        assert request.method=='GET'
        if path.endswith('/categories'):
            offset=int(request.url.params['offset']);calls.append(offset)
            rows=[dict(category_no=offset+i+1,category_name=f'분류{offset+i}',parent_category_no=1 if offset else None,
                       category_depth=2,use_display='F' if i==1 else 'T') for i in range(100 if cutoff[0] or offset<300 else 2)]
            return httpx.Response(200,json={'categories':rows})
        if path.endswith('/products/setting'):return httpx.Response(200,json={'products_setting':{'calculate_price_based_on':'B'}})
        return httpx.Response(200,json={'images_setting':{'detail_image_width':800,'detail_image_height':1000}})
    async def token(app,*,user_id,connection_id,fn):return await fn('fixture-token')
    monkeypatch.setattr(mall_catalog,'Cafe24Client',lambda *a,**kw:Cafe24Client(*a,**kw,transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(mall_catalog,'call_with_token',token)
    async def scenario():
        app=client.app
        first=await mall_catalog.load_mall_facts(app,user_id='owner',mall='cafe24')
        assert calls==[0,100,200,300] and first['complete'] and first['priceBasis']=='B'
        assert any(c['parentCategoryId']=='1' for c in first['categories']) and len(first['categories'])==298
        cached=await mall_catalog.load_mall_facts(app,user_id='owner',mall='cafe24');assert cached==first and len(calls)==4
        store['facts']['cafe24']['connectionId']=str(uuid.uuid4())
        await mall_catalog.load_mall_facts(app,user_id='owner',mall='cafe24');assert len(calls)==8
        mall_catalog.invalidate_catalog(app,'owner','cafe24');cutoff[0]=True
        incomplete=await mall_catalog.load_mall_facts(app,user_id='owner',mall='cafe24')
        assert not incomplete['complete'] and len(calls)==58 and not app.state.mall_catalog_cache
    asyncio.run(scenario())


def test_price_basis_b_requires_explicit_tax_excluded_confirmation():
    c=contract();d=c.normalize_register_draft(draft())
    with pytest.raises(ValueError):c.freeze_registration(d,source=source(),facts=facts(priceBasis='B'))
    d['priceOverrides']={'cafe24':{'amount':39000,'basis':'tax_excluded'}}
    assert c.freeze_registration(d,source=source(),facts=facts(priceBasis='B'))['price']==d['priceOverrides']['cafe24']
    with pytest.raises(ValueError):c.freeze_registration(d,source=source(),facts=facts(priceBasis='S'))


def test_notice_free_is_not_a_real_measurement():
    c=contract();blocks=[{'infoType':'required_notice','info':{'fields':[{'key':'sizes','value':'FREE'}]}}]
    assert 'sizes' in c.resolve_notice(blocks,{}, {})['missing']


def test_snapshot_freezes_catalog_settings_and_unicode_product_name():
    c=contract();d=c.normalize_register_draft(draft(productName='  편안한 👩\u200d💻 니트  '))
    f=facts();s=c.freeze_registration(d,source=source(),facts=f)
    assert s['imageRatio']==f['imageRatio'] and s['catalogVersion']=='cat1' and s['priceSetting']=='S'
    f['imageRatio']['width']=99
    assert s['imageRatio']['width']==1 and d['productName']=='편안한 👩\u200d💻 니트'


def test_saved_editor_size_and_care_blocks_use_native_kind_contract():
    c=contract();s=source()
    # Exact persisted shape emitted by buildSizeTable and buildCare, without infoType.
    blocks=[{'kind':'size','info':{'rows':[
                {'label':'M','values':{'totalLength':64,'shoulderWidth':42,'chestWidth':52,'hipWidth':50,'waistWidth':0,'sleeveLength':None,'hemWidth':''}},
                {'label':'L','values':{'totalLength':66,'chestWidth':55}}],'unit':'cm'}},
            {'kind':'care','info':{'text':'중성세제로 손세탁. 케어라벨을 확인해주세요.'}}]
    s['project']['editor_blocks']=blocks
    d=c.normalize_register_draft(draft(sizes=['M','L']))
    snapshot=c.freeze_registration(d,source=s,facts=facts())
    assert [v['size'] for v in snapshot['variants']]==['M','L']
    expected='M: 총장 64, 어깨너비 42, 가슴단면 52, 힙단면 50, 허리단면 0 cm; L: 총장 66, 가슴단면 55 cm'
    assert snapshot['notice']['fields']['sizes']==expected
    assert c.freeze_registration(d,source=s,facts=facts('zigzag'))['notice']['fields']['sizes']==expected
    assert snapshot['notice']['fields']['care']=='중성세제로 손세탁. 케어라벨을 확인해주세요.'
    with pytest.raises(ValueError):c.freeze_registration(c.normalize_register_draft(draft()),source=s,facts=facts())


@pytest.mark.parametrize('labels',[['FREE',''],['M',' M '],['FREE',None]])
def test_saved_invalid_size_labels_return_per_mall_issues(labels):
    from app.services.mall_preflight import evaluate_preflight
    c=contract();s=source()
    s['project']['editor_blocks']=[{'kind':'size','info':{'rows':[{'label':label,'values':{}} for label in labels]}}]
    result=evaluate_preflight(c.normalize_register_draft(draft()),source=s,facts_by_mall={
        'cafe24':facts(),'zigzag':facts('zigzag',status='preparing')})
    assert result['readyMalls']==[]
    issue=result['items'][0]['issues'][0]
    assert issue['code']=='invalid_options' and issue['path']=='sizes' and issue['action']=='edit_options'


def test_category_recommendations_compare_parent_paths_and_do_not_guess_ties():
    from app.services.mall_catalog import recommend_categories
    categories=[{'categoryId':'10','name':'남성','parentCategoryId':None},
                {'categoryId':'11','name':'상의','parentCategoryId':'10'},
                {'categoryId':'12','name':'니트','parentCategoryId':'11'}]
    selected=recommend_categories({'customCategory':'남성 상의'},categories)
    assert {s['categoryId'] for s in selected}=={'11','12'}
    assert all(not s['suggested'] for s in selected)
    exact=recommend_categories({'subCategory':'knit'},categories)
    assert len(exact)==1 and exact[0]['categoryId']=='12' and exact[0]['suggested']
