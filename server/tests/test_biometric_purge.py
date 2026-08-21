import contextlib
import asyncio
import os
import types
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.services.biometric_purge import PurgeIncomplete, purge_biometric_scope


LIVE_DB_URL = os.environ.get("FACEMARKET_TEST_DATABASE_URL")


class StrictFakeR2:
    def __init__(self, keys=()):
        self.keys = set(keys)
        self.deleted = []
        self.fail_delete = set()
        self.fail_list = set()
        self.fail_head = set()

    def delete(self, key):
        if key in self.fail_delete:
            raise RuntimeError("delete_failed")
        self.deleted.append(key)
        self.keys.discard(key)

    def list_prefix(self, prefix):
        if prefix in self.fail_list:
            raise RuntimeError("list_failed")
        return sorted(key for key in self.keys if key.startswith(prefix))

    def head(self, key):
        if key in self.fail_head:
            raise RuntimeError("head_failed")
        return {"size": 1, "mime": "image/png"} if key in self.keys else None


class _NoPool:
    @contextlib.asynccontextmanager
    async def connection(self):
        raise AssertionError("database must not be touched")
        yield


def _app(*, pool=None, r2=None, r2_face=None):
    state = types.SimpleNamespace(pool=pool or _NoPool())
    if r2 is not None:
        state.r2 = r2
    if r2_face is not None:
        state.r2_face = r2_face
    return types.SimpleNamespace(state=state)


def test_rejects_missing_or_ambiguous_scope_before_storage_or_database():
    with pytest.raises(PurgeIncomplete) as missing:
        asyncio.run(purge_biometric_scope(_app(), reason="withdrawal"))
    assert missing.value.code == "invalid_scope"

    with pytest.raises(PurgeIncomplete) as ambiguous:
        asyncio.run(
            purge_biometric_scope(
                _app(),
                user_id=str(uuid.uuid4()),
                batch_id=str(uuid.uuid4()),
                reason="withdrawal",
            )
        )
    assert ambiguous.value.code == "invalid_scope"

    with pytest.raises(PurgeIncomplete) as wrong_reason:
        asyncio.run(
            purge_biometric_scope(
                _app(),
                user_id=str(uuid.uuid4()),
                reason="reverification",
            )
        )
    assert wrong_reason.value.code == "invalid_scope"


def test_requires_both_storage_clients_before_database_discovery():
    with pytest.raises(PurgeIncomplete) as exc:
        asyncio.run(
            purge_biometric_scope(
                _app(r2=StrictFakeR2()),
                user_id=str(uuid.uuid4()),
                reason="withdrawal",
            )
        )
    assert exc.value.code == "storage_unavailable"


class _LivePool:
    def __init__(self, url):
        self.url = url

    @contextlib.asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(
            self.url, row_factory=dict_row
        ) as conn:
            yield conn


def _sync(url):
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def _require_live_schema(conn):
    required = {
        "fm_biometric_enrollments": {"id", "user_id", "model_id"},
        "fm_biometric_enrollment_photos": {"enrollment_id", "r2_key"},
        "fm_biometric_enrollment_photo_cleanup": {"enrollment_id", "r2_key"},
        "fm_model_asset_cleanup": {"model_id", "r2_key"},
        "fm_models": {"reverification_batch_id", "current_enrollment_id"},
        "fm_licenses": {"reverification_batch_id", "enrollment_id"},
        "fm_model_assets": {"source_enrollment_id"},
    }
    rows = conn.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema='public' and table_name = any(%s)",
        (list(required),),
    ).fetchall()
    found = {}
    for row in rows:
        found.setdefault(row["table_name"], set()).add(row["column_name"])
    missing = {
        table: sorted(cols - found.get(table, set()))
        for table, cols in required.items()
        if cols - found.get(table, set())
    }
    if missing:
        pytest.skip(f"FACEMARKET_TEST_DATABASE_URL schema missing: {missing}")


