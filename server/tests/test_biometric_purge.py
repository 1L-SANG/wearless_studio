import contextlib
import asyncio
import copy
import traceback
import os
import types
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app import facemarket_cutover, repo, r2 as r2_module
from app.r2 import R2Client
from app.services import biometric_purge
from app.services.biometric_purge import PurgeIncomplete, purge_biometric_scope


LIVE_DB_URL = os.environ.get("FACEMARKET_TEST_DATABASE_URL")


class StrictFakeR2:
    def __init__(self, keys=()):
        self.keys = set(keys)
        self.deleted = []
        self.purged = []
        self.fail_purge = False
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

    def purge_public_cache(self, keys):
        self.purged.append(tuple(keys))
        if self.fail_purge:
            raise RuntimeError("provider dumped bearer secret")

    def preflight_public_cache_purge(self, keys):
        return None


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


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, query, params=()):
        q = " ".join(query.lower().split())
        self.rowcount = 0
        self.rows = self.db.select(q, params or ())
        if self.rows is None:
            mutated = self.db.mutate(q, params or ())
            if isinstance(mutated, list):
                self.rows = mutated
                self.rowcount = len(mutated)
            else:
                self.rowcount = mutated
                self.rows = []

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def cursor(self):
        return FakeCursor(self.db)

    async def commit(self):
        self.db.commits += 1
        if self.db.on_commit:
            self.db.on_commit(self.db)

    async def rollback(self):
        self.db.rollbacks += 1


