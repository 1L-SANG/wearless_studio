"""HTTP test data boundary; PostgreSQL suites exercise the real repository."""
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient

from app import repo
from app.auth import require_user
from app.main import create_app
from app.services.mall_catalog import REQUIRED_SCOPES
from conftest import make_settings
from test_mall_connect import USER, OTHER
from test_multimall_routes import PROJECT, PUBLICATION, ASSET, draft, source, facts


class Cursor:
    def __init__(self, store): self.store = store; self.row = None
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def execute(self, query, params=()):
        self.store['sql'].append((query, params)); self.row = None
        if 'from jobs' in query and 'kind = ' in query and 'where id = ' in query:
            jid, user, *rest = params
            job = self.store['jobs'].get(jid)
            if job and job['user_id'] == user and job['kind'] == 'mall_register' and job['payload'].get('mall') in ('cafe24', 'zigzag', 'ably'):
                self.row = copy.deepcopy(job)
        elif 'client_event_rate_limits' in query:
            key = params[0]
            if query.strip().startswith('select'):
                self.row = {'count': self.store['limits'].get(key, 0)}
            else: self.store['limits'][key] = self.store['limits'].get(key, 0) + 1
    async def fetchone(self): return self.row


class Pool:
    def __init__(self, store): self.store = store
    @asynccontextmanager
    async def connection(self):
        before = copy.deepcopy(self.store)
        conn = SimpleNamespace(cursor=lambda: Cursor(self.store))
        async def commit(): self.store['commits'] += 1
        conn.commit = commit
        try: yield conn
        except BaseException:
            self.store.clear(); self.store.update(before); raise


