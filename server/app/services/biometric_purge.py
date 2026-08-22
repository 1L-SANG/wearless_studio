from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Literal

from app import repo


PurgeReason = Literal["withdrawal", "reverification", "account_delete"]
_CODES = {
    "invalid_scope",
    "scope_not_found",
    "scope_not_frozen",
    "scope_not_quiesced",
    "storage_unavailable",
    "r2_list_failed",
    "r2_delete_failed",
    "r2_reconcile_failed",
    "cdn_purge_failed",
    "db_cleanup_failed",
    "vc_revocation_missing",
}
class PurgeIncomplete(RuntimeError):
    def __init__(self, code: str):
        if code not in _CODES:
            code = "db_cleanup_failed"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class PurgeResult:
    complete: bool
    target_count: int
    confirmed_absent_count: int
    model_count: int
    profile_count: int
    enrollment_count: int
    asset_count: int
    target_digest: str


def _digest(targets: set[tuple[str, str]]) -> str:
    material = "\n".join(f"{bucket}:{key}" for bucket, key in sorted(targets))
    return hashlib.sha256(material.encode()).hexdigest()


def _prefixes(
    *,
    user_id: str | None,
    profile_ids: set[str],
    model_ids: set[str],
    enrollment_ids: set[str],
    jobs: list[dict],
) -> tuple[str, ...]:
    values = [f"facemarket/models/{model_id}/" for model_id in model_ids]
    values.extend(f"facemarket/enrollments/{eid}/" for eid in enrollment_ids)
    values.extend(f"personalization/profiles/{pid}/faces/" for pid in profile_ids)
    if user_id is not None:
        values.append(f"personalization/{user_id}/generations/")
    values.extend(
        f"users/{j['user_id']}/projects/{j['project_id']}/ai/{j['id']}/"
        for j in jobs
        if j.get("user_id") and j.get("project_id") and j.get("id")
    )
    return tuple(sorted(set(values)))


def _require_storage(app):
    r2 = getattr(app.state, "r2", None)
    r2_face = getattr(app.state, "r2_face", None)
    if r2 is None or r2_face is None:
        raise PurgeIncomplete("storage_unavailable")
    for client in (r2, r2_face):
        if not all(hasattr(client, name) for name in ("delete", "list_prefix")):
            raise PurgeIncomplete("storage_unavailable")
        if not (hasattr(client, "head") or hasattr(client, "head_strict")):
            raise PurgeIncomplete("storage_unavailable")
    return {"r2": r2, "r2_face": r2_face}


def _validate_scope_args(
    *, user_id: str | None, batch_id: str | None, reason: str
) -> None:
    if (user_id is None) == (batch_id is None):
        raise PurgeIncomplete("invalid_scope")
    if user_id is not None and reason not in {"withdrawal", "account_delete"}:
        raise PurgeIncomplete("invalid_scope")
    if batch_id is not None and reason != "reverification":
        raise PurgeIncomplete("invalid_scope")


async def _schema(conn) -> dict[str, set[str]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema='public'"
        )
        rows = await cur.fetchall()
    schema: dict[str, set[str]] = {}
    for row in rows:
        schema.setdefault(row["table_name"], set()).add(row["column_name"])
    return schema


def _has(schema: dict[str, set[str]], table: str, *cols: str) -> bool:
    existing = schema.get(table)
    return existing is not None and all(c in existing for c in cols)


def _ids(rows, key="id") -> set[str]:
    return {str(r[key]) for r in rows if r.get(key) is not None}


