"""Name-origin persistence without a new column, tested against the real repository."""
import asyncio
from copy import deepcopy
import re
from unittest.mock import AsyncMock

import pytest
from app import repo
from app.models import Product,ProductPatch
from conftest import assert_query_binds
from test_analysis_server_owned_fields import _save


class Cursor:
    def __init__(self,conn):self.conn=conn;self.result=None
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
    async def execute(self,sql,params=None):
        assert_query_binds(sql,params);self.conn.calls.append((sql,params));self.result=None
        if sql.startswith('select') and 'from products' in sql:self.result=deepcopy(self.conn.product)
        elif sql.startswith('select payload from analyses'):self.result={'payload':deepcopy(self.conn.analysis)}
        elif sql.startswith('insert into products'):pass
        elif sql.startswith('update products set'):
            columns=re.findall(r'(\w+) = %s',sql.split(' where ')[0])
            for key,value in zip(columns,params):self.conn.product[key]=getattr(value,'obj',value)
            self.result=deepcopy(self.conn.product)
        elif sql.startswith('insert into analyses'):
            self.conn.analysis.update(params[1].obj)
            self.result={'payload':deepcopy(self.conn.analysis)}
        elif sql.startswith('update projects'):pass
        else:raise AssertionError(sql)
    async def fetchone(self):return self.result


class Connection:
    def __init__(self,name='구김 없는 셔츠',origin=None):
        self.product={'id':'product','project_id':'p1','name':name,'colors':[],'measurements':[]}
        self.analysis={'productNameOrigin':origin} if origin else {}
        self.calls=[]
    def cursor(self):return Cursor(self)


def test_product_contract_accepts_source_and_rejects_unknown_values():
    assert ProductPatch(name='이름',nameSource='ai').model_dump(exclude_unset=True)=={'name':'이름','name_source':'ai'}
    with pytest.raises(ValueError):ProductPatch(nameSource='suggested')
    with pytest.raises(ValueError):ProductPatch(nameSource=None)
    assert Product(id='p',projectId='p1',name='이름').model_dump(by_alias=True)['nameSource']=='unknown'


def test_auto_name_source_survives_read_and_unchanged_legacy_save_then_explicit_confirmation():
    async def check():
        conn=Connection()
        saved=await repo.save_product(conn,'p1','owner',{'name':'구김 없는 셔츠','name_source':'ai'})
        assert saved['name_source']=='ai'
        assert conn.analysis['productNameOrigin']=={'name':'구김 없는 셔츠','source':'ai'}
        assert (await repo.get_product(conn,'p1'))['name_source']=='ai'
        assert (await repo.save_product(conn,'p1','owner',{'name':'구김 없는 셔츠'}))['name_source']=='ai'
        confirmed=await repo.save_product(conn,'p1','owner',{'name':'구김 없는 셔츠','name_source':'seller'})
        assert confirmed['name_source']=='seller'
        assert (await repo.get_product(conn,'p1'))['name_source']=='seller'
    asyncio.run(check())


@pytest.mark.parametrize('origin',[None,{'name':'다른 이름','source':'seller'}])
def test_unmarked_or_stale_name_never_gains_seller_authority(origin):
    conn=Connection(origin=origin)
    assert asyncio.run(repo.get_product(conn,'p1'))['name_source']=='unknown'
    assert asyncio.run(repo.save_product(conn,'p1','owner',{'name':'새로 바뀐 이름'}))['name_source']=='unknown'


@pytest.mark.parametrize('merge',[False,True])
def test_analysis_updates_cannot_forge_or_erase_name_provenance(merge):
    origin={'name':'자동 이름','source':'ai'}
    for incoming in [{}, {'productNameOrigin':None}, {'productNameOrigin':{'name':'자동 이름','source':'seller'}}]:
        assert _save({'productNameOrigin':origin},incoming,merge=merge,delete_nulls=merge)['productNameOrigin']==origin


