"""Opt-in registration transactions on the isolated PR280 PostgreSQL instance."""

import asyncio
from contextlib import asynccontextmanager
import io
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from PIL import Image

from app import facemarket, facemarket_enrollment as enrollment
from app.workers import fm_model_asset_job as asset_worker
from conftest import make_settings
from test_facemarket_biometric_enrollment import FakeR2

EID = "11111111-1111-1111-1111-111111111111"
MID = "22222222-2222-2222-2222-222222222222"
UID = "synthetic-owner"

DDL = """
create table fm_models (
 id uuid primary key, user_id text, status text, display_name text, ci_hash text,
 gender text, height_bucket text, body_type text, did text, cover_image_url text,
 assets_status text, assets_source_hash text, current_enrollment_id uuid, reverification_batch_id uuid);
create table fm_biometric_enrollments (
 id uuid primary key, user_id text, model_id uuid, status text, decision text, reason text,
 cooldown_until timestamptz, expires_at timestamptz, created_at timestamptz default now(), completed_at timestamptz,
 liveness_session_digest text, liveness_nonce_digest text, device_digest text, height_bucket text,
 body_type text, consent_version text, terms_consent_version text, overseas_consent_version text,
 identity_ci_hash text, identity_name_masked text, identity_birth_year text, identity_tx_digest text,
 identity_contract_version text, profile_image_r2_key text, oacx_tx_digest text, match_policy_version text,
 provider_versions jsonb default '{}', raw_deletion_evidence jsonb default '{}', vc_id text);
create table fm_biometric_enrollment_photos (
 enrollment_id uuid, angle text, r2_key text, image_digest text, mime_type text,
 byte_size integer default 10, qc_status text default 'passed', storage_state text default 'quarantine',
 uploaded_at timestamptz default now(), approved_at timestamptz, primary key(enrollment_id, angle));
create table fm_biometric_enrollment_photo_cleanup (
 enrollment_id uuid, angle text, r2_key text, reason text,
 not_before timestamptz default now(), created_at timestamptz default now(), primary key(enrollment_id, r2_key));
create table fm_identity_verifications (
 model_id uuid, cx_tx_id text unique, cx_tx_id_format text, fields jsonb);
create table fm_model_assets (
 model_id uuid, view text, r2_key text, mime text, bucket text,
 source_enrollment_id uuid, evidence_version text, primary key(model_id, view));
create table fm_licenses (
 id uuid primary key, model_id uuid, enrollment_id uuid, status text, face_image_uri text,
 face_image_key text, face_image_digest text, allowed_use text[], forbidden_use text[], unit_price integer,
 license_valid_until timestamptz, vc_id text, created_at timestamptz default now(), updated_at timestamptz,
 consent_doc_version text default '2026-09-v1', reverification_batch_id uuid);
create unique index on fm_licenses(enrollment_id) where enrollment_id is not null;
create table jobs (id uuid default gen_random_uuid(), user_id text, project_id uuid, kind text, status text,
 payload jsonb, credits_reserved integer, metadata jsonb, locked_by text, locked_at timestamptz,
 finished_at timestamptz, progress integer, result jsonb, error_message text);
create table job_events (id bigserial primary key, job_id uuid, event_type text, payload jsonb);
"""


class PgPool:
    def __init__(self, dsn, schema):
        self.dsn, self.schema = dsn, schema

    @asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(
            self.dsn, row_factory=dict_row, options=f"-c search_path={self.schema},public",
        ) as conn:
            yield conn


