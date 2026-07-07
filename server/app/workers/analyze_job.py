"""AG-01 상품 분석 워커. dispatcher가 claim한 kind='analyze' job 1건을 실행한다.

흐름: 기준 색상 이미지(bytes) 로드 → product_analyst.analyze(프롬프트→vision_llm 폴백→검증→분배)
→ finalize(analyses 저장 + clothingType→products + job done/result 봉투, 원자·lease 펜스).
무과금(ai_agent_modules §3) — reserve/confirm/refund 경로 없음. 비전 입력은 bytes(URL 아님).
"""

import asyncio
import logging

from .. import repo
from ..agents import mannequin, product_analyst
from ..agents.gemini_image import InlineImage
from ..agents.vision_llm import VisionError
from ._common import emit_job_event as _emit  # 공용 헬퍼(mannequin_job과 공유). 테스트가 이 이름을 monkeypatch

log = logging.getLogger("wearless.analyze_job")


async def run_analyze_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]

    async def _fail(message: str, meta: dict, code: str = "analysis_failed"):
        # 자체 예외를 삼킨다 — 내부 실패 브랜치의 _fail 이 DB 오류로 raise 하면 outer except 가
        # _fail 을 재호출해 이중 종결되던 문제(F2). 종결 실패는 lease 복구가 backstop.
        try:
            async with pool.connection() as conn:
                await repo.finalize_analyze_failure(
                    conn, job_id=job_id, lease_token=lease_token, project_id=project_id,
                    message=message, metadata=meta, code=code)
                await conn.commit()
        except Exception:
            log.exception("analyze finalize_failure error for job %s", job_id)

    try:
        # 1) 입력 로드 — 기준 색상 이미지 asset (마네킹과 동일 소스)
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            assets = []
            for _slot, aid in mannequin.base_color_images(product):
                a = await repo.get_asset_for_user(conn, user_id, aid)
                if a:
                    assets.append(a)
        if not assets:
            await _fail("상품 사진을 찾을 수 없어요. 정면 사진을 올렸는지 확인해 주세요.",
                        {"error": "no_product_images"})
            return

        # 2) 바이트 다운로드 (to_thread) → InlineImage
        images = [
            InlineImage(a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"]))
            for a in assets
        ]
        await _emit(pool, job_id, "progress", {"progress": 30, "phase": "inputs_loaded"})

        # 3) 분석 (프롬프트→vision_llm 폴백→검증→분배)
        try:
            distributed, provider = await product_analyst.analyze(s, product, images)
        except VisionError as e:
            await _fail(str(e), {"error": "vision_failed"}, code="analysis_failed")
            return
        await _emit(pool, job_id, "progress", {"progress": 80, "phase": "analyzed",
                                               "provider": provider})

        analysis_payload = distributed["analysis"]
        clothing_type = distributed["product"]["clothingType"]
        # 프론트 반환 객체 — analyses 저장분 + clothingType + 중간산출물(styleTags/스와치, 매칭·폼용)
        # + measurements 는 빈 배열(AG-01 실측 미산출, 사용자 직접 입력 — PRD §6.5).
        result_data = {
            **analysis_payload,
            "clothingType": clothing_type,
            "styleTags": distributed["intermediate"]["styleTags"],
            "swatchSuggestions": distributed["intermediate"]["swatchSuggestions"],
            "measurements": [],
        }

        # 4) 성공 종결 (원자·lease 펜스, 무과금)
        async with pool.connection() as conn:
            out = await repo.finalize_analyze_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, clothing_type=clothing_type,
                analysis_payload=analysis_payload, result={"data": result_data},
                metadata={"provider": provider, "promptVersion": "v1"})
            await conn.commit()
        if out is None:  # lease 상실(복구·재클레임) → 결과 폐기
            log.warning("analyze job %s lost lease", job_id)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("분석 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