def test_detail_finalize_persists_generated_name_as_ai(monkeypatch):
    class FinalCursor(Cursor):
        async def execute(self,sql,params=None):
            if sql.startswith('select id from jobs'):self.result={'id':'job'};return
            if sql.startswith('update products') or sql.startswith('update projects') or sql.startswith('insert into analyses') or 'from products' in sql or sql.startswith('select payload from analyses'):
                return await super().execute(sql,params)
            self.result=None
    class FinalConnection(Connection):
        def cursor(self):return FinalCursor(self)
    monkeypatch.setattr(repo,'_consume_buckets',AsyncMock(return_value=1))
    conn=FinalConnection(name='')
    asyncio.run(repo.finalize_detail_page_success(conn,job_id='job',lease_token='lease',user_id='owner',project_id='p1',
        editor_blocks=[],cut_assets=[],reserved=0,charge=0,metadata={},product_name='구김 없는 셔츠'))
    assert conn.analysis['productNameOrigin']=={'name':'구김 없는 셔츠','source':'ai'}
    assert asyncio.run(repo.get_product(conn,'p1'))['name_source']=='ai'


def test_product_http_roundtrip_preserves_auto_origin_and_explicit_same_name_confirmation(client,make_token,monkeypatch):
    from contextlib import asynccontextmanager
    from app import routes
    from conftest import auth_headers
    conn=Connection()
    async def commit():pass
    conn.commit=commit
    @asynccontextmanager
    async def connection(_request):yield conn
    monkeypatch.setattr(routes,'get_conn',connection)
    monkeypatch.setattr(repo,'get_project',AsyncMock(return_value={'id':'p1'}))
    monkeypatch.setattr(routes,'_enqueue_canonical_cutout',AsyncMock(return_value=False))
    headers=auth_headers(make_token)
    saved=client.patch('/v1/projects/p1/product',headers=headers,json={'name':'구김 없는 셔츠','nameSource':'ai'})
    assert saved.status_code==200,saved.text
    assert saved.json()['nameSource']=='ai'
    assert client.get('/v1/projects/p1/product',headers=headers).json()['nameSource']=='ai'
    confirmed=client.patch('/v1/projects/p1/product',headers=headers,json={'nameSource':'seller'})
    assert confirmed.status_code==200,confirmed.text
    assert confirmed.json()['name']=='구김 없는 셔츠' and confirmed.json()['nameSource']=='seller'


def test_reanalysis_preserves_name_origin_instead_of_provider_forgery():
    origin={'name':'자동 이름','source':'ai'}
    class AnalysisCursor(Cursor):
        async def execute(self,sql,params=None):
            if sql.startswith('select id from jobs'):self.result={'id':'job'};return
            if sql.startswith('insert into analyses'):
                previous=deepcopy(self.conn.analysis)
                self.conn.analysis=deepcopy(params[1].obj)
                if "jsonb_build_object('productNameOrigin'" in sql and 'productNameOrigin' in previous:
                    self.conn.analysis['productNameOrigin']=previous['productNameOrigin']
                self.result={'payload':deepcopy(self.conn.analysis)};return
            self.result=None
    class AnalysisConnection(Connection):
        def cursor(self):return AnalysisCursor(self)
    conn=AnalysisConnection(name='자동 이름',origin=origin)
    asyncio.run(repo.finalize_analyze_success(conn,job_id='job',lease_token='lease',user_id='owner',project_id='p1',
        clothing_type=None,analysis_payload={'sellingPoints':['자수'],'productNameOrigin':{'name':'자동 이름','source':'seller'}},
        result={'data':{}},metadata={}))
    assert conn.analysis=={'sellingPoints':['자수'],'productNameOrigin':origin}


def test_generated_name_finalize_cannot_overwrite_concurrent_seller_confirmation(monkeypatch):
    origin={'name':'구김 없는 셔츠','source':'seller'}
    class RaceCursor(Cursor):
        async def execute(self,sql,params=None):
            if sql.startswith('select id from jobs'):self.result={'id':'job'};return
            if sql.startswith('update products') and 'returning name' in sql and self.conn.product['name'] not in ('','새 상품'):
                self.result=None;return
            if sql.startswith('update products') or sql.startswith('update projects') or sql.startswith('insert into analyses'):
                return await super().execute(sql,params)
            self.result=None
    class RaceConnection(Connection):
        def cursor(self):return RaceCursor(self)
    conn=RaceConnection(origin=origin)
    asyncio.run(repo.finalize_detail_page_success(conn,job_id='job',lease_token='lease',user_id='owner',project_id='p1',
        editor_blocks=[],cut_assets=[],reserved=0,charge=0,metadata={},product_name='구김 없는 셔츠'))
    assert conn.analysis['productNameOrigin']==origin
    assert conn.product['name']=='구김 없는 셔츠'
