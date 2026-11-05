"""Real PostgreSQL quarantine encryption, RLS and concurrent one-use transfer."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import repo
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


async def approved(pool, shop_name="테스트 쇼핑몰"):
    async with pool.connection() as conn:
        key = await repo.create_cafe24_pending_install(conn, mall_id='myshop', return_to='/editor/project',
            cookie_hash=COOKIE_HASH, expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
        assert await repo.consume_cafe24_pending_callback(conn, pending_id=key, cookie_hash=COOKIE_HASH)
    async with pool.connection() as conn:
        assert await repo.save_cafe24_pending_tokens(conn, pending_id=key, shop_name=shop_name, access_token='fixture-pending-access',
            refresh_token='fixture-pending-refresh', token_expires_at=datetime.now(timezone.utc)+timedelta(hours=2),
            refresh_expires_at=datetime.now(timezone.utc)+timedelta(days=14), scopes=['mall.read_store'], kek='mall-test-kek')
    return key


async def claim(conn, key, user=USER, cookie_hash=COOKIE_HASH):
    return await repo.claim_cafe24_pending_install(conn, pending_id=key, cookie_hash=cookie_hash,
        user_id=user, allowed_mall_ids=frozenset({'myshop'}))


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
