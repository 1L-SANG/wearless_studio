"""마네킹 합류 키의 영속성. 격리한 로컬 Postgres에서 실제 route/repo/예약 SQL을 실행한다."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repo, routes
from conftest import make_settings

MIGRATIONS = Path(__file__).resolve().parents[2] / 'supabase' / 'migrations'
ALIAS_MIGRATION = MIGRATIONS / '20260910210906_mannequin_job_requests.sql'


@pytest.fixture(scope='module')
def isolated_db():
    url = os.getenv('MANNEQUIN_REQUEST_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires an explicit disposable local MANNEQUIN_REQUEST_TEST_DATABASE_URL')
    parsed = urlsplit(url)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.path != '/mannequin_request_test':
        pytest.fail('only the disposable local mannequin_request_test database is allowed')
    schema = f'mannequin_alias_test_{uuid4().hex}'
    with psycopg.connect(url, autocommit=True, client_encoding='utf8') as conn:
        conn.execute('create schema if not exists auth')
        conn.execute('create table if not exists auth.users (id uuid primary key)')
        for role in ('anon', 'authenticated'):
            if not conn.execute('select 1 from pg_roles where rolname = %s', (role,)).fetchone():
                conn.execute(sql.SQL('create role {}').format(sql.Identifier(role)))
        conn.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('set search_path to {}, public').format(sql.Identifier(schema)))
        conn.execute('create table projects (id uuid primary key, user_id uuid not null)')
        baseline = (MIGRATIONS / '20260612090000_init.sql').read_text()
        trigger = baseline[baseline.index('create function public.set_updated_at()'):baseline.index('-- ---------- profiles')]
        jobs = baseline[baseline.index('create table public.jobs ('):baseline.index('-- ---------- job_events')]
        conn.execute((trigger + jobs).replace('public.', f'{schema}.'))
        conn.execute((MIGRATIONS / '20260809000000_jobs_status_allow_cancelled.sql').read_text().replace('public.', f'{schema}.'))
        conn.execute('create table credit_accounts (user_id uuid primary key, reserved integer not null default 0)')
        conn.execute('create table credit_sources (user_id uuid, remaining_credits integer, status text)')
        if ALIAS_MIGRATION.exists():
            conn.execute(ALIAS_MIGRATION.read_text().replace('public.', f'{schema}.'))
    yield {'url': url, 'options': f'-c search_path={schema},public', 'schema': schema}
    # 이 fixture가 만든 고유 스키마만 정리한다. 공유·운영 DB는 접속 가드에서 거절한다.
    with psycopg.connect(url, autocommit=True, client_encoding='utf8') as conn:
        conn.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))


@asynccontextmanager
async def connection(db):
    async with await psycopg.AsyncConnection.connect(
        db['url'], options=db['options'], row_factory=dict_row, client_encoding='utf8',
    ) as conn:
        yield conn


async def seed(db):
    user_id, project_id = str(uuid4()), str(uuid4())
    async with connection(db) as conn:
        await conn.execute('insert into auth.users values (%s)', (user_id,))
        await conn.execute('insert into projects values (%s, %s)', (project_id, user_id))
        await conn.execute('insert into credit_accounts (user_id) values (%s)', (user_id,))
        await conn.execute("insert into credit_sources values (%s, 20, 'active')", (user_id,))
    return user_id, project_id


def route_request(monkeypatch, db):
    @asynccontextmanager
    async def get_conn(request):
        async with connection(db) as conn:
            yield conn

    async def get_project(conn, uid, pid):
        cur = await conn.execute('select id from projects where id = %s and user_id = %s', (pid, uid))
        return await cur.fetchone()

    async def cuts(*args): return []
    async def snapshot(*args, **kwargs): return {'version': 1, 'profile': None, 'adjustedAxes': []}
    async def product(*args): return {'colors': [{'isBase': True, 'images': [{'slot': 'Front', 'id': 'source'}]}]}
    monkeypatch.setattr(routes, 'get_conn', get_conn)
    monkeypatch.setattr(repo, 'get_project', get_project)
    monkeypatch.setattr(repo, 'list_mannequin_cuts', cuts)
    monkeypatch.setattr(repo, 'get_product', product)
    monkeypatch.setattr(routes, '_fit_profile_snapshot', snapshot)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=make_settings())))

    async def post(uid, pid, key):
        response = await routes.generate_mannequins(request, pid, uid, idempotency_key=key)
        assert response.status_code == 202
        return json.loads(response.body)['jobId']
    return post


async def finish(db, uid, job_id, status='error'):
    async with connection(db) as conn:
        await conn.execute("update jobs set status = %s, result = %s where id = %s",
                           (status, psycopg.types.json.Json({'errorCode': 'mannequin_quality_failed'}), job_id))
        # 별도 워커가 작업을 종결하고 예약을 해제한 시점. 테스트 대상은 이후의 POST 재합류다.
        await conn.execute('update credit_accounts set reserved = 0 where user_id = %s', (uid,))


@pytest.mark.parametrize('status', ['error', 'done', 'cancelled'])
def test_lost_join_response_never_creates_or_reserves_again_after_terminal(monkeypatch, isolated_db, status):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        original = await post(uid, pid, 'tab-a')
        # B의 202 응답이 네트워크에서 유실돼 브라우저에는 B 키만 남은 상황.
        assert await post(uid, pid, 'tab-b') == original
        await finish(isolated_db, uid, original, status)
        resumed = await post(uid, pid, 'tab-b')
        async with connection(isolated_db) as conn:
            count = await (await conn.execute('select count(*) as n from jobs where project_id = %s', (pid,))).fetchone()
            account = await (await conn.execute('select reserved from credit_accounts where user_id = %s', (uid,))).fetchone()
        assert resumed == original, 'a lost join response must not start another paid job'
        assert count['n'] == 1
        assert account['reserved'] == 0
        # 명시적 재시도는 새 키를 사용하므로 막지 않는다.
        assert await post(uid, pid, 'manual-retry') != original

    asyncio.run(run())


def test_concurrent_join_keys_all_remember_the_same_terminal_job(monkeypatch, isolated_db):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        original = await post(uid, pid, 'original')
        keys = [f'tab-{i}' for i in range(8)]
        assert await asyncio.gather(*(post(uid, pid, key) for key in keys)) == [original] * 8
        await finish(isolated_db, uid, original)
        assert await asyncio.gather(*(post(uid, pid, key) for key in keys)) == [original] * 8

    asyncio.run(run())


def test_request_binding_rolls_back_with_the_transaction(monkeypatch, isolated_db):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        original = await post(uid, pid, 'original')
        async with connection(isolated_db) as conn:
            joined, created = await repo.create_job(conn, user_id=uid, project_id=pid,
                kind='mannequin', payload={}, idempotency_key=f'{pid}:mannequin:rolled-back',
                credits_reserved=2, metadata={})
            assert joined['id'] == original and not created
            await conn.rollback()
        await finish(isolated_db, uid, original)
        assert await post(uid, pid, 'rolled-back') != original

    asyncio.run(run())


def test_aliases_are_scoped_to_the_owned_project(monkeypatch, isolated_db):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        other_uid, other_pid = await seed(isolated_db)
        original = await post(uid, pid, 'a')
        assert await post(uid, pid, 'b') == original
        assert await post(other_uid, other_pid, 'b') != original
        with pytest.raises(routes.HTTPException) as error:
            await post(other_uid, pid, 'b')
        assert error.value.status_code == 404

    asyncio.run(run())


def test_preexisting_canonical_key_is_preserved_when_new_keys_join(monkeypatch, isolated_db):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        canonical_key = f'{pid}:mannequin:legacy'
        async with connection(isolated_db) as conn:
            original, _ = await repo.create_job(conn, user_id=uid, project_id=pid, kind='mannequin',
                payload={'mode': 'generate', 'fitProfileSnapshot': {'version': 1, 'profile': None, 'adjustedAxes': []}},
                idempotency_key=None, credits_reserved=2, metadata={})
            # 배포 전 만들어진 작업은 새 매핑 테이블에 레코드가 없다.
            await conn.execute('update jobs set idempotency_key = %s where id = %s', (canonical_key, original['id']))
        assert await post(uid, pid, 'joined') == original['id']
        await finish(isolated_db, uid, original['id'])
        assert await post(uid, pid, 'legacy') == original['id']
        assert await post(uid, pid, 'joined') == original['id']
        async with connection(isolated_db) as conn:
            row = await (await conn.execute('select idempotency_key from jobs where id = %s', (original['id'],))).fetchone()
        assert row['idempotency_key'] == canonical_key

    asyncio.run(run())


def test_non_mannequin_jobs_do_not_write_mannequin_request_bindings(isolated_db):
    async def run():
        uid, pid = await seed(isolated_db)
        async with connection(isolated_db) as conn:
            row, created = await repo.create_job(conn, user_id=uid, project_id=pid, kind='analyze',
                payload={}, idempotency_key=f'{pid}:analyze:key', credits_reserved=0, metadata={})
            assert created and row['kind'] == 'analyze'
            aliases = await (await conn.execute('select count(*) as n from mannequin_job_requests where project_id = %s', (pid,))).fetchone()
            assert aliases['n'] == 0

    asyncio.run(run())


def test_request_mapping_is_server_only_and_removed_with_the_job(monkeypatch, isolated_db):
    post = route_request(monkeypatch, isolated_db)

    async def run():
        uid, pid = await seed(isolated_db)
        job_id = await post(uid, pid, 'original')
        await post(uid, pid, 'joined')
        async with connection(isolated_db) as conn:
            row = await (await conn.execute(
                "select relrowsecurity as rls, has_table_privilege('anon', oid, 'SELECT') as anon_read, "
                "has_table_privilege('authenticated', oid, 'INSERT') as client_write "
                "from pg_class where oid = 'mannequin_job_requests'::regclass"
            )).fetchone()
            assert row == {'rls': True, 'anon_read': False, 'client_write': False}
            await conn.execute('delete from jobs where id = %s', (job_id,))
            aliases = await (await conn.execute('select count(*) as n from mannequin_job_requests where project_id = %s', (pid,))).fetchone()
            assert aliases['n'] == 0

    asyncio.run(run())