class FakePool:
    def __init__(self, db):
        self.db = db

    def connection(self):
        return FakeConn(self.db)


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.on_commit = None
        self.fail_select = {}
        self.fail_mutate = {}
        self.queries = []
        self.controller_locked = False
        self.controller_lock_attempts = 0
        self.controller_unlocks = 0
        self.tables = {
            "assets": [],
            "edit_sessions": [],
            "exports": [],
            "fm_biometric_enrollment_photo_cleanup": [],
            "fm_biometric_enrollment_photos": [],
            "fm_biometric_enrollments": [],
            "fm_cutover_batches": [],
            "fm_identity_verifications": [],
            "fm_licenses": [],
            "fm_model_asset_cleanup": [],
            "fm_model_assets": [],
            "fm_models": [],
            "fm_biometric_purge_manifests": [],
            "fm_biometric_purge_receipts": [],
            "fm_vc_revocation_jobs": [],
            "fm_settlements": [],
            "generation_outputs": [],
            "generation_runs": [],
            "job_events": [],
            "jobs": [],
            "matching_items": [],
            "personalization_audit_log": [],
            "personalization_consents": [],
            "personalization_face_photos": [],
            "personalization_generations": [],
            "personalization_identity_verifications": [],
            "personalization_profiles": [],
            "product_truth_assets": [],
            "projects": [],
            "wardrobe_images": [],
        }
        self.columns = {
            table: set(rows[0]) if rows else {
                "id", "user_id", "profile_id", "model_id", "license_id", "enrollment_id",
                "source_enrollment_id", "reverification_batch_id", "current_enrollment_id",
                "r2_key", "face_image_key", "face_image_digest", "result_keys", "status",
                "kind", "payload", "project_id", "job_id", "asset_id", "parent_output_id",
                "edit_session_id", "source_asset_id", "output_id", "prompt_r2_key",
                "vc_id", "vc_status_uri", "editor_blocks", "selected_mannequin_id",
                "image_asset_id", "thumbnail_asset_id", "metadata", "checksum",
                "original_filename", "byte_size", "width", "height", "r2_bucket",
                "mime_type", "deleted_at", "result", "height_cm", "weight_kg",
                "body_type", "body_type_custom", "gender", "age_range", "skin_tone",
                "hair", "clothing_size", "assets_status", "qc_score", "assets_source_hash",
                "ci_hash", "did", "cover_image_url", "display_name", "outcome",
                "target_count", "confirmed_absent_count", "model_count", "profile_count",
                "enrollment_count", "asset_count", "source_job_id", "completed_at",
                "scope_key", "target_manifest", "created_at", "updated_at",
            }
            for table, rows in self.tables.items()
        }

    def add(self, table, **row):
        self.tables[table].append(row)
        self.columns.setdefault(table, set()).update(row)
        return row

    def clone(self):
        other = FakeDB()
        other.tables = copy.deepcopy(self.tables)
        other.columns = copy.deepcopy(self.columns)
        return other

    def select(self, q, params):
        self.queries.append(q)
        for needle, message in self.fail_select.items():
            if needle in q:
                raise RuntimeError(message)
        if "information_schema.columns" in q:
            return [
                {"table_name": table, "column_name": col}
                for table, cols in self.columns.items()
                for col in cols
            ]
        if q.startswith("select target_manifest from fm_biometric_purge_manifests"):
            return [
                {"target_manifest": copy.deepcopy(row["target_manifest"])}
                for row in self.tables["fm_biometric_purge_manifests"]
                if row.get("scope_key") == params[0]
            ]
        if q.startswith("select pg_try_advisory_lock"):
            self.controller_lock_attempts += 1
            self.controller_locked = True
            return [{"locked": True}]
        if q.startswith("select pg_advisory_xact_lock"):
            return [{"locked": True}]
        if q.startswith("select pg_advisory_unlock"):
            self.controller_unlocks += 1
            self.controller_locked = False
            return [{"unlocked": True}]
        if "select id::text as id, status, target_digest" in q and "from fm_cutover_batches" in q:
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "target_digest": r["target_digest"],
                    "model_count": r.get("model_count", 0),
                    "license_count": r.get("license_count", 0),
                    "job_count": r.get("job_count", 0),
                    "asset_count": r.get("asset_count", 0),
                }
                for r in self.tables["fm_cutover_batches"]
                if r.get("id") == params[0]
            ]
        if "select id::text as id, status, started_at" in q and "from fm_cutover_batches" in q:
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "started_at": r.get("started_at"),
                    "target_digest": r["target_digest"],
                    "model_count": r.get("model_count", 0),
                    "license_count": r.get("license_count", 0),
                    "job_count": r.get("job_count", 0),
                }
                for r in self.tables["fm_cutover_batches"]
                if r.get("id") == params[0]
            ]
        if "select m.id::text as id" in q and "not exists" in q:
            return [{"id": r["id"]} for r in self.tables["fm_models"] if r.get("legacy_target", True)]
        if q.startswith("select id::text as id") and "from fm_licenses" in q and "where model_id = any" in q and "order by id" in q:
            model_ids = set(params[0])
            return [
                {"id": r["id"]}
                for r in sorted(self.tables["fm_licenses"], key=lambda row: row["id"])
                if r.get("model_id") in model_ids
            ]
        if "from jobs where metadata->>%s = %s" in q:
            batch_id = params[1]
            return [
                {"id": r["id"]}
                for r in sorted(self.tables["jobs"], key=lambda row: (row.get("created_at"), row["id"]))
                if (r.get("metadata") or {}).get(params[0]) == batch_id
            ]
        if "from jobs where id = any" in q:
            ids = set(params[0])
            return [
                _job_row(r)
                for r in sorted(self.tables["jobs"], key=lambda row: (row.get("created_at"), row["id"]))
                if r.get("id") in ids
            ]
        if "from fm_models" in q and "where reverification_batch_id=%s" in q and "count(*)" in q:
            return [{
                "count": len([
                    r for r in self.tables["fm_models"]
                    if r.get("reverification_batch_id") == params[0]
                    and r.get("status") == "reverification_required"
                ])
            }]
        if "from fm_licenses" in q and "where reverification_batch_id=%s" in q and "count(*)" in q:
            allowed = set(params[1])
            return [{
                "count": len([
                    r for r in self.tables["fm_licenses"]
                    if r.get("reverification_batch_id") == params[0]
                    and r.get("status") in allowed
                ])
            }]
        if "from fm_models" in q and "where reverification_batch_id = %s" in q and "status" in q:
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "reverification_batch_id": r.get("reverification_batch_id"),
                }
                for r in sorted(self.tables["fm_models"], key=lambda row: row["id"])
                if r.get("reverification_batch_id") == params[0]
            ]
        if "from fm_licenses" in q and "where reverification_batch_id = %s" in q and "status" in q:
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "reverification_batch_id": r.get("reverification_batch_id"),
                }
                for r in sorted(self.tables["fm_licenses"], key=lambda row: row["id"])
                if r.get("reverification_batch_id") == params[0]
            ]
        if "from fm_licenses l" in q and "for update" in q:
            model_ids = set(params[0])
            return [
                {
                    "id": r["id"],
                    "model_id": r["model_id"],
                    "status": r["status"],
                    "previous_status": r.get("previous_status"),
                    "reverification_batch_id": r.get("reverification_batch_id"),
                    "vc_id": r.get("vc_id"),
                }
                for r in sorted(self.tables["fm_licenses"], key=lambda row: (row["model_id"], row["id"]))
                if r.get("model_id") in model_ids
            ]
        if "select id::text as id, status, previous_status" in q and "from fm_models" in q:
            ids = set(params[0])
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "previous_status": r.get("previous_status"),
                    "reverification_batch_id": r.get("reverification_batch_id"),
                }
                for r in sorted(self.tables["fm_models"], key=lambda row: row["id"])
                if r.get("id") in ids
            ]
        if "select id::text as id from jobs" in q and "status='pending'" in q:
            kinds = set(params[0])
            return [
                {"id": r["id"]}
                for r in self.tables["jobs"]
                if r.get("status") == "pending" and r.get("kind") in kinds
            ]
        if "pending_count" in q and "running_count" in q and "kind = any" in q:
            kinds = set(params[0])
            return [{
                "pending_count": len([
                    r for r in self.tables["jobs"]
                    if r.get("kind") in kinds and r.get("status") == "pending"
                ]),
                "running_count": len([
                    r for r in self.tables["jobs"]
                    if r.get("kind") in kinds and r.get("status") == "running"
                ]),
            }]
        if "from personalization_profiles where user_id" in q:
            user_id = params[0]
            return [
                {"id": r["id"]}
                for r in self.tables["personalization_profiles"]
                if r.get("user_id") == user_id and r.get("status") != "purged"
            ]
        if "from fm_models where user_id" in q:
            return [{"id": r["id"]} for r in self.tables["fm_models"] if r.get("user_id") == params[0]]
        if "from fm_licenses where model_id = any" in q and "face_image_key" not in q:
            model_ids = set(params[0])
            return [{"id": r["id"]} for r in self.tables["fm_licenses"] if r.get("model_id") in model_ids]
        if "select job_count from fm_cutover_batches" in q:
            return [
                {"job_count": r.get("job_count", 0)}
                for r in self.tables["fm_cutover_batches"]
                if r.get("id") == params[0]
            ]
        if "from fm_cutover_batches where id" in q:
            return [{"status": r["status"]} for r in self.tables["fm_cutover_batches"] if r.get("id") == params[0]]
        if "from fm_models where reverification_batch_id" in q:
            return [{"id": r["id"]} for r in self.tables["fm_models"] if r.get("reverification_batch_id") == params[0]]
        if "from fm_licenses where reverification_batch_id" in q:
            return [
                {"id": r["id"], "model_id": r["model_id"]}
                for r in self.tables["fm_licenses"]
                if r.get("reverification_batch_id") == params[0]
            ]
        if q.startswith("select") and "from fm_biometric_enrollments where" in q:
            user_id = params[0] if params else None
            model_ids = set(params[-1]) if params and isinstance(params[-1], list) else set()
            return [
                {"id": r["id"]}
                for r in self.tables["fm_biometric_enrollments"]
                if (user_id is not None and r.get("user_id") == user_id)
                or (model_ids and r.get("model_id") in model_ids)
            ]
        if "select current_enrollment_id::text as id from fm_models" in q:
            ids = set(params[0])
            return [
                {"id": r.get("current_enrollment_id")}
                for r in self.tables["fm_models"]
                if r.get("id") in ids and r.get("current_enrollment_id")
            ]
        if "select enrollment_id::text as id from fm_licenses" in q:
            ids = set(params[0])
            return [
                {"id": r.get("enrollment_id")}
                for r in self.tables["fm_licenses"]
                if r.get("id") in ids and r.get("enrollment_id")
            ]
        if "select source_enrollment_id::text as id from fm_model_assets" in q:
            ids = set(params[0])
            return [
                {"id": r.get("source_enrollment_id")}
                for r in self.tables["fm_model_assets"]
                if r.get("model_id") in ids and r.get("source_enrollment_id")
            ]
        if "from fm_models where id = any" in q and "status = 'verified'" in q:
            ids = set(params[0])
            return [{"?column?": 1} for r in self.tables["fm_models"] if r.get("id") in ids and r.get("status") == "verified"][:1]
        if "from fm_licenses where id = any" in q and "status = 'active'" in q:
            ids = set(params[0])
            return [{"?column?": 1} for r in self.tables["fm_licenses"] if r.get("id") in ids and r.get("status") == "active"][:1]
        if "from fm_licenses l" in q and "fm_vc_revocation_jobs" in q:
            ids = set(params[0])
            missing = [
                r
                for r in self.tables["fm_licenses"]
                if r.get("id") in ids
                and str(r.get("vc_id") or "").strip()
                and not any(j.get("vc_id") == r.get("vc_id") for j in self.tables["fm_vc_revocation_jobs"])
            ]
            return [{"missing_count": len(missing)}]
        if "from jobs where kind='personalization_generation'" in q:
            ids = set(params[0])
            return [
                {"?column?": 1}
                for r in self.tables["jobs"]
                if r.get("kind") == "personalization_generation"
                and r.get("status") in {"pending", "running"}
                and (r.get("payload") or {}).get("profileId") in ids
            ][:1]
        if "metadata->>'facemarketmanifestbatchid'" in q:
            batch_id = params[0]
            return [
                _job_row(r)
                for r in self.tables["jobs"]
                if (r.get("metadata") or {}).get("facemarketManifestBatchId") == batch_id
            ]
        if "with scoped_jobs as" in q:
            model_ids = set(params[0])
            license_ids = set(params[1])
            asset_model_ids = set(params[2])
            settlement_license_ids = set(params[3])
            rows_by_id = {}
            for row in self.tables["jobs"]:
                payload = row.get("payload") or {}
                snapshot = payload.get("_facemarket") or {}
                if (
                    row.get("kind") in {"detail_page", "editor_image"}
                    and (
                        snapshot.get("modelId") in model_ids
                        or snapshot.get("licenseId") in license_ids
                    )
                ):
                    rows_by_id[row["id"]] = _job_row(row)
                if (
                    row.get("kind") == "fm_model_asset_build"
                    and payload.get("modelId") in asset_model_ids
                ):
                    rows_by_id[row["id"]] = _job_row(row)
            settled_job_ids = {
                row.get("job_id")
                for row in self.tables["fm_settlements"]
                if row.get("license_id") in settlement_license_ids
            }
            for row in self.tables["jobs"]:
                if row.get("id") in settled_job_ids:
                    rows_by_id.setdefault(row["id"], _job_row(row))
            if "join projects p" in q:
                fallback_license_ids = set(params[4])
                project_ids = {
                    row["id"]
                    for row in self.tables["projects"]
                    if row.get("facemarket_license_id") in fallback_license_ids
                }
                for row in self.tables["jobs"]:
                    if (
                        row.get("project_id") in project_ids
                        and row.get("kind") in {"detail_page", "editor_image"}
                    ):
                        rows_by_id.setdefault(row["id"], _job_row(row))
            return sorted(rows_by_id.values(), key=lambda row: (row.get("created_at"), row["id"]))
        if "from jobs where kind = any" in q:
            kinds, model_ids = set(params[0]), set(params[1])
            return [
                _job_row(r)
                for r in self.tables["jobs"]
                if r.get("kind") in kinds and ((r.get("payload") or {}).get("_facemarket") or {}).get("modelId") in model_ids
            ]
        if "where kind = 'fm_model_asset_build'" in q:
            ids = set(params[0])
            return [_job_row(r) for r in self.tables["jobs"] if r.get("kind") == "fm_model_asset_build" and (r.get("payload") or {}).get("modelId") in ids]
        if "from fm_settlements s join jobs j" in q:
            license_ids = set(params[0])
            job_ids = {r.get("job_id") for r in self.tables["fm_settlements"] if r.get("license_id") in license_ids}
            return [_job_row(r) for r in self.tables["jobs"] if r.get("id") in job_ids]
        if "from jobs j join projects p" in q:
            license_ids = set(params[0])
            project_ids = {r["id"] for r in self.tables["projects"] if r.get("facemarket_license_id") in license_ids}
            return [_job_row(r) for r in self.tables["jobs"] if r.get("project_id") in project_ids and r.get("kind") in {"detail_page", "editor_image"}]
        if "select face_image_key as k from fm_licenses" in q:
            ids = set(params[0])
            return [{"k": r.get("face_image_key")} for r in self.tables["fm_licenses"] if r.get("id") in ids and r.get("face_image_key")]
        if "select r2_key as k from fm_model_assets" in q:
            ids = set(params[0])
            return [{"k": r.get("r2_key")} for r in self.tables["fm_model_assets"] if r.get("model_id") in ids]
        if "select r2_key as k from fm_model_asset_cleanup" in q:
            ids = set(params[0])
            return [{"k": r.get("r2_key")} for r in self.tables["fm_model_asset_cleanup"] if r.get("model_id") in ids]
        if q.startswith("select") and "from fm_biometric_enrollment_photos" in q:
            ids = set(params[0])
            return [{"k": r.get("r2_key")} for r in self.tables["fm_biometric_enrollment_photos"] if r.get("enrollment_id") in ids]
        if q.startswith("select") and "from fm_biometric_enrollment_photo_cleanup" in q:
            ids = set(params[0])
            return [{"k": r.get("r2_key")} for r in self.tables["fm_biometric_enrollment_photo_cleanup"] if r.get("enrollment_id") in ids]
        if q.startswith("select") and "from personalization_face_photos" in q:
            ids = set(params[0])
            return [{"k": r.get("r2_key")} for r in self.tables["personalization_face_photos"] if r.get("profile_id") in ids]
        if "unnest(result_keys)" in q:
            ids = set(params[0])
            return [{"k": k} for r in self.tables["personalization_generations"] if r.get("profile_id") in ids for k in r.get("result_keys", [])]
        if "from generation_runs" in q and "prompt_r2_key as k" in q:
            ids = set(params[0])
            return [{"k": r.get("prompt_r2_key")} for r in self.tables["generation_runs"] if r.get("job_id") in ids and r.get("prompt_r2_key")]
        if "from edit_sessions" in q and "prompt_r2_key as k" in q:
            ids = set(params[0])
            return [{"k": r.get("prompt_r2_key")} for r in self.tables["edit_sessions"] if r.get("job_id") in ids and r.get("prompt_r2_key")]
        if "from assets where project_id = any" in q and "r2_key = any" in q:
            project_ids, keys = set(params[0]), set(params[1])
            return [{"id": r["id"], "k": r["r2_key"]} for r in self.tables["assets"] if r.get("project_id") in project_ids and r.get("r2_key") in keys]
        if "from assets where" in q and "r2_key like" in q:
            prefixes = [p[:-1] for p in params]
            return [
                {"id": r["id"], "k": r["r2_key"]}
                for r in self.tables["assets"]
                if any((r.get("r2_key") or "").startswith(prefix) for prefix in prefixes)
            ]
        if "select id::text as id, prompt_r2_key as prompt_r2_key from generation_runs" in q:
            job_ids = set(params[0])
            return [{"id": r["id"], "prompt_r2_key": r.get("prompt_r2_key")} for r in self.tables["generation_runs"] if r.get("job_id") in job_ids]
        if "select id::text as id, asset_id::text as asset_id" in q and "from generation_outputs" in q:
            run_ids, output_ids, parent_ids, session_ids, asset_ids = map(set, params)
            return [
                _output_row(r)
                for r in self.tables["generation_outputs"]
                if r.get("generation_run_id") in run_ids
                or r.get("id") in output_ids
                or r.get("parent_output_id") in parent_ids
                or r.get("edit_session_id") in session_ids
                or r.get("asset_id") in asset_ids
            ]
        if "select id::text as id, source_asset_id::text as source_asset_id" in q and "from edit_sessions" in q:
            job_ids, parent_ids, asset_ids, output_ids = map(set, params)
            return [
                _session_row(r)
                for r in self.tables["edit_sessions"]
                if r.get("job_id") in job_ids
                or r.get("parent_output_id") in parent_ids
                or r.get("source_asset_id") in asset_ids
                or r.get("output_id") in output_ids
            ]
        if "select id::text as id, r2_key as k from assets where id = any" in q:
            ids = set(params[0])
            return [{"id": r["id"], "k": r["r2_key"]} for r in self.tables["assets"] if r.get("id") in ids]
        return None

    def mutate(self, q, params):
        for needle, message in self.fail_mutate.items():
            if needle in q:
                raise RuntimeError(message)
        if q.startswith("insert into fm_biometric_purge_manifests"):
            self.add(
                "fm_biometric_purge_manifests",
                scope_key=params[0],
                target_manifest=copy.deepcopy(params[1].obj),
                created_at="now",
                updated_at="now",
            )
            return 1
        if q.startswith("update fm_biometric_purge_manifests set target_manifest"):
            for row in self.tables["fm_biometric_purge_manifests"]:
                if row.get("scope_key") == params[1]:
                    row["target_manifest"] = copy.deepcopy(params[0].obj)
                    row["updated_at"] = "now"
                    return 1
            return 0
        if q.startswith("delete from fm_biometric_purge_manifests"):
            return _delete_where_eq(
                self.tables["fm_biometric_purge_manifests"], "scope_key", params[0]
            )
        if q.startswith("update fm_cutover_batches") and "set status = 'draining'" in q:
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == params[0] and row.get("status") == "approved":
                    row["status"] = "draining"
                    row["started_at"] = row.get("started_at") or "now"
                    return [{"id": row["id"]}]
            return []
        if q.startswith("update fm_cutover_batches") and "set status='completed'" in q:
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == params[0] and row.get("status") == "reconciling":
                    row["status"] = "completed"
                    row["last_error_code"] = None
            return 1
        if q.startswith("update fm_cutover_batches") and "set status='failed'" in q:
            error_code, batch_id = params
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == batch_id and row.get("status") in {"draining", "applying", "reconciling", "failed"}:
                    row["status"] = "failed"
                    row["last_error_code"] = error_code
            return 1
        if q.startswith("update fm_cutover_batches set status=%s"):
            status, batch_id = params
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == batch_id:
                    row["status"] = status
            return 1
        if q.startswith("update fm_cutover_batches") and "set status = case when status = 'draining'" in q:
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == params[0] and row.get("status") in {"draining", "applying"}:
                    if row["status"] == "draining":
                        row["status"] = "applying"
            return 1
        if q.startswith("update fm_cutover_batches") and "set status='reconciling'" in q:
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == params[0] and row.get("status") == "applying":
                    row["status"] = "reconciling"
                    return 1
            return 0
        if q.startswith("update fm_cutover_batches") and "last_error_code='cutover_resume_state_invalid'" in q:
            for row in self.tables["fm_cutover_batches"]:
                if row.get("id") == params[0] and row.get("status") == "failed":
                    row["last_error_code"] = "cutover_resume_state_invalid"
            return 1
        if q.startswith("update fm_licenses") and "previous_status=coalesce" in q and "reverification_batch_id" in q:
            batch_id, ids = params
            count = 0
            for row in self.tables["fm_licenses"]:
                if row.get("id") in set(ids):
                    row["previous_status"] = row.get("previous_status") or row["status"]
                    row["reverification_batch_id"] = row.get("reverification_batch_id") or batch_id
                    if row["status"] in {"pending", "active"}:
                        row["status"] = "reverification_required"
                    count += 1
            return count
        if q.startswith("update fm_models") and "previous_status=coalesce" in q and "reverification_batch_id" in q:
            batch_id, ids = params
            count = 0
            for row in self.tables["fm_models"]:
                if row.get("id") in set(ids):
                    row["previous_status"] = row.get("previous_status") or row["status"]
                    row["reverification_batch_id"] = row.get("reverification_batch_id") or batch_id
                    if row["status"] in {"pending", "verified"}:
                        row["status"] = "reverification_required"
                    count += 1
            return count
        if q.startswith("insert into fm_vc_revocation_jobs"):
            license_id, model_id, vc_id = params
            if not any(row.get("vc_id") == vc_id for row in self.tables["fm_vc_revocation_jobs"]):
                self.add(
                    "fm_vc_revocation_jobs",
                    license_id=license_id,
                    model_id=model_id,
                    vc_id=vc_id,
                    status="pending",
                )
                return 1
            return 0
        if q.startswith("update jobs set metadata = metadata ||"):
            metadata, job_id = params
            for row in self.tables["jobs"]:
                if row.get("id") == job_id:
                    row["metadata"] = {**(row.get("metadata") or {}), **dict(metadata)}
            return 1
        if q.startswith("update matching_items set image_asset_id=null"):
            return _null(self.tables["matching_items"], "image_asset_id", params[0])
        if q.startswith("update matching_items set thumbnail_asset_id=null"):
            return _null(self.tables["matching_items"], "thumbnail_asset_id", params[0])
        if q.startswith("update exports set asset_id=null"):
            return _null(self.tables["exports"], "asset_id", params[0])
        if q.startswith("update product_truth_assets set asset_id=null"):
            return _null(self.tables["product_truth_assets"], "asset_id", params[0])
        if q.startswith("update generation_outputs set asset_id=null"):
            return _null(self.tables["generation_outputs"], "asset_id", params[0])
        if q.startswith("update edit_sessions set source_asset_id=null"):
            return _null(self.tables["edit_sessions"], "source_asset_id", params[0])
        if q.startswith("delete from wardrobe_images"):
            return _delete_where_in(self.tables["wardrobe_images"], "asset_id", params[0])
        if q.startswith("update assets set r2_key='purged/'"):
            count = 0
            for row in self.tables["assets"]:
                if row.get("id") in set(params[0]):
                    row.update({
                        "r2_key": f"purged/{row['id']}",
                        "r2_bucket": "purged",
                        "mime_type": "application/octet-stream",
                        "checksum": None,
                        "metadata": {},
                        "original_filename": None,
                        "byte_size": None,
                        "width": None,
                        "height": None,
                        "deleted_at": row.get("deleted_at") or "now",
                    })
                    count += 1
            return count
        if q.startswith("update projects set editor_blocks=null"):
            count = 0
            for row in self.tables["projects"]:
                if row.get("id") in set(params[0]):
                    row["editor_blocks"] = None
                    row["selected_mannequin_id"] = None
                    count += 1
            return count
        if q.startswith("update jobs set result=null"):
            return _null(self.tables["jobs"], "result", params[0], key="id")
        if q.startswith("update job_events set payload"):
            count = 0
            for row in self.tables["job_events"]:
                if row.get("job_id") in set(params[0]):
                    row["payload"] = {}
                    count += 1
            return count
        if q.startswith("update generation_runs set prompt_r2_key=null"):
            key = "id" if "where id = any" in q else "job_id"
            return _null(self.tables["generation_runs"], "prompt_r2_key", params[0], key=key)
        if q.startswith("update edit_sessions set prompt_r2_key=null"):
            key = "id" if "where id = any" in q else "job_id"
            return _null(self.tables["edit_sessions"], "prompt_r2_key", params[0], key=key)
        if q.startswith("delete from fm_model_assets"):
            return _delete_where_in(self.tables["fm_model_assets"], "model_id", params[0])
        if q.startswith("delete from fm_model_asset_cleanup"):
            return _delete_where_in(self.tables["fm_model_asset_cleanup"], "model_id", params[0])
        if q.startswith("update fm_models set"):
            count = 0
            for row in self.tables["fm_models"]:
                if row.get("id") in set(params[0]):
                    row.update({"assets_status": "none", "qc_score": None, "assets_source_hash": None, "current_enrollment_id": None})
                    if "display_name='삭제된 모델'" in q:
                        row.update({
                            "status": "suspended",
                            "user_id": None,
                            "ci_hash": None,
                            "did": None,
                            "cover_image_url": None,
                            "display_name": "삭제된 모델",
                        })
                    count += 1
            return count
        if q.startswith("update fm_licenses set"):
            count = 0
            for row in self.tables["fm_licenses"]:
                if row.get("id") in set(params[0]):
                    row.update({"face_image_key": None, "face_image_digest": None, "enrollment_id": None})
                    if "status='revoked'" in q:
                        row["status"] = "revoked"
                    count += 1
            return count
        if q.startswith("delete from fm_biometric_enrollment_photo_cleanup"):
            return _delete_where_in(self.tables["fm_biometric_enrollment_photo_cleanup"], "enrollment_id", params[0])
        if q.startswith("delete from fm_biometric_enrollments"):
            _delete_where_in(self.tables["fm_biometric_enrollment_photos"], "enrollment_id", params[0])
            return _delete_where_in(self.tables["fm_biometric_enrollments"], "id", params[0])
        if q.startswith("delete from personalization_face_photos"):
            return _delete_where_in(self.tables["personalization_face_photos"], "profile_id", params[0])
        if q.startswith("delete from personalization_generations"):
            return _delete_where_in(self.tables["personalization_generations"], "profile_id", params[0])
        if q.startswith("delete from personalization_identity_verifications"):
            return _delete_where_eq(self.tables["personalization_identity_verifications"], "user_id", params[0])
        if q.startswith("delete from fm_identity_verifications"):
            return _delete_where_in(self.tables["fm_identity_verifications"], "model_id", params[0])
        if q.startswith("delete from personalization_consents"):
            return _delete_where_eq(self.tables["personalization_consents"], "user_id", params[0])
        if q.startswith("delete from personalization_audit_log"):
            return _delete_where_eq(self.tables["personalization_audit_log"], "user_id", params[0])
        if q.startswith("update profiles set"):
            return 0
        if q.startswith("insert into fm_biometric_purge_receipts"):
            receipt = {
                "id": f"receipt-{len(self.tables['fm_biometric_purge_receipts']) + 1}",
                "source_job_id": params[0],
                "reason": "account_delete",
                "outcome": "ready_for_identity_delete",
                "target_count": params[1],
                "confirmed_absent_count": params[2],
                "model_count": params[3],
                "profile_count": params[4],
                "enrollment_count": params[5],
                "asset_count": params[6],
                "completed_at": "now",
            }
            self.tables["fm_biometric_purge_receipts"].append(receipt)
            return 1
        if q.startswith("update personalization_profiles set"):
            count = 0
            for row in self.tables["personalization_profiles"]:
                if row.get("id") in set(params[0]):
                    for col in ("height_cm", "weight_kg", "body_type", "body_type_custom", "gender", "age_range", "skin_tone", "hair", "clothing_size"):
                        row[col] = None
                    row["status"] = "purged"
                    row["purged_at"] = "now"
                    count += 1
            return count
        raise AssertionError(f"unhandled SQL: {q}")


