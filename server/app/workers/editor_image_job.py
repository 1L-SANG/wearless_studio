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

from .. import facemarket, repo
from ..agents import content_roles, cut_generator, cut_variator, identity_source, image_qc
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.vision_llm import VisionError
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
    scene_qc_attempts: int | None = None  # bg 장소일치 QC 통과까지의 시도 수(관찰용, new 모드 bg만)
    garment_qc_metadata: dict | None = None  # new 모드만; vary 경로는 QC·메타 모두 무변경

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
        fm_source: str | None = None      # 에디터 컷 아이덴티티 소스 — REAL 이면 성공 시 정산 대상
        fm_license_row: dict | None = None
        fm_face_injected = False          # REAL 자산 2장이 실제 첨부됐을 때만 정산(미첨부 과금 방지)

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

            # 아이덴티티 소스 1회 결정(detail_page 와 동일 계약, codex [P1]) — 실존 모델(UUID)은
            # REAL 로 비공개 자산을 첨부하고, 라이선스 실패면 조용한 폴백 없이 잡 실패(라우트 409
            # 게이트 이후 해지 레이스 방어). 가상모델('mA' 등)은 기존 VIRTUAL 경로 그대로.
            selected_model_id = normalized.get("modelId") or normalized.get("model_id")
            real_refs = None
            if s.facemarket_enabled and selected_model_id:
                async with pool.connection() as conn:
                    real_refs = await identity_source.resolve_real_model_assets(
                        conn, selected_model_id)
                    if real_refs is not None:
                        fm_license_row = await facemarket.resolve_model_license(
                            conn, selected_model_id)
                fm_source = identity_source.select_source(
                    selected_model_id=selected_model_id, license_row=fm_license_row,
                    has_real_assets=real_refs is not None, has_license_face=False)
                log.info("AG-06 identity source=%s job=%s hasReal=%s",
                         fm_source, job_id, real_refs is not None)
                if fm_source == "REJECTED":
                    await _fail("모델의 얼굴 라이선스가 활성 상태가 아니에요. 다시 확인해 주세요.",
                                {"error": "license_rejected", "modelId": str(selected_model_id)})
                    return

            # NewCutRequest.modelId가 이 경로의 정본. C방식 두 장을 원자적으로 로드하며,
            # 모르는 modelId/manifest/R2 실패는 모델 참조만 빼고 기존 상품 참조로 계속한다
            # (단, REAL 은 라이선스 소비 대상이라 자산 로드 실패 시 계속하지 않고 잡 실패).
            model_images: list[InlineImage] = []
            try:
                model_refs = (real_refs if real_refs is not None
                              else cut_generator.resolve_virtual_model_assets(normalized))
                if model_refs is not None:
                    # 버킷 인지 — 실존 모델 그리드(bucket='face')는 비공개 r2_face 에서 로드해
                    # 공개 버킷으로 얼굴 키가 새지 않게 한다(가상 모델은 bucket='public').
                    model_images = []
                    for ref in model_refs:
                        client = (app.state.r2_face if ref.get("bucket") == "face"
                                  else app.state.r2)
                        if client is None:
                            raise RuntimeError("bucket client unavailable")
                        model_images.append(
                            InlineImage(ref["mime"],
                                        await asyncio.to_thread(client.get_bytes, ref["key"])))
            except Exception as e:
                if fm_source == "REAL":
                    await _fail("모델 자산을 불러오지 못했어요. 다시 시도해 주세요.",
                                {"error": "real_model_assets_unavailable",
                                 "detail": repr(e)[:200]})
                    return
                log.warning(
                    "AG-06 virtual model assets unavailable for job %s model %s; "
                    "continuing without model references: %r",
                    job_id, normalized.get("modelId"), e)
                model_images = []
            fm_face_injected = fm_source == "REAL" and len(model_images) == 2
            product_images = [
                InlineImage(a["mime_type"], await asyncio.to_thread(
                    app.state.r2.get_bytes, a["r2_key"]))
                for a in assets
            ]
            mood_images = [
                InlineImage(a["mime_type"], await asyncio.to_thread(
                    app.state.r2.get_bytes, a["r2_key"]))
                for a in mood_rows
            ]
            images = [*model_images, *product_images, *mood_images]
            # 순서 = 매니페스트: MODEL 2장? → 상품 슬롯들 → 무드
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
                elif scope == "pose" and not cut_generator.pose_direction_compatible(
                    example_id, normalized
                ):
                    # 단건 에디터는 배치의 빈 슬롯 대신 명시적 실패로 닫는다. 이 지점은
                    # 이미지 로드와 Gemini 호출보다 앞이라 불일치 조합의 생성 호출은 0회다.
                    await _fail(
                        "이 예시의 포즈 방향이 현재 컷 방향과 맞지 않아요. 다른 예시를 선택해 주세요.",
                        {
                            "error": "pose_direction_incompatible",
                            "exampleId": example_id,
                            "direction": normalized.get("direction"),
                        },
                    )
                    return
                else:
                    example_image = await cut_generator.load_example_image(
                        s, example_id, scope=scope, clothing_type=clothing_type)
                    if example_image is None and scope in ("pose", "bg"):
                        # 전용 자산 없이 pose/bg를 생성하면 "참고한 척"이 된다 — 무음 강등 금지
                        # (2026-07-20 실측: 이 강등이 bg 실패의 실제 원인 일부였다. ADR-0009 §2)
                        await _fail("예시 자산을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                                    {"error": "example_asset_unavailable",
                                     "exampleId": example_id, "refScope": scope})
                        return
                    if example_image is not None:
                        # bg 플레이트는 시각 앵커가 약해 마지막 첨부로는 무시된다(2026-07-20
                        # 파일럿 실측: 텍스트 강화만으로 2/7) — 첫 첨부로 올려 프라이머시를 준다.
                        if scope == "bg":
                            images.insert(0, example_image)
                        else:
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

            scene_plate = None
            # bg 편집 컷 — 장소일치 QC 게이트(2026-07-20): 생성은 샘플링이라 편집 프레이밍을
            # 줘도 확률적으로 다른 장소가 나온다. 플레이트(첫 첨부)와 대조해 불일치면 재생성,
            # 상한 초과면 실패 종결(부분 성공 아님 — 에디터는 단건). QC 판정 불능은 fail-open.
            if example_scope == "bg" and example_image is not None:
                attempts_max = max(1, s.bg_scene_qc_attempts)
                scene_plate = example_image
                attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, scene_plate, InlineImage(mime, image))
                    except VisionError as e:
                        log.warning("AG-06 scene QC unavailable job %s: %r — fail-open", job_id, e)
                        example_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if attempt >= attempts_max:
                        await _fail("배경 예시의 장소를 재현하지 못했어요. 다시 시도해 주세요.",
                                    {"error": "bg_scene_mismatch",
                                     "attempts": attempt,
                                     "mismatches": scene_qc["mismatches"][:5]})
                        return
                    attempt += 1
                    try:
                        image, mime = await cut_generator.generate(
                            s, app.state.gemini, cut_spec, product, images,
                            analysis=analysis, manifest=manifest)
                    except (GeminiError, ValueError) as e:
                        await _fail("컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
                        return
                scene_qc_attempts = attempt

            async def _generate_candidate():
                candidate_image, candidate_mime = await cut_generator.generate(
                    s, app.state.gemini, cut_spec, product, images,
                    analysis=analysis, manifest=manifest)
                if scene_plate is None:
                    return InlineImage(candidate_mime, candidate_image)

                candidate_attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, scene_plate, InlineImage(candidate_mime, candidate_image))
                    except VisionError as e:
                        log.warning(
                            "AG-06 candidate scene QC unavailable job %s: %r — fail-open",
                            job_id, e)
                        example_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if candidate_attempt >= max(1, s.bg_scene_qc_attempts):
                        raise RuntimeError("bg candidate scene mismatch")
                    candidate_attempt += 1
                    candidate_image, candidate_mime = await cut_generator.generate(
                        s, app.state.gemini, cut_spec, product, images,
                        analysis=analysis, manifest=manifest)
                return InlineImage(candidate_mime, candidate_image)

            chosen, garment_qc_metadata, garment_warnings = await image_qc.best_of(
                s,
                product_images,
                InlineImage(mime, image),
                _generate_candidate,
            )
            image, mime = chosen.data, chosen.mime
            example_warnings.extend(garment_warnings)
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
        if scene_qc_attempts is not None:
            success_metadata["sceneQc"] = {"attempts": scene_qc_attempts}
        if garment_qc_metadata is not None:
            success_metadata["garmentQc"] = garment_qc_metadata
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
        elif (s.facemarket_enabled and fm_face_injected and fm_license_row is not None
              and fm_license_row.get("unit_price") is not None
              and getattr(app.state, "fm_chain", None) is not None):
            # FaceMarket 온체인 정산 훅(선택과제2) — 에디터 컷도 얼굴 라이선스 1회 사용으로
            # detail_page 와 동일하게 70/20/10 기록. payment_key=job:{id} 멱등(컨트랙트 중복
            # revert + fm_settlements UNIQUE). best-effort: 정산 실패가 완료된 생성을 안 되돌림.
            try:
                await facemarket.record_license_settlement(
                    app, payment_key=f"job:{job_id}", license_id=str(fm_license_row["id"]),
                    model_id=str(fm_license_row["model_id"]),
                    total=int(fm_license_row["unit_price"]), job_id=job_id)
            except Exception:
                log.exception("editor_image settlement hook failed for job %s", job_id)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