@pytest.fixture
def pg_registration(monkeypatch):
    dsn = os.environ.get("PR280_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("isolated registration PostgreSQL not requested")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 55482 or parsed.path != "/pr280_enrollment":
        pytest.skip("registration integration requires isolated localhost:55482/pr280_enrollment")
    schema = "registration_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    admin.execute(psycopg.sql.SQL("create schema {}").format(psycopg.sql.Identifier(schema)))
    admin.execute(psycopg.sql.SQL("set search_path to {}, public").format(psycopg.sql.Identifier(schema)))
    admin.execute(DDL)
    migration = Path(__file__).resolve().parents[2] / "supabase/migrations/20260911235000_facemarket_photo_revalidation.sql"
    admin.execute(migration.read_text().replace("public.fm_biometric_enrollments", "fm_biometric_enrollments"))
    admin.execute("insert into fm_models(id,user_id,status,ci_hash,assets_status,current_enrollment_id) values(%s,%s,'pending','ci-hash','ready',%s)", (MID, UID, EID))
    admin.execute("""insert into fm_biometric_enrollments
        (id,user_id,model_id,status,decision,expires_at,identity_ci_hash,identity_name_masked,
         identity_birth_year,identity_tx_digest,identity_contract_version,match_policy_version,
         consent_version,terms_consent_version,overseas_consent_version)
        values(%s,%s,%s,'license_pending','passed',now()+interval '1 day','ci-hash','합*인','1990',
         'cxsha256:original','test-contract','test-policy','2026-09-v1','2026-09-v1','2026-09-v1')""", (EID, UID, MID))
    admin.execute("insert into fm_identity_verifications values(%s,'cxsha256:original','sha256-v1','{}')", (MID,))
    storage = FakeR2()
    image = io.BytesIO()
    Image.new("RGB", (128, 128), color="white").save(image, format="JPEG")
    image_bytes = image.getvalue()
    def copy_photo(source, destination, *_args):
        storage.objects[destination] = storage.objects[source]
    storage.copy = copy_photo
    for slot in enrollment.PHOTO_SLOTS:
        state = "approved" if slot in {"face01", "face03", "face05"} else "quarantine"
        admin.execute("insert into fm_biometric_enrollment_photos(enrollment_id,angle,r2_key,image_digest,mime_type,storage_state) values(%s,%s,%s,%s,'image/jpeg',%s)", (EID, slot, f"private/{slot}.jpg", f"hash-{slot}", state))
        storage.objects[f"private/{slot}.jpg"] = (image_bytes, "image/jpeg")
    for view in ("face_front", "grid_sedcard"):
        admin.execute("insert into fm_model_assets values(%s,%s,%s,'image/jpeg','face',%s,'test-policy')", (MID, view, f"private/{view}.jpg", EID))
        storage.objects[f"private/{view}.jpg"] = (view.encode(), "image/jpeg")
    state = SimpleNamespace(pool=PgPool(dsn, schema), r2_face=storage, settings=make_settings(
        facemarket_enabled=True, fm_biometric_enrollment_enabled=True, fm_ci_pepper="test-only",
        fm_face_match_enabled=False, fm_liveness_enabled=False, fm_match_policy_version="test-policy",
    ))
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={"content-type": "application/json"})
    async def body(): return {"enrollmentId": EID, "allowedUse": ["일반 의류"]}
    request.json = body
    async def noop(*a, **kw): pass
    # Account/cutover guards are covered separately. Keep all enrollment/license SQL real.
    for module in (facemarket, enrollment, asset_worker):
        monkeypatch.setattr(module, "_assert_account_open", noop)
        monkeypatch.setattr(module, "_reject_cutover_closed", noop)
    monkeypatch.setattr(facemarket, "issue_and_activate_pending_face_vc", noop)
    try:
        yield request, admin, storage
    finally:
        admin.execute(psycopg.sql.SQL("drop schema {} cascade").format(psycopg.sql.Identifier(schema)))
        admin.close()


def test_real_postgres_reopen_upload_complete_reuses_identity(pg_registration):
    request, db, storage = pg_registration
    async def scenario():
        opened = await enrollment.reopen_enrollment_photos(request, EID, UID)
        assert opened.photo_revision == 1
        image = UploadFile(io.BytesIO(storage.objects["private/face01.jpg"][0]), filename="new.jpg", headers=Headers({"content-type": "image/jpeg"}))
        await enrollment.upload_enrollment_photo(request, EID, angle=None, slot="face01", photo=image, user_id=UID)
        result = await enrollment.process_enrollment_completion(request, enrollment_id=EID, user_id=UID, session_id=None)
        assert result.status == "asset_building"
        assert db.execute("select count(*) as n from fm_biometric_enrollment_photos where storage_state='approved'").fetchone()["n"] == 2
        job = db.execute("update jobs set status='running',locked_by='test-lease' returning id::text,payload").fetchone()
        await asset_worker.run_fm_model_asset_job(request.app, {"id": job["id"], "payload": job["payload"], "user_id": UID, "lease_token": "test-lease"})
        assert db.execute("select status from jobs").fetchone()["status"] == "done"
        async with request.app.state.pool.connection() as conn:
            evidence = await facemarket._load_license_evidence(conn, UID, EID)
            assert "/revision-1/" in facemarket._checked_license_evidence(evidence)[1]
    asyncio.run(scenario())
    assert db.execute("select count(*) as n from fm_identity_verifications").fetchone()["n"] == 1
    assert db.execute("select payload from jobs").fetchone()["payload"]["photoRevision"] == 1
    assert db.execute("select count(*) as n from fm_biometric_enrollment_photos where storage_state='approved'").fetchone()["n"] == 3