def _job_row(row):
    return {k: row.get(k) for k in ("id", "user_id", "project_id", "status", "kind", "created_at")}


def _output_row(row):
    return {
        "id": row.get("id"),
        "asset_id": row.get("asset_id"),
        "parent_output_id": row.get("parent_output_id"),
        "edit_session_id": row.get("edit_session_id"),
    }


def _session_row(row):
    return {
        "id": row.get("id"),
        "source_asset_id": row.get("source_asset_id"),
        "parent_output_id": row.get("parent_output_id"),
        "output_id": row.get("output_id"),
        "prompt_r2_key": row.get("prompt_r2_key"),
    }


def _null(rows, col, ids, *, key=None):
    ids = set(ids)
    key = key or col
    count = 0
    for row in rows:
        if row.get(key) in ids:
            row[col] = None
            count += 1
    return count


def _delete_where_in(rows, col, ids):
    ids = set(ids)
    before = len(rows)
    rows[:] = [row for row in rows if row.get(col) not in ids]
    return before - len(rows)


def _delete_where_eq(rows, col, value):
    before = len(rows)
    rows[:] = [row for row in rows if row.get(col) != value]
    return before - len(rows)


def test_fake_facemarket_scope_job_discovery_uses_only_canonical_evidence(caplog):
    db = FakeDB()
    model = "model-a"
    license_id = "license-a"
    stale_project = "project-stale"
    for status in ("pending", "running", "done", "error", "cancelled"):
        db.add(
            "jobs",
            id=f"snapshot-model-{status}",
            user_id="user-a",
            project_id="project-a",
            kind="detail_page",
            status=status,
            created_at=f"2026-08-21T00:00:0{len(db.tables['jobs'])}Z",
            payload={"_facemarket": {"modelId": model, "licenseId": "other-license"}},
            result={"secret": "must-not-return"},
        )
    db.add(
        "jobs",
        id="snapshot-license",
        user_id="user-a",
        project_id="project-b",
        kind="editor_image",
        status="done",
        created_at="2026-08-21T00:00:10Z",
        payload={"_facemarket": {"modelId": "other-model", "licenseId": license_id}},
    )
    db.add(
        "jobs",
        id="flat-detail-ignored",
        user_id="user-a",
        project_id="project-flat",
        kind="detail_page",
        status="done",
        created_at="2026-08-21T00:00:11Z",
        payload={"modelId": model},
    )
    db.add(
        "jobs",
        id="asset-build",
        user_id="user-a",
        project_id=None,
        kind="fm_model_asset_build",
        status="error",
        created_at="2026-08-21T00:00:12Z",
        payload={"modelId": model},
    )
    db.add(
        "jobs",
        id="personalization-ignored",
        user_id="user-a",
        project_id=None,
        kind="personalization_generation",
        status="done",
        created_at="2026-08-21T00:00:13Z",
        payload={"profileId": model},
    )
    db.add(
        "jobs",
        id="settled-snapshotless",
        user_id="user-a",
        project_id="project-settled",
        kind="detail_page",
        status="done",
        created_at="2026-08-21T00:00:14Z",
        payload={},
    )
    db.add("fm_settlements", job_id="settled-snapshotless", license_id=license_id)
    db.add(
        "jobs",
        id="project-fallback",
        user_id="user-a",
        project_id=stale_project,
        kind="editor_image",
        status="done",
        created_at="2026-08-21T00:00:15Z",
        payload={},
    )
    db.add("projects", id=stale_project, facemarket_license_id=license_id)
    db.add(
        "jobs",
        id="overlap",
        user_id="user-a",
        project_id=stale_project,
        kind="detail_page",
        status="done",
        created_at="2026-08-21T00:00:16Z",
        payload={"_facemarket": {"modelId": model, "licenseId": license_id}},
    )
    db.add("fm_settlements", job_id="overlap", license_id=license_id)

    rows = asyncio.run(
        repo.list_facemarket_scope_jobs(
            FakeConn(db),
            model_ids=(model,),
            license_ids=(license_id,),
        )
    )

    assert [row["id"] for row in rows] == [
        "snapshot-model-pending",
        "snapshot-model-running",
        "snapshot-model-done",
        "snapshot-model-error",
        "snapshot-model-cancelled",
        "snapshot-license",
        "asset-build",
        "settled-snapshotless",
        "overlap",
    ]
    assert all(set(row) == {"id", "user_id", "project_id", "kind", "status", "created_at"} for row in rows)
    assert "flat-detail-ignored" not in {row["id"] for row in rows}
    assert "personalization-ignored" not in {row["id"] for row in rows}
    assert "project-fallback" not in {row["id"] for row in rows}
    sql = " ".join(db.queries)
    assert "for update" not in sql
    assert "skip locked" not in sql
    assert "payload ->> 'modelid'" not in sql
    assert "analyses.selectedmodelid" not in sql
    assert caplog.text == ""

    fallback_rows = asyncio.run(
        repo.list_facemarket_scope_jobs(
            FakeConn(db),
            model_ids=(model,),
            license_ids=(license_id,),
            initial_legacy_project_fallback=True,
        )
    )
    assert [row["id"] for row in fallback_rows].count("overlap") == 1
    assert "project-fallback" in {row["id"] for row in fallback_rows}


