"""FaceMarket cutover writer quiescence primitives.

No route, deploy, Holder, or CLI mutation action lives here.
"""

import asyncio
import hashlib
from dataclasses import dataclass

from psycopg.types.json import Json

from . import facemarket
from . import repo
from .services import biometric_purge
from .services.biometric_purge import purge_biometric_scope
from .facemarket_enrollment import (
    BIOMETRIC_CONSENT_VERSION,
    _MODEL_ASSET_FENCE_NAMESPACE,
    _PHOTO_FENCE_NAMESPACE,
)

_CUTOVER_CANCEL_MESSAGE = "실물 모델 보안 전환으로 작업을 취소하고 크레딧을 돌려드렸어요."
_PERSONALIZATION_CANCEL_MESSAGE = "개인화 파기로 작업을 취소하고 크레딧을 돌려드렸어요."
_MANIFEST_BATCH_TAG = "facemarketManifestBatchId"
_MANIFEST_DIGEST_VERSION = "facemarket-cutover-v1"
_FROZEN_LICENSE_STATUSES = ("reverification_required", "revoked", "expired")
_CONTROLLER_LOCK_NAMESPACE = 89123017
_CONTROLLER_LOCK_KEY = 8


@dataclass(frozen=True, slots=True, repr=False)
class WriterQuiescence:
    cancelled_count: int
    pending_count: int
    running_count: int


@dataclass(frozen=True, slots=True, repr=False)
class FreezeSummary:
    model_count: int
    license_count: int
    revocation_target_count: int


@dataclass(frozen=True, slots=True, repr=False)
class FreezeResult:
    profile_count: int
    model_count: int
    license_count: int
    revocation_target_count: int


@dataclass(frozen=True, slots=True, repr=False)
class CutoverManifest:
    model_ids: tuple[str, ...]
    license_ids: tuple[str, ...]
    job_ids: tuple[str, ...]
    asset_count: int

    @property
    def target_digest(self) -> str:
        values = [
            _MANIFEST_DIGEST_VERSION,
            *(f"model:{value}" for value in self.model_ids),
            *(f"license:{value}" for value in self.license_ids),
            *(f"job:{value}" for value in self.job_ids),
        ]
        return hashlib.sha256("\n".join([values[0], *sorted(values[1:])]).encode()).hexdigest()

    def public_summary(self) -> dict:
        return {
            "targetDigest": self.target_digest,
            "modelCount": len(self.model_ids),
            "licenseCount": len(self.license_ids),
            "jobCount": len(self.job_ids),
            "assetCount": self.asset_count,
        }