@pytest.fixture()
def live_case():
    if not LIVE_DB_URL:
        pytest.skip("set FACEMARKET_TEST_DATABASE_URL for live purge DB test")
    user_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    model_id = str(uuid.uuid4())
    batch_id = str(uuid.uuid4())
    enrollment_id = str(uuid.uuid4())
    license_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    conn = _sync(LIVE_DB_URL)
    _require_live_schema(conn)
    try:
        conn.execute("insert into auth.users (id) values (%s)", (user_id,))
        conn.execute(
            "insert into fm_cutover_batches "
            "(id, status, target_digest, model_count, license_count, job_count, asset_count) "
            "values (%s, 'reconciling', 'test', 1, 1, 1, 1)",
            (batch_id,),
        )
        conn.execute(
            "insert into personalization_profiles "
            "(id, user_id, status, height_cm, weight_kg) values (%s, %s, 'purging', 170, 60)",
            (profile_id, user_id),
        )
        conn.execute(
            "insert into personalization_identity_verifications (user_id, cx_tx_hash, is_adult) "
            "values (%s, %s, true)",
            (user_id, f"cx-{uuid.uuid4()}"),
        )
        conn.execute(
            "insert into fm_models "
            "(id, user_id, display_name, status, ci_hash, assets_status, qc_score, "
            "assets_source_hash, reverification_batch_id) "
            "values (%s, %s, 'Test', 'reverification_required', %s, 'ready', 0.9, 'src', %s)",
            (model_id, user_id, f"ci-{uuid.uuid4()}", batch_id),
        )
        conn.execute(
            "insert into fm_biometric_enrollments "
            "(id, user_id, model_id, device_digest, consent_version, status, completed_at) "
            "values (%s, %s, %s, 'device', 'v1', 'passed', now())",
            (enrollment_id, user_id, model_id),
        )
        conn.execute(
            "update fm_models set current_enrollment_id=%s where id=%s",
            (enrollment_id, model_id),
        )
        conn.execute(
            "insert into fm_licenses "
            "(id, model_id, face_image_uri, face_image_key, face_image_digest, "
            "license_valid_until, status, enrollment_id, reverification_batch_id) "
            "values (%s, %s, '/face', %s, 'sha256-face', %s, 'revoked', %s, %s)",
            (
                license_id,
                model_id,
                f"facemarket/models/{model_id}/licenses/{license_id}/face.png",
                datetime.now(timezone.utc) + timedelta(days=1),
                enrollment_id,
                batch_id,
            ),
        )
        for angle in ("front", "side", "angle45"):
            conn.execute(
                "insert into personalization_face_photos "
                "(profile_id, angle, r2_key, image_digest, mime_type, byte_size) "
                "values (%s, %s, %s, %s, 'image/png', 1)",
                (
                    profile_id,
                    angle,
                    f"personalization/profiles/{profile_id}/faces/{angle}.png",
                    f"sha256-{angle}",
                ),
            )
        conn.execute(
            "insert into personalization_generations (profile_id, status, result_keys) "
            "values (%s, 'done', %s)",
            (profile_id, [f"personalization/{user_id}/generations/{uuid.uuid4()}/0.png"]),
        )
        conn.execute(
            "insert into fm_model_assets (model_id, view, r2_key, mime, source_enrollment_id) "
            "values (%s, 'face_front', %s, 'image/png', %s), "
            "(%s, 'grid_sedcard', %s, 'image/png', %s)",
            (
                model_id,
                f"facemarket/models/{model_id}/enrollments/{enrollment_id}/assets/face_front.png",
                enrollment_id,
                model_id,
                f"facemarket/models/{model_id}/enrollments/{enrollment_id}/assets/grid_sedcard.png",
                enrollment_id,
            ),
        )
        conn.execute(
            "insert into fm_model_asset_cleanup (model_id, r2_key, reason) values (%s, %s, 'superseded')",
            (model_id, f"facemarket/models/{model_id}/cleanup/old.png"),
        )
        conn.execute(
            "insert into fm_biometric_enrollment_photos "
            "(enrollment_id, angle, r2_key, image_digest, mime_type, byte_size) "
            "values (%s, 'front', %s, 'sha256-enroll', 'image/png', 1)",
            (enrollment_id, f"facemarket/enrollments/{enrollment_id}/quarantine/front.png"),
        )
        conn.execute(
            "insert into fm_biometric_enrollment_photo_cleanup "
            "(enrollment_id, angle, r2_key, reason) values (%s, 'side', %s, 'delete')",
            (enrollment_id, f"facemarket/enrollments/{enrollment_id}/cleanup/side.png"),
        )
        conn.execute(
            "insert into projects (id, user_id, status, title, editor_blocks, selected_mannequin_id, facemarket_license_id) "
            "values (%s, %s, 'done', 'p', %s, 'm1', %s)",
            (project_id, user_id, Json([{"src": "secret"}]), license_id),
        )
        conn.execute(
            "insert into jobs (id, user_id, project_id, kind, status, payload, result) "
            "values (%s, %s, %s, 'detail_page', 'done', %s, %s)",
            (
                job_id,
                user_id,
                project_id,
                Json({"_facemarket": {"modelId": model_id}}),
                Json({"url": f"users/{user_id}/projects/{project_id}/ai/{job_id}/x.png"}),
            ),
        )
        conn.execute(
            "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, r2_key, mime_type, "
            "byte_size, width, height, checksum, original_filename, metadata) "
            "values (%s, %s, %s, 'ai', 'private', 'r2', %s, 'image/png', 1, 1, 1, 'sum', 'x.png', %s)",
            (
                asset_id,
                user_id,
                project_id,
                f"users/{user_id}/projects/{project_id}/ai/{job_id}/{asset_id}.png",
                Json({"secret": True}),
            ),
        )
        conn.execute(
            "insert into wardrobe_images (project_id, asset_id, ai, sort_order) values (%s, %s, true, 0)",
            (project_id, asset_id),
        )
        face_keys = {
            row["r2_key"]
            for row in conn.execute(
                "select r2_key from fm_model_assets where model_id=%s "
                "union all select face_image_key from fm_licenses where id=%s "
                "union all select r2_key from personalization_face_photos where profile_id=%s "
                "union all select unnest(result_keys) from personalization_generations where profile_id=%s",
                (model_id, license_id, profile_id, profile_id),
            )
            if row["r2_key"]
        }
        face_keys.update(
            {
                f"facemarket/models/{model_id}/orphan.png",
                f"facemarket/enrollments/{enrollment_id}/orphan.png",
                f"personalization/{user_id}/generations/orphan/0.png",
            }
        )
        r2_keys = {
            f"users/{user_id}/projects/{project_id}/ai/{job_id}/{asset_id}.png",
            f"users/{user_id}/projects/{project_id}/ai/{job_id}/orphan.png",
        }
        yield types.SimpleNamespace(
            app=_app(pool=_LivePool(LIVE_DB_URL), r2=StrictFakeR2(r2_keys), r2_face=StrictFakeR2(face_keys)),
            user_id=user_id,
            batch_id=batch_id,
            model_id=model_id,
            profile_id=profile_id,
            enrollment_id=enrollment_id,
            asset_id=asset_id,
        )
    finally:
        with contextlib.suppress(Exception):
            conn.execute("delete from auth.users where id=%s", (user_id,))
        with contextlib.suppress(Exception):
            conn.execute("delete from fm_cutover_batches where id=%s", (batch_id,))
        conn.close()