def test_fake_purge_uses_shared_facemarket_scope_job_discovery_twice(monkeypatch):
    ctx = _fake_case()
    calls = []

    async def fake_list(conn, *, model_ids, license_ids=(), initial_legacy_project_fallback=False):
        calls.append(
            {
                "model_ids": model_ids,
                "license_ids": license_ids,
                "initial_legacy_project_fallback": initial_legacy_project_fallback,
            }
        )
        return [_job_row(ctx.db.tables["jobs"][0])]

    monkeypatch.setattr(biometric_purge.repo, "list_facemarket_scope_jobs", fake_list)

    _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert calls == [
        {
            "model_ids": (ctx.model,),
            "license_ids": (ctx.license,),
            "initial_legacy_project_fallback": False,
        },
        {
            "model_ids": (ctx.model,),
            "license_ids": (ctx.license,),
            "initial_legacy_project_fallback": False,
        },
    ]


def test_fake_batch_purge_uses_manifest_tagged_jobs_not_project_fallback(monkeypatch):
    """Break caught: initial cutover purge rediscovers a different job after project pointer drift."""
    ctx = _fake_case()
    ctx.db.tables["fm_cutover_batches"][0]["job_count"] = 2
    ctx.db.tables["jobs"][0]["metadata"] = {"facemarketManifestBatchId": ctx.batch}
    ctx.db.add(
        "jobs",
        id="legacy-tagged-error",
        user_id=ctx.user,
        project_id="project-drifted",
        kind="editor_image",
        status="error",
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc) + timedelta(seconds=1),
        payload={},
        result={},
        metadata={"facemarketManifestBatchId": ctx.batch},
    )
    calls = []

    async def forbidden_helper(*_args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(biometric_purge.repo, "list_facemarket_scope_jobs", forbidden_helper)
    schema = {
        "fm_cutover_batches": {"status", "job_count"},
        "fm_models": {"reverification_batch_id"},
        "fm_licenses": {"reverification_batch_id"},
        "jobs": {"metadata"},
    }
    scope = {
        "user_id": None,
        "batch_id": ctx.batch,
        "profile_ids": set(),
        "model_ids": {ctx.model},
        "license_ids": {ctx.license},
    }

    rows = asyncio.run(biometric_purge._derived_jobs(FakeConn(ctx.db), schema, scope))

    assert [row["id"] for row in rows] == [ctx.job, "legacy-tagged-error"]
    assert calls == []
    assert any("facemarketmanifestbatchid" in q for q in ctx.db.queries)


class StickyFakeR2(StrictFakeR2):
    def __init__(self, keys=()):
        super().__init__(keys)
        self.sticky = set()
        self.fail_after_delete = set()
        self.fail_delete_message = None
        self.fail_list_message = None
        self.fail_head_message = None

    def delete(self, key):
        if key in self.fail_delete:
            raise RuntimeError(self.fail_delete_message or f"delete_failed:{key}")
        self.deleted.append(key)
        if key not in self.sticky:
            self.keys.discard(key)

    def list_prefix(self, prefix):
        if prefix in self.fail_list:
            raise RuntimeError(self.fail_list_message or f"list_failed:{prefix}")
        if self.deleted and prefix in self.fail_after_delete:
            raise RuntimeError(self.fail_list_message or f"list_failed:{prefix}")
        return super().list_prefix(prefix)

    def head(self, key):
        if key in self.fail_head:
            raise RuntimeError(self.fail_head_message or f"head_failed:{key}")
        return super().head(key)


def _fake_case():
    db = FakeDB()
    user = "user-a"
    other = "user-b"
    profile = "profile-a"
    other_profile = "profile-b"
    model = "model-a"
    other_model = "model-b"
    batch = "batch-a"
    other_batch = "batch-b"
    enrollment = "enroll-a"
    license_id = "license-a"
    project = "project-a"
    job = "job-a"
    run = "run-a"
    output = "output-a"
    child_output = "output-child"
    session = "session-a"
    asset = "asset-a"
    child_asset = "asset-child"
    source_asset = "asset-source"
    shared_key = f"facemarket/models/{model}/shared.png"
    db.add("fm_cutover_batches", id=batch, status="reconciling")
    db.add("fm_cutover_batches", id=other_batch, status="reconciling")
    db.add("personalization_profiles", id=profile, user_id=user, status="purging", height_cm=170, weight_kg=60)
    db.add("personalization_profiles", id=other_profile, user_id=other, status="purging", height_cm=180, weight_kg=70)
    db.add("personalization_identity_verifications", id="piv-a", user_id=user, cx_tx_hash="cx-a")
    db.add("personalization_consents", id="consent-a", user_id=user, profile_id=profile)
    db.add("personalization_audit_log", id="audit-a", user_id=user, profile_id=profile, detail={"kept": True})
    db.add(
        "fm_models",
        id=model,
        user_id=user,
        status="reverification_required",
        current_enrollment_id=enrollment,
        reverification_batch_id=batch,
        assets_status="ready",
        qc_score=0.9,
        assets_source_hash="src",
        ci_hash="ci-a",
        did="did:a",
        cover_image_url="/cover-a.png",
        display_name="모델A",
    )
    db.add("fm_models", id=other_model, user_id=other, status="reverification_required", current_enrollment_id="enroll-b", reverification_batch_id=other_batch, assets_status="ready", qc_score=0.8, assets_source_hash="other")
    db.add("fm_identity_verifications", id="fmiv-a", model_id=model, fields={"birthYear": "1990"})
    db.add("fm_biometric_enrollments", id=enrollment, user_id=user, model_id=model)
    db.add("fm_licenses", id=license_id, model_id=model, status="revoked", face_image_key=f"facemarket/models/{model}/licenses/{license_id}/face.png", face_image_digest="sha256-face", enrollment_id=enrollment, reverification_batch_id=batch, vc_id="vc-a", vc_status_uri="holder/vc-a")
    db.add("fm_model_assets", model_id=model, view="face_front", r2_key=f"facemarket/models/{model}/enrollments/{enrollment}/assets/face_front.png", source_enrollment_id=enrollment)
    db.add("fm_model_assets", model_id=model, view="grid_sedcard", r2_key=shared_key, source_enrollment_id=enrollment)
    db.add("fm_model_asset_cleanup", model_id=model, r2_key=f"facemarket/models/{model}/cleanup/old.png")
    db.add("fm_biometric_enrollment_photos", enrollment_id=enrollment, angle="front", r2_key=f"facemarket/enrollments/{enrollment}/quarantine/front.png")
    db.add("fm_biometric_enrollment_photo_cleanup", enrollment_id=enrollment, angle="side", r2_key=f"facemarket/enrollments/{enrollment}/cleanup/side.png")
    for angle in ("front", "side", "angle45"):
        db.add("personalization_face_photos", profile_id=profile, angle=angle, r2_key=f"personalization/profiles/{profile}/faces/{angle}.png", image_digest=f"sha256-{angle}")
    db.add("personalization_face_photos", profile_id=other_profile, angle="front", r2_key=f"personalization/profiles/{other_profile}/faces/front.png", image_digest="sha256-other")
    db.add("personalization_generations", profile_id=profile, result_keys=[f"personalization/{user}/generations/gen-a/0.png"])
    db.add("personalization_generations", profile_id=other_profile, result_keys=[f"personalization/{other}/generations/gen-b/0.png"])
    db.add("projects", id=project, user_id=user, facemarket_license_id=license_id, editor_blocks=[{"src": "secret"}], selected_mannequin_id="m1")
    db.add("jobs", id=job, user_id=user, project_id=project, kind="detail_page", status="done", payload={"_facemarket": {"modelId": model}}, result={"url": "secret"})
    db.add("job_events", id="event-a", job_id=job, payload={"url": "secret"})
    db.add("fm_settlements", id="settlement-a", job_id=job, license_id=license_id)
    db.add("generation_runs", id=run, job_id=job, project_id=project, user_id=user, prompt_r2_key=f"users/{user}/projects/{project}/ai/{job}/prompt.txt")
    db.add("generation_outputs", id=output, generation_run_id=run, project_id=project, asset_id=asset, parent_output_id=None, edit_session_id=None)
    db.add("generation_outputs", id=child_output, generation_run_id=None, project_id=project, asset_id=child_asset, parent_output_id=output, edit_session_id=session)
    db.add("edit_sessions", id=session, job_id=None, project_id=project, parent_output_id=output, source_asset_id=source_asset, output_id=child_output, prompt_r2_key=f"users/{user}/projects/{project}/ai/{job}/edit-prompt.txt")
    for aid, key in (
        (asset, f"users/{user}/projects/{project}/ai/{job}/{asset}.png"),
        (child_asset, f"users/{user}/projects/{project}/ai/other-job/{child_asset}.png"),
        (source_asset, f"users/{user}/projects/{project}/uploads/{source_asset}.png"),
    ):
        db.add("assets", id=aid, user_id=user, project_id=project, source="ai", visibility="private", r2_bucket="r2", r2_key=key, mime_type="image/png", checksum="sum", metadata={"secret": True}, original_filename="x.png", byte_size=1, width=1, height=1, deleted_at=None)
    db.add("wardrobe_images", id="wardrobe-a", project_id=project, asset_id=asset)
    db.add("wardrobe_images", id="wardrobe-child", project_id=project, asset_id=child_asset)
    db.add("matching_items", id="match-a", image_asset_id=asset, thumbnail_asset_id=child_asset)
    db.add("exports", id="export-a", asset_id=asset)
    db.add("product_truth_assets", id="truth-a", asset_id=child_asset)
    face_keys = {r["r2_key"] for table in ("fm_model_assets", "fm_model_asset_cleanup", "fm_biometric_enrollment_photos", "fm_biometric_enrollment_photo_cleanup") for r in db.tables[table]}
    face_keys |= {db.tables["fm_licenses"][0]["face_image_key"], shared_key, f"facemarket/models/{model}/orphan.png", f"facemarket/enrollments/{enrollment}/orphan.png"}
    face_keys |= {r["r2_key"] for r in db.tables["personalization_face_photos"]}
    face_keys |= {f"personalization/{user}/generations/gen-a/0.png", f"personalization/{user}/generations/orphan/0.png"}
    r2_keys = {r["r2_key"] for r in db.tables["assets"] if r["user_id"] == user}
    r2_keys |= {shared_key, f"users/{user}/projects/{project}/ai/{job}/orphan.png", f"users/{user}/projects/{project}/ai/{job}/prompt.txt", f"users/{user}/projects/{project}/ai/{job}/edit-prompt.txt"}
    ctx = types.SimpleNamespace(
        db=db,
        user=user,
        other=other,
        profile=profile,
        other_profile=other_profile,
        model=model,
        other_model=other_model,
        batch=batch,
        other_batch=other_batch,
        enrollment=enrollment,
        license=license_id,
        project=project,
        job=job,
        asset=asset,
        child_asset=child_asset,
        source_asset=source_asset,
        shared_key=shared_key,
    )
    ctx.r2 = StickyFakeR2(r2_keys)
    ctx.r2_face = StickyFakeR2(face_keys)
    ctx.app = _app(pool=FakePool(db), r2=ctx.r2, r2_face=ctx.r2_face)
    return ctx


def _run(ctx, **kwargs):
    return asyncio.run(purge_biometric_scope(ctx.app, **kwargs))


def test_fake_cutover_apply_resumes_same_batch_after_partial_r2_failure_without_duplicate_state():
    """Break caught: a failed cutover purge can require a new batch or duplicate durable state."""
    ctx = _fake_case()
    ctx.db.tables["fm_models"][1]["legacy_target"] = False
    ctx.db.tables["jobs"][0]["metadata"] = {"facemarketManifestBatchId": ctx.batch}
    batch_count = len(ctx.db.tables["fm_cutover_batches"])
    manifest = asyncio.run(
        facemarket_cutover.build_initial_cutover_manifest(ctx.app, batch_id=ctx.batch)
    )
    assert manifest.model_ids == (ctx.model,)
    assert manifest.license_ids == (ctx.license,)
    assert manifest.job_ids == (ctx.job,)
    ctx.db.tables["fm_cutover_batches"][0].update(
        status="approved",
        started_at=None,
        target_digest=manifest.target_digest,
        model_count=1,
        license_count=1,
        job_count=1,
        asset_count=manifest.asset_count,
    )
    failing_prefix = f"facemarket/models/{ctx.model}/"
    ctx.r2_face.fail_after_delete.add(failing_prefix)

    with pytest.raises(facemarket_cutover.CutoverBlocked) as exc:
        asyncio.run(
            facemarket_cutover.apply_initial_cutover(
                ctx.app,
                batch_id=ctx.batch,
                confirmation=ctx.batch,
                drain_timeout_seconds=1,
            )
        )

    assert exc.value.code == "r2_list_failed"
    assert ctx.db.tables["fm_cutover_batches"][0]["status"] == "failed"
    assert ctx.db.tables["fm_models"][0]["status"] == "reverification_required"
    assert ctx.db.tables["fm_licenses"][0]["status"] == "revoked"
    assert ctx.db.tables["fm_model_assets"]
    assert ctx.db.tables["fm_licenses"][0]["face_image_key"]
    assert ctx.db.tables["jobs"][0]["metadata"] == {"facemarketManifestBatchId": ctx.batch}
    assert len(ctx.db.tables["fm_cutover_batches"]) == batch_count
    assert len(ctx.db.tables["fm_vc_revocation_jobs"]) == 1
    assert {row["vc_id"] for row in ctx.db.tables["fm_vc_revocation_jobs"]} == {"vc-a"}
    assert ctx.r2_face.deleted
    failed_delete_set = set(ctx.r2.deleted) | set(ctx.r2_face.deleted)
    first_delete_count = len(ctx.r2.deleted) + len(ctx.r2_face.deleted)

    ctx.r2_face.fail_after_delete.clear()
    completed = asyncio.run(
        facemarket_cutover.apply_initial_cutover(
            ctx.app,
            batch_id=ctx.batch,
            confirmation=ctx.batch,
            drain_timeout_seconds=1,
        )
    )

    assert completed == {
        "targetDigest": manifest.target_digest,
        "modelCount": 1,
        "licenseCount": 1,
        "jobCount": 1,
        "assetCount": manifest.asset_count,
    }
    assert ctx.db.tables["fm_cutover_batches"][0]["status"] == "completed"
    assert len(ctx.db.tables["fm_cutover_batches"]) == batch_count
    assert len(ctx.db.tables["fm_vc_revocation_jobs"]) == 1
    assert not ctx.db.tables["fm_model_assets"]
    assert ctx.db.tables["fm_licenses"][0]["face_image_key"] is None
    assert ctx.db.tables["fm_licenses"][0]["face_image_digest"] is None
    assert ctx.db.tables["fm_licenses"][0]["enrollment_id"] is None
    assert ctx.db.tables["fm_models"][0]["current_enrollment_id"] is None
    assert not any(key.startswith("facemarket/") for key in ctx.r2.keys)
    assert not any(key.startswith("facemarket/") for key in ctx.r2_face.keys)
    resumed_deletes = (
        ctx.r2.deleted + ctx.r2_face.deleted
    )[first_delete_count:]
    assert failed_delete_set.intersection(resumed_deletes)
    terminal_delete_count = len(ctx.r2.deleted) + len(ctx.r2_face.deleted)

    replay = asyncio.run(
        facemarket_cutover.apply_initial_cutover(
            ctx.app,
            batch_id=ctx.batch,
            confirmation=ctx.batch,
            drain_timeout_seconds=1,
        )
    )

    assert replay == completed
    assert len(ctx.r2.deleted) + len(ctx.r2_face.deleted) == terminal_delete_count
    assert len(ctx.db.tables["fm_vc_revocation_jobs"]) == 1
    assert len(ctx.db.tables["fm_cutover_batches"]) == batch_count


def _assert_public_error_is_sanitized(exc, raw):
    formatted = "".join(traceback.format_exception(exc))
    context = repr(exc.__context__)
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert raw not in str(exc)
    assert raw not in repr(exc)
    assert raw not in formatted
    assert raw not in context


def test_fake_user_purge_reconciles_both_buckets_and_tombstones_recursive_lineage(caplog):
    ctx = _fake_case()

    result = _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert result.complete is True
    assert result.target_count == result.confirmed_absent_count
    assert result.model_count == 1
    assert result.profile_count == 1
    assert result.enrollment_count == 1
    assert ctx.shared_key in ctx.r2.deleted and ctx.shared_key in ctx.r2_face.deleted
    assert ctx.r2.keys == set()
    assert ctx.r2_face.keys == {f"personalization/profiles/{ctx.other_profile}/faces/front.png"}
    assert {a["id"] for a in ctx.db.tables["assets"] if a["r2_bucket"] == "purged"} == {
        ctx.asset,
        ctx.child_asset,
        ctx.source_asset,
    }
    for asset in ctx.db.tables["assets"]:
        if asset["id"] in {ctx.asset, ctx.child_asset, ctx.source_asset}:
            assert asset["r2_key"] == f"purged/{asset['id']}"
            assert asset["metadata"] == {}
            assert asset["checksum"] is None
            assert asset["original_filename"] is None
            assert asset["byte_size"] is None
            assert asset["width"] is None
            assert asset["height"] is None
            assert asset["deleted_at"] is not None
    assert ctx.db.tables["wardrobe_images"] == []
    assert ctx.db.tables["generation_outputs"][0]["asset_id"] is None
    assert ctx.db.tables["edit_sessions"][0]["source_asset_id"] is None
    assert ctx.db.tables["matching_items"][0]["image_asset_id"] is None
    assert ctx.db.tables["matching_items"][0]["thumbnail_asset_id"] is None
    assert ctx.db.tables["projects"][0]["editor_blocks"] is None
    assert ctx.db.tables["jobs"][0]["result"] is None
    assert ctx.db.tables["job_events"][0]["payload"] == {}
    assert ctx.db.tables["fm_licenses"][0]["status"] == "revoked"
    assert ctx.db.tables["fm_licenses"][0]["vc_id"] == "vc-a"
    assert ctx.db.tables["fm_model_asset_cleanup"] == []
    assert ctx.db.tables["fm_biometric_enrollment_photos"] == []
    assert ctx.db.tables["fm_biometric_enrollment_photo_cleanup"] == []
    assert ctx.db.tables["personalization_face_photos"] == [
        {
            "profile_id": ctx.other_profile,
            "angle": "front",
            "r2_key": f"personalization/profiles/{ctx.other_profile}/faces/front.png",
            "image_digest": "sha256-other",
        }
    ]
    assert ctx.db.tables["personalization_generations"] == [
        {
            "profile_id": ctx.other_profile,
            "result_keys": [f"personalization/{ctx.other}/generations/gen-b/0.png"],
        }
    ]
    assert ctx.db.tables["fm_identity_verifications"]
    assert ctx.db.tables["personalization_consents"]
    assert ctx.db.tables["personalization_audit_log"]
    assert "facemarket/" not in caplog.text and ctx.user not in caplog.text


def test_fake_account_delete_anonymizes_identity_and_writes_aggregate_receipt(caplog):
    ctx = _fake_case()
    ctx.db.add("fm_vc_revocation_jobs", vc_id="vc-a", license_id=ctx.license, model_id=ctx.model)

    result = _run(ctx, user_id=ctx.user, reason="account_delete")

    assert result.complete is True
    model = ctx.db.tables["fm_models"][0]
    assert model["status"] == "suspended"
    assert model["user_id"] is None
    assert model["ci_hash"] is None
    assert model["did"] is None
    assert model["cover_image_url"] is None
    assert model["display_name"] == "삭제된 모델"
    assert ctx.db.tables["fm_identity_verifications"] == []
    assert ctx.db.tables["personalization_identity_verifications"] == []
    assert ctx.db.tables["personalization_consents"] == []
    assert ctx.db.tables["personalization_audit_log"] == []
    assert len(ctx.db.tables["fm_settlements"]) == 1
    assert ctx.db.tables["fm_biometric_purge_receipts"] == [
        {
            "id": "receipt-1",
            "source_job_id": None,
            "reason": "account_delete",
            "outcome": "ready_for_identity_delete",
            "target_count": result.target_count,
            "confirmed_absent_count": result.confirmed_absent_count,
            "model_count": 1,
            "profile_count": 1,
            "enrollment_count": 1,
            "asset_count": result.asset_count,
            "completed_at": "now",
        }
    ]
    receipt_blob = repr(ctx.db.tables["fm_biometric_purge_receipts"])
    for secret in (ctx.user, ctx.model, ctx.profile, ctx.enrollment, "ci-a", "did:a", "vc-a"):
        assert secret not in receipt_blob
    assert "facemarket/" not in caplog.text and ctx.user not in caplog.text


def test_fake_account_delete_requires_vc_revocation_job_before_receipt_or_anonymization():
    ctx = _fake_case()

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="account_delete")

    assert exc.value.code == "vc_revocation_missing"
    assert ctx.db.tables["fm_biometric_purge_receipts"] == []
    model = ctx.db.tables["fm_models"][0]
    assert model["user_id"] == ctx.user
    assert model["ci_hash"] == "ci-a"
    assert model["did"] == "did:a"
    assert model["display_name"] == "모델A"
    assert ctx.db.tables["fm_identity_verifications"]
    assert ctx.db.tables["personalization_consents"]


