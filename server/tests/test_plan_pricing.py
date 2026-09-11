"""2026-09-11 플랜별 과금표, 예약, 견적 계약."""
import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from app import repo, routes


@pytest.mark.parametrize('plan,fee,adjusts', [
    ('free', 19, 1), ('starter', 19, 1), ('seller', 10, 1), ('pro', 0, 2),
])
@pytest.mark.parametrize('model_id,is_extension', [
    ('mA', False), ('mB', False),
    *((f'm{letter}', True) for letter in 'CDEFGHIJKLMN'),
    ('b89d4972-54fa-413f-89d8-b1c123ec1424', False), (None, False), ('unknown', False),
])
def test_plan_table(plan, fee, adjusts, model_id, is_extension):
    from app import plan_pricing
    assert plan_pricing.extension_model_fee(plan, model_id) == (fee if is_extension else 0)
    assert plan_pricing.free_mannequin_adjusts(plan) == adjusts


@pytest.mark.parametrize('raw,expected', [
    ('free', 'free'), ('starter', 'starter'), ('seller', 'seller'), ('pro', 'pro'),
    (None, 'free'), ('enterprise', 'free'), ('PRO', 'free'), ('', 'free'),
])
def test_plan_normalization(raw, expected):
    from app import plan_pricing
    assert plan_pricing.plan_of(raw) == expected


class _Conn:
    async def commit(self):
        pass


def _wire(monkeypatch, *, plan='starter', model='mE', done_count=0, balance=100,
          owned=True, created=True, cuts=None, already_paid=False):
    state = {'plan': plan, 'selected_model_id': model, 'done_count': done_count,
             'extension_fee_already_paid': already_paid}
    @contextlib.asynccontextmanager
    async def connection(_request):
        yield _Conn()
    monkeypatch.setattr(routes, 'get_conn', connection)
    monkeypatch.setattr(repo, 'get_project', AsyncMock(return_value={'id': 'p1'} if owned else None))
    monkeypatch.setattr(repo, 'get_mannequin_pricing_state', AsyncMock(return_value=state), raising=False)
    monkeypatch.setattr(repo, 'get_analysis', AsyncMock(return_value={'selectedModelId': model}))
    monkeypatch.setattr(repo, 'list_mannequin_cuts', AsyncMock(return_value=cuts or []))
    monkeypatch.setattr(repo, 'get_account', AsyncMock(return_value={'credits': balance, 'plan': plan}))
    monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value={
        'colors': [{'isBase': True, 'images': [{'slot': 'Front', 'id': 'a1'}]}],
    }))
    create = AsyncMock(return_value=({'id': 'j1'}, created))
    reserve = AsyncMock(side_effect=lambda _c, _u, cost: balance - cost if balance >= cost else None)
    record = AsyncMock()
    monkeypatch.setattr(repo, 'create_job', create)
    monkeypatch.setattr(repo, 'reserve_credits', reserve)
    monkeypatch.setattr(repo, 'record_free_mannequin_adjust', record, raising=False)
    monkeypatch.setattr(routes, '_enqueue_base_fidelity_observation', AsyncMock())
    return create, reserve, record


def _headers(make_token):
    return {'Authorization': f'Bearer {make_token()}'}


@pytest.mark.parametrize('plan,model,total', [
    ('starter', 'mE', 64), ('seller', 'mE', 55), ('pro', 'mE', 45),
    ('free', 'mE', 64), ('starter', 'mA', 45), ('starter', 'mB', 45),
    ('starter', 'b89d4972-54fa-413f-89d8-b1c123ec1424', 45), ('starter', None, 45),
])
def test_generate_reserves_plan_total(client, make_token, monkeypatch, plan, model, total):
    create, reserve, record = _wire(monkeypatch, plan=plan, model=model)
    response = client.post('/v1/projects/p1/mannequins:generate', headers=_headers(make_token))
    assert response.status_code == 202, response.text
    assert create.call_args.kwargs['credits_reserved'] == total
    assert create.call_args.kwargs['metadata'] == {
        'creditCostVersion': 'v6', 'extensionModelFee': total - 45,
        'plan': plan, 'selectedModelId': model,
    }
    assert reserve.call_args.args[-1] == total
    record.assert_not_awaited()


