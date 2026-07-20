"""AG-01 상품 분석 워커. dispatcher가 claim한 kind='analyze' job 1건을 실행한다.

흐름: 기준 색상 이미지(bytes) 로드 → product_analyst.analyze(프롬프트→vision_llm 폴백→검증→분배)
→ finalize(analyses 저장 + clothingType→products + job done/result 봉투, 원자·lease 펜스).
무과금(ai_agent_modules §3) — reserve/confirm/refund 경로 없음. 비전 입력은 bytes(URL 아님).
"""

import asyncio
import logging
from io import BytesIO

from PIL import Image

from .. import repo
from ..agents import feature_extractor, mannequin, product_analyst
from ..agents.gemini_image import InlineImage
from ..agents.vision_llm import VisionError
from ._common import emit_job_event as _emit  # 공용 헬퍼(mannequin_job과 공유). 테스트가 이 이름을 monkeypatch

log = logging.getLogger("wearless.analyze_job")

# 분석 비전 입력 축소 (2026-07-07 속도 개선) — 판정(종류·핏·색·특징)엔 원본 해상도가
# 불필요한데 셀러 원본은 수 MB라 base64 전송·인코딩이 지연의 큰 몫을 차지한다.
# 마네킹/컷 '생성' 입력은 디테일 재현이 필요해 축소하지 않는다(이 함수는 분석 전용).
_VISION_MAX_DIM = 1024
_VISION_SKIP_BYTES = 400_000  # 이보다 작으면 그대로 (재인코딩 이득 없음)


def shrink_for_vision(data: bytes, mime: str) -> tuple[bytes, str]:
    """최장변 1024px JPEG(q82)로 축소. 작거나 실패하면 원본 그대로 (안전 폴백)."""
    if len(data) <= _VISION_SKIP_BYTES:
        return data, mime
    try:
        im = Image.open(BytesIO(data))
        if max(im.size) > _VISION_MAX_DIM:
            im.thumbnail((_VISION_MAX_DIM, _VISION_MAX_DIM))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=82)
        out = buf.getvalue()
        if len(out) < len(data):
            return out, "image/jpeg"
        return data, mime
    except Exception:  # 손상 파일 등 — 축소 실패가 분석을 막지 않게
        return data, mime


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
        # 1) 입력 로드 — 기준 색상 이미지 asset (마네킹과 동일 소스). slot(Front/Back/Detail/Fit)은
        #    AG-08 관찰 가이드용으로 보존한다(디테일 컷 집중 지시 — 2026-07-13).
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            assets, slots = [], []
            for slot, aid in mannequin.base_color_images(product):
                a = await repo.get_asset_for_user(conn, user_id, aid)
                if a:
                    assets.append(a)
                    slots.append(slot)
        if not assets:
            await _fail("상품 사진을 찾을 수 없어요. 정면 사진을 올렸는지 확인해 주세요.",
                        {"error": "no_product_images"})
            return

        # 2) 바이트 다운로드 → 분석용 축소 (둘 다 to_thread — 이벤트 루프 비차단)
        #    다운로드는 장수만큼 병렬 — 순차일 땐 한 장의 R2 지연이 통째로 prep 을 세웠다
        #    (2026-07-16 실측: 7회 중 1회 32s 스톨). gather 는 입력 순서를 보존한다.
        async def _load_one(a: dict) -> tuple[int, bytes, str]:
            raw = await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])
            data, mime = await asyncio.to_thread(shrink_for_vision, raw, a["mime_type"])
            return len(raw), data, mime

        loaded = await asyncio.gather(*(_load_one(a) for a in assets))
        bytes_in = sum(n for n, _, _ in loaded)
        bytes_out = sum(len(d) for _, d, _ in loaded)
        images = [InlineImage(mime, data) for _, data, mime in loaded]
        await _emit(pool, job_id, "progress", {"progress": 30, "phase": "inputs_loaded",
                                               "bytesIn": bytes_in, "bytesOut": bytes_out})

        # 3) 분석(AG-01)과 특징 발굴(AG-08)을 병렬 실행 — 전체 지연 = 둘 중 느린 쪽.
        #    특징 에이전트는 실패해도 분석을 막지 않는다(AG-01의 points로 폴백 — 2026-07-13).
        analyze_res, feature_res = await asyncio.gather(
            product_analyst.analyze(s, product, images),
            feature_extractor.extract(s, product, images, slots=slots),
            return_exceptions=True,
        )
        if isinstance(analyze_res, BaseException):
            if isinstance(analyze_res, VisionError):
                await _fail(str(analyze_res), {"error": "vision_failed"}, code="analysis_failed")
                return
            raise analyze_res  # 예기치 못한 오류 → outer except (lease 펜스 종결)
        distributed, provider = analyze_res
        feature_provider = None
        if isinstance(feature_res, BaseException):
            log.warning("AG-08 feature extract failed for job %s: %r", job_id, feature_res)
        else:
            points, feature_provider = feature_res
            if points:  # 전용 에이전트 결과가 있으면 교체, 비면 AG-01 것 유지
                distributed["analysis"]["aiSuggestedPoints"] = points
        await _emit(pool, job_id, "progress", {"progress": 80, "phase": "analyzed",
                                               "provider": provider,
                                               "featureProvider": feature_provider})

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
                metadata={"provider": provider, "featureProvider": feature_provider,
                          "promptVersion": "v1"})
            await conn.commit()
        if out is None:  # lease 상실(복구·재클레임) → 결과 폐기
            log.warning("analyze job %s lost lease", job_id)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("분석 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
