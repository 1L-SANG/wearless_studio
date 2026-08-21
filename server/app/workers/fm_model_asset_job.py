"""Enrollment-bound FaceMarket model asset promotion.

Task 6 owns biometric matching. This worker only promotes the exact enrollment
photos that already passed, records evidence binding, and never emits R2 keys.
"""

import asyncio
import hashlib
import logging

from psycopg.types.json import Json

from .. import repo
from ..agents.face_grid import compose_sedcard
from ..facemarket_enrollment import (
    _MODEL_ASSET_FENCE_NAMESPACE,
    _drain_model_asset_cleanup,
    _run_r2_call_until_done,
)
from ..r2 import IMMUTABLE_CACHE, enrollment_original_key, ext_for_mime, model_asset_key
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.fm_model_asset_job")

_ANGLES = ("front", "angle45", "side")
_OLD_ASSET_ANGLE = {"face_front": "front", "grid_sedcard": "side"}
_CUTOVER_CODE = "facemarket_cutover_in_progress"
_CUTOVER_MESSAGE = "실물 모델 보안 전환 중이라 잠시 후 다시 시도해 주세요."


class CutoverInProgress(RuntimeError):
    pass


class AccountClosed(RuntimeError):
    pass


async def _assert_account_open(conn, user_id: str) -> None:
    if await repo.user_account_delete_completed(conn, user_id):
        raise AccountClosed("account_closed")


def _ordered_faces(rows: list[dict]) -> list[dict] | None:
    by_angle = {row.get("angle"): row for row in rows}
    if set(by_angle) != set(_ANGLES):
        return None
    faces = [by_angle[angle] for angle in _ANGLES]
    if any(face.get("storage_state") != "quarantine" for face in faces):
        return None
    if any(face.get("status") != "asset_building" for face in faces):
        return None
    return faces


def _source_hash(faces: list[dict]) -> str:
    return hashlib.sha256(
        "|".join(
            str(face.get("image_digest") or face.get("r2_key") or "")
            for face in faces
        ).encode()
    ).hexdigest()


async def _register_cleanup(conn, enrollment_id: str, angle: str, key: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into fm_biometric_enrollment_photo_cleanup
                (enrollment_id, angle, r2_key, reason)
            values (%s, %s, %s, 'delete')
            on conflict (enrollment_id, r2_key)
            do update set angle = excluded.angle, reason = 'delete', not_before = now()
            """,
            (enrollment_id, angle, key),
        )


async def _remove_cleanup(conn, enrollment_id: str, key: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            delete from fm_biometric_enrollment_photo_cleanup
            where enrollment_id = %s and r2_key = %s
            """,
            (enrollment_id, key),
        )


async def _try_model_asset_fence(conn, model_id: str) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_try_advisory_lock(%s, hashtext(%s)) as locked",
            (_MODEL_ASSET_FENCE_NAMESPACE, str(model_id).lower()),
        )
        return bool((await cur.fetchone())["locked"])


async def _unlock_model_asset_fence_once(conn, model_id: str) -> None:
    await conn.rollback()
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_unlock(%s, hashtext(%s)) as unlocked",
            (_MODEL_ASSET_FENCE_NAMESPACE, str(model_id).lower()),
        )
        if not (await cur.fetchone())["unlocked"]:
            raise RuntimeError("model asset fence was not owned by this connection")
    await conn.rollback()


async def _unlock_model_asset_fence(conn, model_id: str) -> None:
    task = asyncio.create_task(_unlock_model_asset_fence_once(conn, model_id))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            await conn.close()
        raise
    except Exception:
        await conn.close()
        raise


async def _reject_cutover_closed(conn) -> None:
    await repo.lock_facemarket_writer_boundary(conn)
    if await repo.facemarket_writer_boundary_closed(conn):
        raise CutoverInProgress(_CUTOVER_CODE)


