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
_REASON_STRENGTH = {"withdrawal": 0, "account_delete": 1}

#: 스코프가 비었다 = 지울 대상이 없다. 파기 실패가 아니라 파기 의무가 이미 충족된
#: 상태라, 재시도하면 결과가 영원히 안 바뀐다. 실제로 프로덕션에서 백오프 상한(900s)에
#: 걸린 채 20시간 동안 attempt=88 까지 도는 무한 루프가 됐다(2026-08-26 실측).
#: 확인했다는 사실을 감사에 남기고 done 으로 종결한다.
_EMPTY_SCOPE_CODES = {"scope_not_found"}
#: 계약 위반 — 같은 입력이면 재시도해도 같은 결과다. error 로 종결해 운영자가 본다.
_CONTRACT_CODES = {"invalid_scope"}
#: 일시적 코드(스토리지 장애 등)의 재시도 상한. 넘으면 error 로 종결한다 —
#: 상한이 없으면 영구 장애가 큐에 영원히 남는다.
_MAX_RETRY_ATTEMPTS = 20


async def _audit(cur, user_id: str, profile_id: str, event_type: str, detail: dict) -> None:
    await cur.execute(
        "insert into personalization_audit_log (user_id, profile_id, event_type, detail) "
        "values (%s, %s, %s, %s)",
        (user_id, profile_id, event_type, Json(detail)),
    )


def _strongest_reason(current: str, candidate: str) -> str:
    if current not in _REASON_STRENGTH or candidate not in _REASON_STRENGTH:
        raise ValueError("invalid_purge_reason")
    return candidate if _REASON_STRENGTH[candidate] > _REASON_STRENGTH[current] else current


async def _leased_reason(conn, *, job_id: str, lease_token: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select payload from jobs where id = %s and locked_by = %s and status = 'running' "
            "for update",
            (job_id, lease_token),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return str((row.get("payload") or {}).get("reason") or "withdrawal")


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

    async def _fatal(code: str, message: str = "개인화 파기 계약이 올바르지 않아요.") -> None:
        try:
            async with pool.connection() as conn:
                await repo._finalize_job_failure(
                    conn,
                    job_id=job_id,
                    lease_token=lease_token,
                    message=message,
                    metadata={"code": code},
                    code=code,
                )
                await conn.commit()
        except Exception:
            log.warning("personalization_purge fatal update failed for job %s", job_id)

    async def _attempts_so_far() -> int:
        """이 잡이 지금까지 재시도된 횟수. 읽기 실패는 0으로 봐서 재시도를 막지 않는다."""
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select coalesce((metadata->>'attempt')::int, 0) as attempt "
                        "from jobs where id = %s",
                        (job_id,),
                    )
                    row = await cur.fetchone()
            return int((row or {}).get("attempt") or 0)
        except Exception:
            log.warning("personalization_purge attempt read failed for job %s", job_id)
            return 0

    async def _complete_empty_scope() -> None:
        """지울 대상이 없는 파기를 done 으로 종결하고 감사에 남긴다.

        event_type 은 'purge_completed' 를 쓴다 — personalization_audit_log 의 CHECK 가
        허용 값을 고정하고 있어 새 값은 마이그레이션이 필요한데, 의미상으로도 "파기가
        완료됐다(지울 것이 없었다)"가 맞다. 구분은 detail.code='scope_empty' 로 한다.
        """
        counts = {
            "code": "scope_empty",
            "targetCount": 0,
            "confirmedAbsentCount": 0,
            "modelCount": 0,
            "profileCount": 0,
            "enrollmentCount": 0,
            "assetCount": 0,
            "generationResults": 0,
            "generationResultsR2Deleted": 0,
            "generationOrphansDeleted": 0,
            "generationOrphanScan": "skipped",
        }
        envelope = {
            "status": "done",
            "reason": reason,
            "outcome": "scope_empty",
            "receiptId": None,
            "counts": counts,
        }
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    # lease 펜스 — 회수된 잡을 옛 워커가 done 으로 덮지 않게 한다.
                    await cur.execute(
                        "update jobs set status = 'done', result = %s, progress = 100, "
                        "locked_by = null, locked_at = null, finished_at = now(), "
                        "metadata = metadata || jsonb_build_object("
                        "  'stage', 'done', 'code', 'scope_empty') "
                        "where id = %s and kind = 'personalization_purge' "
                        "  and status = 'running' and locked_by = %s",
                        (Json(envelope), job_id, lease_token),
                    )
                    if cur.rowcount == 0:
                        await conn.rollback()
                        return
                    await _audit(cur, user_id, None, "purge_completed", counts)
                    await cur.execute(
                        "insert into job_events (job_id, event_type, payload) "
                        "values (%s, 'done', %s)",
                        (job_id, Json(envelope)),
                    )
                await conn.commit()
        except Exception:
            log.warning("personalization_purge empty-scope finalize failed for job %s", job_id)

    async def _dispatch_failure(code: str) -> None:
        """실패 코드를 재시도/종결로 라우팅한다 — 무조건 재시도가 무한 루프의 원인이었다."""
        if code in _EMPTY_SCOPE_CODES:
            await _complete_empty_scope()
            return
        if code in _CONTRACT_CODES:
            await _fatal(code)
            return
        if await _attempts_so_far() >= _MAX_RETRY_ATTEMPTS:
            await _fatal(code, "개인화 파기가 반복 실패해 중단했어요.")
            return
        await _retry(code)

    try:
        if reason not in {"withdrawal", "account_delete"}:
            await _fatal("invalid_purge_reason")
            return

        while True:
            try:
                async with pool.connection() as conn:
                    leased_reason = await _leased_reason(
                        conn, job_id=job_id, lease_token=lease_token
                    )
                    if leased_reason is None:
                        await conn.rollback()
                        return
                    reason = _strongest_reason(reason, leased_reason)
                    await conn.commit()
            except ValueError:
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
                try:
                    final_reason = await _leased_reason(
                        conn, job_id=job_id, lease_token=lease_token
                    )
                    if final_reason is None:
                        await conn.rollback()
                        return
                    final_reason = _strongest_reason(reason, final_reason)
                except ValueError:
                    await _fatal("invalid_purge_reason")
                    return
                if final_reason != reason:
                    await conn.rollback()
                    reason = final_reason
                    continue
                async with conn.cursor() as cur:
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
                        # 생성물 회계(카운트만 — §1.4: 키·경로 미기록). 스캔 실패는 '고아 0건'과 구분.
                        "generationResults": result.generation_results,
                        "generationResultsR2Deleted": result.generation_results_r2_deleted,
                        "generationOrphansDeleted": result.generation_orphans_deleted,
                        "generationOrphanScan": result.generation_orphan_scan,
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
                return
    except PurgeIncomplete as exc:
        await _dispatch_failure(exc.code)
    except Exception:
        await _dispatch_failure("unexpected_purge_failure")
