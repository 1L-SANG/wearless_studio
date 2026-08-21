"""Durable personalization/FaceMarket biometric purge worker.

PII rule: job payload/result/events/logs carry only reason, bounded codes, and aggregate counts.
"""

import logging
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

from .. import facemarket_cutover, repo
from ..services.biometric_purge import PurgeIncomplete, purge_biometric_scope

log = logging.getLogger("wearless.personalization_purge_job")

_BACKUP_RETENTION_DAYS = 30


async def _audit(cur, user_id: str, profile_id: str, event_type: str, detail: dict) -> None:
    await cur.execute(
        "insert into personalization_audit_log (user_id, profile_id, event_type, detail) "
        "values (%s, %s, %s, %s)",
        (user_id, profile_id, event_type, Json(detail)),
    )


async def run_personalization_purge_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id, lease_token = job["id"], job["user_id"], job["lease_token"]
    reason = str((job.get("payload") or {}).get("reason") or "withdrawal")

    async def _retry(code: str) -> None:
        try:
            async with pool.connection() as conn:
                await repo.requeue_personalization_purge(
                    conn, job_id=job_id, lease_token=lease_token, code=code
                )
                await conn.commit()
        except Exception:
            log.warning("personalization_purge retry update failed for job %s", job_id)

    async def _fatal(code: str) -> None:
        try:
            async with pool.connection() as conn:
                await repo._finalize_job_failure(
                    conn,
                    job_id=job_id,
                    lease_token=lease_token,
                    message="개인화 파기 계약이 올바르지 않아요.",
                    metadata={"code": code},
                    code=code,
                )
                await conn.commit()
        except Exception:
            log.warning("personalization_purge fatal update failed for job %s", job_id)

    try:
        if reason not in {"withdrawal", "account_delete"}:
            await _fatal("invalid_purge_reason")
            return

        try:
            await facemarket_cutover.quiesce_personalization_writers(
                pool, user_id=user_id, timeout_seconds=30.0
            )
            await facemarket_cutover.quiesce_user_facemarket_writers(
                pool, user_id=user_id, timeout_seconds=30.0
            )
        except facemarket_cutover.CutoverBlocked as exc:
            await _retry(exc.code)
            return

        result = await purge_biometric_scope(
            app,
            user_id=user_id,
            reason=reason,  # type: ignore[arg-type]
            source_job_id=job_id if reason == "account_delete" else None,
        )

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id from jobs where id = %s and locked_by = %s and status = 'running' "
                    "for update",
                    (job_id, lease_token),
                )
                if await cur.fetchone() is None:
                    await conn.rollback()
                    return
                await cur.execute(
                    "select id::text as id from personalization_profiles where user_id=%s",
                    (user_id,),
                )
                profile_ids = [row["id"] for row in await cur.fetchall()]
                receipt_id = None
                if reason == "account_delete":
                    await cur.execute(
                        "select id::text as id from fm_biometric_purge_receipts where source_job_id=%s",
                        (job_id,),
                    )
                    row = await cur.fetchone()
                    receipt_id = row["id"] if row else None
                counts = {
                    "targetCount": result.target_count,
                    "confirmedAbsentCount": result.confirmed_absent_count,
                    "modelCount": result.model_count,
                    "profileCount": result.profile_count,
                    "enrollmentCount": result.enrollment_count,
                    "assetCount": result.asset_count,
                    "backupPurgeDueAt": (
                        datetime.now(timezone.utc) + timedelta(days=_BACKUP_RETENTION_DAYS)
                    ).isoformat(),
                }
                if reason == "withdrawal":
                    for profile_id in profile_ids:
                        await _audit(cur, user_id, profile_id, "purge_completed", counts)
                envelope = {
                    "status": "done",
                    "reason": reason,
                    "outcome": (
                        "ready_for_identity_delete"
                        if reason == "account_delete"
                        else "biometric_purged"
                    ),
                    "receiptId": receipt_id,
                    "counts": counts,
                }
                await cur.execute(
                    "update jobs set status = 'done', result = %s, progress = 100, "
                    "locked_by = null, locked_at = null, finished_at = now() where id = %s",
                    (Json(envelope), job_id),
                )
                await cur.execute(
                    "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
                    (job_id, Json(envelope)),
                )
            await conn.commit()
    except PurgeIncomplete as exc:
        await _retry(exc.code)
    except Exception:
        await _retry("unexpected_purge_failure")