def test_fake_batch_scope_excludes_unrelated_personalization_and_other_batches():
    ctx = _fake_case()

    result = _run(ctx, batch_id=ctx.batch, reason="reverification")

    assert result.profile_count == 0
    assert any(r["profile_id"] == ctx.profile for r in ctx.db.tables["personalization_face_photos"])
    assert any(r["profile_id"] == ctx.profile for r in ctx.db.tables["personalization_generations"])
    assert any(r["id"] == ctx.other_model for r in ctx.db.tables["fm_models"])
    assert ctx.r2_face.keys & {
        f"personalization/profiles/{ctx.profile}/faces/front.png",
        f"personalization/{ctx.user}/generations/gen-a/0.png",
    }


def test_fake_preflight_fails_before_r2_for_unfrozen_model_or_active_writer():
    ctx = _fake_case()
    ctx.db.tables["fm_models"][0]["status"] = "verified"

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == "scope_not_frozen"
    assert ctx.r2.deleted == [] and ctx.r2_face.deleted == []

    ctx = _fake_case()
    ctx.db.tables["fm_licenses"][0]["status"] = "active"
    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")
    assert exc.value.code == "scope_not_frozen"
    assert ctx.r2.deleted == [] and ctx.r2_face.deleted == []

    ctx = _fake_case()
    ctx.db.tables["fm_cutover_batches"][0]["status"] = "pending"
    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, batch_id=ctx.batch, reason="reverification")
    assert exc.value.code == "scope_not_frozen"
    assert ctx.r2.deleted == [] and ctx.r2_face.deleted == []

    ctx = _fake_case()
    ctx.db.add("jobs", id="running-build", user_id=ctx.user, project_id=None, kind="fm_model_asset_build", status="running", payload={"modelId": ctx.model})
    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")
    assert exc.value.code == "scope_not_quiesced"
    assert ctx.r2.deleted == [] and ctx.r2_face.deleted == []