def test_generate_extended_starter_402_at_60(client, make_token, monkeypatch):
    _wire(monkeypatch, balance=60)
    response = client.post('/v1/projects/p1/mannequins:generate', headers=_headers(make_token))
    assert response.status_code == 402
    assert response.json()['error']['code'] == 'insufficient_credits'


def test_generate_complete_cache_never_reserves(client, make_token, monkeypatch):
    cut = {'candidate': 'A', 'version': 1, 'asset_id': 'a1', 'base_fit': 'regular',
           'fit_adjust': None, 'length_adjust': None, 'match_adjust': None}
    create, reserve, _ = _wire(monkeypatch, balance=0, cuts=[cut])
    response = client.post('/v1/projects/p1/mannequins:generate', headers=_headers(make_token))
    assert response.status_code == 200, response.text
    create.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.parametrize('plan,done_count,cost', [
    ('starter', 1, 0), ('starter', 2, 45), ('pro', 1, 0), ('pro', 2, 0), ('pro', 3, 45),
    ('free', 1, 0), ('seller', 1, 0), ('seller', 2, 45),
])
def test_regenerate_free_ladder(client, make_token, monkeypatch, plan, done_count, cost):
    create, reserve, record = _wire(monkeypatch, plan=plan, done_count=done_count, balance=cost)
    response = client.post('/v1/projects/p1/mannequins:regenerate', headers=_headers(make_token), json={})
    assert response.status_code == 202, response.text
    assert create.call_args.kwargs['credits_reserved'] == cost
    assert reserve.call_args.args[-1] == cost
    assert create.call_args.kwargs['metadata'] == {
        'creditCostVersion': 'v6', 'freeAdjust': cost == 0, 'adjustIndex': done_count, 'plan': plan,
    }
    if cost == 0:
        assert record.call_args.kwargs['metadata']['freeAdjust'] is True
        assert record.call_args.kwargs['job_id'] == 'j1'
    else:
        record.assert_not_awaited()


def test_regenerate_paid_402(client, make_token, monkeypatch):
    _, _, record = _wire(monkeypatch, done_count=2, balance=44)
    response = client.post('/v1/projects/p1/mannequins:regenerate', headers=_headers(make_token), json={})
    assert response.status_code == 402
    record.assert_not_awaited()


@pytest.mark.parametrize('mode', ['generate', 'regenerate'])
def test_existing_job_never_reserves_twice(client, make_token, monkeypatch, mode):
    _, reserve, record = _wire(monkeypatch, created=False)
    response = client.post(f'/v1/projects/p1/mannequins:{mode}', headers=_headers(make_token), json={})
    assert response.status_code == 202, response.text
    reserve.assert_not_awaited()
    record.assert_not_awaited()