def test_real_postgres_terminal_revalidation_cleans_previous_assets_and_retries(pg_registration):
    request, db, storage = pg_registration
    storage.fail_delete_for.add("private/grid_sedcard.jpg")
    async def scenario():
        await enrollment.reopen_enrollment_photos(request, EID, UID)
        await enrollment.cancel_enrollment(request, EID, UID)
        assert not db.execute("select raw_deletion_evidence from fm_biometric_enrollments").fetchone()["raw_deletion_evidence"]["quarantineDeleted"]
        assert db.execute("select count(*) as n from fm_model_assets").fetchone()["n"] == 0
        assert list(storage.objects) == ["private/grid_sedcard.jpg"]
        storage.fail_delete_for.clear()
        assert await enrollment.cleanup_terminal_enrollment(request.app, enrollment_id=EID)
    asyncio.run(scenario())
    assert db.execute("select count(*) as n from fm_biometric_enrollment_photos").fetchone()["n"] == 0
    assert db.execute("select count(*) as n from fm_biometric_enrollment_photo_cleanup").fetchone()["n"] == 0
    assert storage.objects == {}


def test_terminal_cleanup_preserves_issuance_evidence_but_cleans_other_quarantine(pg_registration):
    request, db, storage = pg_registration
    db.execute("update fm_biometric_enrollments set status='cancelled'")
    db.execute("insert into fm_licenses(id,model_id,enrollment_id,status) values(gen_random_uuid(),%s,%s,'pending')", (MID, EID))
    assert asyncio.run(enrollment.cleanup_terminal_enrollment(request.app, enrollment_id=EID)) is True
    assert db.execute("select count(*) as n from fm_model_assets").fetchone()["n"] == 2
    assert db.execute("select count(*) as n from fm_biometric_enrollment_photos").fetchone()["n"] == 3
    assert len(storage.deletes) == 15
    assert set(storage.objects) == {"private/face01.jpg", "private/face03.jpg", "private/face05.jpg", "private/face_front.jpg", "private/grid_sedcard.jpg"}


def test_real_postgres_stale_photo_job_cannot_fail_current_revision(pg_registration):
    request, db, storage = pg_registration
    db.execute("update fm_biometric_enrollments set status='asset_building',photo_revision=2")
    db.execute("update fm_models set assets_status='building'")
    job = db.execute("insert into jobs(status,locked_by) values('running','old-lease') returning id::text").fetchone()
    asyncio.run(asset_worker.run_fm_model_asset_job(request.app, {
        "id": job["id"], "user_id": UID, "lease_token": "old-lease",
        "payload": {"modelId": MID, "enrollmentId": EID, "photoRevision": 1},
    }))
    assert db.execute("select status from jobs").fetchone()["status"] == "error"
    assert db.execute("select status from fm_biometric_enrollments").fetchone()["status"] == "asset_building"
    assert db.execute("select assets_status from fm_models").fetchone()["assets_status"] == "building"
    assert storage.puts == storage.deletes == []


@pytest.mark.parametrize("winner", ["reopen", "issue"])
def test_real_postgres_reopen_and_license_creation_serialize(pg_registration, monkeypatch, winner):
    request, db, _storage = pg_registration
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        module, name = ((enrollment, "_read_registration_photos") if winner == "reopen" else (facemarket, "_find_license_by_enrollment"))
        original = getattr(module, name)
        async def hold(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(module, name, hold)
        async def reopen():
            return await enrollment.reopen_enrollment_photos(request, EID, UID)
        async def issue():
            return await facemarket.create_license(request, UID)
        first = asyncio.create_task(reopen() if winner == "reopen" else issue())
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(issue() if winner == "reopen" else reopen())
        await asyncio.sleep(0.05)
        assert not second.done(), "second writer bypassed the enrollment row lock"
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 3)
        assert isinstance(results[1], HTTPException)
        assert results[1].status_code == 409
        if winner == "reopen":
            assert results[0].status == "liveness_pending"
        else:
            # The Holder is deliberately unavailable; its pending row must still fence edits.
            assert isinstance(results[0], HTTPException) and results[0].status_code == 503
    asyncio.run(scenario())
    row = db.execute("select status,photo_revision from fm_biometric_enrollments").fetchone()
    assert row == ({"status": "liveness_pending", "photo_revision": 1} if winner == "reopen" else {"status": "vc_pending", "photo_revision": 0})
    assert db.execute("select count(*) as n from fm_licenses").fetchone()["n"] == (0 if winner == "reopen" else 1)
