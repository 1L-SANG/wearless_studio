"""AG-06/AG-07 에디터 이미지 워커 (PL-5/6). 에디터 AI 탭 '새 컷 추가'(mode:'new')는
cut_generator(AG-06)를, '현재 컷 변형'(mode:'vary')은 cut_variator(AG-07)를 재사용한다.
mannequin_adjust_job.py의 reserve→generate→finalize 패턴을 단일 이미지(부분 성공 없음)에
맞춰 미러한다. lease 펜스·크레딧 정산은 repo.finalize_editor_image_success/failure.
"""

import asyncio
import logging
import re
import uuid
from io import BytesIO

from PIL import Image

from .. import repo
from ..agents import cut_generator, cut_variator, mannequin
from ..agents.gemini_image import GeminiError, InlineImage
from ..r2 import ai_key, ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.editor_image_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_ASSET_FILE_RE = re.compile(r"/v1/assets/([^/]+)/file")


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


def _parse_source_asset_id(src: str | None) -> str | None:
    """VaryRequest.source.src(`/v1/assets/{id}/file` 안정 앱 URL)에서 asset id 추출.
    다른 형태(외부 URL 등)는 미상 → None(호출자가 실패 처리)."""
    if not src:
        return None
    m = _ASSET_FILE_RE.search(src)
    return m.group(1) if m else None