def test_fake_partial_delete_preserves_references_then_retry_succeeds():
    ctx = _fake_case()
    failing = f"facemarket/models/{ctx.model}/enrollments/{ctx.enrollment}/assets/face_front.png"
    ctx.r2_face.fail_delete.add(failing)

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == "r2_delete_failed"
    assert ctx.db.tables["fm_model_assets"]
    assert ctx.db.tables["personalization_face_photos"]

    ctx.r2_face.fail_delete.clear()
    result = _run(ctx, user_id=ctx.user, reason="withdrawal")
    assert result.complete is True
    assert ctx.db.tables["fm_model_assets"] == []


def test_missing_cloudflare_config_fails_before_manifest_or_origin_delete():
    ctx = _fake_case()
    ctx.r2._public_base = "https://images.example.test"
    ctx.r2._cloudflare_zone_id = None
    ctx.r2._cloudflare_cache_purge_token = None
    ctx.r2.preflight_public_cache_purge = types.MethodType(
        R2Client.preflight_public_cache_purge, ctx.r2
    )

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == "cdn_purge_failed"
    assert ctx.r2.deleted == [] and ctx.r2_face.deleted == []
    assert ctx.db.tables["fm_biometric_purge_manifests"] == []


def test_durable_manifest_select_is_serialized_by_scope_advisory_lock():
    ctx = _fake_case()

    _run(ctx, user_id=ctx.user, reason="withdrawal")

    lock_index = next(
        index
        for index, query in enumerate(ctx.db.queries)
        if query.startswith("select pg_advisory_xact_lock")
        and "fm-biometric-purge-manifest" in query
    )
    select_index = next(
        index
        for index, query in enumerate(ctx.db.queries)
        if query.startswith("select target_manifest from fm_biometric_purge_manifests")
    )
    assert lock_index < select_index


