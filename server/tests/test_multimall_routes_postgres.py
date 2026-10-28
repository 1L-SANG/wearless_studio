"""Real admission transactions against an explicitly supplied disposable local DB."""
import asyncio
import copy
import uuid

import httpx
from psycopg.types.json import Json
import pytest

from app import mall_registration, repo
from app.services.mall_catalog import REQUIRED_SCOPES, connection_version
from test_mall_connect import USER, OTHER
from test_mall_connect_postgres import mall_pg
from test_multimall_storage_postgres import storage_pg, PROJECT, PUBLICATION, ASSET, stop_pending


@pytest.fixture
def registration_pg(storage_pg,monkeypatch):
    admin,pool,_roles=storage_pg
    from app.auth import require_user
    from app.main import create_app
    from conftest import make_settings
    app=create_app(make_settings(multimall_register_enabled=True,mall_connect_enabled=True,detail_publish_enabled=True,
        cafe24_enabled=True,cafe24_client_id='fixture',cafe24_client_secret='private',mall_keys_kek='kek'))
    app.state.pool=pool;app.dependency_overrides[require_user]=lambda:USER
    admin.execute("alter table products add column colors jsonb not null default '[]'")
    admin.execute("update products set colors = '[{\"id\":\"black\",\"name\":\"검정\"}]'")
    admin.execute('create table fm_output_records (job_id uuid, seller_id uuid)')
    admin.execute("update assets set mime_type='image/png', r2_key='owned/main.png', width=800,height=1000")
    admin.execute('update detail_publications set files=%s where id=%s',
        (Json([dict(index=1,width=860,height=100,bytes=100)]),PUBLICATION))
    row=admin.execute("insert into mall_connections (user_id,mall,shop_id,refresh_expires_at,scopes) values (%s,'cafe24','myshop',now()+interval '10 days',%s) returning id::text",
        (USER,list(REQUIRED_SCOPES))).fetchone()
    version=connection_version(row)
    facts=dict(mall='cafe24',status='available',connectionId=row['id'],connectionVersion=version,shopId='myshop',
        catalogVersion='catalog1',complete=True,categories=[],priceBasis='S',imageRatio={'width':1,'height':1},capabilities={})
    async def load(app,*,user_id,mall):
        return copy.deepcopy(facts if mall=='cafe24' else {'mall':mall,'status':'preparing'})
    monkeypatch.setattr(mall_registration,'load_mall_facts',load)
    value=dict(projectId=PROJECT,publicationId=PUBLICATION,editorRevision=3,selectedMalls=['cafe24','zigzag'],productName='니트',
        basePrice=39000,defaultQuantity=0,sizes=['FREE'],mainAssetId=ASSET,additionalAssetIds=[],variantOverrides=[],categories={})
    return app,admin,pool,value


async def confirm(client,value):
    value=copy.deepcopy(value)
    response=await client.post('/v1/malls/registrations/preflight',json=value)
    assert response.status_code==200,response.text
    value['confirmedSourcesByMall']={i['mall']:i['sourceVersions'] for i in response.json()['items'] if i['status']=='ready'}
    assert set(value['confirmedSourcesByMall'])=={'cafe24'},response.text
    return value


async def submit(client,value,batch=None):
    return await client.post('/v1/malls/registration-batches',json={'batchId':batch or str(uuid.uuid4()),'draft':value})


def test_editor_save_and_admission_revision_race(registration_pg):
    app,admin,pool,value=registration_pg
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            value_confirmed=await confirm(client,value)
            async with pool.connection() as editor:
                await editor.execute('update projects set editor_revision=4 where id=%s',(PROJECT,))
                pending=asyncio.create_task(submit(client,value_confirmed))
                try:
                    async def waiting():
                        while not admin.execute("select exists(select 1 from pg_stat_activity where application_name=%s and cardinality(pg_blocking_pids(pid))>0) as waiting",(pool.schema,)).fetchone()['waiting']:
                            if pending.done():pytest.fail('admission did not wait for editor revision row lock')
                            await asyncio.sleep(.01)
                    await asyncio.wait_for(waiting(),5)
                    await editor.commit()
                    response=await asyncio.wait_for(pending,5)
                    assert response.status_code==409,response.text
                    assert response.json()['error']['items'][0]['issues'][0]['code']=='editor_revision_changed'
                finally:await stop_pending(pending)
            assert admin.execute('select count(*) as n from jobs').fetchone()['n']==0
            assert admin.execute('select count(*) as n from client_event_rate_limits').fetchone()['n']==0
    asyncio.run(scenario())