class CutoverBlocked(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_INITIAL_LEGACY_MODEL_SCOPE_SQL = """
select m.id::text as id
  from fm_models m
 where not exists (
       select 1
         from fm_biometric_enrollments e
         join fm_licenses l
           on l.model_id = m.id and l.enrollment_id = e.id
         join fm_biometric_enrollment_photos p
           on p.enrollment_id = e.id and p.angle = 'front'
         join fm_model_assets fa
           on fa.model_id = m.id and fa.view = 'face_front'
         join fm_model_assets ga
           on ga.model_id = m.id and ga.view = 'grid_sedcard'
        where e.id = m.current_enrollment_id
          and e.model_id = m.id
          and e.status = 'passed'
          and e.decision = 'passed'
          and e.consent_version = %s
          and nullif(btrim(e.match_policy_version), '') is not null
          and m.assets_status = 'ready'
          and nullif(btrim(l.vc_id), '') is not null
          and l.vc_id = e.vc_id
          and p.storage_state = 'approved'
          and p.mime_type like 'image/%%'
          and nullif(btrim(p.r2_key), '') is not null
          and nullif(btrim(l.face_image_key), '') is not null
          and p.r2_key = l.face_image_key
          and nullif(btrim(fa.r2_key), '') is not null
          and fa.bucket = 'face'
          and fa.mime like 'image/%%'
          and fa.source_enrollment_id = e.id
          and fa.evidence_version = e.match_policy_version
          and nullif(btrim(ga.r2_key), '') is not null
          and ga.bucket = 'face'
          and ga.mime like 'image/%%'
          and ga.source_enrollment_id = e.id
          and ga.evidence_version = e.match_policy_version
 )
 order by m.id
"""


def _ids(rows: list[dict]) -> list[str]:
    return [str(row["id"]) for row in rows]


def _foreign_batch(row: dict, batch_id: str) -> bool:
    linked = row.get("reverification_batch_id")
    return linked is not None and str(linked) != str(batch_id)


def _vc_targets(licenses: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for row in sorted(licenses, key=lambda item: str(item["id"])):
        vc_id = str(row.get("vc_id") or "").strip()
        if not vc_id or vc_id in seen:
            continue
        seen.add(vc_id)
        out.append(
            {
                "license_id": str(row["id"]),
                "model_id": str(row["model_id"]),
                "vc_id": vc_id,
            }
        )
    return out


async def close_initial_cutover_writers(pool, *, batch_id: str) -> None:
    """Atomically close new biometric writers for one approved cutover batch."""
    async with pool.connection() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update fm_cutover_batches
                   set status = 'draining', started_at = coalesce(started_at, now())
                 where id = %s and status = 'approved'
                 returning id::text as id
                """,
                (batch_id,),
            )
            if await cur.fetchone() is None:
                await cur.execute(
                    "select status from fm_cutover_batches where id = %s",
                    (batch_id,),
                )
                row = await cur.fetchone()
                if row is None or row["status"] not in {"draining", "failed"}:
                    raise CutoverBlocked("cutover_batch_not_approved")
        await conn.commit()


async def _lock_freeze_batch(conn, batch_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, status, started_at, target_digest,
                   model_count, license_count, job_count
              from fm_cutover_batches
             where id = %s
             for update
            """,
            (batch_id,),
        )
        batch = await cur.fetchone()
    if batch is None:
        raise CutoverBlocked("cutover_batch_not_found")
    if batch["started_at"] is None:
        raise CutoverBlocked("cutover_batch_not_started")
    if batch["status"] not in {"draining", "applying"}:
        raise CutoverBlocked("cutover_batch_not_draining")
    return batch


async def _initial_legacy_model_ids(conn) -> list[str]:
    async with conn.cursor() as cur:
        await cur.execute(_INITIAL_LEGACY_MODEL_SCOPE_SQL, (BIOMETRIC_CONSENT_VERSION,))
        return _ids(await cur.fetchall())


async def _initial_legacy_license_ids(conn, model_ids: list[str]) -> list[str]:
    if not model_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id
              from fm_licenses
             where model_id = any(%s)
             order by id
            """,
            (model_ids,),
        )
        return _ids(await cur.fetchall())


async def _batch_tagged_job_ids(conn, batch_id: str) -> list[str]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id
              from jobs
             where metadata->>%s = %s
             order by created_at, id
            """,
            (_MANIFEST_BATCH_TAG, batch_id),
        )
        return _ids(await cur.fetchall())


def _uniq(values) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if value is not None}))


async def _manifest_identity(conn, *, batch_id: str | None = None) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    model_ids = _uniq(await _initial_legacy_model_ids(conn))
    license_ids = _uniq(await _initial_legacy_license_ids(conn, list(model_ids)))
    if batch_id is None:
        jobs = await repo.list_facemarket_scope_jobs(
            conn,
            model_ids=model_ids,
            license_ids=license_ids,
            initial_legacy_project_fallback=True,
        )
        job_ids = _uniq(row.get("id") for row in jobs)
    else:
        job_ids = _uniq(await _batch_tagged_job_ids(conn, batch_id))
    return model_ids, license_ids, job_ids


async def build_initial_cutover_manifest(app, *, batch_id: str | None = None) -> CutoverManifest:
    """Build the PII-free initial legacy cutover identity and mutable inventory count."""
    async with app.state.pool.connection() as conn:
        model_ids, license_ids, job_ids = await _manifest_identity(conn, batch_id=batch_id)
        await conn.commit()
    asset_count = await biometric_purge.initial_cutover_asset_count(
        app,
        model_ids=model_ids,
        license_ids=license_ids,
        job_ids=job_ids,
    )
    return CutoverManifest(model_ids, license_ids, job_ids, asset_count)


