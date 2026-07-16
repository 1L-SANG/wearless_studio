"""개인화 파기 캐스케이드 워커 (api-spec §3.5). payload = {profileId}.

전체 철회·service_use/cross_border 철회·계정 삭제·보관기간 만료가 종착하는 비동기 파기 잡.
§3.5 의 7단계 캐스케이드를 순서대로 실행하고 프로필을 purged 로 종결한다.

PII 하드룰(§1.4): 감사로그·이벤트·로그에 얼굴 바이트·digest·임베딩·비공개 키 절대 미포함.
남기는 것은 상태 enum·집계 카운트·타임스탬프·id 뿐. face_photos 는 hard delete(digest 잔존 =
멤버십 테스트 벡터라 §3.5-2 로 금지).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

from .. import repo

log = logging.getLogger("wearless.personalization_purge_job")

# 백업(DB PITR·R2 스냅샷)은 즉시삭제 불가 → 보존주기 경과로 소멸(§3.5-6). 운영 확정값 자리
# (법무 고지 문구와 일치시킬 것). 잡은 backupPurgeDueAt 만 감사로그에 기록한다.
_BACKUP_RETENTION_DAYS = 30


async def _audit(cur, user_id: str, profile_id: str, event_type: str, detail: dict) -> None:
    """PII 금지 감사로그(§5). detail = 카운트·타임스탬프 등 비-PII 메타만."""
    await cur.execute(
        "insert into personalization_audit_log (user_id, profile_id, event_type, detail) "
        "values (%s, %s, %s, %s)",
        (user_id, profile_id, event_type, Json(detail)),
    )


async def _cancel_pending_generation_jobs(app, user_id: str, jobs: list[dict]) -> int:
    """진행 전(pending) 개인화 생성 잡을 error(purge_cancelled) 종결 + 예약 크레딧 release(§3.5).

    각 잡을 독립 tx 로 처리. `status='pending'` 로 재펜싱해 그 사이 워커가 클레임(running)한
    잡은 건드리지 않는다(running 은 자기 finalize 에서 프로필 purging 을 보고 스스로 폐기).
    release_credits 는 settle_key 멱등이라 라우트가 이미 release 했어도 이중 해제되지 않는다.
    """
    pool = app.state.pool
    cancelled = 0
    for gj in jobs:
        gid = gj["id"]
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update jobs set status = 'error', error_message = 'purge_cancelled', "
                        "finished_at = now(), locked_by = null, locked_at = null "
                        "where id = %s and status = 'pending' returning id",
                        (gid,),
                    )
                    updated = await cur.fetchone()
                    if updated is not None:
                        await cur.execute(
                            "insert into job_events (job_id, event_type, payload) "
                            "values (%s, 'error', %s)",
                            (gid, Json({"code": "purge_cancelled",
                                        "message": "개인화가 파기되어 생성을 취소했어요."})),
                        )
                if updated is not None:
                    cancelled += 1
                    if (gj.get("credits_reserved") or 0) > 0:
                        await repo.release_credits(
                            conn, user_id=user_id, project_id=gj.get("project_id"),
                            job_id=gid, reserved=gj["credits_reserved"],
                            settle_key=f"credit:job:{gid}:settle",
                            metadata={"reason": "purge_cancelled"})
                await conn.commit()
        except Exception:
            log.exception("purge: pending generation cancel failed for job %s", gid)
    return cancelled


async def run_personalization_purge_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id = job["id"], job["user_id"]
    lease_token = job["lease_token"]
    payload = job.get("payload") or {}
    profile_id = payload.get("profileId")
    r2_face = getattr(app.state, "r2_face", None)

    async def _fail(message: str, meta: dict, code: str = "purge_failed") -> None:
        try:
            async with pool.connection() as conn:
                await repo._finalize_job_failure(
                    conn, job_id=job_id, lease_token=lease_token,
                    message=message, metadata=meta, code=code)
                await conn.commit()
        except Exception:
            log.exception("personalization_purge finalize_failure error for job %s", job_id)

    try:
        if not profile_id:
            await _fail("파기 대상 프로필이 지정되지 않았어요.", {"error": "missing_profile_id"})
            return

        # ── 0) 로드(read). 얼굴 R2 키(digest·bytes 미로드) + 산출물 result_keys + 취소 대상 pending 생성잡 ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id, status from personalization_profiles where id = %s",
                    (profile_id,))
                profile = await cur.fetchone()
                await cur.execute(
                    "select angle, r2_key from personalization_face_photos where profile_id = %s",
                    (profile_id,))
                face_rows = await cur.fetchall()
                # 개인화 산출물 = generation.result_keys 가 가리키는 R2 객체(비공개 얼굴 버킷).
                # 공유 assets 테이블 미적재(CRITICAL-B) → generation 행에서 키만 직접 수집.
                # result_keys 는 `not null default '{}'` — 미종결 generation 은 null 이 아니라
                # 빈 배열이다. `is not null` 로 거르면 항상 참이라 아무것도 못 거른다.
                await cur.execute(
                    "select result_keys from personalization_generations "
                    "where profile_id = %s and result_keys <> '{}'",
                    (profile_id,))
                gen_key_rows = await cur.fetchall()
                gen_result_keys = [k for row in gen_key_rows for k in row["result_keys"]]
                await cur.execute(
                    "select id::text as id, project_id::text as project_id, credits_reserved "
                    "from jobs where user_id = %s and kind = 'personalization_generation' "
                    "and status = 'pending'",
                    (user_id,))
                pending_gen_jobs = await cur.fetchall()
            await conn.commit()

        if profile is None:
            await _fail("파기할 프로필을 찾을 수 없어요.", {"error": "profile_not_found"},
                        code="profile_not_found")
            return

        # ── 7a) purge_started 감사(먼저 기록 — 이후 단계 부분 실패해도 시작 증적 남김) ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await _audit(cur, user_id, profile_id, "purge_started",
                             {"faceSlots": len(face_rows), "generationResults": len(gen_result_keys)})
            await conn.commit()

        # ── §3.5 진행 중 개인화 생성 잡 처리: pending 은 error+release ──
        pending_cancelled = await _cancel_pending_generation_jobs(app, user_id, pending_gen_jobs)

        # ── 1) 얼굴 원본 3장 비공개 R2 delete (best-effort. key·digest 미로그) ──
        faces_r2_deleted = 0
        if r2_face is not None:
            for fr in face_rows:
                try:
                    await asyncio.to_thread(r2_face.delete, fr["r2_key"])
                    faces_r2_deleted += 1
                except Exception:
                    log.warning("purge: face original R2 delete failed for job %s", job_id)

        # ── 4a) 개인화 산출물 R2 객체 delete — result_keys(비공개 얼굴 버킷) best-effort. 키·digest 미로그 ──
        gen_r2_deleted = 0
        if r2_face is not None:
            for k in gen_result_keys:
                try:
                    await asyncio.to_thread(r2_face.delete, k)
                    gen_r2_deleted += 1
                except Exception:
                    log.warning("purge: generation result R2 delete failed for job %s", job_id)

        # ── 4c) 고아 산출물 회수(MINOR-6) — DB 미참조 R2 객체까지 prefix 스캔으로 삭제 ──
        # 생성 워커는 put_bytes 후 별도 tx 의 finalize 에서 result_keys 를 저장한다. 그 사이
        # 크래시하면 객체는 남고 DB 참조는 없어 4a 로는 못 찾는다 → 얼굴 산출물이 파기 후 잔존
        # (§3.5 파기 완전성 위반). prefix 전체를 지우는 게 안전한 이유:
        #   · 활성 프로필은 사용자당 1개(personalization_profiles_active_user_idx) → 이 prefix 에
        #     다른 살아있는 프로필의 산출물이 섞일 수 없다.
        #   · 진행 중 생성이 방금 put 한 미종결 키도 §3.5 상 폐기 대상(finalize 가 purging 을 보고
        #     스스로 버리는 그 키) → 여기서 지워도 정상. 유예 윈도우 불필요.
        #   · 얼굴 원본은 personalization/profiles/{profile_id}/faces/ 로 prefix 가 달라 무관.
        # 4a 이후에 스캔하므로 여기 남은 것 = 진짜 고아. 키는 로그·감사로그 미기록(§1.4) — 카운트만.
        orphans_deleted = 0
        orphan_scan = "skipped"
        if r2_face is not None:
            try:
                orphan_keys = await asyncio.to_thread(
                    r2_face.list_prefix, f"personalization/{user_id}/generations/")
                orphan_scan = "ok"
            except Exception:
                orphan_keys = []
                orphan_scan = "failed"  # 파기 완전성 미보장 → 감사로그로 식별 가능하게 남긴다
                log.warning("purge: generation orphan scan failed for job %s", job_id)
            for k in orphan_keys:
                try:
                    await asyncio.to_thread(r2_face.delete, k)
                    orphans_deleted += 1
                except Exception:
                    orphan_scan = "partial"
                    log.warning("purge: generation orphan R2 delete failed for job %s", job_id)

        # ── DB 캐스케이드(원자, 파기잡 lease 펜스). 2·3·4b·5·6·7b + job done ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 파기잡 자신을 먼저 펜싱 — lease 상실 시 파괴적 DB 쓰기를 하지 않는다.
                await cur.execute(
                    "select id from jobs where id = %s and locked_by = %s and status = 'running' "
                    "for update",
                    (job_id, lease_token))
                if await cur.fetchone() is None:
                    await conn.rollback()
                    return  # lease 빼앗김(복구·재클레임) → 부수효과 0

                # 2) personalization_face_photos hard delete (r2_key·image_digest·byte_size 잔존 금지)
                await cur.execute(
                    "delete from personalization_face_photos where profile_id = %s", (profile_id,))
                face_rows_deleted = cur.rowcount

                # 3) 얼굴 임베딩·QC 파생물 방어 스캔. 저장 자체가 §1.4 금지 → 정상 경로 0건.
                #    별도 키스페이스/테이블이 없어 스캔 대상 0(존재하면 버그 — 여기서 삭제·카운트).
                embeddings_found = 0

                # 4b) 개인화 산출물 generation 행 삭제 (산출물은 assets 미적재 — result_keys 만 존재)
                await cur.execute(
                    "delete from personalization_generations where profile_id = %s", (profile_id,))
                generations_deleted = cur.rowcount

                # 4d) 본인확인(연령 게이트) 레코드 삭제 — cx_tx_id 는 신원 연결 가능한 값이라
                #     전체 철회 시 함께 파기(api-spec §3.0). 재온보딩은 재인증 필요(동의와 동일 논리).
                await cur.execute(
                    "delete from personalization_identity_verifications where user_id = %s",
                    (user_id,))
                identity_rows_deleted = cur.rowcount

                # 5) 신체 프로필 값 null 처리 + 행 유지(purged 증적: status='purged' + purged_at)
                await cur.execute(
                    "update personalization_profiles set "
                    "height_cm = null, weight_kg = null, body_type = null, body_type_custom = null, "
                    "gender = null, age_range = null, skin_tone = null, hair = null, "
                    "clothing_size = null, status = 'purged', purged_at = now() "
                    "where id = %s",
                    (profile_id,))

                # 6) 백업 = 즉시삭제 불가 → 소멸 예정 시각을 감사로그에 기록
                backup_due_at = (datetime.now(timezone.utc)
                                 + timedelta(days=_BACKUP_RETENTION_DAYS)).isoformat()

                counts = {
                    "faceOriginalsR2Deleted": faces_r2_deleted,
                    "faceRowsDeleted": face_rows_deleted,
                    "embeddingsFound": embeddings_found,
                    "generationRowsDeleted": generations_deleted,
                    "generationResults": len(gen_result_keys),
                    "generationResultsR2Deleted": gen_r2_deleted,
                    "identityRowsDeleted": identity_rows_deleted,
                    # 고아 = DB 미참조 잔존 객체(4c). scan 상태를 함께 남겨 "0건"과 "스캔 실패"를
                    # 구분한다 — 둘 다 deleted=0 이지만 후자는 파기 완전성 미보장(§3.5).
                    "generationOrphansDeleted": orphans_deleted,
                    "generationOrphanScan": orphan_scan,
                    "pendingGenerationJobsCancelled": pending_cancelled,
                    "backupPurgeDueAt": backup_due_at,
                }
                # 7b) purge_completed 감사(단계별 카운트, PII 없음)
                await _audit(cur, user_id, profile_id, "purge_completed", counts)

                # 파기잡 done 종결(같은 tx·같은 락). result·done 이벤트에 PII 없음(카운트만).
                envelope = {"status": "purged", "profileId": profile_id, "counts": counts}
                await cur.execute(
                    "update jobs set status = 'done', result = %s, progress = 100, "
                    "locked_by = null, locked_at = null, finished_at = now() where id = %s",
                    (Json(envelope), job_id))
                await cur.execute(
                    "insert into job_events (job_id, event_type, payload) values (%s, 'done', %s)",
                    (job_id, Json(envelope)))
            await conn.commit()
    except Exception as e:
        await _fail("개인화 파기 중 오류가 발생했어요.", {"error": str(e)[:300]})