async def run_editor_image_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    payload = job.get("payload") or {}
    mode = payload.get("mode")

    async def _fail(message: str, meta: dict):
        try:
            async with pool.connection() as conn:
                await repo.finalize_editor_image_failure(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, reserved=reserved, settle_key=settle_key,
                    message=message, metadata=meta)
                await conn.commit()
        except Exception:
            log.exception("editor_image finalize_failure error for job %s", job_id)

    try:
        image: bytes
        mime: str
        group: str | None
        cut_type: str | None

        if mode == "vary":
            source = payload.get("source") or {}
            asset_id = _parse_source_asset_id(source.get("src"))
            src_asset = None
            ref_bg_asset = None
            async with pool.connection() as conn:
                if asset_id:
                    src_asset = await repo.get_asset_for_user(conn, user_id, asset_id)
                # 배경 레퍼런스(refBgAssetId) — 배경·조명·무드만 반영 (소유 검증 겸함, ADR-0004)
                rb = payload.get("refBgAssetId")
                if rb:
                    ref_bg_asset = await repo.get_asset_for_user(conn, user_id, str(rb))
            if src_asset is None:
                await _fail("변형할 컷을 찾을 수 없어요. 다시 시도해 주세요.",
                            {"error": "source_asset_missing", "src": source.get("src")})
                return
            src_img = InlineImage(
                src_asset["mime_type"],
                await asyncio.to_thread(app.state.r2.get_bytes, src_asset["r2_key"]))
            ref_bg_img = None
            if ref_bg_asset is not None:
                ref_bg_img = InlineImage(
                    ref_bg_asset["mime_type"],
                    await asyncio.to_thread(app.state.r2.get_bytes, ref_bg_asset["r2_key"]))
            await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

            cut_type = source.get("cutType")
            changes = payload.get("changes") or []
            try:
                image, mime = await cut_variator.generate(
                    s, app.state.gemini, src_img, changes, cut_type, ref_bg=ref_bg_img)
            except GeminiError as e:
                await _fail("컷 변형에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
                return
            group = None  # AG-07 결과는 misc 그룹 (계약 §6)
            cut_type = cut_type or "styling"  # cutType 미상 소스 → styling 가정(계약 §6)

        elif mode == "new":
            async with pool.connection() as conn:
                product = await repo.get_product(conn, project_id) or {}
                # analysis 도 로드 — 프롬프트 ground truth(소재·강조특징)와 확정 fitProfile
                # 텍스트 제약이 detail_page 경로와 동일하게 반영되도록(컷 파이프라인 계약 정합).
                analysis = await repo.get_analysis(conn, project_id) or {}
                assets = []
                for slot, aid in mannequin.base_color_images(product):
                    a = await repo.get_asset_for_user(conn, user_id, aid)
                    if a:
                        a["slot"] = slot  # 매니페스트 역할 라벨용
                        assets.append(a)
                # 무드 레퍼런스(refAssetIds) — 분위기(조명·색감)만 참고, 최대 3장 (ADR-0004)
                mood_rows = []
                for rid in [str(r) for r in (payload.get("refAssetIds") or [])][:3]:
                    ma = await repo.get_asset_for_user(conn, user_id, rid)  # 소유 검증 겸함
                    if ma:
                        mood_rows.append(ma)
            if not assets:
                await _fail("기준 색상 이미지를 찾을 수 없어요. 다시 시도해 주세요.",
                            {"error": "no_base_color_images"})
                return
            # 컷 계약 필드 통과(ADR-0004) — mirror·얼굴·포즈·생성예시·공간그룹까지 서버 정규화에 맡긴다.
            # colorId/matchIds 는 이 경로에서 제외: 색상별 첨부·매칭 첨부는 아직 없어서
            # 매니페스트와 첨부 순서가 어긋나지 않게 한다(첨부 확장 시 함께 통과시킬 것).
            cut_spec = {
                k: payload.get(k)
                for k in ("cutType", "direction", "shot", "faceExposure", "pose",
                          "exampleId", "spaceGroupId", "spaceVariation", "modelId", "model_id")
            }
            clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
            try:
                normalized = cut_generator.normalize_spec(cut_spec, clothing_type=clothing_type)
            except ValueError:
                await _fail("컷 설정이 올바르지 않아요. 다시 시도해 주세요.", {"error": "invalid_spec"})
                return

            # NewCutRequest.modelId가 이 경로의 정본. C방식 두 장을 원자적으로 로드하며,
            # 모르는 modelId/manifest/R2 실패는 모델 참조만 빼고 기존 상품 참조로 계속한다.
            model_images: list[InlineImage] = []
            try:
                model_refs = cut_generator.resolve_virtual_model_assets(normalized)
                if model_refs is not None:
                    model_images = [
                        InlineImage(
                            ref["mime"],
                            await asyncio.to_thread(app.state.r2.get_bytes, ref["key"]),
                        )
                        for ref in model_refs
                    ]
            except Exception as e:
                log.warning(
                    "AG-06 virtual model assets unavailable for job %s model %s; "
                    "continuing without model references: %r",
                    job_id, normalized.get("modelId"), e)
                model_images = []
            images = [
                *model_images,
                *[
                    InlineImage(a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"]))
                    for a in (*assets, *mood_rows)
                ],
            ]  # 순서 = 매니페스트: MODEL 2장? → 상품 슬롯들 → 무드
            await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

            manifest = cut_generator.build_manifest(
                assets, has_mannequin=False, has_match=False, mood_count=len(mood_rows),
                has_model_face=len(model_images) == 2, has_model_sheet=len(model_images) == 2)
            try:
                image, mime = await cut_generator.generate(
                    s, app.state.gemini, cut_spec, product, images,
                    analysis=analysis, manifest=manifest)
            except ValueError:
                await _fail("컷 설정이 올바르지 않아요. 다시 시도해 주세요.", {"error": "invalid_spec"})
                return
            except GeminiError as e:
                await _fail("컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
                return
            group = payload.get("colorId") or None
            cut_type = payload.get("cutType")

        else:
            await _fail("알 수 없는 요청이에요. 다시 시도해 주세요.", {"error": "unknown_mode", "mode": mode})
            return

        await _emit(pool, job_id, "progress", {"progress": 70, "phase": "generated"})

        # R2 저장
        ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
        asset_id = str(uuid.uuid4())
        key = ai_key(user_id, project_id, job_id, asset_id, ext)
        await asyncio.to_thread(app.state.r2.put_bytes, key, image, mime)
        w, h = _image_dims(image)
        image_row = {
            "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
            "size": len(image), "width": w, "height": h,
        }

        # 성공 종결 (원자·lease 펜스). charge = reserved — 예약 시점 견적 확정(부분 성공 없음.
        # 실행 시점 설정 재조회 금지 — 단가 변경이 배포 사이에 끼면 예약액과 다른 차감 발생).
        charge = reserved
        async with pool.connection() as conn:
            out = await repo.finalize_editor_image_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, image=image_row, group=group, cut_type=cut_type,
                reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version})
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            try:
                await asyncio.to_thread(app.state.r2.delete, key)
            except Exception:
                log.warning("orphan R2 cleanup failed: %s", key)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
