"""Publication concurrency on real PostgreSQL, isolated from app and production data."""

import asyncio
import contextlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app import detail_publications as dp, repo
from test_detail_publications import FakeR2


DB_URL = os.environ.get("DETAIL_PUBLICATIONS_TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
DB_INFO = conninfo_to_dict(DB_URL)
DB_HOST = DB_INFO.get("host", "")
assert DB_HOST in ("127.0.0.1", "localhost") or DB_HOST.startswith("/"), "local test DB only"
assert not (DB_INFO.get("service") or os.environ.get("PGSERVICE")), "test DB cannot use a service override"
assert (DB_INFO.get("hostaddr") or os.environ.get("PGHOSTADDR", "")) in ("", "127.0.0.1", "::1"), "local test hostaddr only"
if DB_HOST.startswith("/"):
    socket_stat = os.stat(DB_HOST)
    assert socket_stat.st_uid == os.getuid() and socket_stat.st_mode & 0o077 == 0, "private local test socket only"


@pytest.fixture
def publication_db():
    try:
        admin = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row,
                               connect_timeout=3, client_encoding="UTF8")
    except psycopg.OperationalError:
        if os.environ.get("CI"):
            pytest.fail("Publication concurrency test requires the local PostgreSQL test DB")
        pytest.skip("Local PostgreSQL test DB unavailable")
    schema = "publication_test_" + uuid.uuid4().hex
    try:
        admin.execute(sql.SQL("create schema {} ").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("set search_path to {} ").format(sql.Identifier(schema)))
        admin.execute("create table profiles (user_id uuid primary key, plan text not null)")
        admin.execute("""create table detail_publications (
            id uuid primary key default gen_random_uuid(), user_id uuid not null,
            project_id uuid, version integer not null, status text not null default 'pending',
            preset text not null, width integer not null, slice_count integer not null default 0,
            total_bytes bigint not null default 0, product_name text not null default '',
            editor_revision integer, files jsonb not null default '[]'::jsonb,
            expires_at timestamptz, created_at timestamptz not null default now(),
            ready_at timestamptz, deleted_at timestamptz, unique (project_id, version))""")
        admin.execute("create table assets (metadata jsonb, deleted_at timestamptz)")
        # Run the forward migration itself, when present, against the isolated table.
        for migration in sorted((Path(__file__).resolve().parents[2] / "supabase/migrations").glob(
                "*_detail_publication_cleanup_*.sql")):
            admin.execute(migration.read_text().replace("public.", schema + "."))

        async def connection():
            return await psycopg.AsyncConnection.connect(
                DB_URL, options=f"-csearch_path={schema}", row_factory=dict_row, client_encoding="UTF8")

        yield admin, connection
    finally:
        admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        admin.close()


