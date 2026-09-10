"""테스트컷 안내도 모델이 지원서에 지정한 연락 이메일을 우선한다."""
import asyncio
import sqlite3

import pytest

from app.facemarket_admin_models import _load_admin_model


@pytest.mark.parametrize(
    ('contact_email', 'account_email', 'expected'),
    [
        ('contact@example.com', 'google@example.com', 'contact@example.com'),
        ('contact@example.com', None, 'contact@example.com'),
        (None, 'legacy@example.com', 'legacy@example.com'),
    ],
)
def test_test_cut_recipient_uses_application_contact_before_oauth_email(
    contact_email, account_email, expected,
):
    # Execute the real SELECT and joins; adapt only PostgreSQL syntax/functions.
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.create_function('btrim', 1, lambda value: value.strip() if value else value)
    db.create_function('now', 0, lambda: '2026-09-10')
    db.executescript("""
        attach database ':memory:' as auth;
        create table auth.users (id text, email text);
        create table fm_model_applications (id text, contact_email text);
        create table fm_biometric_enrollments (id text, application_id text, status text);
        create table fm_models (id text, user_id text, current_enrollment_id text,
            status text, redo_count integer, fullbody_image_url text);
        create table fm_licenses (model_id text, enrollment_id text,
            status text, vc_id text, license_valid_until text);
        insert into fm_biometric_enrollments values ('enrollment-1', 'application-1', 'passed');
        insert into fm_models values ('model-1', 'user-1', 'enrollment-1', 'awaiting_confirm', 0, null);
    """)
    db.execute('insert into auth.users values (?, ?)', ('user-1', account_email))
    if contact_email is not None:
        db.execute('insert into fm_model_applications values (?, ?)', ('application-1', contact_email))

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, sql, params):
            self.cursor = db.execute(sql.replace('::text', '').replace('%s', '?'), params)

        async def fetchone(self):
            row = self.cursor.fetchone()
            return dict(row) if row else None

    class Connection:
        def cursor(self):
            return Cursor()

    try:
        model = asyncio.run(_load_admin_model(Connection(), 'model-1'))
        assert model['contact_email'] == expected
    finally:
        db.close()