async def _scope(conn, schema, *, user_id: str | None, batch_id: str | None, reason: str):
    _validate_scope_args(user_id=user_id, batch_id=batch_id, reason=reason)

    profile_ids: set[str] = set()
    model_ids: set[str] = set()
    license_ids: set[str] = set()
    async with conn.cursor() as cur:
        if user_id is not None:
            profile_filter = "" if reason == "account_delete" else " and status <> 'purged'"
            await cur.execute(
                "select id::text as id from personalization_profiles "
                f"where user_id=%s{profile_filter}",
                (user_id,),
            )
            profile_ids = _ids(await cur.fetchall())
            await cur.execute(
                "select id::text as id from fm_models where user_id=%s",
                (user_id,),
            )
            model_ids = _ids(await cur.fetchall())
            if model_ids:
                await cur.execute(
                    "select id::text as id from fm_licenses where model_id = any(%s)",
                    (list(model_ids),),
                )
                license_ids = _ids(await cur.fetchall())
        else:
            if not _has(schema, "fm_cutover_batches", "status"):
                raise PurgeIncomplete("scope_not_found")
            await cur.execute(
                "select status from fm_cutover_batches where id=%s", (batch_id,)
            )
            batch = await cur.fetchone()
            if batch is None:
                raise PurgeIncomplete("scope_not_found")
            if batch["status"] != "reconciling":
                raise PurgeIncomplete("scope_not_frozen")
            if _has(schema, "fm_models", "reverification_batch_id"):
                await cur.execute(
                    "select id::text as id from fm_models where reverification_batch_id=%s",
                    (batch_id,),
                )
                model_ids |= _ids(await cur.fetchall())
            if _has(schema, "fm_licenses", "reverification_batch_id"):
                await cur.execute(
                    "select id::text as id, model_id::text as model_id "
                    "from fm_licenses where reverification_batch_id=%s",
                    (batch_id,),
                )
                rows = await cur.fetchall()
                license_ids |= _ids(rows)
                model_ids |= {r["model_id"] for r in rows if r.get("model_id")}
    if not profile_ids and not model_ids and not license_ids:
        raise PurgeIncomplete("scope_not_found")
    return {
        "user_id": user_id,
        "batch_id": batch_id,
        "profile_ids": profile_ids,
        "model_ids": model_ids,
        "license_ids": license_ids,
    }


async def _enrollment_ids(conn, schema, scope) -> set[str]:
    ids: set[str] = set()
    model_ids = scope["model_ids"]
    license_ids = scope["license_ids"]
    async with conn.cursor() as cur:
        if _has(schema, "fm_biometric_enrollments", "id"):
            clauses = []
            args = []
            if scope["user_id"] is not None and _has(schema, "fm_biometric_enrollments", "user_id"):
                clauses.append("user_id=%s")
                args.append(scope["user_id"])
            if model_ids and _has(schema, "fm_biometric_enrollments", "model_id"):
                clauses.append("model_id = any(%s)")
                args.append(list(model_ids))
            if clauses:
                await cur.execute(
                    "select id::text as id from fm_biometric_enrollments where "
                    + " or ".join(clauses),
                    tuple(args),
                )
                ids |= _ids(await cur.fetchall())
        if model_ids and _has(schema, "fm_models", "current_enrollment_id"):
            await cur.execute(
                "select current_enrollment_id::text as id from fm_models "
                "where id = any(%s) and current_enrollment_id is not null",
                (list(model_ids),),
            )
            ids |= _ids(await cur.fetchall())
        if license_ids and _has(schema, "fm_licenses", "enrollment_id"):
            await cur.execute(
                "select enrollment_id::text as id from fm_licenses "
                "where id = any(%s) and enrollment_id is not null",
                (list(license_ids),),
            )
            ids |= _ids(await cur.fetchall())
        if model_ids and _has(schema, "fm_model_assets", "source_enrollment_id"):
            await cur.execute(
                "select source_enrollment_id::text as id from fm_model_assets "
                "where model_id = any(%s) and source_enrollment_id is not null",
                (list(model_ids),),
            )
            ids |= _ids(await cur.fetchall())
    return ids


async def _ensure_frozen(conn, schema, scope, derived_jobs: list[dict]) -> None:
    async with conn.cursor() as cur:
        if scope["model_ids"]:
            await cur.execute(
                "select 1 from fm_models where id = any(%s) and status = 'verified' limit 1",
                (list(scope["model_ids"]),),
            )
            if await cur.fetchone():
                raise PurgeIncomplete("scope_not_frozen")
        if scope["license_ids"]:
            await cur.execute(
                "select 1 from fm_licenses where id = any(%s) and status = 'active' limit 1",
                (list(scope["license_ids"]),),
            )
            if await cur.fetchone():
                raise PurgeIncomplete("scope_not_frozen")
        active_job_ids = {j["id"] for j in derived_jobs if j.get("status") in {"pending", "running"}}
        if scope["profile_ids"]:
            await cur.execute(
                "select 1 from jobs where kind='personalization_generation' "
                "and status in ('pending','running') and payload->>'profileId' = any(%s) limit 1",
                (list(scope["profile_ids"]),),
            )
            if await cur.fetchone():
                raise PurgeIncomplete("scope_not_quiesced")
        if active_job_ids:
            raise PurgeIncomplete("scope_not_quiesced")