def _count_refs(url, case):
    conn = _sync(url)
    try:
        return conn.execute(
            """
            select
              (select count(*) from fm_model_assets where model_id=%s)
            + (select count(*) from fm_model_asset_cleanup where model_id=%s)
            + (select count(*) from fm_biometric_enrollments where id=%s)
            + (select count(*) from personalization_face_photos where profile_id=%s)
            + (select count(*) from personalization_generations where profile_id=%s)
            + (select count(*) from personalization_identity_verifications where user_id=%s)
            + (select count(*) from assets where id=%s and r2_bucket <> 'purged')
              as c
            """,
            (
                case.model_id,
                case.model_id,
                case.enrollment_id,
                case.profile_id,
                case.profile_id,
                case.user_id,
                case.asset_id,
            ),
        ).fetchone()["c"]
    finally:
        conn.close()


def test_live_user_purge_deletes_both_buckets_then_cleans_database(live_case):
    result = asyncio.run(
        purge_biometric_scope(live_case.app, user_id=live_case.user_id, reason="withdrawal")
    )

    assert result.complete is True
    assert result.target_count == result.confirmed_absent_count
    assert result.model_count == 1
    assert result.profile_count == 1
    assert result.enrollment_count == 1
    assert live_case.app.state.r2.keys == set()
    assert live_case.app.state.r2_face.keys == set()
    assert _count_refs(LIVE_DB_URL, live_case) == 0

    second = asyncio.run(
        purge_biometric_scope(live_case.app, user_id=live_case.user_id, reason="withdrawal")
    )
    assert second.complete is True
    assert second.target_count == 0


def test_live_delete_failure_preserves_database_references_for_retry(live_case):
    failing_key = next(k for k in live_case.app.state.r2_face.keys if "face_front" in k)
    live_case.app.state.r2_face.fail_delete.add(failing_key)

    with pytest.raises(PurgeIncomplete) as exc:
        asyncio.run(
            purge_biometric_scope(live_case.app, user_id=live_case.user_id, reason="withdrawal")
        )

    assert exc.value.code == "r2_delete_failed"
    assert _count_refs(LIVE_DB_URL, live_case) > 0

    live_case.app.state.r2_face.fail_delete.clear()
    result = asyncio.run(
        purge_biometric_scope(live_case.app, user_id=live_case.user_id, reason="withdrawal")
    )
    assert result.complete is True
