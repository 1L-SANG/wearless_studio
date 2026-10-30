"""Opt-in storage races on a disposable local PostgreSQL, never a remote DSN."""

import asyncio
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Json
import pytest

from app import repo
from app.services.mall_registration_contract import registration_view
from test_mall_connect import OTHER, USER
from test_mall_connect_postgres import mall_pg


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261028124908_multimall_registration.sql"
PROJECT = "11111111-1111-4111-8111-111111111111"
PUBLICATION = "22222222-2222-4222-8222-222222222222"
ASSET = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def storage_pg(mall_pg):
    # mall_pg owns local-only DSN validation, connection timeouts and schema cleanup.
    _app, admin, pool = mall_pg
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
        add column locked_by text, add column locked_at timestamptz,
        add column created_at timestamptz not null default now(),
        add column updated_at timestamptz not null default now(),
        add column finished_at timestamptz""")
    prior = MIGRATION.with_name("20260813010000_matching_cutout_job_kind.sql").read_text()
    index = next(part for part in prior.split(";") if "create unique index jobs_active_unique_idx" in part)
    admin.execute(index.replace("public.jobs", f"{pool.schema}.jobs"))
    admin.execute("create table test_users (id uuid primary key)")
    admin.execute("insert into test_users values (%s), (%s)", (USER, OTHER))
    # Isolated role names let a plain local PostgreSQL exercise the production ACL
    # even when Supabase's global roles are absent. No existing role is modified.
    roles = {name: pool.schema + "_" + name for name in ("anon", "authenticated", "service_role")}
    created_roles = []
    try:
        for role in roles.values():
            admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
            created_roles.append(role)
        admin.execute(sql.SQL("alter default privileges in schema {} grant all on tables to {}, {}")
                      .format(*(sql.Identifier(value) for value in (pool.schema, roles["anon"], roles["authenticated"]))))
        migration = MIGRATION.read_text().replace(
            "alter table public.mall_defaults add column revision bigint not null default 1 check (revision >= 1);", "")
        migration = migration.replace("public.", pool.schema + ".")
        migration = migration.replace("auth.users(id)", f"{pool.schema}.test_users(id)")
        migration = migration.replace("from anon, authenticated", f"from {roles['anon']}, {roles['authenticated']}")
        migration = migration.replace("to service_role", f"to {roles['service_role']}")
        admin.execute(migration)
        # Match the existing mall_defaults ACL before the forward migration.
        admin.execute("alter table mall_defaults enable row level security")
        admin.execute(f"revoke all on mall_defaults from {roles['anon']}, {roles['authenticated']}")
        admin.execute(f"grant select on mall_defaults to {roles['authenticated']}")
        admin.execute(f"grant all on mall_defaults to {roles['service_role']}")
        per_mall = MIGRATION.with_name("20261029184338_multimall_per_mall_defaults.sql").read_text()
        # mall_pg already ran the new ALTER, so run its remaining DROP after A01.
        admin.execute(per_mall[per_mall.index("drop table"):per_mall.index("commit;")].replace("public.", pool.schema + "."))
        admin.execute("""create table projects (
            id uuid primary key, user_id uuid, status text default 'draft', title text,
            compose_mode text, copywriting boolean, selected_mannequin_id text, adjust_count integer,
            facemarket_license_id uuid, editor_revision integer default 3, editor_blocks jsonb default '[]',
            created_at timestamptz default now(), updated_at timestamptz default now(), deleted_at timestamptz)""")
        admin.execute("insert into projects (id, user_id) values (%s, %s)", (PROJECT, USER))
        admin.execute("create table products (project_id uuid primary key, name text)")
        admin.execute("insert into products values (%s, 'sample')", (PROJECT,))
        admin.execute("create table analyses (project_id uuid primary key, payload jsonb)")
        admin.execute("insert into analyses values (%s, '{}')", (PROJECT,))
        publication = MIGRATION.with_name("20261023120000_detail_publications.sql").read_text().split(";")[0]
        admin.execute(publication.replace("public.", pool.schema + ".")
                      .replace("references auth.users(id) on delete cascade", ""))
        admin.execute("alter table detail_publications add column cleanup_after timestamptz, "
                      "add column cleanup_generation integer not null default 0")
        admin.execute("""insert into detail_publications
            (id, user_id, project_id, version, status, preset, width, editor_revision)
            values (%s, %s, %s, 1, 'ready', 'smartstore', 860, 3)""", (PUBLICATION, USER, PROJECT))
        admin.execute("""create table assets (id uuid primary key, user_id uuid, project_id uuid, r2_bucket text,
            r2_key text, mime_type text, source text, width integer, height integer, metadata jsonb,
            deleted_at timestamptz)""")
        admin.execute("insert into assets (id, user_id, project_id) values (%s, %s, %s)", (ASSET, USER, PROJECT))
        yield admin, pool, roles
    finally:
        # Failed SQL can leave the explicit migration transaction aborted.
        if admin.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            admin.execute("rollback")
        for role in reversed(created_roles):
            # Supabase's local postgres is not a superuser and cannot "drop owned by" these roles.
            # The admin granted every privilege here, so it can revoke them before dropping the role.
            schema, ident = sql.Identifier(pool.schema), sql.Identifier(role)
            admin.execute(sql.SQL("alter default privileges in schema {} revoke all on tables from {}").format(schema, ident))
            admin.execute(sql.SQL("revoke all on all tables in schema {} from {}").format(schema, ident))
            admin.execute(sql.SQL("drop role {}").format(ident))


def payloads(batch_id, malls=("cafe24", "zigzag"), request_hash="hash"):
    return {mall: {"mall": mall, "batchId": batch_id, "requestHash": request_hash,
                   "connectionId": str(uuid.uuid4()), "shopId": mall + "-shop", "publicationId": PUBLICATION,
                   "editorRevision": 3, "snapshot": {"name": "sample", "images": [ASSET], "quantities": [0, 2]}}
            for mall in malls}


async def enqueue(conn, batch, malls=("cafe24", "zigzag"), request_hash="hash", blocked=None):
    return await repo.enqueue_mall_registration_batch(conn, user_id=USER, project_id=PROJECT,
        batch_id=batch, request_hash=request_hash, payloads=payloads(batch, malls, request_hash), blocked=blocked or [])


async def insert_job(conn, mall, batch):
    return await repo.create_job(conn, user_id=USER, project_id=PROJECT, kind="mall_register",
        payload=payloads(batch, (mall,))[mall], idempotency_key=f"mall-register:{USER}:{batch}:{mall}",
        credits_reserved=0, metadata={})


async def wait_for_block(admin, pending, first, second):
    async def observe():
        while not admin.execute("select %s = any(pg_blocking_pids(%s)) as waiting",
                                (first.info.backend_pid, second.info.backend_pid)).fetchone()["waiting"]:
            if pending.done():
                pytest.fail("second operation finished without waiting for the database lock")
            await asyncio.sleep(0.01)
    await asyncio.wait_for(observe(), timeout=5)


async def stop_pending(pending):
    if not pending.done():
        pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
@pytest.mark.parametrize("unfinished", [None, "mapping_variants", "setting_inventory",
                                      "uploading_thumbnail", "uploading_additional_images", "activating"])
def test_checkpoint_completion_controls_recovery_and_new_admission(storage_pg, status, unfinished):
    admin, pool, _roles = storage_pg
    job_id = str(uuid.uuid4())
    checkpoints = {stage: {"status": "complete"} for stage in (
        "mapping_variants", "setting_inventory", "uploading_thumbnail",
        "uploading_additional_images", "activating")}
    if unfinished:
        del checkpoints[unfinished]
    frozen = payloads(str(uuid.uuid4()), ("cafe24",))["cafe24"]
    admin.execute("""insert into jobs
        (id, user_id, project_id, kind, status, payload, metadata, result)
        values (%s, %s, %s, 'mall_register', %s, %s, %s, null)""",
        (job_id, USER, PROJECT, status, Json(frozen),
         Json({"productId": "123", "checkpoints": checkpoints})))

    async def scenario():
        async with pool.connection() as conn:
            job = await repo.get_job(conn, USER, job_id)
            assert registration_view(job)["setupStatus"] == ("needs_attention" if unfinished else "complete")
            recoveries = await repo.find_mall_registration_recoveries(conn, user_id=USER,
                project_id=PROJECT, mall="cafe24", shop_id="cafe24-shop")
            assert [row["id"] for row in recoveries] == ([job_id] if unfinished else [])
            if unfinished:
                with pytest.raises(repo.CreditError) as blocked:
                    await enqueue(conn, str(uuid.uuid4()), ("cafe24",))
                assert blocked.value.code == "no_ready_malls"
                assert blocked.value.items[0]["existingJobId"] == job_id
            else:
                receipt = await enqueue(conn, str(uuid.uuid4()), ("cafe24",))
                assert receipt["items"][0]["status"] == "accepted"
                assert receipt["items"][0]["jobId"] != job_id

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["pending", "running"])
def test_active_registration_stays_recoverable_after_setup_checkpoints(storage_pg, status):
    admin, pool, _roles = storage_pg
    job_id = str(uuid.uuid4())
    checkpoints = {stage: {"status": "complete"} for stage in (
        "mapping_variants", "setting_inventory", "uploading_thumbnail",
        "uploading_additional_images", "activating")}
    admin.execute("""insert into jobs (id, user_id, project_id, kind, status, payload, metadata)
        values (%s, %s, %s, 'mall_register', %s, %s, %s)""",
        (job_id, USER, PROJECT, status, Json(payloads(str(uuid.uuid4()), ("cafe24",))["cafe24"]),
         Json({"productId": "123", "checkpoints": checkpoints})))

    async def scenario():
        async with pool.connection() as conn:
            rows = await repo.find_mall_registration_recoveries(conn, user_id=USER, project_id=PROJECT,
                mall="cafe24", shop_id="cafe24-shop")
            assert [row["id"] for row in rows] == [job_id]
            assert registration_view(rows[0])["setupStatus"] == "pending"
            with pytest.raises(repo.CreditError) as blocked:
                await enqueue(conn, str(uuid.uuid4()), ("cafe24",))
            assert blocked.value.code == "no_ready_malls"
            assert blocked.value.items[0]["existingJobId"] == job_id

    asyncio.run(scenario())


def test_batch_lookup_can_use_the_migration_partial_index(storage_pg):
    admin, _pool, _roles = storage_pg
    batch_id = str(uuid.uuid4())
    admin.execute("""insert into jobs (user_id, project_id, kind, status, payload)
        select %s, %s, 'mall_register', 'done', jsonb_build_object(
            'mall', 'cafe24', 'batchId', gen_random_uuid()::text)
        from generate_series(1, 2000)""", (OTHER, PROJECT))
    admin.execute("analyze jobs")
    statements = []

    class CaptureCursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, query, params): statements.append((query, params))
        async def fetchall(self): return []

    class CaptureConnection:
        def cursor(self): return CaptureCursor()

    # Capture the actual repository query; EXPLAIN a copied query would not catch a regression.
    assert asyncio.run(repo.get_mall_batch_jobs(CaptureConnection(), user_id=USER, batch_id=batch_id)) == []
    query, params = statements[0]
    try:
        # Assert index eligibility rather than a timing threshold or planner cost preference.
        admin.execute("set enable_seqscan = off")
        plan = admin.execute("explain (format json) " + query, params).fetchone()["QUERY PLAN"][0]["Plan"]
    finally:
        admin.execute("reset enable_seqscan")

    def indexes(node):
        return {node.get("Index Name")} | set().union(*(indexes(child) for child in node.get("Plans", [])))

    assert "jobs_mall_register_batch_idx" in indexes(plan)


def test_per_mall_defaults_inherit_rls_acl_and_migration_shape(storage_pg):
    admin, pool, roles = storage_pg
    original = MIGRATION.with_name("20261029184338_multimall_per_mall_defaults.sql").read_text().lower()
    assert "security definer" not in original and "create table" not in original
    assert original.strip().startswith("begin;") and original.strip().endswith("commit;")
    assert admin.execute("select to_regclass(%s) as table", (pool.schema + ".mall_sales_defaults",)).fetchone()["table"] is None
    table = pool.schema + ".mall_defaults"
    assert admin.execute("select relrowsecurity from pg_class where oid = %s::regclass", (table,)).fetchone()["relrowsecurity"]
    assert not admin.execute("select has_table_privilege(%s, %s, 'SELECT') as allowed", (roles['anon'], table)).fetchone()['allowed']
    assert admin.execute("select has_table_privilege(%s, %s, 'SELECT') as allowed", (roles['authenticated'], table)).fetchone()['allowed']
    for name in ("anon", "authenticated"):
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not admin.execute("select has_table_privilege(%s, %s, %s) as allowed",
                                     (roles[name], table, privilege)).fetchone()["allowed"]
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert admin.execute("select has_table_privilege(%s, %s, %s) as allowed",
                             (roles["service_role"], table, privilege)).fetchone()["allowed"]
    row = admin.execute("insert into mall_defaults (user_id, mall) values (%s, 'cafe24') returning *", (USER,)).fetchone()
    assert row["revision"] == 1 and row["shipping_days"] == 2 and row["listing_mode"] == "HIDDEN"
    for assignment in ("shipping_days = 0", "shipping_days = 31", "listing_mode = 'T'", "revision = 0"):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(f"update mall_defaults set {assignment} where user_id = %s", (USER,))
    for assignment in ("shipping_days = NULL", "listing_mode = NULL"):
        with pytest.raises(psycopg.errors.NotNullViolation):
            admin.execute(f"update mall_defaults set {assignment} where user_id = %s", (USER,))
    constraints = admin.execute("select pg_get_constraintdef(oid) as definition from pg_constraint where conrelid = %s::regclass", (table,)).fetchall()
    assert any("shipping_days" in row['definition'] and "30" in row['definition'] for row in constraints)
    assert any("HIDDEN" in row['definition'] and "ON_SALE" in row['definition'] for row in constraints)
    for project, mall in ((None, "cafe24"), (PROJECT, "smartstore"), (PROJECT, None)):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute("insert into jobs (user_id, project_id, kind, status, payload) "
                          "values (%s, %s, 'mall_register', 'pending', %s)", (USER, project, Json({"mall": mall})))
    definitions = admin.execute("select indexname, indexdef from pg_indexes where schemaname = %s "
                               "and tablename = 'jobs'", (pool.schema,)).fetchall()
    indexes = {row["indexname"]: row["indexdef"] for row in definitions}
    general = indexes["jobs_active_unique_idx"]
    for kind in ("editor_image", "personalization_generation", "personalization_purge", "export",
                 "base_fidelity_observe", "editor_garment_mask", "mall_register"):
        assert kind in general
    assert "payload ->> 'mall'" in indexes["jobs_mall_register_active_unique_idx"]
    assert "batchId" in indexes["jobs_mall_register_batch_idx"]


@pytest.mark.parametrize("section", ["sales", "addresses"])
def test_concurrent_defaults_revision_has_one_winner(storage_pg, section):
    admin, pool, _roles = storage_pg
    admin.execute("insert into mall_connections (user_id, mall, shop_id) values (%s, 'zigzag', 'store')", (USER,))
    admin.execute("insert into mall_defaults (user_id, mall, shop_id) values (%s, 'zigzag', 'store')", (USER,))

    async def save(conn, value):
        if section == "sales":
            return await repo.save_mall_defaults(conn, user_id=USER, mall="zigzag", base_revision=1,
                                                  patch={"shipping": {"baseFee": value}})
        return await repo.save_mall_defaults(conn, user_id=USER, mall="zigzag", base_revision=1,
                                                       patch={"addresses": {"returnAddressId": str(value)}})

    async def scenario():
        async with pool.connection() as first, pool.connection() as second:
            await first.execute("select 1")
            winner = await save(first, 100)
            inserted = asyncio.Event()
            async def compete():
                await inserted.wait()
                return await save(second, 200)
            pending = asyncio.create_task(compete())
            inserted.set()
            try:
                await wait_for_block(admin, pending, first, second)
                await first.commit()
                with pytest.raises(repo.CreditError) as conflict:
                    await asyncio.wait_for(pending, timeout=5)
                assert conflict.value.code == "defaults_conflict"
                await second.rollback()
            finally:
                await stop_pending(pending)
            assert winner["revision"] == 2
    asyncio.run(scenario())
    if section == "sales":
        assert admin.execute("select shipping from mall_defaults").fetchone()["shipping"] == {"baseFee": 100}
    else:
        assert admin.execute("select addresses from mall_defaults").fetchone()["addresses"] == {"returnAddressId": "100"}


def test_defaults_revisions_are_independent_for_each_mall(storage_pg):
    admin, pool, _roles = storage_pg
    for mall in ("cafe24", "zigzag"):
        admin.execute("insert into mall_connections (user_id, mall, shop_id) values (%s, %s, 'store')", (USER, mall))
        admin.execute("insert into mall_defaults (user_id, mall, shop_id) values (%s, %s, 'store')", (USER, mall))

    async def scenario():
        async with pool.connection() as conn:
            zigzag = await repo.save_mall_defaults(conn, user_id=USER, mall="zigzag", base_revision=1,
                patch={"shipping": {"baseFee": 3000}, "shippingDays": 3})
            cafe24 = await repo.save_mall_defaults(conn, user_id=USER, mall="cafe24", base_revision=1,
                patch={"disclosure": {"country": "KR"}, "taxType": "FREE", "listingMode": "ON_SALE"})
            assert zigzag["revision"] == cafe24["revision"] == 2
            await conn.commit()
    asyncio.run(scenario())
    rows = {row["mall"]: row for row in admin.execute("select * from mall_defaults").fetchall()}
    assert rows["cafe24"]["shipping"] == {} and rows["cafe24"]["tax_type"] == "FREE"
    assert rows["zigzag"]["shipping"] == {"baseFee": 3000} and rows["zigzag"]["tax_type"] == "TAX"


@pytest.mark.parametrize("mode", ["index", "batch"])
def test_two_malls_create_distinct_active_jobs(storage_pg, mode):
    admin, pool, _roles = storage_pg
    batch = str(uuid.uuid4())

    async def scenario():
        async with pool.connection() as conn:
            if mode == "batch":
                for key in (f"mall-batch:{USER}:{batch}", f"mall-register:{USER}:{PROJECT}"):
                    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            source = await repo.load_registration_source(conn, user_id=USER, project_id=PROJECT,
                publication_id=PUBLICATION, asset_ids=[ASSET], lock=True)
            assert source["project"]["editor_revision"] == source["publication"]["editor_revision"] == 3
            assert source["assets"][0]["id"] == ASSET and source["product"]["name"] == "sample"
            if mode == "index":
                rows = [await insert_job(conn, mall, batch) for mall in ("cafe24", "zigzag")]
                assert all(created for _, created in rows) and len({row["id"] for row, _ in rows}) == 2
            else:
                receipt = await enqueue(conn, batch)
                assert len(receipt["items"]) == 2 and receipt["selectedMalls"] == ["cafe24", "zigzag"]
            assert admin.execute("select count(*) as n from jobs").fetchone()["n"] == 0
            await conn.commit()
        if mode == "batch":
            async with pool.connection() as conn:
                # A replay is returned before inspecting changed or absent resources.
                replay = await repo.enqueue_mall_registration_batch(conn, user_id=USER, project_id=PROJECT,
                    batch_id=batch, request_hash="hash", payloads={}, blocked=[])
                assert replay == receipt
                with pytest.raises(repo.IdempotencyKeyReused):
                    await enqueue(conn, batch, request_hash="different")
                jobs = await repo.get_mall_batch_jobs(conn, user_id=USER, batch_id=batch)
                assert len(jobs) == 2 and all(job["metadata"]["batchReceipt"] == receipt for job in jobs)
                assert all(job["metadata"]["externalCode"] == "wl" + uuid.UUID(job["id"]).hex[:16] for job in jobs)
                assert await repo.get_mall_batch_jobs(conn, user_id=OTHER, batch_id=batch) == []
            assert admin.execute("select sum(request_count) as n from client_event_rate_limits").fetchone()["n"] == 2
            admin.execute("update jobs set metadata = metadata || '{\"batchReceipt\": {}}'::jsonb "
                          "where payload->>'mall' = 'zigzag'")
            async with pool.connection() as conn:
                with pytest.raises(repo.CreditError) as corrupt:
                    await enqueue(conn, batch)
                assert corrupt.value.code == "registration_receipt_invalid"
        async with pool.connection() as conn:
            foreign = await repo.load_registration_source(conn, user_id=OTHER, project_id=PROJECT,
                publication_id=PUBLICATION, asset_ids=[ASSET])
            assert foreign["project"] is None and foreign["publication"] is None and foreign["assets"] == []
    asyncio.run(scenario())
    assert admin.execute("select count(*) as n from jobs where status = 'pending'").fetchone()["n"] == 2


@pytest.mark.parametrize("mode", ["index", "batch", "replay"])
def test_competing_batches_same_mall_have_one_active_winner(storage_pg, mode):
    admin, pool, _roles = storage_pg
    first_batch = str(uuid.uuid4())
    second_batch = first_batch if mode == "replay" else str(uuid.uuid4())

    async def scenario():
        async with pool.connection() as first, pool.connection() as second:
            await first.execute("select 1")
            winner = await insert_job(first, "cafe24", first_batch) if mode == "index" else await enqueue(first, first_batch, ("cafe24",))
            inserted = asyncio.Event()
            async def compete():
                await inserted.wait()
                if mode == "index":
                    return await insert_job(second, "cafe24", second_batch)
                malls = ("cafe24",) if mode == "replay" else ("cafe24", "zigzag")
                return await enqueue(second, second_batch, malls)
            pending = asyncio.create_task(compete())
            inserted.set()
            try:
                await wait_for_block(admin, pending, first, second)
                await first.commit()
                result = await asyncio.wait_for(pending, timeout=5)
                await second.commit()
            finally:
                await stop_pending(pending)
            if mode == "index":
                assert winner[1] and not result[1] and winner[0]["id"] == result[0]["id"]
            elif mode == "replay":
                assert winner == result
            else:
                cafe, zigzag = result["items"]
                assert cafe["status"] == "blocked" and cafe["existingJobId"] == winner["items"][0]["jobId"]
                assert zigzag["status"] == "accepted"
    asyncio.run(scenario())
    assert admin.execute("select count(*) as n from jobs where payload->>'mall' = 'cafe24'").fetchone()["n"] == 1
    if mode != "index":
        assert admin.execute("select sum(request_count) as n from client_event_rate_limits").fetchone()["n"] == (2 if mode == "batch" else 1)


@pytest.mark.parametrize("failure", ["second_insert", "quota", "receipt", "no_ready", "caller_rollback"])
def test_receipt_jobs_and_quota_commit_or_rollback_together(storage_pg, failure):
    admin, pool, _roles = storage_pg
    if failure == "second_insert":
        admin.execute("alter table jobs add check (payload->>'mall' <> 'zigzag')")
    if failure == "receipt":
        admin.execute("alter table jobs add check (payload->>'mall' <> 'zigzag' or not (metadata ? 'batchReceipt'))")
    if failure == "quota":
        admin.execute("insert into client_event_rate_limits values ('user', %s, clock_timestamp(), 9)", (f"mall-register:{USER}",))

    async def scenario():
        async with pool.connection() as conn:
            await conn.execute("select 1")
            if failure == "caller_rollback":
                await enqueue(conn, str(uuid.uuid4()))
                await conn.rollback()
            else:
                expected = psycopg.errors.CheckViolation if failure in ("second_insert", "receipt") else repo.CreditError
                with pytest.raises(expected) as caught:
                    await enqueue(conn, str(uuid.uuid4()), () if failure == "no_ready" else ("cafe24", "zigzag"),
                                  blocked=[{"mall": "ably", "issues": [{"code": "preparing"}]}])
                if failure in ("quota", "no_ready"):
                    assert caught.value.code == ("rate_limited" if failure == "quota" else "no_ready_malls")
                # Catching a storage error must leave a usable transaction with no partial writes.
                assert (await (await conn.execute("select count(*) as n from jobs")).fetchone())["n"] == 0
                await conn.commit()
    asyncio.run(scenario())
    assert admin.execute("select count(*) as n from jobs").fetchone()["n"] == 0
    assert admin.execute("select coalesce(sum(request_count), 0) as n from client_event_rate_limits").fetchone()["n"] == (9 if failure == "quota" else 0)


def test_unresolved_job_beyond_latest_five_is_recovered(storage_pg):
    admin, pool, _roles = storage_pg
    old_id = str(uuid.uuid4())
    frozen = payloads(str(uuid.uuid4()), ("cafe24",))["cafe24"]
    admin.execute("""insert into jobs (id, user_id, project_id, kind, status, payload, metadata, created_at)
        values (%s, %s, %s, 'mall_register', 'error', %s, %s, now() - interval '1 day')""",
        (old_id, USER, PROJECT, Json(frozen), Json({"create": {"sentAt": "2026-10-27T00:00:00Z"}})))
    for _ in range(6):
        admin.execute("""insert into jobs (user_id, project_id, kind, status, payload, metadata, result)
            values (%s, %s, 'mall_register', 'done', %s, '{"productId":"123"}', '{"setupStatus":"complete"}')""",
            (USER, PROJECT, Json(frozen)))

    async def scenario():
        async with pool.connection() as conn:
            rows = await repo.find_mall_registration_recoveries(conn, user_id=USER, project_id=PROJECT,
                mall="cafe24", shop_id="cafe24-shop")
            assert [row["id"] for row in rows] == [old_id]
            assert await repo.find_mall_registration_recoveries(conn, user_id=USER, project_id=PROJECT,
                mall="cafe24", shop_id="another-shop") == []
            assert await repo.find_mall_registration_recoveries(conn, user_id=OTHER, project_id=PROJECT,
                mall="cafe24", shop_id="cafe24-shop") == []
            with pytest.raises(repo.CreditError) as blocked:
                await enqueue(conn, str(uuid.uuid4()), ("cafe24",))
            assert blocked.value.code == "no_ready_malls"
            assert blocked.value.items[0]["existingJobId"] == old_id
            receipt = await enqueue(conn, str(uuid.uuid4()), ("cafe24", "zigzag"))
            assert [item["status"] for item in receipt["items"]] == ["blocked", "accepted"]
            await conn.commit()
    asyncio.run(scenario())
    assert admin.execute("select count(*) as n from jobs").fetchone()["n"] == 8
    assert admin.execute("select sum(request_count) as n from client_event_rate_limits").fetchone()["n"] == 1


@pytest.mark.parametrize("resume_mode", ["same", "different", "other_active", "stale_result"])
def test_concurrent_resume_preserves_snapshot_and_checkpoints(storage_pg, resume_mode):
    admin, pool, _roles = storage_pg
    job_id, request_key = str(uuid.uuid4()), str(uuid.uuid4())
    frozen = payloads(str(uuid.uuid4()), ("cafe24",))["cafe24"]
    metadata = {"productId": "123", "externalCode": "wl-original", "leaseRecoveries": 2,
                "checkpoints": {"inventory": {"complete": True}, "images": {"uploaded": [ASSET]}}}
    result = {"outcome": "created", "setupStatus": "needs_attention", "retryAction": "resume", "code": "image_failed"}
    if resume_mode == "stale_result":
        result = None
    admin.execute("""insert into jobs
        (id, user_id, project_id, kind, status, payload, metadata, result, locked_by, locked_at, finished_at, error_message)
        values (%s, %s, %s, 'mall_register', 'done', %s, %s, %s, 'old-lease', now(), now(), 'old error')""",
        (job_id, USER, PROJECT, Json(frozen), Json(metadata), Json(result)))
    if resume_mode == "other_active":
        admin.execute("insert into jobs (user_id, project_id, kind, status, payload) "
                      "values (%s, %s, 'mall_register', 'pending', %s)", (USER, PROJECT, Json(frozen)))

    async def scenario():
        if resume_mode == "other_active":
            async with pool.connection() as conn:
                with pytest.raises(repo.CreditError) as blocked:
                    await repo.requeue_mall_registration(conn, user_id=USER, job_id=job_id, request_key=request_key)
                assert blocked.value.code == "registration_in_progress"
            return
        async with pool.connection() as first, pool.connection() as second:
            await first.execute("select 1")
            row, created = await repo.requeue_mall_registration(first, user_id=USER, job_id=job_id, request_key=request_key)
            assert created and row["payload"] == frozen
            inserted = asyncio.Event()
            async def compete():
                await inserted.wait()
                return await repo.requeue_mall_registration(second, user_id=USER, job_id=job_id,
                    request_key=request_key if resume_mode in ("same", "stale_result") else str(uuid.uuid4()))
            pending = asyncio.create_task(compete())
            inserted.set()
            try:
                await wait_for_block(admin, pending, first, second)
                await first.commit()
                if resume_mode in ("same", "stale_result"):
                    replay, created = await asyncio.wait_for(pending, timeout=5)
                    assert not created and replay["id"] == job_id
                else:
                    with pytest.raises(repo.CreditError) as blocked:
                        await asyncio.wait_for(pending, timeout=5)
                    assert blocked.value.code == "registration_not_resumable"
                await second.commit()
            finally:
                await stop_pending(pending)
    asyncio.run(scenario())
    row = admin.execute("select * from jobs where id = %s", (job_id,)).fetchone()
    assert row["payload"] == frozen and row["result"] == result
    assert row["metadata"]["checkpoints"] == metadata["checkpoints"]
    assert row["metadata"]["externalCode"] == "wl-original"
    if resume_mode != "other_active":
        assert row["status"] == "pending" and row["metadata"]["leaseRecoveries"] == 0
        assert row["locked_by"] is None and row["locked_at"] is None and row["finished_at"] is None and row["error_message"] is None
        assert row["metadata"]["resumeRequestKeys"] == [request_key]
        assert row["metadata"]["resumeHistory"][0]["leaseRecoveries"] == 2
    else:
        assert row["status"] == "done" and row["metadata"] == metadata
    assert admin.execute("select count(*) as n from client_event_rate_limits").fetchone()["n"] == 0
