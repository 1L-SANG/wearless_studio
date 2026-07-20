"""AG-06/AG-07 에디터 이미지 워커 (PL-5/6). 에디터 AI 탭 '새 이미지 추가'(mode:'new')는
cut_generator(AG-06)를, '현재 이미지 수정'(mode:'vary')은 cut_variator(AG-07)를 재사용한다.
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
from ..agents import content_roles, cut_generator, cut_variator
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
    example_warnings: list[dict] = []

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
            changes = payload.get("changes") or []
            # AG-07에는 상품 Detail 원본이 첨부되지 않는다. 일반 제품컷 한 장을 확대해
            # 디테일을 지어내지 않도록 '디테일로 변경'은 새 이미지 추가(AG-06)에서만 허용한다.
            # 라우트가 raw dict를 받으므로 type 대소문자/미상값으로 우회해도, 계약상 detail을
            # 뜻하는 값 자체를 차단한다.
            detail_values = {"detail", "detail shot", "detailshot", "디테일", "디테일샷"}
            wants_detail = any(
                isinstance(change, dict)
                and str(change.get("value") or "").strip().lower() in detail_values
                for change in changes
            )
            if wants_detail:
                await _fail(
                    "현재 이미지 수정으로는 디테일샷을 만들 수 없어요. "
                    "새 이미지 추가에서 디테일샷을 선택해 주세요.",
                    {"error": "detail_variation_unsupported"},
                )
                return
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
            new_payload = content_roles.canonicalize_storyboard_block(payload)
            requested_color_id = new_payload.get("colorId")
            is_detail = new_payload.get("cutType") == "product" and new_payload.get("shot") == "detail"
            async with pool.connection() as conn:
                product = await repo.get_product(conn, project_id) or {}
                # analysis 도 로드 — 프롬프트 ground truth(소재·강조특징)와 확정 fitProfile
                # 텍스트 제약이 detail_page 경로와 동일하게 반영되도록(컷 파이프라인 계약 정합).
                analysis = await repo.get_analysis(conn, project_id) or {}
                # 일반 컷은 선택 색상을 엄격히 쓴다. 디테일만 목표 색상에 Detail이 없을 때
                # 기준색→첫 Detail 보유 색상의 근거를 붙이고 프롬프트에 색 전환을 명시한다.
                if is_detail:
                    image_refs, detail_color_transfer = cut_generator.detail_reference_images(
                        product, requested_color_id)
                else:
                    image_refs = cut_generator.color_images(product, requested_color_id)
                    detail_color_transfer = None
                assets = []
                for slot, aid in image_refs:
                    a = await repo.get_asset_for_user(conn, user_id, aid)
                    if a:
                        a["slot"] = slot  # 매니페스트 역할 라벨용
                        assets.append(a)
                # 무드 레퍼런스(refAssetIds) — 분위기(조명·색감)만 참고, 최대 3장 (ADR-0004)
                mood_rows = []
                for rid in [str(r) for r in (new_payload.get("refAssetIds") or [])][:3]:
                    ma = await repo.get_asset_for_user(conn, user_id, rid)  # 소유 검증 겸함
                    if ma:
                        mood_rows.append(ma)
            if is_detail and not any(asset.get("slot") == "Detail" for asset in assets):
                await _fail(
                    "디테일 참고 사진을 찾을 수 없어 디테일샷을 만들 수 없어요.",
                    {"error": "detail_reference_required", "colorId": requested_color_id},
                )
                return
            if not assets:
                if requested_color_id is not None:
                    await _fail("선택한 색상 이미지를 찾을 수 없어요. 다시 시도해 주세요.",
                                {"error": "no_selected_color_images",
                                 "colorId": requested_color_id})
                else:
                    # colorId가 없는 레거시 요청은 기존 기준 색상 폴백·에러 계약을 유지한다.
                    await _fail("기준 색상 이미지를 찾을 수 없어요. 다시 시도해 주세요.",
                                {"error": "no_base_color_images"})
                return
            # 컷 계약 필드 통과(ADR-0004) — mirror·얼굴·포즈·생성예시·공간그룹까지 서버 정규화에 맡긴다.
            # 에디터 새 이미지 패널은 아직 매칭 의류를 고르는 UI·payload를 제공하지 않으므로
            # matchIds를 의도적으로 제외한다. 후속 배선 시에는 상세페이지와 같은 정책으로
            # styling·horizon·mirror에만 MATCHING을 첨부하고 product에는 적용하지 않는다.
            # colorId는 목표 색상이며, 디테일만 위 정책에 따라 타색 근거가 추가될 수 있다.
            cut_spec = {
                k: new_payload.get(k)
                for k in ("contentRole", "cutType", "direction", "shot", "faceExposure", "pose",
                          "outerClosureState", "exampleId", "spaceGroupId", "spaceVariation", "modelId", "model_id",
                          "colorId", "refScope")
            }
            if detail_color_transfer:
                cut_spec["_detailColorTransfer"] = detail_color_transfer
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
            example_scope = None
            example_id = normalized.get("exampleId")
            pose_overrides_example = (
                normalized["pose"] != "auto" and normalized["refScope"] == "pose"
            )
            if example_id and not pose_overrides_example:
                scope = normalized["refScope"]
                status = cut_generator.example_asset_status(
                    example_id, clothing_type, scope)
                if status in ("not_applicable", "variant_unpublished"):
                    example_warnings.append({
                        "code": "example_not_applicable"
                        if status == "not_applicable" else "example_variant_unpublished",
                        "exampleId": example_id,
                        "clothingType": clothing_type,
                        "refScope": scope,
                    })
                    # 미첨부 all 예시의 레거시 EXNUANCE까지 제거해 예시가 완전히 무효가 되게 한다.
                    cut_spec["exampleId"] = None
                else:
                    example_image = await cut_generator.load_example_image(
                        s, example_id, scope=scope, clothing_type=clothing_type)
                    if example_image is not None:
                        images.append(example_image)
                        example_scope = scope
            await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

            manifest = cut_generator.build_manifest(
                assets, has_mannequin=False, has_match=False, mood_count=len(mood_rows),
                has_model_face=len(model_images) == 2, has_model_sheet=len(model_images) == 2,
                example_scope=example_scope,
                example_is_product=normalized["cutType"] == "product")
            try:
                image, mime = await cut_generator.generate(
                    s, app.state.gemini, cut_spec, product, images,
                    analysis=analysis, manifest=manifest)
            except ValueError as e:
                if str(e) == "detail_reference_required":
                    await _fail(
                        "디테일 참고 사진을 찾을 수 없어 디테일샷을 만들 수 없어요.",
                        {"error": "detail_reference_required", "colorId": requested_color_id},
                    )
                else:
                    await _fail("컷 설정이 올바르지 않아요. 다시 시도해 주세요.",
                                {"error": "invalid_spec"})
                return
            except GeminiError as e:
                await _fail("컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
                return
            group = normalized["colorId"] or None
            cut_type = normalized["cutType"]

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
        success_metadata = {"creditCostVersion": s.credit_cost_version}
        if example_warnings:
            success_metadata["warnings"] = example_warnings
        async with pool.connection() as conn:
            out = await repo.finalize_editor_image_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, image=image_row, group=group, cut_type=cut_type,
                reserved=reserved, charge=charge,
                metadata=success_metadata)
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            try:
                await asyncio.to_thread(app.state.r2.delete, key)
            except Exception:
                log.warning("orphan R2 cleanup failed: %s", key)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