async def _derived_jobs(conn, schema, scope) -> list[dict]:
    batch_id = scope.get("batch_id")
    if batch_id is not None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select j.id::text as id,
                       j.user_id::text as user_id,
                       j.project_id::text as project_id,
                       j.kind,
                       j.status,
                       j.created_at
                  from jobs j
                 where j.metadata->>'facemarketManifestBatchId' = %s
                 order by j.created_at, j.id
                """,
                (batch_id,),
            )
            rows = await cur.fetchall()
            if _has(schema, "fm_cutover_batches", "job_count"):
                await cur.execute(
                    "select job_count from fm_cutover_batches where id=%s",
                    (batch_id,),
                )
                batch = await cur.fetchone()
                if batch is not None and int(batch["job_count"]) != len({r["id"] for r in rows}):
                    raise PurgeIncomplete("scope_not_quiesced")
        return rows
    return await repo.list_facemarket_scope_jobs(
        conn,
        model_ids=tuple(sorted(scope["model_ids"])),
        license_ids=tuple(sorted(scope["license_ids"])),
    )


async def _known_targets(conn, schema, scope, enrollment_ids, derived_jobs):
    face_keys: set[str] = set()
    r2_keys: set[str] = set()
    asset_ids: set[str] = set()
    project_ids = {j["project_id"] for j in derived_jobs if j.get("project_id")}
    job_ids = {j["id"] for j in derived_jobs if j.get("id")}
    prefixes = _prefixes(
        user_id=scope["user_id"],
        profile_ids=scope["profile_ids"],
        model_ids=scope["model_ids"],
        enrollment_ids=enrollment_ids,
        jobs=derived_jobs,
    )
    async with conn.cursor() as cur:
        if scope["license_ids"]:
            await cur.execute(
                "select face_image_key as k from fm_licenses "
                "where id = any(%s) and face_image_key is not null",
                (list(scope["license_ids"]),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if scope["model_ids"]:
            await cur.execute(
                "select r2_key as k from fm_model_assets where model_id = any(%s)",
                (list(scope["model_ids"]),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if scope["model_ids"] and _has(schema, "fm_model_asset_cleanup", "r2_key"):
            await cur.execute(
                "select r2_key as k from fm_model_asset_cleanup where model_id = any(%s)",
                (list(scope["model_ids"]),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if enrollment_ids and _has(schema, "fm_biometric_enrollment_photos", "r2_key"):
            await cur.execute(
                "select r2_key as k from fm_biometric_enrollment_photos "
                "where enrollment_id = any(%s)",
                (list(enrollment_ids),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if enrollment_ids and _has(schema, "fm_biometric_enrollment_photo_cleanup", "r2_key"):
            await cur.execute(
                "select r2_key as k from fm_biometric_enrollment_photo_cleanup "
                "where enrollment_id = any(%s)",
                (list(enrollment_ids),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if scope["profile_ids"]:
            await cur.execute(
                "select r2_key as k from personalization_face_photos where profile_id = any(%s)",
                (list(scope["profile_ids"]),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
            await cur.execute(
                "select unnest(result_keys) as k from personalization_generations "
                "where profile_id = any(%s)",
                (list(scope["profile_ids"]),),
            )
            face_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if prefixes:
            await cur.execute(
                "select id::text as id, r2_key as k from assets "
                "where " + " or ".join(["r2_key like %s" for _ in prefixes]),
                tuple(f"{p}%" for p in prefixes),
            )
            for row in await cur.fetchall():
                asset_ids.add(row["id"])
                r2_keys.add(row["k"])
        if job_ids and _has(schema, "generation_runs", "prompt_r2_key"):
            await cur.execute(
                "select prompt_r2_key as k from generation_runs "
                "where job_id = any(%s) and prompt_r2_key is not null",
                (list(job_ids),),
            )
            r2_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if job_ids and _has(schema, "edit_sessions", "prompt_r2_key"):
            await cur.execute(
                "select prompt_r2_key as k from edit_sessions "
                "where job_id = any(%s) and prompt_r2_key is not null",
                (list(job_ids),),
            )
            r2_keys |= {r["k"] for r in await cur.fetchall() if r.get("k")}
        if project_ids:
            await cur.execute(
                "select id::text as id, r2_key as k from assets "
                "where project_id = any(%s) and r2_key = any(%s)",
                (list(project_ids), list(r2_keys) or [""]),
            )
            for row in await cur.fetchall():
                asset_ids.add(row["id"])
        lineage_keys, lineage_asset_ids, lineage = await _lineage_targets(
            cur, schema, job_ids, asset_ids
        )
        r2_keys |= lineage_keys
        asset_ids |= lineage_asset_ids
    targets = {("r2_face", k) for k in face_keys}
    targets |= {("r2", k) for k in r2_keys}
    return targets, set(asset_ids), prefixes, lineage


async def _lineage_targets(cur, schema, job_ids: set[str], seed_asset_ids: set[str]):
    r2_keys: set[str] = set()
    asset_ids = set(seed_asset_ids)
    run_ids: set[str] = set()
    output_ids: set[str] = set()
    session_ids: set[str] = set()
    if job_ids and _has(schema, "generation_runs", "id", "prompt_r2_key"):
        await cur.execute(
            "select id::text as id, prompt_r2_key as prompt_r2_key from generation_runs "
            "where job_id = any(%s)",
            (list(job_ids),),
        )
        for row in await cur.fetchall():
            if row.get("id"):
                run_ids.add(row["id"])
            if row.get("prompt_r2_key"):
                r2_keys.add(row["prompt_r2_key"])

    changed = True
    while changed:
        changed = False
        if _has(
            schema,
            "generation_outputs",
            "id",
            "asset_id",
            "generation_run_id",
            "parent_output_id",
            "edit_session_id",
        ):
            await cur.execute(
                "select id::text as id, asset_id::text as asset_id, "
                "parent_output_id::text as parent_output_id, edit_session_id::text as edit_session_id "
                "from generation_outputs where generation_run_id = any(%s) or id = any(%s) "
                "or parent_output_id = any(%s) or edit_session_id = any(%s) or asset_id = any(%s)",
                (
                    list(run_ids),
                    list(output_ids),
                    list(output_ids),
                    list(session_ids),
                    list(asset_ids),
                ),
            )
            for row in await cur.fetchall():
                for target, key in (
                    (output_ids, "id"),
                    (asset_ids, "asset_id"),
                    (output_ids, "parent_output_id"),
                    (session_ids, "edit_session_id"),
                ):
                    value = row.get(key)
                    if value and value not in target:
                        target.add(value)
                        changed = True
        if _has(
            schema,
            "edit_sessions",
            "id",
            "source_asset_id",
            "job_id",
            "parent_output_id",
            "output_id",
            "prompt_r2_key",
        ):
            await cur.execute(
                "select id::text as id, source_asset_id::text as source_asset_id, "
                "parent_output_id::text as parent_output_id, output_id::text as output_id, "
                "prompt_r2_key as prompt_r2_key from edit_sessions "
                "where job_id = any(%s) or parent_output_id = any(%s) "
                "or source_asset_id = any(%s) or output_id = any(%s)",
                (list(job_ids), list(output_ids), list(asset_ids), list(output_ids)),
            )
            for row in await cur.fetchall():
                for target, key in (
                    (session_ids, "id"),
                    (asset_ids, "source_asset_id"),
                    (output_ids, "parent_output_id"),
                    (output_ids, "output_id"),
                ):
                    value = row.get(key)
                    if value and value not in target:
                        target.add(value)
                        changed = True
                if row.get("prompt_r2_key"):
                    r2_keys.add(row["prompt_r2_key"])

    if asset_ids:
        await cur.execute(
            "select id::text as id, r2_key as k from assets where id = any(%s)",
            (list(asset_ids),),
        )
        for row in await cur.fetchall():
            if row.get("k"):
                r2_keys.add(row["k"])
    return r2_keys, asset_ids, {"run_ids": run_ids, "session_ids": session_ids}


async def _list_targets(clients, prefixes) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    failed = False
    try:
        for label, client in clients.items():
            for prefix in prefixes:
                for key in await asyncio.to_thread(client.list_prefix, prefix):
                    targets.add((label, key))
    except Exception:
        failed = True
    if failed:
        raise PurgeIncomplete("r2_list_failed")
    return targets


async def _head(client, key):
    if hasattr(client, "head"):
        return await asyncio.to_thread(client.head, key)
    return await asyncio.to_thread(client.head_strict, key)


async def _delete_and_reconcile(clients, targets, prefixes):
    failed_code = None
    try:
        for label, key in sorted(targets):
            await asyncio.to_thread(clients[label].delete, key)
    except Exception:
        failed_code = "r2_delete_failed"
    if failed_code:
        raise PurgeIncomplete(failed_code)
    try:
        survivors = await _list_targets(clients, prefixes)
        if survivors:
            raise PurgeIncomplete("r2_reconcile_failed")
        for label, key in sorted(targets):
            if await _head(clients[label], key) is not None:
                raise PurgeIncomplete("r2_reconcile_failed")
    except PurgeIncomplete:
        raise
    except Exception:
        failed_code = "r2_reconcile_failed"
    if failed_code:
        raise PurgeIncomplete(failed_code)


async def _cleanup(
    conn,
    schema,
    scope,
    enrollment_ids,
    asset_ids,
    derived_jobs,
    lineage,
    *,
    reason: PurgeReason,
    source_job_id: str | None,
    target_count: int,
    confirmed_absent_count: int,
):
    model_ids = scope["model_ids"]
    license_ids = scope["license_ids"]
    profile_ids = scope["profile_ids"]
    job_ids = {j["id"] for j in derived_jobs if j.get("id")}
    project_ids = {j["project_id"] for j in derived_jobs if j.get("project_id")}
    async with conn.cursor() as cur:
        if reason == "account_delete" and license_ids:
            if not _has(schema, "fm_vc_revocation_jobs", "vc_id"):
                raise PurgeIncomplete("vc_revocation_missing")
            await cur.execute(
                """
                select count(*)::int as missing_count
                from fm_licenses l
                left join fm_vc_revocation_jobs j on j.vc_id = l.vc_id
                where l.id = any(%s)
                  and nullif(btrim(l.vc_id), '') is not null
                  and j.vc_id is null
                """,
                (list(license_ids),),
            )
            if int((await cur.fetchone() or {}).get("missing_count") or 0) > 0:
                raise PurgeIncomplete("vc_revocation_missing")
        if asset_ids:
            ids = list(asset_ids)
            for table, col in (
                ("matching_items", "image_asset_id"),
                ("matching_items", "thumbnail_asset_id"),
                ("exports", "asset_id"),
                ("product_truth_assets", "asset_id"),
                ("generation_outputs", "asset_id"),
                ("edit_sessions", "source_asset_id"),
            ):
                if _has(schema, table, col):
                    await cur.execute(
                        f"update {table} set {col}=null where {col} = any(%s)", (ids,)
                    )
            if _has(schema, "wardrobe_images", "asset_id"):
                await cur.execute("delete from wardrobe_images where asset_id = any(%s)", (ids,))
            await cur.execute(
                "update assets set r2_key='purged/' || id::text, r2_bucket='purged', "
                "mime_type='application/octet-stream', checksum=null, metadata='{}'::jsonb, "
                "original_filename=null, byte_size=null, width=null, height=null, "
                "deleted_at=coalesce(deleted_at, now()) where id = any(%s)",
                (ids,),
            )
        if project_ids:
            await cur.execute(
                "update projects set editor_blocks=null, selected_mannequin_id=null where id = any(%s)",
                (list(project_ids),),
            )
        if job_ids:
            await cur.execute("update jobs set result=null where id = any(%s)", (list(job_ids),))
            await cur.execute(
                "update job_events set payload='{}'::jsonb where job_id = any(%s)",
                (list(job_ids),),
            )
            if _has(schema, "generation_runs", "prompt_r2_key"):
                await cur.execute(
                    "update generation_runs set prompt_r2_key=null where job_id = any(%s)",
                    (list(job_ids),),
                )
            if _has(schema, "edit_sessions", "prompt_r2_key"):
                await cur.execute(
                    "update edit_sessions set prompt_r2_key=null where job_id = any(%s)",
                    (list(job_ids),),
                )
        if lineage.get("run_ids") and _has(schema, "generation_runs", "prompt_r2_key"):
            await cur.execute(
                "update generation_runs set prompt_r2_key=null where id = any(%s)",
                (list(lineage["run_ids"]),),
            )
        if lineage.get("session_ids") and _has(schema, "edit_sessions", "prompt_r2_key"):
            await cur.execute(
                "update edit_sessions set prompt_r2_key=null where id = any(%s)",
                (list(lineage["session_ids"]),),
            )
        if model_ids:
            await cur.execute("delete from fm_model_assets where model_id = any(%s)", (list(model_ids),))
            if _has(schema, "fm_model_asset_cleanup", "model_id"):
                await cur.execute(
                    "delete from fm_model_asset_cleanup where model_id = any(%s)",
                    (list(model_ids),),
                )
            model_sets = ["assets_status='none'", "qc_score=null", "assets_source_hash=null"]
            if _has(schema, "fm_models", "current_enrollment_id"):
                model_sets.append("current_enrollment_id=null")
            await cur.execute(
                "update fm_models set " + ", ".join(model_sets) + " where id = any(%s)",
                (list(model_ids),),
            )
        if license_ids:
            sets = ["face_image_key=null", "face_image_digest=null"]
            if _has(schema, "fm_licenses", "enrollment_id"):
                sets.append("enrollment_id=null")
            await cur.execute(
                "update fm_licenses set " + ", ".join(sets) + " where id = any(%s)",
                (list(license_ids),),
            )
        if enrollment_ids:
            ids = list(enrollment_ids)
            if _has(schema, "fm_biometric_enrollment_photo_cleanup", "enrollment_id"):
                await cur.execute(
                    "delete from fm_biometric_enrollment_photo_cleanup where enrollment_id = any(%s)",
                    (ids,),
                )
            if _has(schema, "fm_biometric_enrollments", "id"):
                await cur.execute("delete from fm_biometric_enrollments where id = any(%s)", (ids,))
        if profile_ids and scope["user_id"] is not None:
            ids = list(profile_ids)
            await cur.execute("delete from personalization_face_photos where profile_id = any(%s)", (ids,))
            await cur.execute("delete from personalization_generations where profile_id = any(%s)", (ids,))
            await cur.execute(
                "delete from personalization_identity_verifications where user_id=%s",
                (scope["user_id"],),
            )
            await cur.execute(
                "update personalization_profiles set height_cm=null, weight_kg=null, body_type=null, "
                "body_type_custom=null, gender=null, age_range=null, skin_tone=null, hair=null, "
                "clothing_size=null, status='purged', purged_at=now() where id = any(%s)",
                (ids,),
            )
        if reason == "account_delete" and scope["user_id"] is not None:
            if license_ids:
                await cur.execute(
                    "update fm_licenses set status='revoked', face_image_key=null, "
                    "face_image_digest=null where id = any(%s)",
                    (list(license_ids),),
                )
            if model_ids:
                await cur.execute(
                    "delete from fm_identity_verifications where model_id = any(%s)",
                    (list(model_ids),),
                )
                await cur.execute(
                    "update fm_models set status='suspended', user_id=null, ci_hash=null, did=null, "
                    "cover_image_url=null, display_name='삭제된 모델', assets_status='none', "
                    "qc_score=null, assets_source_hash=null where id = any(%s)",
                    (list(model_ids),),
                )
            await cur.execute(
                "delete from personalization_identity_verifications where user_id=%s",
                (scope["user_id"],),
            )
            await cur.execute(
                "delete from personalization_consents where user_id=%s",
                (scope["user_id"],),
            )
            await cur.execute(
                "delete from personalization_audit_log where user_id=%s",
                (scope["user_id"],),
            )
            if _has(schema, "profiles", "display_name", "avatar_asset_id"):
                await cur.execute(
                    "update profiles set display_name=null, avatar_asset_id=null where user_id=%s",
                    (scope["user_id"],),
                )
            if _has(schema, "fm_biometric_purge_receipts", "target_count"):
                await cur.execute(
                    """
                    insert into fm_biometric_purge_receipts
                        (source_job_id, target_count, confirmed_absent_count,
                         model_count, profile_count, enrollment_count, asset_count)
                    values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (source_job_id) do nothing
                    """,
                    (
                        source_job_id,
                        target_count,
                        confirmed_absent_count,
                        len(model_ids),
                        len(profile_ids),
                        len(enrollment_ids),
                        len(asset_ids),
                    ),
                )


async def purge_biometric_scope(
    app,
    *,
    user_id: str | None = None,
    batch_id: str | None = None,
    reason: PurgeReason,
    source_job_id: str | None = None,
) -> PurgeResult:
    _validate_scope_args(user_id=user_id, batch_id=batch_id, reason=reason)
    clients = _require_storage(app)
    pool = app.state.pool
    db_failed = False
    try:
        async with pool.connection() as conn:
            schema = await _schema(conn)
            scope = await _scope(conn, schema, user_id=user_id, batch_id=batch_id, reason=reason)
            derived_jobs = await _derived_jobs(conn, schema, scope)
            await _ensure_frozen(conn, schema, scope, derived_jobs)
            enrollment_ids = await _enrollment_ids(conn, schema, scope)
            known, asset_ids, prefixes, lineage = await _known_targets(
                conn, schema, scope, enrollment_ids, derived_jobs
            )
            await conn.commit()
    except PurgeIncomplete:
        raise
    except Exception:
        db_failed = True
    if db_failed:
        raise PurgeIncomplete("db_cleanup_failed")

    listed = await _list_targets(clients, prefixes)
    targets = known | listed
    await _delete_and_reconcile(clients, targets, prefixes)
    try:
        await asyncio.to_thread(
            clients["r2"].purge_public_cache,
            [key for label, key in sorted(targets) if label == "r2"],
        )
    except Exception:
        raise PurgeIncomplete("cdn_purge_failed") from None

    db_failed = False
    async with pool.connection() as conn:
        try:
            schema = await _schema(conn)
            scope = await _scope(conn, schema, user_id=user_id, batch_id=batch_id, reason=reason)
            derived_jobs = await _derived_jobs(conn, schema, scope)
            await _ensure_frozen(conn, schema, scope, derived_jobs)
            enrollment_ids = await _enrollment_ids(conn, schema, scope)
            _known, asset_ids, _prefixes2, lineage = await _known_targets(
                conn, schema, scope, enrollment_ids, derived_jobs
            )
            if not _known.issubset(targets) or not set(_prefixes2).issubset(set(prefixes)):
                raise PurgeIncomplete("r2_reconcile_failed")
            await _cleanup(
                conn,
                schema,
                scope,
                enrollment_ids,
                asset_ids,
                derived_jobs,
                lineage,
                reason=reason,
                source_job_id=source_job_id,
                target_count=len(targets),
                confirmed_absent_count=len(targets),
            )
            await conn.commit()
        except PurgeIncomplete:
            await conn.rollback()
            raise
        except Exception:
            await conn.rollback()
            db_failed = True
    if db_failed:
        raise PurgeIncomplete("db_cleanup_failed")

    return PurgeResult(
        complete=True,
        target_count=len(targets),
        confirmed_absent_count=len(targets),
        model_count=len(scope["model_ids"]),
        profile_count=len(scope["profile_ids"]),
        enrollment_count=len(enrollment_ids),
        asset_count=len(asset_ids),
        target_digest=_digest(targets),
    )


async def initial_cutover_asset_count(
    app,
    *,
    model_ids: tuple[str, ...],
    license_ids: tuple[str, ...],
    job_ids: tuple[str, ...],
) -> int:
    """Read-only inventory count for Task8; deletion/reconcile still lives in purge."""
    clients = _require_storage(app)
    pool = app.state.pool
    async with pool.connection() as conn:
        schema = await _schema(conn)
        scope = {
            "user_id": None,
            "batch_id": None,
            "profile_ids": set(),
            "model_ids": set(model_ids),
            "license_ids": set(license_ids),
        }
        jobs = []
        if job_ids:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select id::text as id,
                           user_id::text as user_id,
                           project_id::text as project_id,
                           kind,
                           status,
                           created_at
                      from jobs
                     where id = any(%s)
                     order by created_at, id
                    """,
                    (list(job_ids),),
                )
                jobs = await cur.fetchall()
        enrollment_ids = await _enrollment_ids(conn, schema, scope)
        known, _asset_ids, prefixes, _lineage = await _known_targets(
            conn, schema, scope, enrollment_ids, jobs
        )
        await conn.commit()
    listed = await _list_targets(clients, prefixes)
    return len(known | listed)