@pytest.mark.parametrize('same_key,used_slots',[(False,0),(True,0),(True,9)])
def test_competing_route_admissions_replay_or_block_without_extra_quota(registration_pg,monkeypatch,same_key,used_slots):
    app,admin,pool,value=registration_pg
    if used_slots:admin.execute("insert into client_event_rate_limits values ('user',%s,clock_timestamp(),%s)",(f'mall-register:{USER}',used_slots))
    async def scenario():
        inserted,release=asyncio.Event(),asyncio.Event();original=repo.create_job
        async def hold(conn,**kwargs):
            row,created=await original(conn,**kwargs)
            if created:inserted.set();await release.wait()
            return row,created
        monkeypatch.setattr(repo,'create_job',hold)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            confirmed=await confirm(client,value);batch=str(uuid.uuid4())
            first=asyncio.create_task(submit(client,confirmed,batch));second=None
            try:
                await asyncio.wait_for(inserted.wait(),5)
                second=asyncio.create_task(submit(client,confirmed,batch if same_key else None))
                async def waiting():
                    while not admin.execute("select exists(select 1 from pg_stat_activity where application_name=%s and cardinality(pg_blocking_pids(pid))>0) as waiting",(pool.schema,)).fetchone()['waiting']:
                        if second.done():pytest.fail('second admission did not serialize')
                        await asyncio.sleep(.01)
                await asyncio.wait_for(waiting(),5);release.set()
                a,b=await asyncio.wait_for(asyncio.gather(first,second),5)
                assert a.status_code==202,a.text
                assert b.status_code==(200 if same_key else 409),b.text
                if same_key:assert a.json()==b.json()
                else:assert b.json()['error']['items'][0]['existingJobId']==a.json()['items'][0]['jobId']
            finally:
                release.set()
                for task in (first,second):
                    if task:await stop_pending(task)
        assert admin.execute('select count(*) as n from jobs').fetchone()['n']==1
        assert admin.execute('select sum(request_count) as n from client_event_rate_limits').fetchone()['n']==used_slots+1
    asyncio.run(scenario())


def test_route_quota_failure_rolls_back_jobs_and_receipts(registration_pg):
    app,admin,pool,value=registration_pg
    admin.execute("insert into client_event_rate_limits values ('user',%s,clock_timestamp(),10)",(f'mall-register:{USER}',))
    class Dispatcher:
        def wake(self):pytest.fail('rejected batch woke dispatcher')
    app.state.dispatcher=Dispatcher()
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await submit(client,await confirm(client,value))
            assert response.status_code==429,response.text
        assert admin.execute('select count(*) as n from jobs').fetchone()['n']==0
        assert admin.execute('select sum(request_count) as n from client_event_rate_limits').fetchone()['n']==10
    asyncio.run(scenario())


@pytest.mark.parametrize('resource',['project','publication','asset','job','kind','mall'])
def test_registration_sql_ownership_boundaries(registration_pg,resource):
    app,admin,pool,value=registration_pg
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            confirmed=await confirm(client,value)
            if resource in ('project','publication','asset'):
                table={'project':'projects','publication':'detail_publications','asset':'assets'}[resource]
                admin.execute(f'update {table} set user_id=%s',(OTHER,))
                response=await submit(client,confirmed)
            else:
                response=await submit(client,confirmed);assert response.status_code==202,response.text
                jid=response.json()['items'][0]['jobId']
                if resource=='job':admin.execute('update jobs set user_id=%s',(OTHER,))
                elif resource=='kind':admin.execute("update jobs set kind='export'")
                else:
                    # A valid mall with no registration contract must still be inaccessible.
                    admin.execute("update jobs set kind='export', payload=jsonb_set(payload,'{mall}','\"smartstore\"')")
                response=await client.get('/v1/malls/registrations/'+jid)
            assert response.status_code==404,response.text
    asyncio.run(scenario())
