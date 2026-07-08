"""AG-05 마네킹 조정 워커 — @deprecated (2026-07) 툼스톤.

조정 흐름이 fitProfile 재생성(`mannequin_job`)으로 통합됐고 라우트(`:adjust`)는 410 Gone 이라
새 `mannequin_adjust` 잡은 더 이상 생기지 않는다. 이 워커는 배포 전 큐에 남은 legacy 잡을
**AI 호출 없이** 실패 종결(예약 크레딧 release)하는 드레인 전용이다 — 단가가 0으로 내려간
상태에서 legacy 잡을 그대로 실행하면 무과금 이미지 생성 경로가 되므로 생성 코드를 제거했다.
(구 생성 구현은 git 히스토리 — 필요 시 mannequin_job 의 fitProfile 파이프라인을 쓸 것.)
"""

import logging

from .. import repo

log = logging.getLogger("wearless.mannequin_adjust_job")


async def run_mannequin_adjust_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    log.info("draining deprecated mannequin_adjust job %s — AI 호출 없이 실패 종결", job_id)
    try:
        async with pool.connection() as conn:
            await repo.finalize_mannequin_adjust_failure(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, reserved=reserved, settle_key=settle_key,
                message="마네킹 조정은 종료된 기능이에요. 핏 수정 후 재생성을 이용해 주세요.",
                metadata={"error": "deprecated_job_kind"})
            await conn.commit()
    except Exception:
        log.exception("mannequin_adjust drain finalize error for job %s", job_id)
