"""Opt-in registration ledger races on a disposable local PostgreSQL."""

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from psycopg.types.json import Json

from app import mall_connect, repo
from test_mall_cafe24_register_routes import BASE, BODY, PROJECT, PUBLICATION, publication_row
from test_mall_connect import USER
from test_mall_connect_postgres import mall_pg


@pytest.mark.parametrize("same_key", [False, True])
def test_two_submissions_same_project_join_one_active_job(mall_pg, monkeypatch, same_key):
    app, admin, pool = mall_pg
    admin.execute("""alter table jobs
        add column id uuid primary key default gen_random_uuid(),
        add column project_id uuid,
        add column idempotency_key text unique,
        add column progress integer not null default 0,
        add column steps jsonb not null default '[]',
        add column error_message text,
        add column credits_reserved integer not null default 0,
        add column credits_charged integer not null default 0,
        add column metadata jsonb not null default '{}',
        add column created_at timestamptz not null default now(),
        add column updated_at timestamptz not null default now(),
        add column finished_at timestamptz""")
    migration = Path(__file__).resolve().parents[2] / "supabase/migrations/20260813010000_matching_cutout_job_kind.sql"
    index = next(s for s in migration.read_text().split(";") if "create unique index jobs_active_unique_idx" in s)
    admin.execute(index.replace("public.jobs", f"{pool.schema}.jobs"))
    admin.execute("""create table projects (
        id uuid primary key, user_id uuid, status text default 'draft', title text,
        compose_mode text, copywriting boolean, selected_mannequin_id text, adjust_count integer,
        facemarket_license_id uuid, created_at timestamptz default now(), updated_at timestamptz default now(),
        deleted_at timestamptz)""")
    admin.execute("create table fm_output_records (job_id uuid, seller_id uuid)")
    publication_migration = migration.with_name("20261023120000_detail_publications.sql")
    table = publication_migration.read_text().split(";")[0]
    admin.execute(table.replace("public.detail_publications", f"{pool.schema}.detail_publications")
                  .replace("references auth.users(id) on delete cascade", "")
                  .replace("public.projects", f"{pool.schema}.projects"))
    admin.execute("alter table detail_publications add column cleanup_after timestamptz, "
                  "add column cleanup_generation integer not null default 0")
    admin.execute("insert into projects (id, user_id) values (%s, %s)", (PROJECT, USER))
    admin.execute("""insert into detail_publications (id, user_id, project_id, version, status, preset, width, files)
        values (%s, %s, %s, 1, 'ready', 'smartstore', 860, %s)""",
        (PUBLICATION, USER, PROJECT, Json(publication_row()["files"])))
    admin.execute("""insert into mall_connections (user_id, mall, shop_id, refresh_expires_at)
        values (%s, 'cafe24', 'myshop', now() + interval '10 days')""", (USER,))
    app.state.settings = replace(app.state.settings, detail_publish_enabled=True, cafe24_enabled=True,
        cafe24_client_id="fixture-client", cafe24_client_secret="fixture-private")
    monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *a, **kw: pytest.fail("registration must not call Cafe24"))

    async def scenario():
        first_key = str(uuid.uuid4())
        second_key = first_key if same_key else str(uuid.uuid4())
        inserted, release = asyncio.Event(), asyncio.Event()
        create_job = repo.create_job

        async def hold_first_insert(conn, **kwargs):
            row, created = await create_job(conn, **kwargs)
            if created:
                inserted.set()
                await release.wait()
            return row, created

        monkeypatch.setattr(repo, "create_job", hold_first_insert)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post(BASE, json={**BODY, "requestKey": first_key}))
            second = None
            try:
                await asyncio.wait_for(inserted.wait(), timeout=5)
                second = asyncio.create_task(client.post(BASE, json={**BODY, "requestKey": second_key}))
                async def wait_for_conflict():
                    while not admin.execute("""select exists (
                        select 1 from pg_stat_activity where application_name = %s
                        and cardinality(pg_blocking_pids(pid)) > 0) as waiting""", (pool.schema,)).fetchone()["waiting"]:
                        if second.done():
                            pytest.fail("second submission did not wait for the active-job conflict")
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_for_conflict(), timeout=5)
                release.set()
                winner, joined = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
                assert winner.status_code == 202, winner.text
                assert joined.status_code == (200 if same_key else 409), joined.text
                joined_id = joined.json()["jobId"] if same_key else joined.json()["error"]["jobId"]
                assert joined_id == winner.json()["jobId"]
                if not same_key:
                    assert joined.json()["error"]["code"] == "registration_in_progress"
                async with pool.connection() as conn:
                    row = await repo.get_job(conn, USER, joined_id)
                    assert "idempotency_key" not in row
                    identity = await repo.get_job_request_identity(conn, user_id=USER, job_id=joined_id)
                    assert identity["idempotency_key"] == f"cafe24-register:{USER}:{first_key}"
                    assert identity["project_id"] == PROJECT
            finally:
                release.set()
                tasks = [task for task in (first, second) if task is not None]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        assert admin.execute("select count(*) as n from jobs where kind = 'mall_register'").fetchone()["n"] == 1

    asyncio.run(scenario())
