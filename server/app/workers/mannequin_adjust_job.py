"""AG-05 마네킹 조정 워커. 기존 마네킹컷 1장(baseId) → 지시된 차원(fit/length/match)만 바꾼
새 버전 컷 생성. mannequin_job.py의 reserve→generate→finalize 패턴을 단일 컷(부분 성공 없음)에
맞춰 미러한다. lease 펜스·크레딧 정산은 repo.finalize_mannequin_adjust_success/failure.
"""

import asyncio
import logging
import uuid
from io import BytesIO

from PIL import Image

log = logging.getLogger("wearless.mannequin_adjust_job")

from .. import repo
from ..agents import mannequin_adjuster
from ..agents.gemini_image import GeminiError, InlineImage
from ..r2 import ai_key, ext_for_mime
from ._common import emit_job_event as _emit

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


async def run_mannequin_adjust_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    payload = job.get("payload") or {}
    base_id = payload.get("baseId")

    async def _fail(message: str, meta: dict):
        try:
            async with pool.connection() as conn:
                await repo.finalize_mannequin_adjust_failure(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, reserved=reserved, settle_key=settle_key,
                    message=message, metadata=meta)
                await conn.commit()
        except Exception:
            log.exception("mannequin_adjust finalize_failure error for job %s", job_id)

    try:
        # 1) 베이스 컷 로드
        async with pool.connection() as conn:
            base_cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, base_id)
            if base_cut is None:
                cuts = []
            else:
                cuts = await repo.list_mannequin_cuts(conn, user_id, project_id)

        if base_cut is None:
            await _fail("조정할 마네킹컷을 찾을 수 없어요. 다시 시도해 주세요.",
                        {"error": "base_cut_missing", "baseId": base_id})
            return
        base_row = next(
            (c for c in cuts if c["asset_id"] == base_cut["id"]), None
        )
        base_candidate = base_row["candidate"] if base_row else (base_id or "A-0").split("-")[0]
        base_fit = base_row["base_fit"] if base_row else "regular"

        # 2) 바이트 다운로드 (to_thread)
        base_img = InlineImage(base_cut["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, base_cut["r2_key"]))
        await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

        # 3) 생성 (단일 컷, 부분 성공 없음 — 실패 시 job 실패)
        adjust_spec = {
            "fitAdjust": payload.get("fitAdjust"),
            "lengthAdjust": payload.get("lengthAdjust"),
            "matchAdjust": payload.get("matchAdjust"),
        }
        try:
            image, mime = await mannequin_adjuster.generate(s, app.state.gemini, base_img, adjust_spec)
        except GeminiError as e:
            await _fail("마네킹 조정에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
            return
        await _emit(pool, job_id, "progress", {"progress": 70, "phase": "generated"})

        # 4) R2 저장
        ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
        asset_id = str(uuid.uuid4())
        key = ai_key(user_id, project_id, job_id, asset_id, ext)
        await asyncio.to_thread(app.state.r2.put_bytes, key, image, mime)
        w, h = _image_dims(image)
        cut = {
            "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
            "size": len(image), "width": w, "height": h, "base_fit": base_fit,
            "fit_adjust": adjust_spec["fitAdjust"], "length_adjust": adjust_spec["lengthAdjust"],
            "match_adjust": adjust_spec["matchAdjust"],
        }

        # 5) 성공 종결 (원자·lease 펜스). charge = credit_cost_mannequin_adjust(고정, 부분 성공 없음).
        charge = s.credit_cost_mannequin_adjust
        async with pool.connection() as conn:
            out = await repo.finalize_mannequin_adjust_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, base_candidate=base_candidate, cut=cut,
                reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version})
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            try:
                await asyncio.to_thread(app.state.r2.delete, key)
            except Exception:
                log.warning("orphan R2 cleanup failed: %s", key)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("마네킹 조정 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