async def _load_batch(conn, batch_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, status, target_digest, model_count,
                   license_count, job_count, asset_count
              from fm_cutover_batches
             where id = %s
             for update
            """,
            (batch_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise CutoverBlocked("cutover_batch_not_found")
    return row


def _batch_summary(batch: dict) -> dict:
    return {
        "targetDigest": batch["target_digest"],
        "modelCount": int(batch["model_count"]),
        "licenseCount": int(batch["license_count"]),
        "jobCount": int(batch["job_count"]),
        "assetCount": int(batch["asset_count"]),
    }


def _require_matching_manifest(batch: dict, manifest: CutoverManifest, *, include_assets: bool) -> None:
    if (
        batch["target_digest"] != manifest.target_digest
        or int(batch["model_count"]) != len(manifest.model_ids)
        or int(batch["license_count"]) != len(manifest.license_ids)
        or int(batch["job_count"]) != len(manifest.job_ids)
        or (include_assets and int(batch["asset_count"]) != manifest.asset_count)
    ):
        raise CutoverBlocked("target_digest_changed")


async def create_initial_cutover_batch(app) -> str:
    manifest = await build_initial_cutover_manifest(app)
    async with app.state.pool.connection() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id::text as id, status
                  from fm_cutover_batches
                 order by created_at
                 for update
                """
            )
            rows = await cur.fetchall()
            if any(row["status"] != "planned" for row in rows):
                raise CutoverBlocked("initial_cutover_batch_exists")
            if rows:
                batch_id = rows[0]["id"]
                await cur.execute(
                    """
                    update fm_cutover_batches
                       set target_digest=%s, model_count=%s, license_count=%s,
                           job_count=%s, asset_count=%s
                     where id=%s and status='planned'
                    """,
                    (
                        manifest.target_digest,
                        len(manifest.model_ids),
                        len(manifest.license_ids),
                        len(manifest.job_ids),
                        manifest.asset_count,
                        batch_id,
                    ),
                )
            else:
                await cur.execute(
                    """
                    insert into fm_cutover_batches
                        (target_digest, model_count, license_count, job_count, asset_count)
                    values (%s, %s, %s, %s, %s)
                    returning id::text as id
                    """,
                    (
                        manifest.target_digest,
                        len(manifest.model_ids),
                        len(manifest.license_ids),
                        len(manifest.job_ids),
                        manifest.asset_count,
                    ),
                )
                batch_id = (await cur.fetchone())["id"]
        await conn.commit()
        return batch_id


async def approve_initial_cutover_batch(app, *, batch_id: str, admin_user_id: str) -> None:
    async with app.state.pool.connection() as conn:
        try:
            await repo.lock_facemarket_writer_boundary(conn)
            if not await repo.is_admin(conn, admin_user_id):
                raise CutoverBlocked("admin_required")
            batch = await _load_batch(conn, batch_id)
            if batch["status"] != "planned":
                raise CutoverBlocked("cutover_batch_not_planned")
            model_ids, license_ids, job_ids = await _manifest_identity(conn)
            manifest = CutoverManifest(model_ids, license_ids, job_ids, asset_count=0)
            _require_matching_manifest(batch, manifest, include_assets=False)
            async with conn.cursor() as cur:
                if manifest.job_ids:
                    await cur.execute(
                        """
                        select id::text as id, metadata->>%s as manifest_batch_id
                          from jobs
                         where id = any(%s)
                         for update
                        """,
                        (_MANIFEST_BATCH_TAG, list(manifest.job_ids)),
                    )
                    locked = await cur.fetchall()
                    if _uniq(row["id"] for row in locked) != manifest.job_ids or any(
                        row.get("manifest_batch_id")
                        and row.get("manifest_batch_id") != batch_id
                        for row in locked
                    ):
                        raise CutoverBlocked("manifest_job_tag_conflict")
                    await cur.execute(
                        """
                        update jobs
                           set metadata = metadata || %s::jsonb
                         where id = any(%s)
                           and coalesce(metadata->>%s, %s) = %s
                         returning id::text as id
                        """,
                        (
                            Json({_MANIFEST_BATCH_TAG: batch_id}),
                            list(manifest.job_ids),
                            _MANIFEST_BATCH_TAG,
                            batch_id,
                            batch_id,
                        ),
                    )
                    if _uniq(row["id"] for row in await cur.fetchall()) != manifest.job_ids:
                        raise CutoverBlocked("manifest_job_tag_conflict")
                await cur.execute(
                    """
                    update fm_cutover_batches
                       set status='approved', approved_by=%s, approved_at=now()
                     where id=%s and status='planned'
                    """,
                    (admin_user_id, batch_id),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


async def _try_controller_lock(conn) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_try_advisory_lock(%s, %s) as locked",
            (_CONTROLLER_LOCK_NAMESPACE, _CONTROLLER_LOCK_KEY),
        )
        row = await cur.fetchone()
    await conn.commit()
    return bool(row and row["locked"])


