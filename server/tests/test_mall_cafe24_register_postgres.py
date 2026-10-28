"""Cafe24 history recovery through the single multimall storage contract."""
import asyncio
import uuid

import httpx
from psycopg.types.json import Json
import pytest

from app import repo
from test_mall_connect import USER, OTHER
from test_mall_connect_postgres import mall_pg
from test_multimall_storage_postgres import storage_pg, PROJECT, PUBLICATION
from test_multimall_routes_postgres import registration_pg


@pytest.mark.parametrize('status,metadata,result,recover',[
    ('pending',{'productId':'42'},None,True),('running',{},None,True),
    ('done',{'create':{'sentAt':'now'}},None,True),('cancelled',{'create':{'sentAt':'now'}},None,True),
    ('error',{'create':{'sentAt':'now'},'productId':None,'rejectedAt':None},None,True),
    ('error',{'create':{'sentAt':'now'},'productId':'','rejectedAt':''},None,True),
    ('error',{},None,False),('error',{'create':{'sentAt':None}},None,False),('error',{'create':{'sentAt':''}},None,False),
    ('error',{'productId':'42'},{'setupStatus':'needs_attention'},True),
    ('done',{'productId':'42'},{'setupStatus':'complete'},False),
    ('error',{'create':{'sentAt':'now'},'rejectedAt':'later'},None,False),
    ('done',{}, {'outcome':'duplicate_suspected'},True),
])
def test_recovery_repository_matches_metadata_outcome_precedence(registration_pg,status,metadata,result,recover):
    _,admin,pool,_=registration_pg
    admin.execute("insert into jobs(user_id,project_id,kind,status,payload,metadata,result) values(%s,%s,'mall_register',%s,%s,%s,%s)",
        (USER,PROJECT,status,Json({'mall':'cafe24','shopId':'myshop'}),Json(metadata),Json(result)))
    async def scenario():
        async with pool.connection() as conn:
            rows=await repo.find_mall_registration_recoveries(conn,user_id=USER,project_id=PROJECT,mall='cafe24',shop_id='myshop')
            assert bool(rows) is recover
    asyncio.run(scenario())


def test_recovery_searches_all_owned_current_store_history_with_outcome_priority(registration_pg):
    app,admin,pool,_=registration_pg
    payload={'mall':'cafe24','shopId':'myshop','publicationId':PUBLICATION};original=str(uuid.uuid4());other_store=str(uuid.uuid4())
    admin.execute("insert into jobs(id,user_id,project_id,kind,status,payload,metadata,created_at) values(%s,%s,%s,'mall_register','error',%s,'{\"create\":{\"sentAt\":\"now\"}}',now()-interval '1 day')",(original,USER,PROJECT,Json(payload)))
    for i in range(6):
        admin.execute("insert into jobs(user_id,project_id,kind,status,payload,metadata,result) values(%s,%s,'mall_register','done',%s,%s,'{\"setupStatus\":\"complete\"}')",(USER,PROJECT,Json(payload),Json({'productId':str(i+1)})))
    for user,project,kind,shop,jid in [(OTHER,PROJECT,'mall_register','myshop',str(uuid.uuid4())),
        (USER,str(uuid.uuid4()),'mall_register','myshop',str(uuid.uuid4())),(USER,PROJECT,'export','myshop',str(uuid.uuid4())),
        (USER,PROJECT,'mall_register','another-store',other_store)]:
        admin.execute("insert into jobs(id,user_id,project_id,kind,status,payload,metadata) values(%s,%s,%s,%s,'error',%s,'{\"create\":{\"sentAt\":\"now\"}}')",(jid,user,project,kind,Json({**payload,'shopId':shop})))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            data=(await client.get(f'/v1/projects/{PROJECT}/mall-registrations')).json()
            assert len(data['items'])==5 and original not in {r['jobId'] for r in data['items']}
            assert [r['jobId'] for r in data['recoveries']['cafe24']]==[original]
            assert data['recoveries']['zigzag']==[]
            admin.execute("update mall_connections set shop_id='another-store',refresh_expires_at=now()")
            data=(await client.get(f'/v1/projects/{PROJECT}/mall-registrations')).json()
            assert [r['jobId'] for r in data['recoveries']['cafe24']]==[other_store]
            admin.execute("update mall_connections set status='revoked'")
            assert (await client.get(f'/v1/projects/{PROJECT}/mall-registrations')).json()['recoveries']['cafe24']==[]
    asyncio.run(scenario())