def test_cdn_failure_keeps_db_less_orphan_in_durable_manifest_for_retry():
    ctx = _fake_case()
    orphan = f"users/{ctx.user}/projects/{ctx.project}/ai/{ctx.job}/db-less.png"
    ctx.r2.keys.add(orphan)
    ctx.r2.fail_purge = True

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == "cdn_purge_failed"
    assert orphan not in ctx.r2.keys
    assert ctx.db.tables["fm_model_assets"]
    manifests = ctx.db.tables["fm_biometric_purge_manifests"]
    assert len(manifests) == 1
    assert ["r2", orphan] in manifests[0]["target_manifest"]["targets"]

    # 프로세스가 사라졌다고 가정하고 in-memory 호출 기록을 버린다. R2 list에서도 이미
    # 사라진 orphan은 durable manifest를 읽지 않으면 두 번째 실행에서 복원할 수 없다.
    ctx.r2.purged.clear()
    ctx.r2.fail_purge = False
    result = _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert result.complete is True
    assert any(orphan in batch for batch in ctx.r2.purged)
    assert ctx.db.tables["fm_biometric_purge_manifests"] == []


def test_fake_partial_cdn_batch_preserves_references_then_retry_succeeds(monkeypatch):
    ctx = _fake_case()
    ctx.db.add(
        "fm_vc_revocation_jobs",
        vc_id="vc-a",
        license_id=ctx.license,
        model_id=ctx.model,
    )
    for index in range(65):
        asset_id = f"bulk-asset-{index:03d}"
        asset = copy.deepcopy(ctx.db.tables["assets"][0])
        asset.update(
            id=asset_id,
            r2_key=(
                f"users/{ctx.user}/projects/{ctx.project}/ai/{ctx.job}/"
                f"{asset_id}.png"
            ),
        )
        ctx.db.tables["assets"].append(asset)
        ctx.db.add(
            "generation_outputs",
            id=f"bulk-output-{index:03d}",
            generation_run_id="run-a",
            project_id=ctx.project,
            asset_id=asset_id,
            parent_output_id=None,
            edit_session_id=None,
        )
        ctx.r2.keys.add(asset["r2_key"])

    ctx.r2._public_base = "https://images.example.test"
    ctx.r2._cloudflare_zone_id = "zone-1"
    ctx.r2._cloudflare_cache_purge_token = "never-log-this-token"
    ctx.r2.purge_public_cache = types.MethodType(R2Client.purge_public_cache, ctx.r2)
    calls = []
    fail_second_batch = True

    def fake_post(_url, **kwargs):
        nonlocal fail_second_batch
        calls.append(tuple(kwargs["json"]["prefixes"]))
        success = not (fail_second_batch and len(calls) == 2)
        return types.SimpleNamespace(status_code=200, json=lambda: {"success": success})

    monkeypatch.setattr(r2_module.httpx, "post", fake_post)

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="account_delete")

    assert exc.value.code == "cdn_purge_failed"
    assert [len(batch) for batch in calls] == [30, 30]
    assert ctx.r2_face.purged == []
    assert ctx.db.tables["fm_model_assets"]
    assert ctx.db.tables["fm_biometric_purge_receipts"] == []
    assert ctx.db.tables["fm_biometric_purge_manifests"]
    assert ctx.db.tables["fm_models"][0]["user_id"] == ctx.user

    first_attempt = list(calls)
    fail_second_batch = False
    result = _run(ctx, user_id=ctx.user, reason="account_delete")

    assert result.complete is True
    retry_prefixes = {prefix for batch in calls[2:] for prefix in batch}
    assert set(first_attempt[0]).issubset(retry_prefixes)
    assert set(first_attempt[1]).issubset(retry_prefixes)
    assert all(
        f"images.example.test/users/{ctx.user}/projects/{ctx.project}/"
        f"ai/{ctx.job}/bulk-asset-{index:03d}.png" in retry_prefixes
        for index in range(65)
    )
    assert ctx.db.tables["fm_model_assets"] == []
    assert len(ctx.db.tables["fm_biometric_purge_receipts"]) == 1
    assert ctx.db.tables["fm_biometric_purge_manifests"] == []
    assert ctx.db.tables["fm_models"][0]["user_id"] is None