async def _release_controller_lock(conn) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_unlock(%s, %s)",
            (_CONTROLLER_LOCK_NAMESPACE, _CONTROLLER_LOCK_KEY),
        )
    await conn.commit()


async def _set_batch_status(pool, batch_id: str, status: str, *, error_code: str | None = None) -> None:
    async with pool.connection() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        async with conn.cursor() as cur:
            if status == "completed":
                await cur.execute(
                    """
                    update fm_cutover_batches
                       set status='completed', completed_at=coalesce(completed_at, now()),
                           last_error_code=null
                     where id=%s and status='reconciling'
                    """,
                    (batch_id,),
                )
            elif status == "failed":
                await cur.execute(
                    """
                    update fm_cutover_batches
                       set status='failed', last_error_code=%s
                     where id=%s and status in ('draining','applying','reconciling','failed')
                    """,
                    (error_code or "cutover_failed", batch_id),
                )
            else:
                await cur.execute(
                    "update fm_cutover_batches set status=%s where id=%s",
                    (status, batch_id),
                )
        await conn.commit()


async def _batch_status(pool, batch_id: str) -> dict:
    async with pool.connection() as conn:
        batch = await _load_batch(conn, batch_id)
        await conn.commit()
        return batch


async def _linked_resume_state(conn, batch_id: str) -> tuple[list[dict], list[dict]]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, status, reverification_batch_id::text as reverification_batch_id
              from fm_models
             where reverification_batch_id = %s
             order by id
            """,
            (batch_id,),
        )
        models = await cur.fetchall()
        await cur.execute(
            """
            select id::text as id, status, reverification_batch_id::text as reverification_batch_id
              from fm_licenses
             where reverification_batch_id = %s
             order by id
            """,
            (batch_id,),
        )
        licenses = await cur.fetchall()
    return models, licenses


async def _mark_resume_invalid(conn, batch_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update fm_cutover_batches
               set last_error_code='cutover_resume_state_invalid'
             where id=%s and status='failed'
            """,
            (batch_id,),
        )


async def _resume_failed_batch(app, batch_id: str) -> str:
    async with app.state.pool.connection() as conn:
        try:
            await repo.lock_facemarket_writer_boundary(conn)
            batch = await _load_batch(conn, batch_id)
            if batch["status"] != "failed":
                raise CutoverBlocked("cutover_batch_not_failed")
            model_ids, license_ids, job_ids = await _manifest_identity(conn, batch_id=batch_id)
            manifest = CutoverManifest(model_ids, license_ids, job_ids, asset_count=0)
            _require_matching_manifest(batch, manifest, include_assets=False)
            models, licenses = await _linked_resume_state(conn, batch_id)
            linked_model_ids = _uniq(row["id"] for row in models)
            linked_license_ids = _uniq(row["id"] for row in licenses)
            if not linked_model_ids and not linked_license_ids:
                status = "draining"
            elif (
                linked_model_ids == manifest.model_ids
                and linked_license_ids == manifest.license_ids
                and all(row["status"] == "reverification_required" for row in models)
                and all(row["status"] in _FROZEN_LICENSE_STATUSES for row in licenses)
            ):
                status = "reconciling"
            else:
                await _mark_resume_invalid(conn, batch_id)
                await conn.commit()
                raise CutoverBlocked("cutover_resume_state_invalid")
            async with conn.cursor() as cur:
                await cur.execute(
                    "update fm_cutover_batches set status=%s where id=%s and status='failed'",
                    (status, batch_id),
                )
            await conn.commit()
            return status
        except Exception:
            await conn.rollback()
            raise