async def run_fm_model_asset_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id, lease = job["id"], job["user_id"], job["lease_token"]
    payload = job.get("payload") or {}
    model_id = payload.get("modelId")
    enrollment_id = payload.get("enrollmentId")
    r2_face = getattr(app.state, "r2_face", None)
    settings = app.state.settings
    attempt_keys: list[str] = []

    async def cleanup_attempt() -> None:
        return None

    async def fail(
        reason: str,
        code: str = "asset_build_failed",
        message: str = "자산 생성 중 오류가 발생했어요.",
    ) -> None:
        await cleanup_attempt()
        try:
            async with pool.connection() as conn:
                finalized = await repo._finalize_job_failure(
                    conn,
                    job_id=job_id,
                    lease_token=lease,
                    message=message,
                    metadata={"error": reason},
                    code=code,
                )
                if finalized and model_id and enrollment_id:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            update fm_models
                            set assets_status='failed'
                            where id=%s and user_id=%s and current_enrollment_id=%s
                              and assets_status='building'
                            """,
                            (model_id, user_id, enrollment_id),
                        )
                        if enrollment_id:
                            await cur.execute(
                                """
                                update fm_biometric_enrollments
                                set status='failed', decision='failed', reason=%s,
                                    completed_at=now()
                                where id=%s and model_id=%s and user_id=%s
                                  and status='asset_building'
                                """,
                                (reason, enrollment_id, model_id, user_id),
                            )
                await conn.commit()
        except Exception as exc:
            log.warning(
                "fm_model_asset_failure_finalize_failed",
                extra={"job_id": job_id, "error_type": type(exc).__name__},
            )

    try:
        if not model_id:
            await fail("missing_model_id", "missing_model_id")
            return
        if r2_face is None:
            await fail("face_storage_unavailable", "face_storage_unavailable")
            return
        biometric_enabled = getattr(settings, "fm_biometric_enrollment_enabled", False)
        if not enrollment_id and biometric_enabled:
            await fail("missing_enrollment_id", "missing_enrollment_id")
            return
        if not enrollment_id:
            old_assets: list[dict] = []
            cleanup_targets: list[str] = []
            async with pool.connection() as conn:
                locked = False
                try:
                    await _reject_cutover_closed(conn)
                    locked = await _try_model_asset_fence(conn, model_id)
                    if not locked:
                        await repo._finalize_job_failure(
                            conn,
                            job_id=job_id,
                            lease_token=lease,
                            message="자산 생성 중 오류가 발생했어요.",
                            metadata={"error": "legacy_model_asset_busy"},
                            code="legacy_model_asset_busy",
                        )
                        await conn.commit()
                        return
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                            (job_id, lease),
                        )
                        if await cur.fetchone() is None:
                            raise RuntimeError("lease_lost")
                        await _assert_account_open(conn, user_id)
                        await cur.execute(
                            "select m.status, p.id as profile_id from fm_models m "
                            "join personalization_profiles p on p.user_id = m.user_id "
                            "where m.id=%s and m.user_id=%s "
                            "order by p.created_at desc limit 1 "
                            "for update",
                            (model_id, user_id),
                        )
                        mrow = await cur.fetchone()
                        if not mrow or mrow.get("status") != "verified":
                            raise RuntimeError("legacy_model_missing")
                        await cur.execute(
                            """
                            update fm_models
                            set assets_status='building'
                            where id=%s and user_id=%s and status='verified'
                            """,
                            (model_id, user_id),
                        )
                        await cur.execute(
                            "select angle, r2_key, mime_type from personalization_face_photos "
                            "where profile_id=%s",
                            (mrow["profile_id"],),
                        )
                        legacy_rows = await cur.fetchall()
                        await cur.execute(
                            "select view, r2_key from fm_model_assets where model_id=%s for update",
                            (model_id,),
                        )
                        old_assets = await cur.fetchall()
                    await conn.commit()
                    by_angle = {row.get("angle"): row for row in legacy_rows}
                    if set(by_angle) != set(_ANGLES):
                        raise RuntimeError("legacy_face_photos_incomplete")
                    faces = [by_angle[angle] for angle in _ANGLES]
                    face_bytes = [
                        await _run_r2_call_until_done(r2_face.get_bytes, face["r2_key"])
                        for face in faces
                    ]
                    grid = compose_sedcard(face_bytes)
                    registered = []
                    for view, data, mime in (
                        ("grid_sedcard", grid, "image/png"),
                        ("face_front", face_bytes[0], faces[0]["mime_type"]),
                    ):
                        key = model_asset_key(model_id, "legacy", view, ext_for_mime(mime) or "png")
                        attempt_keys.append(key)
                        async with conn.cursor() as cur:
                            await cur.execute(
                                """
                                insert into fm_model_asset_cleanup
                                    (model_id, r2_key, reason)
                                values (%s, %s, 'upload_orphan')
                                on conflict (model_id, r2_key) do update set
                                    reason='upload_orphan', not_before=now()
                                """,
                                (model_id, key),
                            )
                        await conn.commit()
                        await _run_r2_call_until_done(
                            lambda key=key, data=data, mime=mime: r2_face.put_bytes(
                                key, data, mime, cache=IMMUTABLE_CACHE
                            )
                        )
                        registered.append((view, key, mime))
                    registered_keys = {key for _view, key, _mime in registered}
                    cleanup_targets = [
                        row["r2_key"]
                        for row in old_assets
                        if row.get("r2_key") and row["r2_key"] not in registered_keys
                    ]
                    await _reject_cutover_closed(conn)
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                            (job_id, lease),
                        )
                        if await cur.fetchone() is None:
                            raise RuntimeError("lease_lost")
                        await _assert_account_open(conn, user_id)
                        await cur.execute(
                            """
                            select id
                            from fm_models
                            where id=%s and user_id=%s and status='verified'
                              and assets_status='building'
                            for update
                            """,
                            (model_id, user_id),
                        )
                        if await cur.fetchone() is None:
                            raise RuntimeError("legacy_model_binding_lost")
                        for old_key in cleanup_targets:
                            await cur.execute(
                                """
                                insert into fm_model_asset_cleanup
                                    (model_id, r2_key, reason)
                                values (%s, %s, 'superseded')
                                on conflict (model_id, r2_key) do update set
                                    reason='superseded', not_before=now()
                                """,
                                (model_id, old_key),
                            )
                        for _view, key, _mime in registered:
                            await cur.execute(
                                "delete from fm_model_asset_cleanup where model_id=%s and r2_key=%s",
                                (model_id, key),
                            )
                        for view, key, mime in registered:
                            await cur.execute(
                                """
                                insert into fm_model_assets
                                    (model_id, view, r2_key, mime, bucket, evidence_version)
                                values (%s, %s, %s, %s, 'face', 'legacy-personalization-v1')
                                on conflict (model_id, view) do update set
                                    r2_key=excluded.r2_key,
                                    mime=excluded.mime,
                                    bucket='face',
                                    evidence_version=excluded.evidence_version
                                """,
                                (model_id, view, key, mime),
                            )
                        await cur.execute(
                            """
                            update fm_models
                            set assets_status='ready', assets_source_hash=%s
                            where id=%s and user_id=%s and status='verified'
                              and assets_status='building'
                            """,
                            (_source_hash(faces), model_id, user_id),
                        )
                        await cur.execute(
                            """
                            update jobs set status='done', progress=100, locked_by=null,
                                locked_at=null, finished_at=now(), result=%s
                            where id=%s
                            """,
                            (
                                Json({"data": {"modelId": model_id, "assetsStatus": "ready"}}),
                                job_id,
                            ),
                        )
                    await conn.commit()
                finally:
                    if locked:
                        await _unlock_model_asset_fence(conn, model_id)
            if cleanup_targets:
                await _drain_model_asset_cleanup(
                    app, model_id=model_id, limit=len(cleanup_targets)
                )
            return

        async with pool.connection() as conn:
            locked = False
            try:
                await _reject_cutover_closed(conn)
                locked = await _try_model_asset_fence(conn, model_id)
                if not locked:
                    await fail("model_asset_busy", "model_asset_busy")
                    return
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                        (job_id, lease),
                    )
                    if await cur.fetchone() is None:
                        raise RuntimeError("lease_lost")
                    await _assert_account_open(conn, user_id)
                    await cur.execute(
                        """
                        select e.status, e.match_policy_version, p.angle, p.r2_key,
                               p.mime_type, p.image_digest, p.storage_state
                        from fm_biometric_enrollment_photos p
                        join fm_biometric_enrollments e on e.id=p.enrollment_id
                        join fm_models m on m.id=e.model_id
                        where e.id=%s and e.model_id=%s and e.user_id=%s
                          and e.status='asset_building'
                          and m.current_enrollment_id=e.id
                          and m.reverification_batch_id is null
                        order by case p.angle
                          when 'front' then 0 when 'angle45' then 1 when 'side' then 2 end
                        """,
                        (enrollment_id, model_id, user_id),
                    )
                    rows = await cur.fetchall()
                await conn.commit()

                faces = _ordered_faces(rows)
                if faces is None:
                    await fail("enrollment_photos_invalid", "enrollment_photos_invalid")
                    return

                originals: list[tuple[str, str, str]] = []
                for face in faces:
                    ext = ext_for_mime(face.get("mime_type")) or "png"
                    key = enrollment_original_key(model_id, enrollment_id, face["angle"], ext)
                    attempt_keys.append(key)
                    await _register_cleanup(conn, enrollment_id, face["angle"], key)
                    await conn.commit()
                    await _run_r2_call_until_done(r2_face.copy, face["r2_key"], key, face["mime_type"])
                    originals.append((face["angle"], face["r2_key"], key))

                face_bytes = [
                    await _run_r2_call_until_done(r2_face.get_bytes, face["r2_key"])
                    for face in faces
                ]
                await _emit(pool, job_id, "progress", {"progress": 40, "phase": "inputs_loaded"})

                grid = compose_sedcard(face_bytes)
                derived = [
                    ("grid_sedcard", grid, "image/png"),
                    ("face_front", face_bytes[0], faces[0]["mime_type"]),
                ]
                registered = []
                for view, data, mime in derived:
                    ext = ext_for_mime(mime) or "png"
                    key = model_asset_key(model_id, enrollment_id, view, ext)
                    attempt_keys.append(key)
                    await _register_cleanup(
                        conn,
                        enrollment_id,
                        _OLD_ASSET_ANGLE.get(view, "front"),
                        key,
                    )
                    await conn.commit()
                    await _run_r2_call_until_done(
                        lambda key=key, data=data, mime=mime: r2_face.put_bytes(
                            key, data, mime, cache=IMMUTABLE_CACHE
                        )
                    )
                    registered.append((view, key, mime))
                await _emit(pool, job_id, "progress", {"progress": 80, "phase": "stored"})

                evidence_version = faces[0]["match_policy_version"]
                await _reject_cutover_closed(conn)
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                        (job_id, lease),
                    )
                    if await cur.fetchone() is None:
                        raise RuntimeError("lease_lost")
                    await _assert_account_open(conn, user_id)
                    await cur.execute(
                        """
                        select status, match_policy_version
                        from fm_biometric_enrollments
                        where id=%s and model_id=%s and user_id=%s and status='asset_building'
                        for update
                        """,
                        (enrollment_id, model_id, user_id),
                    )
                    enrollment = await cur.fetchone()
                    if not enrollment or enrollment.get("match_policy_version") != evidence_version:
                        raise RuntimeError("enrollment_binding_lost")
                    await cur.execute(
                        """
                        select status, current_enrollment_id
                        from fm_models
                        where id=%s and user_id=%s and current_enrollment_id=%s
                          and status in ('pending', 'reverification_required')
                          and reverification_batch_id is null
                        for update
                        """,
                        (model_id, user_id, enrollment_id),
                    )
                    model = await cur.fetchone()
                    if not model:
                        raise RuntimeError("model_binding_lost")
                    await cur.execute(
                        "select view, r2_key from fm_model_assets where model_id=%s for update",
                        (model_id,),
                    )
                    old_assets = await cur.fetchall()
                    registered_keys = {key for _view, key, _mime in registered}
                    cleanup_targets = [
                        (angle, old_key)
                        for angle, old_key, new_key in originals
                        if old_key != new_key
                    ]
                    cleanup_targets.extend(
                        (_OLD_ASSET_ANGLE.get(row.get("view"), "front"), row["r2_key"])
                        for row in old_assets
                        if row.get("r2_key") and row["r2_key"] not in registered_keys
                    )
                    for angle, key in cleanup_targets:
                        await _register_cleanup(conn, enrollment_id, angle, key)
                    for view, key, mime in registered:
                        await cur.execute(
                            """
                            insert into fm_model_assets
                                (model_id, view, r2_key, mime, bucket,
                                 source_enrollment_id, evidence_version)
                            values (%s, %s, %s, %s, 'face', %s, %s)
                            on conflict (model_id, view) do update set
                                r2_key=excluded.r2_key,
                                mime=excluded.mime,
                                bucket='face',
                                source_enrollment_id=excluded.source_enrollment_id,
                                evidence_version=excluded.evidence_version
                            """,
                            (model_id, view, key, mime, enrollment_id, evidence_version),
                        )
                    for angle, _old_key, new_key in originals:
                        await cur.execute(
                            """
                            update fm_biometric_enrollment_photos
                            set r2_key=%s, storage_state='approved', approved_at=now()
                            where enrollment_id=%s and angle=%s and storage_state='quarantine'
                            """,
                            (new_key, enrollment_id, angle),
                        )
                    await cur.execute(
                        """
                        update fm_models
                        set assets_status='ready',
                            current_enrollment_id=%s,
                            assets_source_hash=%s
                        where id=%s and user_id=%s and status in ('pending', 'reverification_required')
                          and reverification_batch_id is null
                        """,
                        (enrollment_id, _source_hash(faces), model_id, user_id),
                    )
                    await cur.execute(
                        """
                        update fm_biometric_enrollments
                        set status='license_pending', decision='passed', completed_at=now()
                        where id=%s and model_id=%s and user_id=%s and status='asset_building'
                        """,
                        (enrollment_id, model_id, user_id),
                    )
                    await cur.execute(
                        """
                        update jobs set status='done', progress=100, locked_by=null,
                            locked_at=null, finished_at=now(), result=%s
                        where id=%s
                        """,
                        (
                            Json({"data": {
                                "modelId": model_id,
                                "enrollmentId": enrollment_id,
                                "assetsStatus": "ready",
                            }}),
                            job_id,
                        ),
                    )
                    await cur.execute(
                        "insert into job_events (job_id, event_type, payload) values (%s,'done',%s)",
                        (job_id, Json({"data": {
                            "modelId": model_id,
                            "enrollmentId": enrollment_id,
                            "assetsStatus": "ready",
                        }})),
                    )
                await conn.commit()
            finally:
                if locked:
                    await _unlock_model_asset_fence(conn, model_id)

        attempt_keys = []
        current_keys = [key for _angle, _old_key, key in originals] + [
            key for _view, key, _mime in registered
        ]
        for key in current_keys:
            async with pool.connection() as conn:
                await _remove_cleanup(conn, enrollment_id, key)
                await conn.commit()
        for angle, key in cleanup_targets:
            try:
                await _run_r2_call_until_done(r2_face.delete, key)
            except Exception as exc:
                log.warning(
                    "fm_model_asset_post_commit_cleanup_failed",
                    extra={
                        "enrollment_id": enrollment_id,
                        "angle": angle,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            async with pool.connection() as conn:
                await _remove_cleanup(conn, enrollment_id, key)
                await conn.commit()
    except asyncio.CancelledError:
        await cleanup_attempt()
        raise
    except CutoverInProgress:
        await fail(_CUTOVER_CODE, _CUTOVER_CODE, _CUTOVER_MESSAGE)
    except AccountClosed:
        await fail("account_closed", "account_closed")
    except Exception as exc:
        log.warning(
            "fm_model_asset_failed",
            extra={"job_id": job_id, "error_type": type(exc).__name__},
        )
        await fail("asset_build_failed", "asset_build_failed")