@pytest.mark.parametrize("mode", ["survivor", "head_error", "list_error"])
def test_fake_reconcile_failures_preserve_database_references(mode):
    ctx = _fake_case()
    key = f"facemarket/models/{ctx.model}/licenses/{ctx.license}/face.png"
    if mode == "survivor":
        ctx.r2_face.sticky.add(key)
    elif mode == "head_error":
        ctx.r2_face.fail_head.add(key)
    else:
        ctx.r2_face.fail_list.add(f"facemarket/models/{ctx.model}/")

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code in {"r2_list_failed", "r2_reconcile_failed"}
    assert ctx.db.tables["fm_model_assets"]
    assert ctx.db.tables["personalization_profiles"][0]["status"] == "purging"


def test_fake_second_pass_discovery_expansion_fails_closed_before_cleanup():
    ctx = _fake_case()

    def add_late_known(db):
        if db.commits == 1:
            db.add("fm_model_assets", model_id=ctx.model, view="late", r2_key=f"facemarket/models/{ctx.model}/late.png", source_enrollment_id=ctx.enrollment)

    ctx.db.on_commit = add_late_known

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == "r2_reconcile_failed"
    assert any(r.get("view") == "late" for r in ctx.db.tables["fm_model_assets"])
    assert ctx.db.tables["personalization_face_photos"]


def test_fake_idempotent_replay_after_success_has_empty_targets():
    ctx = _fake_case()
    first = _run(ctx, user_id=ctx.user, reason="withdrawal")
    second = _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert first.target_count > 0
    assert second.complete is True
    assert second.target_count == 0
    assert second.confirmed_absent_count == 0


@pytest.mark.parametrize(
    ("mode", "want_code"),
    [
        ("initial_list", "r2_list_failed"),
        ("delete", "r2_delete_failed"),
        ("reconcile_list", "r2_list_failed"),
        ("head", "r2_reconcile_failed"),
        ("initial_db", "db_cleanup_failed"),
        ("final_db", "db_cleanup_failed"),
    ],
)
def test_fake_public_errors_do_not_retain_raw_exception_context(mode, want_code):
    ctx = _fake_case()
    raw = f"RAW-SENTINEL-{mode}-{ctx.shared_key}"
    if mode == "initial_list":
        ctx.r2_face.fail_list_message = raw
        ctx.r2_face.fail_list.add(f"facemarket/models/{ctx.model}/")
    elif mode == "delete":
        ctx.r2_face.fail_delete_message = raw
        ctx.r2_face.fail_delete.add(
            f"facemarket/models/{ctx.model}/licenses/{ctx.license}/face.png"
        )
    elif mode == "reconcile_list":
        ctx.r2_face.fail_list_message = raw
        ctx.r2_face.fail_after_delete.add(f"facemarket/models/{ctx.model}/")
    elif mode == "head":
        ctx.r2_face.fail_head_message = raw
        ctx.r2_face.fail_head.add(
            f"facemarket/models/{ctx.model}/licenses/{ctx.license}/face.png"
        )
    elif mode == "initial_db":
        ctx.db.fail_select["information_schema.columns"] = raw
    else:
        ctx.db.fail_mutate["update assets set r2_key='purged/'"] = raw

    with pytest.raises(PurgeIncomplete) as exc:
        _run(ctx, user_id=ctx.user, reason="withdrawal")

    assert exc.value.code == want_code
    _assert_public_error_is_sanitized(exc.value, raw)


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


def test_live_facemarket_scope_job_discovery_json_paths_and_joins():
    if not LIVE_DB_URL:
        pytest.skip("set FACEMARKET_TEST_DATABASE_URL for live job discovery test")

    async def run():
        user_id = str(uuid.uuid4())
        model_id = str(uuid.uuid4())
        license_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        snapshot_job_id = str(uuid.uuid4())
        flat_job_id = str(uuid.uuid4())
        settled_job_id = str(uuid.uuid4())
        conn = await psycopg.AsyncConnection.connect(LIVE_DB_URL, row_factory=dict_row)
        try:
            rows = await conn.execute(
                "select table_name, column_name from information_schema.columns "
                "where table_schema='public' and table_name = any(%s)",
                ([
                    "fm_biometric_enrollments",
                    "fm_biometric_enrollment_photos",
                    "fm_biometric_enrollment_photo_cleanup",
                    "fm_model_asset_cleanup",
                    "fm_models",
                    "fm_licenses",
                    "fm_model_assets",
                    "jobs",
                    "projects",
                    "fm_settlements",
                ],),
            )
            rows = await rows.fetchall()
            found = {}
            for row in rows:
                found.setdefault(row["table_name"], set()).add(row["column_name"])
            for table, cols in {
                "fm_biometric_enrollments": {"id", "user_id", "model_id"},
                "fm_biometric_enrollment_photos": {"enrollment_id", "r2_key"},
                "fm_biometric_enrollment_photo_cleanup": {"enrollment_id", "r2_key"},
                "fm_model_asset_cleanup": {"model_id", "r2_key"},
                "fm_models": {"reverification_batch_id", "current_enrollment_id"},
                "fm_licenses": {"reverification_batch_id", "enrollment_id"},
                "fm_model_assets": {"source_enrollment_id"},
                "jobs": {"id", "user_id", "project_id", "kind", "status", "payload", "created_at"},
                "projects": {"id", "user_id", "facemarket_license_id"},
                "fm_settlements": {"job_id", "license_id"},
            }.items():
                missing = cols - found.get(table, set())
                if missing:
                    pytest.skip(f"FACEMARKET_TEST_DATABASE_URL schema missing {table}: {sorted(missing)}")

            await conn.execute("insert into auth.users (id) values (%s)", (user_id,))
            await conn.execute(
                "insert into fm_models (id, user_id, display_name, status, ci_hash) "
                "values (%s, %s, 'Scope Test', 'verified', %s)",
                (model_id, user_id, f"ci-{uuid.uuid4()}"),
            )
            await conn.execute(
                "insert into fm_licenses "
                "(id, model_id, face_image_uri, face_image_key, face_image_digest, license_valid_until, status) "
                "values (%s, %s, '/face', 'face-key', 'sha256-face', %s, 'revoked')",
                (license_id, model_id, datetime.now(timezone.utc) + timedelta(days=1)),
            )
            await conn.execute(
                "insert into projects (id, user_id, status, title, facemarket_license_id) "
                "values (%s, %s, 'done', 'scope', %s)",
                (project_id, user_id, license_id),
            )
            await conn.execute(
                "insert into jobs (id, user_id, project_id, kind, status, payload) "
                "values (%s, %s, %s, 'detail_page', 'done', %s)",
                (
                    snapshot_job_id,
                    user_id,
                    project_id,
                    Json({"_facemarket": {"modelId": model_id, "licenseId": license_id}}),
                ),
            )
            await conn.execute(
                "insert into jobs (id, user_id, project_id, kind, status, payload) "
                "values (%s, %s, %s, 'detail_page', 'done', %s)",
                (flat_job_id, user_id, project_id, Json({"modelId": model_id})),
            )
            await conn.execute(
                "insert into jobs (id, user_id, project_id, kind, status, payload) "
                "values (%s, %s, %s, 'editor_image', 'done', '{}'::jsonb)",
                (settled_job_id, user_id, project_id),
            )
            await conn.execute(
                "insert into fm_settlements "
                "(payment_id, job_id, license_id, model_ref, total_amount, model_amount, platform_amount, ops_amount) "
                "values (%s, %s, %s, '0xscope', 100, 70, 20, 10)",
                (f"payment-{uuid.uuid4()}", settled_job_id, license_id),
            )

            rows = await repo.list_facemarket_scope_jobs(
                conn,
                model_ids=(model_id,),
                license_ids=(license_id,),
            )

            assert {row["id"] for row in rows} == {snapshot_job_id, settled_job_id}
            assert all("payload" not in row for row in rows)
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(run())


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