async def _verify_reconciling_links(pool, batch_id: str, manifest: CutoverManifest) -> None:
    async with pool.connection() as conn:
        await repo.lock_facemarket_writer_boundary(conn)
        batch = await _load_batch(conn, batch_id)
        _require_matching_manifest(batch, manifest, include_assets=False)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select count(*)::int as count
                  from fm_models
                 where reverification_batch_id=%s
                   and status='reverification_required'
                """,
                (batch_id,),
            )
            model_count = int((await cur.fetchone() or {}).get("count") or 0)
            await cur.execute(
                """
                select count(*)::int as count
                  from fm_licenses
                 where reverification_batch_id=%s
                   and status = any(%s)
                """,
                (batch_id, list(_FROZEN_LICENSE_STATUSES)),
            )
            license_count = int((await cur.fetchone() or {}).get("count") or 0)
            if model_count != len(manifest.model_ids) or license_count != len(manifest.license_ids):
                raise CutoverBlocked("cutover_resume_state_invalid")
            await cur.execute(
                "update fm_cutover_batches set status='reconciling' where id=%s and status='applying'",
                (batch_id,),
            )
        await conn.commit()


async def apply_initial_cutover(
    app,
    *,
    batch_id: str,
    confirmation: str,
    drain_timeout_seconds: float,
) -> dict:
    if confirmation != batch_id:
        raise CutoverBlocked("confirmation_mismatch")
    pool = app.state.pool
    batch = await _batch_status(pool, batch_id)
    if batch["status"] == "completed":
        return _batch_summary(batch)
    async with pool.connection() as controller_conn:
        if not await _try_controller_lock(controller_conn):
            raise CutoverBlocked("cutover_controller_busy")
        try:
            batch = await _batch_status(pool, batch_id)
            if batch["status"] == "completed":
                return _batch_summary(batch)
            if batch["status"] == "failed":
                resumed = await _resume_failed_batch(app, batch_id)
                batch = {**batch, "status": resumed}
            if batch["status"] == "approved":
                await close_initial_cutover_writers(pool, batch_id=batch_id)
            if batch["status"] in {"approved", "draining"}:
                await quiesce_initial_cutover_writers(
                    pool,
                    batch_id=batch_id,
                    timeout_seconds=drain_timeout_seconds,
                )
                manifest = await build_initial_cutover_manifest(app, batch_id=batch_id)
                _require_matching_manifest(batch, manifest, include_assets=False)
                await freeze_initial_cutover_batch(pool, batch_id=batch_id)
            else:
                manifest = await build_initial_cutover_manifest(app, batch_id=batch_id)
            await _verify_reconciling_links(pool, batch_id, manifest)
            result = await purge_biometric_scope(
                app,
                batch_id=batch_id,
                reason="reverification",
            )
            if (
                not result.complete
                or result.target_count != result.confirmed_absent_count
                or result.model_count != len(manifest.model_ids)
            ):
                raise CutoverBlocked("purge_incomplete")
            await _set_batch_status(pool, batch_id, "completed")
            return manifest.public_summary()
        except CutoverBlocked as exc:
            await _set_batch_status(pool, batch_id, "failed", error_code=exc.code)
            raise
        except biometric_purge.PurgeIncomplete as exc:
            await _set_batch_status(pool, batch_id, "failed", error_code=exc.code)
            raise CutoverBlocked(exc.code)
        finally:
            await _release_controller_lock(controller_conn)


async def _lock_target_licenses(conn, model_ids: list[str]) -> list[dict]:
    if not model_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, model_id::text as model_id, status, previous_status,
                   reverification_batch_id::text as reverification_batch_id, vc_id
              from fm_licenses l
             where l.model_id = any(%s)
             order by l.model_id, l.id
             for update
            """,
            (model_ids,),
        )
        return await cur.fetchall()


