"""새 마이그레이션을 명시한 로컬 PostgreSQL의 격리 스키마에만 적용한다."""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest


MIGRATION = Path(__file__).resolve().parents[2] / 'supabase' / 'migrations' / '20261006120000_free_credits_and_topup_bonus.sql'


@pytest.fixture()
def migration_db():
    url = os.getenv('SIGNUP_BONUS_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires explicit local SIGNUP_BONUS_TEST_DATABASE_URL')
    options = conninfo_to_dict(url)
    if (options.get('host') not in ('localhost', '127.0.0.1', '::1')
            or options.get('hostaddr', '127.0.0.1') not in ('127.0.0.1', '::1')
            or 'service' in options):
        pytest.fail('SIGNUP_BONUS_TEST_DATABASE_URL must point to a local disposable database')
    schema = f'signup_migration_test_{uuid4().hex}'
    conn = psycopg.connect(url, host='127.0.0.1', hostaddr='127.0.0.1', autocommit=True, connect_timeout=2)
    try:
        conn.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
        conn.execute('''
            create table credit_sources (
                id integer generated always as identity primary key,
                source_type text not null constraint legacy_source_allowlist check (source_type in ('subscription', 'topup')),
                period_end timestamptz,
                remaining_credits integer not null default 0 check (remaining_credits >= 0),
                constraint legacy_expiration_contract check (
                    (source_type = 'subscription' and period_end is not null)
                    or (source_type = 'topup' and period_end is null)
                )
            );
            create table credit_ledger (action_key text not null);
            create table bank_transfer_requests (id integer primary key);
            create table toss_payment_orders (id integer primary key);
            insert into bank_transfer_requests values (1);
            insert into toss_payment_orders values (1);
        ''')
        yield conn, schema
    finally:
        conn.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))
        conn.close()


def apply_migration(conn, schema):
    assert MIGRATION.exists(), 'the signup and topup bonus migration is missing'
    conn.execute(MIGRATION.read_text().replace('public.', f'{schema}.'))


@pytest.mark.parametrize('source,period_end', [
    ('subscription', '2027-01-01'), ('topup', None), ('free', None),
])
def test_migration_allows_supported_sources_with_valid_expiration(migration_db, source, period_end):
    conn, schema = migration_db
    apply_migration(conn, schema)
    conn.execute('insert into credit_sources (source_type, period_end) values (%s, %s)', (source, period_end))
    assert conn.execute('select source_type from credit_sources').fetchone() == (source,)


@pytest.mark.parametrize('source,period_end', [
    ('free', '2027-01-01'), ('topup', '2027-01-01'), ('subscription', None), ('unknown', None),
])
def test_migration_rejects_invalid_source_or_expiration(migration_db, source, period_end):
    conn, schema = migration_db
    apply_migration(conn, schema)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute('insert into credit_sources (source_type, period_end) values (%s, %s)', (source, period_end))


def test_migration_keeps_unrelated_checks_and_adds_named_source_contracts(migration_db):
    conn, schema = migration_db
    apply_migration(conn, schema)
    definitions = dict(conn.execute(
        'select conname, pg_get_constraintdef(oid) from pg_constraint where conrelid = %s::regclass and contype = %s',
        (f'{schema}.credit_sources', 'c'),
    ).fetchall())
    assert 'legacy_source_allowlist' not in definitions
    assert 'legacy_expiration_contract' not in definitions
    assert 'credit_sources_source_type_check' in definitions
    assert 'credit_sources_source_period_check' in definitions
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into credit_sources (source_type, remaining_credits) values ('free', -1)")


@pytest.mark.parametrize('existing_check', [False, True])
def test_migration_extends_optional_ledger_action_check_without_restricting_open_ledger(migration_db, existing_check):
    conn, schema = migration_db
    if existing_check:
        conn.execute("alter table credit_ledger add constraint old_action_contract check (action_key in ('grant_topup', 'refund_topup'))")
    apply_migration(conn, schema)
    for action in ('grant_signup', 'grant_topup', 'refund_topup'):
        conn.execute('insert into credit_ledger values (%s)', (action,))
    if existing_check:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("insert into credit_ledger values ('unknown')")
    else:
        conn.execute("insert into credit_ledger values ('unknown')")
    assert conn.execute('select count(*) from credit_ledger').fetchone()[0] == (3 if existing_check else 4)


@pytest.mark.parametrize('table', ['bank_transfer_requests', 'toss_payment_orders'])
def test_migration_order_snapshots_default_legacy_bonus_to_zero_and_reject_negative_values(migration_db, table):
    conn, schema = migration_db
    apply_migration(conn, schema)
    query = sql.SQL('select bonus_credits, bonus_percent, plan_at_order from {} where id = 1').format(sql.Identifier(table))
    assert conn.execute(query).fetchone() == (0, 0, None)
    for column in ('bonus_credits', 'bonus_percent'):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(sql.SQL('update {} set {} = -1 where id = 1').format(sql.Identifier(table), sql.Identifier(column)))
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(sql.SQL('update {} set {} = null where id = 1').format(sql.Identifier(table), sql.Identifier(column)))
    conn.execute(sql.SQL("update {} set bonus_credits = 5, bonus_percent = 5, plan_at_order = 'seller' where id = 1").format(sql.Identifier(table)))
    assert conn.execute(query).fetchone() == (5, 5, 'seller')
