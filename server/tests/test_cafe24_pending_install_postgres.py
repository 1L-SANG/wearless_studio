"""Real PostgreSQL quarantine encryption, RLS and concurrent one-use transfer."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app import mall_connect, repo
from test_mall_cafe24_install import BASE, launch_query
from test_mall_connect import USER, OTHER
from test_mall_connect_postgres import mall_pg
from test_mall_cafe24_install_postgres import install_pg

MIGRATION = Path(__file__).resolve().parents[2] / 'supabase/migrations/20261105090000_cafe24_pending_installs.sql'
COOKIE_HASH = hashlib.sha256(b'fixture-browser').digest()


@pytest.fixture
def pending_pg(install_pg):
    admin, pool = install_pg
    # Apply the complete new migration in the fixture's isolated schema.
    admin.execute(MIGRATION.read_text().replace('public.cafe24_pending_installs', f'{pool.schema}.cafe24_pending_installs'))
    return admin, pool


async def approved(pool, shop_name="테스트 쇼핑몰", cookie_hash=COOKIE_HASH):
    async with pool.connection() as conn:
        key = await repo.create_cafe24_pending_install(conn, mall_id='myshop', return_to='/editor/project',
            cookie_hash=cookie_hash, expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
        assert await repo.consume_cafe24_pending_callback(conn, pending_id=key, cookie_hash=cookie_hash)
    async with pool.connection() as conn:
        assert await repo.save_cafe24_pending_tokens(conn, pending_id=key, shop_name=shop_name, access_token='fixture-pending-access',
            refresh_token='fixture-pending-refresh', token_expires_at=datetime.now(timezone.utc)+timedelta(hours=2),
            refresh_expires_at=datetime.now(timezone.utc)+timedelta(days=14), scopes=['mall.read_store'], kek='mall-test-kek')
    return key


async def claim(conn, key, user=USER, cookie_hash=COOKIE_HASH):
    return await repo.claim_cafe24_pending_install(conn, pending_id=key, cookie_hash=cookie_hash,
        user_id=user, allowed_mall_ids=frozenset({'myshop'}))


async def wait_for_blocked(admin, blocker, waiter, task):
    async def blocked():
        while not admin.execute('select %s = any(pg_blocking_pids(%s)) as blocked',
                (blocker.info.backend_pid, waiter.info.backend_pid)).fetchone()['blocked']:
            if task.done(): pytest.fail('operation bypassed the shared PostgreSQL lock')
            await asyncio.sleep(0.01)
    await asyncio.wait_for(blocked(), timeout=5)


def test_launch_limit_serializes_thirtieth_attempt_and_keeps_other_malls_independent(pending_pg):
    admin, pool = pending_pg
    admin.execute("""insert into client_event_rate_limits (scope, key, window_start, request_count)
        values ('global', 'mall-launch:myshop', clock_timestamp(), 29)""")
    async def scenario():
        async with pool.connection() as first, pool.connection() as second:
            await first.execute('select 1')
            assert await repo.claim_cafe24_launch_attempt(first, mall_id='myshop')
            pending = asyncio.create_task(repo.claim_cafe24_launch_attempt(second, mall_id='myshop'))
            try:
                await wait_for_blocked(admin, first, second, pending)
                async with pool.connection() as independent:
                    assert await repo.claim_cafe24_launch_attempt(independent, mall_id='othershop')
                await first.commit()
                assert await asyncio.wait_for(pending, timeout=5) is False
                await second.commit()
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        rows = admin.execute("""select scope, key, sum(request_count) as count
            from client_event_rate_limits group by scope, key order by key""").fetchall()
        assert rows == [
            {'scope': 'global', 'key': 'mall-launch:myshop', 'count': 30},
            {'scope': 'global', 'key': 'mall-launch:othershop', 'count': 1},
        ]
    asyncio.run(scenario())


def test_launch_limit_uses_only_its_live_global_hourly_window(pending_pg):
    admin, pool = pending_pg
    admin.execute("""insert into client_event_rate_limits (scope, key, window_start, request_count)
        values ('global', 'mall-launch:myshop', clock_timestamp() - interval '61 minutes', 30),
               ('user', 'mall-launch:myshop', clock_timestamp(), 100)""")
    async def scenario():
        async with pool.connection() as conn:
            assert await repo.claim_cafe24_launch_attempt(conn, mall_id='myshop')
    asyncio.run(scenario())
    assert admin.execute("""select sum(request_count) as count from client_event_rate_limits
        where scope = 'global' and key = 'mall-launch:myshop'
          and window_start > clock_timestamp() - interval '1 hour'""").fetchone()['count'] == 1


def test_concurrent_launches_with_same_signature_create_only_one_pending_install(pending_pg, mall_pg, monkeypatch):
    admin, pool = pending_pg
    app = mall_pg[0]
    app.state.settings = replace(app.state.settings, cafe24_enabled=True, cafe24_client_id='fixture-client',
        cafe24_client_secret='fixture-client-secret', cafe24_redirect_uri='https://api.test/v1/malls/cafe24/callback')
    create_pending = repo.create_cafe24_pending_install

    async def scenario():
        inserted, release = asyncio.Event(), asyncio.Event()
        async with pool.connection() as first, pool.connection() as second:
            # Keep the first request's locks until its explicit commit, including nested transactions.
            await first.execute('select 1')
            connections = iter((first, second))
            @asynccontextmanager
            async def get_conn(_request):
                yield next(connections)
            async def hold_insert(conn, **kwargs):
                key = await create_pending(conn, **kwargs)
                if conn is first:
                    inserted.set()
                    await release.wait()
                return key
            monkeypatch.setattr(mall_connect, 'get_conn', get_conn)
            monkeypatch.setattr(repo, 'create_cafe24_pending_install', hold_insert)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.test') as client:
                body = {'query': launch_query()}
                winning = asyncio.create_task(client.post(BASE+'/launch', json=body))
                pending = None
                try:
                    await asyncio.wait_for(inserted.wait(), timeout=5)
                    pending = asyncio.create_task(client.post(BASE+'/launch', json=body))
                    await wait_for_blocked(admin, first, second, pending)
                    release.set()
                    assert (await asyncio.wait_for(winning, timeout=5)).status_code == 200
                    reused = await asyncio.wait_for(pending, timeout=5)
                    assert reused.status_code == 409 and reused.json()['error']['code'] == 'launch_reused'
                finally:
                    release.set()
                    tasks = [task for task in (winning, pending) if task is not None]
                    for task in tasks:
                        if not task.done(): task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        assert admin.execute('select count(*) as n from cafe24_pending_installs').fetchone()['n'] == 1
        assert admin.execute("""select key, request_count from client_event_rate_limits
            where key = 'mall-launch:myshop'""").fetchall() == [{'key': 'mall-launch:myshop', 'request_count': 1}]
        assert admin.execute("""select count(*) as n from client_event_rate_limits
            where scope = 'global' and key like 'mall-launch-sig:%%'""").fetchone()['n'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('age_minutes, allowed', [(119, False), (121, True)])
def test_launch_signature_uses_only_its_live_global_two_hour_window(pending_pg, age_minutes, allowed):
    admin, pool = pending_pg
    signature = hashlib.sha256(b'fixture-launch-signature').digest()
    key = 'mall-launch-sig:' + hashlib.sha256(signature).hexdigest()
    admin.execute("""insert into client_event_rate_limits (scope, key, window_start, request_count)
        values ('global', %s, clock_timestamp() - %s * interval '1 minute', 1),
               ('user', %s, clock_timestamp(), 1),
               ('global', 'mall-launch-sig:other', clock_timestamp(), 1)""", (key, age_minutes, key))
    async def scenario():
        async with pool.connection() as conn:
            assert await repo.claim_cafe24_launch_signature(conn, signature=signature) is allowed
    asyncio.run(scenario())
    assert admin.execute("""select count(*) as n from client_event_rate_limits
        where scope = 'global' and key = %s""", (key,)).fetchone()['n'] == (2 if allowed else 1)


def test_rate_limited_launch_commits_signature_before_returning_error(pending_pg, mall_pg):
    admin, pool = pending_pg
    app = mall_pg[0]
    app.state.settings = replace(app.state.settings, cafe24_enabled=True, cafe24_client_id='fixture-client',
        cafe24_client_secret='fixture-client-secret', cafe24_redirect_uri='https://api.test/v1/malls/cafe24/callback')
    app.state.pool = pool
    admin.execute("""insert into client_event_rate_limits (scope, key, window_start, request_count)
        values ('global', 'mall-launch:myshop', clock_timestamp(), 30)""")
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.test') as client:
            body = {'query': launch_query()}
            limited = await client.post(BASE+'/launch', json=body)
            assert limited.status_code == 429 and limited.json()['error']['code'] == 'rate_limited'
            reused = await client.post(BASE+'/launch', json=body)
            assert reused.status_code == 409 and reused.json()['error']['code'] == 'launch_reused'
        assert admin.execute('select count(*) as n from cafe24_pending_installs').fetchone()['n'] == 0
        assert admin.execute("""select sum(request_count) as n from client_event_rate_limits
            where key = 'mall-launch:myshop'""").fetchone()['n'] == 30
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['before_callback', 'callback_consumed', 'tokens_saved'])
def test_void_pending_is_cookie_bound_and_late_callback_cannot_restore_it(pending_pg, stage):
    admin, pool = pending_pg
    async def scenario():
        async with pool.connection() as conn:
            key = await repo.create_cafe24_pending_install(conn, mall_id='myshop', return_to=None,
                cookie_hash=COOKIE_HASH, expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
            if stage != 'before_callback':
                assert await repo.consume_cafe24_pending_callback(conn, pending_id=key, cookie_hash=COOKIE_HASH)
            if stage == 'tokens_saved':
                assert await repo.save_cafe24_pending_tokens(conn, pending_id=key, shop_name='테스트 쇼핑몰',
                    access_token='fixture-access', refresh_token='fixture-refresh', scopes=['mall.read_store'],
                    token_expires_at=None, refresh_expires_at=None, kek='mall-test-kek')
        async with pool.connection() as conn:
            assert not await repo.void_cafe24_pending_install(conn, pending_id=key, cookie_hash=b'other-browser')
        assert admin.execute('select count(*) as n from cafe24_pending_installs where id = %s', (key,)).fetchone()['n'] == 1
        async with pool.connection() as conn:
            assert await repo.void_cafe24_pending_install(conn, pending_id=key, cookie_hash=COOKIE_HASH)
        async with pool.connection() as conn:
            assert await repo.consume_cafe24_pending_callback(conn, pending_id=key, cookie_hash=COOKIE_HASH) is None
            assert not await repo.save_cafe24_pending_tokens(conn, pending_id=key, shop_name='늦은 콜백',
                access_token='fixture-late-access', refresh_token='fixture-late-refresh', scopes=['mall.read_store'],
                token_expires_at=None, refresh_expires_at=None, kek='mall-test-kek')
            assert await claim(conn, key) is None
        assert admin.execute('select count(*) as n from cafe24_pending_installs').fetchone()['n'] == 0
        assert admin.execute('select count(*) as n from mall_connections').fetchone()['n'] == 0
    asyncio.run(scenario())


def test_other_browser_pending_cannot_restore_disconnect_but_fresh_consent_can(pending_pg):
    admin, pool = pending_pg
    other_browser = hashlib.sha256(b'fixture-other-browser').digest()
    async def scenario():
        original = await approved(pool)
        async with pool.connection() as conn:
            original_row = await claim(conn, original)
        stale = await approved(pool, cookie_hash=other_browser)
        async with pool.connection() as conn:
            assert await repo.revoke_mall_connection(conn, user_id=USER, mall='cafe24')
        async with pool.connection() as conn:
            with pytest.raises(repo.Cafe24ConnectionChanged):
                await claim(conn, stale, cookie_hash=other_browser)
        # Rejection rolls back the DELETE rather than consuming another browser's credentials.
        assert admin.execute('select count(*) as n from cafe24_pending_installs where id = %s', (stale,)).fetchone()['n'] == 1
        assert admin.execute("select count(*) as n from mall_connections where status = 'connected'").fetchone()['n'] == 0
        fresh = await approved(pool)
        async with pool.connection() as conn:
            restored = await claim(conn, fresh)
            assert restored['id'] != original_row['id']
        repeat = await approved(pool)
        async with pool.connection() as conn:
            assert (await claim(conn, repeat))['id'] == restored['id']
    asyncio.run(scenario())


def test_disconnect_timestamp_is_after_waiting_for_the_mall_lock(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        initial = await approved(pool)
        async with pool.connection() as conn:
            await claim(conn, initial)
        async with pool.connection() as blocker, pool.connection() as revoker:
            await blocker.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'mall:{USER}:cafe24',))
            started = (await (await revoker.execute('select now() as started')).fetchone())['started']
            pending = asyncio.create_task(repo.revoke_mall_connection(revoker, user_id=USER, mall='cafe24'))
            try:
                await wait_for_blocked(admin, blocker, revoker, pending)
                stale = await approved(pool)
                created = admin.execute('select created_at from cafe24_pending_installs where id = %s', (stale,)).fetchone()['created_at']
                assert started < created
                await blocker.commit()
                assert await asyncio.wait_for(pending, timeout=5)
                await revoker.commit()
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        revoked = admin.execute('select revoked_at from mall_connections').fetchone()['revoked_at']
        assert revoked > created
        async with pool.connection() as conn:
            with pytest.raises(repo.Cafe24ConnectionChanged): await claim(conn, stale)
        assert admin.execute('select count(*) as n from cafe24_pending_installs where id = %s', (stale,)).fetchone()['n'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('writer', ['pending_claim', 'settings_callback'])
def test_new_connection_timestamp_is_after_waiting_for_the_mall_lock(pending_pg, writer):
    admin, pool = pending_pg
    async def scenario():
        if writer == 'pending_claim':
            winning_pending = await approved(pool)
            async def save(conn): return await claim(conn, winning_pending)
        else:
            async with pool.connection() as conn:
                attempt = await repo.create_install_attempt(conn, user_id=USER, mall='cafe24', mall_id='myshop',
                    expected_generation='none', return_to=None, cookie_hash=COOKIE_HASH,
                    expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
                assert await repo.start_install_attempt(conn, attempt_id=attempt, cookie_hash=COOKIE_HASH)
                assert await repo.consume_install_attempt(conn, attempt_id=attempt, cookie_hash=COOKIE_HASH)
            async def save(conn):
                return await repo.save_cafe24_connection(conn, attempt_id=attempt, user_id=USER, mall_id='myshop',
                    shop_name='먼저 저장한 쇼핑몰', access_token='fixture-winner', refresh_token='fixture-refresh',
                    token_expires_at=None, refresh_expires_at=None, scopes=['mall.read_store'],
                    permissions=['쇼핑몰 정보 조회'], kek='mall-test-kek')
        async with pool.connection() as blocker, pool.connection() as saver:
            await blocker.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'mall:{USER}:cafe24',))
            started = (await (await saver.execute('select now() as started')).fetchone())['started']
            pending = asyncio.create_task(save(saver))
            try:
                await wait_for_blocked(admin, blocker, saver, pending)
                stale = await approved(pool)
                created = admin.execute('select created_at from cafe24_pending_installs where id = %s', (stale,)).fetchone()['created_at']
                assert started < created
                await blocker.commit()
                winner = await asyncio.wait_for(pending, timeout=5)
                assert winner is not None
                await saver.commit()
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        connection = admin.execute('select id::text, created_at from mall_connections').fetchone()
        assert connection['created_at'] > created
        async with pool.connection() as conn:
            with pytest.raises(repo.Cafe24ConnectionChanged): await claim(conn, stale)
        assert admin.execute('select id::text from mall_connections').fetchone()['id'] == winner['id']
        assert admin.execute('select count(*) as n from cafe24_pending_installs where id = %s', (stale,)).fetchone()['n'] == 1
    asyncio.run(scenario())


def test_new_pending_uses_insert_time_after_disconnect_in_an_older_transaction(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        original = await approved(pool)
        async with pool.connection() as conn:
            await claim(conn, original)
        async with pool.connection() as older:
            started = (await (await older.execute('select now() as started')).fetchone())['started']
            async with pool.connection() as revoker:
                assert await repo.revoke_mall_connection(revoker, user_id=USER, mall='cafe24')
            revoked = admin.execute('select revoked_at from mall_connections').fetchone()['revoked_at']
            assert started < revoked
            key = await repo.create_cafe24_pending_install(older, mall_id='myshop', return_to=None,
                cookie_hash=COOKIE_HASH, expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
            assert await repo.consume_cafe24_pending_callback(older, pending_id=key, cookie_hash=COOKIE_HASH)
            assert await repo.save_cafe24_pending_tokens(older, pending_id=key, shop_name='새 설치',
                access_token='fixture-new', refresh_token='fixture-refresh', scopes=['mall.read_store'],
                token_expires_at=None, refresh_expires_at=None, kek='mall-test-kek')
        created = admin.execute('select created_at from cafe24_pending_installs where id = %s', (key,)).fetchone()['created_at']
        assert created > revoked
        async with pool.connection() as conn:
            assert await claim(conn, key)
    asyncio.run(scenario())


def test_pending_claim_checks_equal_revocation_time_in_all_connection_history(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        initial = await approved(pool)
        async with pool.connection() as conn:
            existing = await claim(conn, initial)
        stale = await approved(pool)
        created = admin.execute('select created_at from cafe24_pending_installs where id = %s', (stale,)).fetchone()['created_at']
        # An older history row must fence the claim even when the active row is newer.
        admin.execute("""insert into mall_connections (user_id, mall, status, shop_id, created_at, revoked_at)
            values (%s, 'cafe24', 'revoked', 'oldshop', %s - interval '1 day', %s)""", (USER, created, created))
        async with pool.connection() as conn:
            with pytest.raises(repo.Cafe24ConnectionChanged): await claim(conn, stale)
        assert admin.execute('select count(*) as n from cafe24_pending_installs where id = %s', (stale,)).fetchone()['n'] == 1
        assert admin.execute("select id::text from mall_connections where status = 'connected'").fetchone()['id'] == existing['id']
    asyncio.run(scenario())


def test_refresh_and_changes_to_other_owner_or_mall_do_not_reject_pending_claim(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        initial = await approved(pool)
        async with pool.connection() as conn:
            existing = await claim(conn, initial)
        pending = await approved(pool)
        async with pool.connection() as conn:
            assert await repo.update_cafe24_tokens(conn, user_id=USER, connection_id=existing['id'],
                access_token='fixture-refreshed', refresh_token='fixture-refreshed-refresh',
                token_expires_at=None, refresh_expires_at=None, kek='mall-test-kek')
        other = await approved(pool)
        async with pool.connection() as conn:
            assert await claim(conn, other, OTHER)
            assert await repo.revoke_mall_connection(conn, user_id=OTHER, mall='cafe24')
        admin.execute("""insert into mall_connections (user_id, mall, status, shop_id, revoked_at)
            values (%s, 'zigzag', 'revoked', 's1', clock_timestamp())""", (USER,))
        async with pool.connection() as conn:
            assert (await claim(conn, pending))['id'] == existing['id']
    asyncio.run(scenario())


def test_pending_tokens_are_encrypted_and_rls_is_server_only(pending_pg):
    admin, pool = pending_pg
    asyncio.run(approved(pool))
    row = admin.execute("""select access_token_enc, refresh_token_enc,
        public.wl_billing_decrypt(access_token_enc, 'mall-test-kek') as access_token,
        public.wl_billing_decrypt(refresh_token_enc, 'mall-test-kek') as refresh_token,
        extract(epoch from expires_at - clock_timestamp()) as remaining from cafe24_pending_installs""").fetchone()
    assert row['access_token'] == 'fixture-pending-access' and row['refresh_token'] == 'fixture-pending-refresh'
    assert b'fixture-pending' not in bytes(row['access_token_enc'])+bytes(row['refresh_token_enc'])
    assert 1790 < row['remaining'] <= 1800
    assert admin.execute("select count(*) as n from mall_connections").fetchone()['n'] == 0
    assert admin.execute("select relrowsecurity from pg_class where oid = 'cafe24_pending_installs'::regclass").fetchone()['relrowsecurity']
    for role in ('anon', 'authenticated'):
        for privilege in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
            assert not admin.execute("select has_table_privilege(%s, 'cafe24_pending_installs', %s) as allowed", (role, privilege)).fetchone()['allowed']
    assert admin.execute("select has_table_privilege('service_role', 'cafe24_pending_installs', 'SELECT,INSERT,UPDATE,DELETE') as allowed").fetchone()['allowed']


def test_two_accounts_cannot_claim_the_same_pending_install(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        key = await approved(pool)
        async with pool.connection() as first, pool.connection() as second:
            await first.execute("select 1")
            winner = await claim(first, key)
            pending = asyncio.create_task(claim(second, key, OTHER))
            try:
                async def blocked():
                    while not admin.execute('select %s = any(pg_blocking_pids(%s)) as blocked',
                            (first.info.backend_pid, second.info.backend_pid)).fetchone()['blocked']:
                        if pending.done(): pytest.fail('second claim bypassed the one-use row lock')
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(blocked(), timeout=5)
                await first.commit()
                assert await asyncio.wait_for(pending, timeout=5) is None
                await second.commit()
                assert winner['user_id'] == USER
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert admin.execute('select count(*) as n from cafe24_pending_installs').fetchone()['n'] == 0
        assert admin.execute('select count(*) as n from mall_connections').fetchone()['n'] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['expired', 'other_browser', 'not_allowed', 'closed_account'])
def test_invalid_claim_cannot_move_credentials(pending_pg, invalid):
    admin, pool = pending_pg
    async def scenario():
        key = await approved(pool)
        if invalid == 'expired': admin.execute("update cafe24_pending_installs set expires_at = now() - interval '1 second'")
        if invalid == 'closed_account':
            admin.execute("""insert into jobs (user_id, kind, status, payload)
                values (%s, 'personalization_purge', 'pending', '{"reason":"account_delete"}')""", (USER,))
        async with pool.connection() as conn:
            if invalid == 'not_allowed':
                with pytest.raises(repo.Cafe24MallNotAllowed):
                    await repo.claim_cafe24_pending_install(conn, pending_id=key, cookie_hash=COOKIE_HASH,
                        user_id=USER, allowed_mall_ids={'other'})
            elif invalid == 'closed_account':
                with pytest.raises(repo.MallAccountClosed): await claim(conn, key)
            else:
                assert await claim(conn, key, cookie_hash=b'wrong' if invalid == 'other_browser' else COOKIE_HASH) is None
        assert admin.execute('select count(*) as n from mall_connections').fetchone()['n'] == 0
    asyncio.run(scenario())


def test_repeat_consent_updates_same_connection_and_other_account_keeps_existing_policy(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        first = None
        for user, shop_name in ((USER, "처음 쇼핑몰"), (USER, "새 쇼핑몰"), (OTHER, "다른 계정 쇼핑몰")):
            key = await approved(pool, shop_name=shop_name)
            async with pool.connection() as conn:
                row = await claim(conn, key, user)
                assert row['shop_name'] == shop_name
                if first is None: first = row['id']
                elif user == USER: assert row['id'] == first
        assert admin.execute('select count(*) as n from mall_connections').fetchone()['n'] == 2
    asyncio.run(scenario())


def test_pending_claim_waits_for_account_delete_and_rejects_after_its_commit(pending_pg):
    admin, pool = pending_pg
    async def scenario():
        key = await approved(pool)
        async with pool.connection() as closure, pool.connection() as claimant:
            await repo.revoke_all_mall_connections(closure, USER)
            await closure.execute("""insert into jobs (user_id, kind, status, payload)
                values (%s, 'personalization_purge', 'pending', '{"reason":"account_delete"}')""", (USER,))
            pending = asyncio.create_task(claim(claimant, key))
            try:
                async def blocked():
                    while not admin.execute('select %s = any(pg_blocking_pids(%s)) as blocked',
                            (closure.info.backend_pid, claimant.info.backend_pid)).fetchone()['blocked']:
                        if pending.done(): pytest.fail('claim bypassed the account deletion lock')
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(blocked(), timeout=5)
                await closure.commit()
                with pytest.raises(repo.MallAccountClosed): await asyncio.wait_for(pending, timeout=5)
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert admin.execute('select count(*) as n from mall_connections').fetchone()['n'] == 0
    asyncio.run(scenario())