async def _lock_target_models(conn, model_ids: list[str]) -> list[dict]:
    if not model_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id, status, previous_status,
                   reverification_batch_id::text as reverification_batch_id
              from fm_models
             where id = any(%s)
             order by id
             for update
            """,
            (model_ids,),
        )
        return await cur.fetchall()


async def freeze_initial_cutover_batch(pool, *, batch_id: str) -> FreezeSummary:
    """Freeze the server-discovered initial legacy scope in one local transaction."""
    async with pool.connection() as conn:
        try:
            await repo.lock_facemarket_writer_boundary(conn)
            batch = await _lock_freeze_batch(conn, batch_id)
            model_ids = await _initial_legacy_model_ids(conn)
            licenses = await _lock_target_licenses(conn, model_ids)
            models = await _lock_target_models(conn, model_ids)
            if len(models) != int(batch["model_count"]) or len(licenses) != int(batch["license_count"]):
                raise CutoverBlocked("target_scope_changed")
            if any(_foreign_batch(row, batch_id) for row in (*models, *licenses)):
                raise CutoverBlocked("target_scope_link_conflict")
            job_ids = _uniq(await _batch_tagged_job_ids(conn, batch_id))
            stable = CutoverManifest(
                model_ids=_uniq(row["id"] for row in models),
                license_ids=_uniq(row["id"] for row in licenses),
                job_ids=job_ids,
                asset_count=0,
            )
            if batch["target_digest"] != stable.target_digest or int(batch["job_count"]) != len(job_ids):
                raise CutoverBlocked("target_digest_changed")

            license_ids = _ids(licenses)
            if license_ids:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        update fm_licenses
                           set previous_status=coalesce(previous_status,status),
                               reverification_batch_id=coalesce(reverification_batch_id,%s),
                               status=case
                                   when status in ('pending','active')
                                   then 'reverification_required'
                                   else status
                               end
                         where id = any(%s)
                        """,
                        (batch_id, license_ids),
                    )
            targets = _vc_targets(licenses)
            enqueue_failed = False
            try:
                for target in targets:
                    await facemarket.enqueue_vc_revocation(conn, **target)
            except Exception:
                enqueue_failed = True
            if enqueue_failed:
                raise CutoverBlocked("vc_revocation_enqueue_failed")

            if model_ids:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        update fm_models
                           set previous_status=coalesce(previous_status,status),
                               reverification_batch_id=coalesce(reverification_batch_id,%s),
                               status=case
                                   when status in ('pending','verified')
                                   then 'reverification_required'
                                   else status
                               end
                         where id = any(%s)
                        """,
                        (batch_id, model_ids),
                    )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update fm_cutover_batches
                       set status = case when status = 'draining' then 'applying' else status end
                     where id = %s and status in ('draining','applying')
                    """,
                    (batch_id,),
                )
            await conn.commit()
            return FreezeSummary(
                model_count=len(models),
                license_count=len(licenses),
                revocation_target_count=len(targets),
            )
        except Exception:
            await conn.rollback()
            raise


async def _pending_cutover_jobs(conn) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id from jobs
            where status='pending' and kind = any(%s)
            order by created_at
            for update skip locked
            limit 50
            """,
            (list(repo.FACEMARKET_CUTOVER_JOB_KINDS),),
        )
        return await cur.fetchall()


async def _cutover_job_counts(conn) -> tuple[int, int]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select
              count(*) filter (where status='pending')::int as pending_count,
              count(*) filter (where status='running')::int as running_count
            from jobs
            where kind = any(%s) and status in ('pending', 'running')
            """,
            (list(repo.FACEMARKET_CUTOVER_JOB_KINDS),),
        )
        row = await cur.fetchone()
    return int(row["pending_count"]), int(row["running_count"])


async def _session_lock_count(conn, namespace: int, ids: tuple[str, ...]) -> int:
    locked = 0
    async with conn.cursor() as cur:
        for session_id in ids:
            await cur.execute(
                "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
                (namespace, str(session_id).lower()),
            )
            if (await cur.fetchone())["locked"]:
                await cur.execute(
                    "select pg_advisory_unlock(%s, hashtext(%s))",
                    (namespace, str(session_id).lower()),
                )
            else:
                locked += 1
    return locked