@pytest.mark.parametrize('plan,fee,free_adjusts,done_count,next_cost', [
    ('starter', 19, 1, 1, 0), ('seller', 10, 1, 2, 45),
    ('pro', 0, 2, 2, 0), ('pro', 0, 2, 3, 45), ('unknown', 19, 1, 0, 0),
])
def test_credit_quote_shape(client, make_token, monkeypatch, plan, fee, free_adjusts, done_count, next_cost):
    _wire(monkeypatch, plan=plan, done_count=done_count)
    response = client.get('/v1/projects/p1/credit-quote', headers=_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json() == {
        'plan': plan if plan != 'unknown' else 'free',
        'mannequinGenerate': {'base': 45, 'extensionModelFee': fee, 'total': 45 + fee,
                              'selectedModelId': 'mE', 'extensionFeeAlreadyPaid': False},
        'mannequinRegenerate': {'freeAdjusts': free_adjusts, 'usedAdjusts': max(done_count - 1, 0),
                                'nextCost': next_cost},
        'storyboardPerCut': 19, 'editorImage': 19,
    }


@pytest.mark.parametrize('override,fee', [('mA', 0), ('mN', 10), ('', 0)])
def test_quote_selected_model_override(client, make_token, monkeypatch, override, fee):
    _wire(monkeypatch, plan='seller', model='mA')
    response = client.get('/v1/projects/p1/credit-quote', params={'selectedModelId': override}, headers=_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json()['mannequinGenerate']['selectedModelId'] == override
    assert response.json()['mannequinGenerate']['extensionModelFee'] == fee


def test_quote_owner_scope_and_auth(client, make_token, monkeypatch):
    _wire(monkeypatch, owned=False)
    assert client.get('/v1/projects/p1/credit-quote', headers=_headers(make_token)).status_code == 404
    assert client.get('/v1/projects/p1/credit-quote').status_code == 401


def test_credit_quote_openapi(client):
    schema = client.get('/openapi.json').json()
    operation = schema['paths']['/v1/projects/{project_id}/credit-quote']['get']
    assert operation['tags'] == ['Projects']
    assert operation['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/CreditQuote')


class _SqlConn:
    """실제 쿼리를 SQLite에서 실행. 드라이버 표기와 행 잠금만 치환한다."""
    def __init__(self):
        import sqlite3
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        class BoolOr:
            def __init__(self):
                self.value = False
            def step(self, value):
                self.value = self.value or bool(value)
            def finalize(self):
                return self.value
        self.db.create_aggregate('bool_or', 1, BoolOr)

    def cursor(self):
        conn = self
        class Cursor:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                pass
            async def execute(self, sql, params):
                import json
                sql = sql.replace('%s', '?').replace(' for update', '')
                params = tuple(json.dumps(p.obj) if hasattr(p, 'obj') else p for p in params)
                self.cur = conn.db.execute(sql, params)
            async def fetchone(self):
                row = self.cur.fetchone()
                return dict(row) if row else None
        return Cursor()


def test_pricing_counts_only_successful_mannequin_jobs():
    conn = _SqlConn()
    conn.db.executescript('''
        create table profiles(user_id text, plan text);
        create table analyses(project_id text, payload text);
        create table jobs(user_id text, project_id text, kind text, status text, metadata text);
        insert into profiles values ('u1', 'starter');
        insert into analyses values ('p1', '{"selectedModelId":"mE"}');
        insert into jobs values ('u1','p1','mannequin','done','{"extensionModelFee":19}');
        insert into jobs values ('u1','p1','mannequin','error','{}');
        insert into jobs values ('u1','p1','mannequin','cancelled','{}');
        insert into jobs values ('u1','p1','mannequin','running','{}');
        insert into jobs values ('u1','p1','mannequin','pending','{}');
        insert into jobs values ('u1','p1','detail_page','done','{}');
        insert into jobs values ('u1','p2','mannequin','done','{}');
        insert into jobs values ('u2','p1','mannequin','done','{}');
    ''')
    state = asyncio.run(repo.get_mannequin_pricing_state(conn, 'u1', 'p1'))
    assert state == {'plan': 'starter', 'selected_model_id': 'mE', 'done_count': 1,
                     'extension_fee_already_paid': True}
    from app.plan_pricing import mannequin_regenerate_cost
    assert mannequin_regenerate_cost(state['plan'], state['done_count'], 45) == 0
    conn.db.execute("insert into jobs values ('u1','p1','mannequin','done','{}')")
    state = asyncio.run(repo.get_mannequin_pricing_state(conn, 'u1', 'p1'))
    assert state['done_count'] == 2
    assert mannequin_regenerate_cost(state['plan'], state['done_count'], 45) == 45


def test_zero_reserve_records_idempotent_ledger_without_balance_change():
    import json
    conn = _SqlConn()
    conn.db.executescript('''
        create table credit_accounts(user_id text, balance integer, reserved integer);
        create table credit_sources(user_id text, remaining_credits integer, status text);
        create table credit_ledger(id integer primary key, user_id text, project_id text,
            job_id text, action_key text, delta integer, balance_after integer,
            available_after integer, idempotency_key text unique, metadata text);
        insert into credit_accounts values ('u1', 0, 0);
    ''')
    assert asyncio.run(repo.reserve_credits(conn, 'u1', 0)) == 0
    metadata = {'freeAdjust': True, 'adjustIndex': 1, 'plan': 'starter'}
    for _ in range(2):
        available = asyncio.run(repo.record_free_mannequin_adjust(
            conn, user_id='u1', project_id='p1', job_id='j1', metadata=metadata,
        ))
        assert available == 0
    rows = conn.db.execute('select * from credit_ledger').fetchall()
    assert len(rows) == 1
    assert rows[0]['delta'] == 0
    assert rows[0]['action_key'] == 'mannequinGenerate.reserve'
    assert json.loads(rows[0]['metadata']) == metadata
    assert tuple(conn.db.execute('select balance, reserved from credit_accounts').fetchone()) == (0, 0)