@pytest.fixture
def registration_env(monkeypatch):
    from app import mall_registration as route
    store = dict(source=source(), facts={'cafe24': facts(), 'zigzag': facts('zigzag', status='preparing')},
                 jobs={}, limits={}, sql=[], commits=0, wakes=0, closed=False, real=False, reads=0)
    settings = make_settings(multimall_register_enabled=True, mall_connect_enabled=True, detail_publish_enabled=True,
        cafe24_enabled=True, cafe24_client_id='client', cafe24_client_secret='private', mall_keys_kek='kek')
    app = create_app(settings); app.state.pool = Pool(store); app.dependency_overrides[require_user] = lambda: USER
    async def closed(conn, user): return store['closed']
    async def load(conn, *, user_id, project_id, publication_id, asset_ids, lock=False):
        store['reads'] += 1
        s = copy.deepcopy(store['source'])
        if user_id != USER or project_id != PROJECT or (s.get('project') or {}).get('deleted_at') or (s.get('project') or {}).get('user_id', USER) != user_id: s['project'] = None
        if publication_id != PUBLICATION or (s.get('publication') or {}).get('user_id') != str(uuid.UUID(int=1)) or (s.get('publication') or {}).get('project_id') != project_id: s['publication'] = None
        s['assets'] = [a for a in s['assets'] if a['id'] in asset_ids and a.get('user_id', USER)==user_id and a.get('project_id', PROJECT)==project_id and not a.get('deleted_at')]
        return s
    async def get_facts(app, *, user_id, mall): return copy.deepcopy(store['facts'][mall])
    async def defaults(conn, *, user_id, mall): return copy.deepcopy(store['source'].get('mallSpecificDefaults', {}).get(mall))
    async def connection(conn, *, user_id, mall):
        f = store['facts'][mall]
        if not f.get('connectionId'): return None
        from datetime import timedelta
        return dict(id=f['connectionId'], user_id=user_id, mall=mall, shop_id=f['shopId'],
            refresh_expires_at=f.get('refresh_expires_at', datetime.now(timezone.utc) + timedelta(days=1)), scopes=f.get('scopes', list(REQUIRED_SCOPES)))
    async def recover(conn, *, user_id, project_id, mall, shop_id):
        result=[]
        for j in store['jobs'].values():
            m,r=j.get('metadata') or {},j.get('result') or {}
            unresolved = (m.get('productId') and r.get('setupStatus') != 'complete') or (not m.get('productId') and not m.get('rejectedAt') and ((m.get('create') or {}).get('sentAt') or r.get('outcome') in ('unknown','duplicate_suspected')))
            if j['user_id']==user_id and j['project_id']==project_id and j['kind']=='mall_register' and j['payload']['mall']==mall and j['payload']['shopId']==shop_id and (j['status'] in ('pending','running') or unresolved):
                result.append(copy.deepcopy(j))
        return sorted(result,key=lambda j:j.get('created_at', ''),reverse=True)
    async def project(conn,user,pid):
        s=store['source'].get('project')
        return copy.deepcopy(s) if s and user==USER and pid==PROJECT and s.get('user_id',USER)==user and not s.get('deleted_at') else None
    async def listing(conn, *, user_id,project_id,kind,limit):
        return sorted([copy.deepcopy(j) for j in store['jobs'].values() if j['user_id']==user_id and j['project_id']==project_id and j['kind']==kind],key=lambda j:j.get('created_at',''),reverse=True)[:limit]
    async def requeue(conn, *, user_id,job_id,request_key):
        job=store['jobs'][job_id]
        m=job['metadata']
        if request_key in m.get('resumeRequestKeys',[]):return copy.deepcopy(job),False
        m.setdefault('resumeRequestKeys',[]).append(request_key)
        job.update(status='pending',error_message=None,locked_by=None,locked_at=None,finished_at=None)
        return copy.deepcopy(job),True
    async def batches(conn, *, user_id, batch_id):
        jobs = [copy.deepcopy(j) for j in store['jobs'].values() if j['user_id'] == user_id and j['kind'] == 'mall_register'
                and j['payload']['batchId'] == batch_id]
        if jobs: repo._mall_batch_receipt(jobs)
        return jobs
    async def enqueue(conn, *, user_id, project_id, batch_id, request_hash, payloads, blocked):
        if not payloads:
            exc=repo.CreditError('no_ready_malls', '없어요', 409);exc.items=blocked;raise exc
        if store['limits'].get('register', 0)+len(payloads)>10: raise repo.CreditError('rate_limited', '한도', 429)
        items=copy.deepcopy(blocked); new=[]
        for mall,payload in payloads.items():
            jid=str(uuid.uuid4());new.append(jid)
            store['jobs'][jid]=dict(id=jid,user_id=user_id,project_id=project_id,kind='mall_register',status='pending',
                progress=0,payload=copy.deepcopy(payload),metadata={'externalCode':'wl'+uuid.UUID(jid).hex[:16]},result=None)
            items.append(dict(mall=mall,status='accepted',jobId=jid))
        receipt=dict(batchId=batch_id,requestHash=request_hash,acceptedAt=datetime.now(timezone.utc).isoformat(),
            selectedMalls=sorted(i['mall'] for i in items),items=sorted(items,key=lambda i:i['mall']))
        for jid in new: store['jobs'][jid]['metadata']['batchReceipt']=copy.deepcopy(receipt)
        store['limits']['register']=store['limits'].get('register',0)+len(new)
        return receipt
    async def real(*a,**kw): return store['real']
    async def patch(conn, *, job_id, patch, user_id, expect_status):
        j=store['jobs'][job_id]
        if j['user_id']!=user_id or j['status'] not in expect_status:return False
        column='result' if patch.get('outcome') else 'metadata'
        j[column]={**(j.get(column) or {}),**copy.deepcopy(patch)};return True
    def wake(request): store['wakes']+=1
    for name,fn in dict(user_account_purge_closed=closed, load_registration_source=load,
        get_mall_defaults=defaults,get_mall_connection=connection,find_mall_registration_recoveries=recover,
        get_mall_batch_jobs=batches,enqueue_mall_registration_batch=enqueue,project_uses_real_model=real,
        get_project=project,list_project_jobs_by_kind=listing,requeue_mall_registration=requeue,
        set_job_metadata_patch=patch,set_job_result_patch=patch).items(): monkeypatch.setattr(repo,name,fn)
    monkeypatch.setattr(route,'load_mall_facts',get_facts);monkeypatch.setattr(route,'_wake_dispatcher',wake)
    return TestClient(app),store


def confirmed(client, **changes):
    value=draft(**changes)
    response=client.post('/v1/malls/registrations/preflight',json=value)
    assert response.status_code==200,response.text
    value['confirmedSourcesByMall']={i['mall']:i['sourceVersions'] for i in response.json()['items'] if i['status']=='ready'}
    return value


def submit(client, value=None, batch=None):
    return client.post('/v1/malls/registration-batches',json=dict(batchId=batch or str(uuid.uuid4()),draft=value or confirmed(client)))