async def _photo_lock_count(conn) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id from fm_biometric_enrollments
            where status in ('photos_pending', 'liveness_pending', 'processing')
            order by id
            """
        )
        rows = await cur.fetchall()
    return await _session_lock_count(
        conn, _PHOTO_FENCE_NAMESPACE, tuple(row["id"] for row in rows)
    )


async def _user_photo_lock_count(conn, user_id: str) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id
              from fm_biometric_enrollments
             where user_id = %s
               and status in (
                   'photos_pending',
                   'liveness_pending',
                   'processing',
                   'asset_building',
                   'license_pending',
                   'vc_pending'
               )
             order by id
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return await _session_lock_count(
        conn, _PHOTO_FENCE_NAMESPACE, tuple(row["id"] for row in rows)
    )


async def quiesce_initial_cutover_writers(
    pool,
    *,
    batch_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.25,
) -> WriterQuiescence:
    """Cancel pending cutover jobs and wait until running/photo writers drain."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    cancelled = 0
    last_pending = 0
    last_running = 0
    while True:
        async with pool.connection() as conn:
            rows = await _pending_cutover_jobs(conn)
            for row in rows:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="facemarket_cutover",
                    message=_CUTOVER_CANCEL_MESSAGE,
                )
                cancelled += int(changed)
                if changed:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update jobs set metadata = metadata || %s::jsonb where id = %s",
                            (Json({"cutoverBatchId": batch_id}), row["id"]),
                        )
            last_pending, last_running = await _cutover_job_counts(conn)
            photo_locks = await _photo_lock_count(conn)
            await conn.commit()
        if last_pending == 0 and last_running == 0 and photo_locks == 0:
            return WriterQuiescence(cancelled, 0, 0)
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("writers_not_drained")
        await asyncio.sleep(poll_interval_seconds)


async def _load_purging_profile(conn, user_id: str) -> str:
    async with conn.cursor() as cur:
        await cur.execute(
            "select p.status from personalization_profiles p where p.user_id = %s for update",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None or row["status"] != "purging":
        raise CutoverBlocked("personalization_profile_not_purging")
    return user_id


async def quiesce_personalization_writers(
    pool,
    *,
    user_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.25,
) -> WriterQuiescence:
    """Cancel pending and drain running personalization_generation for one purging user."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    cancelled = 0
    last_pending = 0
    last_running = 0
    while True:
        async with pool.connection() as conn:
            await _load_purging_profile(conn, user_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select j.id::text as id, g.id::text as generation_id
                    from jobs j
                    left join personalization_generations g on g.job_id = j.id
                    where j.status='pending'
                      and j.kind='personalization_generation'
                      and j.user_id=%s
                    order by j.created_at
                    for update of j skip locked
                    limit 50
                    """,
                    (user_id,),
                )
                pending = await cur.fetchall()
            for row in pending:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="personalization_purge",
                    message=_PERSONALIZATION_CANCEL_MESSAGE,
                )
                cancelled += int(changed)
                if changed and row.get("generation_id"):
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            update personalization_generations
                            set status='error', error_code='personalization_purge'
                            where id=%s
                            """,
                            (row["generation_id"],),
                        )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    select
                      count(*) filter (where status='pending')::int as pending_count,
                      count(*) filter (where status='running')::int as running_count
                    from jobs
                    where kind='personalization_generation'
                      and user_id=%s
                      and status in ('pending', 'running')
                    """,
                    (user_id,),
                )
                row = await cur.fetchone()
            last_pending = int(row["pending_count"])
            last_running = int(row["running_count"])
            await conn.commit()
        if last_pending == 0 and last_running == 0:
            return WriterQuiescence(cancelled, 0, 0)
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("writers_not_drained")
        await asyncio.sleep(poll_interval_seconds)