async def wait_for_lock(admin, pid, task=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = admin.execute("select wait_event_type from pg_stat_activity where pid = %s", (pid,)).fetchone()
        if row and row["wait_event_type"] == "Lock":
            return True
        if task and task.done():
            return False
        await asyncio.sleep(0.01)
    raise AssertionError("Concurrent publication operation never reached its database lock")


def seed_publication(admin, *, user_id, project_id=None, status="ready", version=1):
    return admin.execute(
        """insert into detail_publications
            (user_id, project_id, version, status, preset, width, expires_at, created_at)
            values (%s, %s, %s, %s, 'smartstore', 860, now() - interval '1 day', now())
            returning id::text as id""",
        (user_id, project_id or str(uuid.uuid4()), version, status)).fetchone()["id"]


def test_renewal_committed_while_expiry_claim_waits_for_publication_preserves_it(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    admin.execute("insert into profiles values (%s, 'free')", (user_id,))
    publication_id = seed_publication(admin, user_id=user_id)

    async def run():
        async with await connect() as blocker, await connect() as claimant:
            await blocker.execute("select id from detail_publications where id = %s for update", (publication_id,))
            task = asyncio.create_task(repo.claim_detail_publication_deletion(
                claimant, user_id=user_id, publication_id=publication_id, now=datetime.now(timezone.utc)))
            try:
                assert await wait_for_lock(admin, claimant.info.backend_pid, task)
                admin.execute("update profiles set plan = 'pro' where user_id = %s", (user_id,))
            finally:
                await blocker.commit()
            assert await asyncio.wait_for(task, 5) is None
            await claimant.commit()

    asyncio.run(run())
    assert admin.execute("select status from detail_publications where id = %s", (publication_id,)).fetchone()["status"] == "ready"


def test_expiry_claim_waits_for_inflight_profile_renewal(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    admin.execute("insert into profiles values (%s, 'free')", (user_id,))
    publication_id = seed_publication(admin, user_id=user_id)

    async def run():
        async with await connect() as renewal, await connect() as claimant:
            await renewal.execute("update profiles set plan = 'pro' where user_id = %s", (user_id,))
            task = asyncio.create_task(repo.claim_detail_publication_deletion(
                claimant, user_id=user_id, publication_id=publication_id, now=datetime.now(timezone.utc)))
            try:
                assert await wait_for_lock(admin, claimant.info.backend_pid, task)
            finally:
                await renewal.commit()
            assert await asyncio.wait_for(task, 5) is None
            await claimant.commit()

    asyncio.run(run())
    assert admin.execute("select status from detail_publications where id = %s", (publication_id,)).fetchone()["status"] == "ready"


def test_expiry_recompute_sees_renewal_committed_while_waiting(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    admin.execute("insert into profiles values (%s, 'free')", (user_id,))
    publication_id = seed_publication(admin, user_id=user_id)
    admin.execute("update detail_publications set expires_at = null where id = %s", (publication_id,))

    async def run():
        async with await connect() as blocker, await connect() as recompute:
            await blocker.execute("select id from detail_publications where id = %s for update", (publication_id,))
            task = asyncio.create_task(repo.recompute_detail_publication_expiry(
                recompute, now=datetime.now(timezone.utc)))
            try:
                assert await wait_for_lock(admin, recompute.info.backend_pid, task)
                admin.execute("update profiles set plan = 'pro' where user_id = %s", (user_id,))
            finally:
                await blocker.commit()
            await asyncio.wait_for(task, 5)
            await recompute.commit()

    asyncio.run(run())
    assert admin.execute("select expires_at from detail_publications where id = %s", (publication_id,)).fetchone()["expires_at"] is None


def test_expiry_recompute_only_updates_the_publications_it_locked(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    admin.execute("insert into profiles values (%s, 'free')", (user_id,))
    first_id = seed_publication(admin, user_id=user_id)
    admin.execute("update detail_publications set expires_at = null where id = %s", (first_id,))
    inserted = []

    async def run():
        async with await connect() as conn:
            class Cursor:
                async def __aenter__(self):
                    self.cursor = await conn.cursor().__aenter__()
                    return self

                async def __aexit__(self, *args):
                    return await self.cursor.__aexit__(*args)

                async def execute(self, *args, **kwargs):
                    return await self.cursor.execute(*args, **kwargs)

                async def fetchall(self):
                    rows = await self.cursor.fetchall()
                    if not inserted:
                        inserted.append(seed_publication(admin, user_id=user_id))
                        admin.execute("update detail_publications set expires_at = null where id = %s", (inserted[0],))
                    return rows

                @property
                def rowcount(self):
                    return self.cursor.rowcount

            await repo.recompute_detail_publication_expiry(
                SimpleNamespace(cursor=Cursor), now=datetime.now(timezone.utc))
            await conn.commit()

    asyncio.run(run())
    assert admin.execute("select expires_at from detail_publications where id = %s", (first_id,)).fetchone()["expires_at"] is not None
    assert admin.execute("select expires_at from detail_publications where id = %s", (inserted[0],)).fetchone()["expires_at"] is None


def test_due_rechecks_do_not_displace_newly_expired_publications(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    admin.execute("insert into profiles values (%s, 'free')", (user_id,))
    tombstones = {seed_publication(admin, user_id=user_id, status="deleted") for _ in range(105)}
    admin.execute("""update detail_publications set expires_at = null,
                     cleanup_after = now() - interval '2 days' where status = 'deleted'""")
    expired = seed_publication(admin, user_id=user_id)

    async def run():
        async with await connect() as conn:
            first = await repo.list_expired_detail_publications(conn, now=datetime.now(timezone.utc))
            assert expired in {row["id"] for row in first}
            checked = {row["id"] for row in first if row["status"] == "deleted"}
            assert len(checked) == 100
            for row in first:
                if row["status"] == "deleted":
                    await repo.finalize_detail_publication_deletion(
                        conn, publication_id=row["id"], cleanup_generation=row["cleanup_generation"])
            await conn.commit()
            second = await repo.list_expired_detail_publications(conn, now=datetime.now(timezone.utc))
            assert {row["id"] for row in second if row["status"] == "deleted"} == tombstones - checked

    asyncio.run(run())


def test_only_due_finalized_tombstones_are_rechecked(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    due = seed_publication(admin, user_id=user_id, status="deleted")
    future = seed_publication(admin, user_id=user_id, status="deleted")
    admin.execute("update detail_publications set deleted_at = now() - interval '2 days'")
    admin.execute("update detail_publications set cleanup_after = now() - interval '1 day' where id = %s", (due,))
    admin.execute("update detail_publications set cleanup_after = now() + interval '1 day' where id = %s", (future,))

    async def run():
        async with await connect() as conn:
            rows = await repo.list_expired_detail_publications(conn, now=datetime.now(timezone.utc))
            assert [row["id"] for row in rows] == [due]

    asyncio.run(run())


def test_successful_recheck_clears_cleanup_schedule(publication_db):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    publication_id = seed_publication(admin, user_id=user_id, status="deleted")
    admin.execute("update detail_publications set cleanup_after = now() - interval '1 day'")

    async def run():
        async with await connect() as conn:
            await repo.finalize_detail_publication_deletion(
                conn, publication_id=publication_id, cleanup_generation=0)
            await conn.commit()
            first = admin.execute("select deleted_at, cleanup_after from detail_publications").fetchone()
            assert first["deleted_at"] is not None
            assert timedelta(days=1) <= first["cleanup_after"] - first["deleted_at"] <= timedelta(days=1, seconds=1)
            admin.execute("update detail_publications set cleanup_after = now() - interval '1 second'")
            claimed = await repo.claim_detail_publication_deletion(
                conn, user_id=user_id, publication_id=publication_id, now=datetime.now(timezone.utc))
            await repo.finalize_detail_publication_deletion(
                conn, publication_id=publication_id, cleanup_generation=claimed["cleanup_generation"])
            await conn.commit()
            completed = admin.execute("select deleted_at, cleanup_after from detail_publications").fetchone()
            assert completed == {"deleted_at": first["deleted_at"], "cleanup_after": None}
            later = datetime.now(timezone.utc) + timedelta(days=2)
            assert await repo.list_expired_detail_publications(conn, now=later) == []
            assert await repo.claim_detail_publication_deletion(
                conn, user_id=user_id, publication_id=publication_id, now=later) is None

    asyncio.run(run())


@pytest.mark.parametrize("already_finalized", [False, True])
def test_older_cleanup_generation_cannot_finalize_new_retry_intent(publication_db, already_finalized):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    publication_id = seed_publication(admin, user_id=user_id, status="deleted")
    admin.execute("update detail_publications set cleanup_after = now() - interval '1 day'")
    if already_finalized:
        admin.execute("update detail_publications set deleted_at = now() - interval '2 days'")

    async def run():
        async with await connect() as old, await connect() as newer:
            first = await repo.claim_detail_publication_deletion(
                old, user_id=user_id, publication_id=publication_id, now=datetime.now(timezone.utc))
            await old.commit()
            second = await repo.soft_delete_detail_publication(newer, user_id=user_id, publication_id=publication_id)
            await newer.commit()
            assert second["cleanup_generation"] > first["cleanup_generation"]
            await repo.finalize_detail_publication_deletion(
                old, publication_id=publication_id, cleanup_generation=first["cleanup_generation"])
            await old.commit()
            pending = admin.execute("select deleted_at, cleanup_after <= now() as due from detail_publications").fetchone()
            assert pending == {"deleted_at": None, "due": True}
            await repo.finalize_detail_publication_deletion(
                newer, publication_id=publication_id, cleanup_generation=second["cleanup_generation"])
            await newer.commit()

    asyncio.run(run())
    completed = admin.execute("select deleted_at is not null as done, cleanup_after > now() as future from detail_publications").fetchone()
    assert completed == {"done": True, "future": True}


@pytest.mark.parametrize("same_project", [True, False])
def test_simultaneous_creates_share_account_hourly_quota(publication_db, monkeypatch, same_project):
    admin, connect = publication_db
    user_id = str(uuid.uuid4())
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    if same_project:
        project_ids[1] = project_ids[0]
    for _ in range(19):
        seed_publication(admin, user_id=user_id, status="pending")
    original_count = repo.count_detail_publications_last_hour

    async def run():
        count_entered = [asyncio.Event(), asyncio.Event()]
        first_count_done = asyncio.Event()
        pids = []

        @contextlib.asynccontextmanager
        async def connection(_request):
            async with await connect() as conn:
                pids.append(conn.info.backend_pid)
                yield conn

        async def count(conn, owner):
            index = pids.index(conn.info.backend_pid)
            count_entered[index].set()
            value = await original_count(conn, owner)
            if index == 0:
                first_count_done.set()
                await count_entered[1].wait()
                # Let the second request either read 19 or block on the account fence.
                await asyncio.sleep(0.05)
            return value

        async def get_project(_conn, _owner, project_id):
            return {"id": project_id}

        async def no(_conn, *_args, **_kwargs):
            return False

        async def revision(_conn, _project_id):
            return 1

        monkeypatch.setattr(dp, "get_conn", connection)
        monkeypatch.setattr(repo, "count_detail_publications_last_hour", count)
        monkeypatch.setattr(repo, "get_project", get_project)
        monkeypatch.setattr(repo, "project_uses_real_model", no)
        monkeypatch.setattr(repo, "user_account_purge_closed", no)
        monkeypatch.setattr(repo, "get_editor_revision", revision)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=SimpleNamespace(detail_publish_token_secret="test", r2_public_base="https://images.test"),
            r2=FakeR2())))
        body = dp.CreatePublication(preset="smartstore", width=860, files=[dp.PublicationFile(
            index=1, byte_size=100, width=860, height=1000)])

        async def create(project_id):
            try:
                await dp.create_publication(request, project_id, body, user_id=user_id)
                return 200
            except HTTPException as exc:
                return exc.status_code

        first = asyncio.create_task(create(project_ids[0]))
        await asyncio.wait_for(first_count_done.wait(), 5)
        second = asyncio.create_task(create(project_ids[1]))
        assert sorted(await asyncio.wait_for(asyncio.gather(first, second), 5)) == [200, 429]

    asyncio.run(run())
    assert admin.execute("select count(*) as count from detail_publications where user_id = %s", (user_id,)).fetchone()["count"] == 20