async def freeze_user_biometric_scope(conn, *, user_id: str, reason: str) -> FreezeResult:
    """Close one user's biometric scope before shared R2 purge."""
    if reason not in {"withdrawal", "account_delete"}:
        raise CutoverBlocked("invalid_purge_reason")
    await repo.lock_facemarket_writer_boundary(conn)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text as id
              from personalization_profiles
             where user_id=%s and status <> 'purged'
             order by created_at, id
             for update
            """,
            (user_id,),
        )
        profiles = await cur.fetchall()
        await cur.execute(
            """
            select id::text as id, status
              from fm_models
             where user_id=%s
             order by id
             for update
            """,
            (user_id,),
        )
        models = await cur.fetchall()
    model_ids = [row["id"] for row in models]
    licenses: list[dict] = []
    if model_ids:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id::text as id, model_id::text as model_id, status,
                       previous_status, vc_id
                  from fm_licenses
                 where model_id = any(%s)
                 order by id
                 for update
                """,
                (model_ids,),
            )
            licenses = await cur.fetchall()
            license_ids = [row["id"] for row in licenses]
            if license_ids:
                await cur.execute(
                    """
                    update fm_licenses
                       set previous_status=coalesce(previous_status,status),
                           status=case
                               when status in ('pending','active','reverification_required')
                               then 'revoked'
                               else status
                           end
                     where id = any(%s)
                    """,
                    (license_ids,),
                )
            targets = _vc_targets(licenses)
            enqueue_failed = False
            try:
                for target in targets:
                    await facemarket.enqueue_vc_revocation(conn, **target)
            except Exception:
                enqueue_failed = True
            if enqueue_failed:
                raise CutoverBlocked("vc_revocation_enqueue_failed")
            if reason == "account_delete":
                model_status = "suspended"
            else:
                model_status = "reverification_required"
            await cur.execute(
                """
                update fm_models
                   set previous_status=coalesce(previous_status,status),
                       status=case
                           when status='suspended' then status
                           else %s
                       end
                 where id = any(%s)
                """,
                (model_status, model_ids),
            )
            await cur.execute(
                """
                update fm_biometric_enrollments
                   set status='cancelled', reason=%s, completed_at=coalesce(completed_at, now())
                 where user_id=%s
                   and status in ('photos_pending','liveness_pending','processing',
                                  'asset_building','license_pending','vc_pending')
                """,
                (reason, user_id),
            )
    else:
        targets = []
    return FreezeResult(
        profile_count=len(profiles),
        model_count=len(models),
        license_count=len(licenses),
        revocation_target_count=len(targets),
    )


async def quiesce_user_facemarket_writers(
    pool,
    *,
    user_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.25,
) -> WriterQuiescence:
    """Cancel pending and drain running FaceMarket writer jobs for one model owner."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    cancelled = 0
    last_pending = 0
    last_running = 0
    last_photo_locks = 0
    last_model_asset_locks = 0
    while True:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id from fm_models where user_id=%s order by id",
                    (user_id,),
                )
                model_ids = tuple(row["id"] for row in await cur.fetchall())
                license_ids = ()
                if model_ids:
                    await cur.execute(
                        "select id::text as id from fm_licenses where model_id = any(%s)",
                        (list(model_ids),),
                    )
                    license_ids = tuple(row["id"] for row in await cur.fetchall())
            jobs = await repo.list_facemarket_scope_jobs(
                conn, model_ids=model_ids, license_ids=license_ids
            )
            for row in [j for j in jobs if j.get("status") == "pending"]:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="personalization_purge",
                    message=_PERSONALIZATION_CANCEL_MESSAGE,
                )
                cancelled += int(changed)
            last_pending = sum(1 for row in jobs if row.get("status") == "pending")
            last_running = sum(1 for row in jobs if row.get("status") == "running")
            last_photo_locks = await _user_photo_lock_count(conn, user_id)
            last_model_asset_locks = await _session_lock_count(
                conn, _MODEL_ASSET_FENCE_NAMESPACE, model_ids
            )
            await conn.commit()
        if (
            last_pending == 0
            and last_running == 0
            and last_photo_locks == 0
            and last_model_asset_locks == 0
        ):
            return WriterQuiescence(cancelled, 0, 0)
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("writers_not_drained")
        await asyncio.sleep(poll_interval_seconds)
