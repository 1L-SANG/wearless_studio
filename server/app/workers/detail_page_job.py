"""PL-4 상세페이지 생성 워커. AG-06 컷 → AG-02 카피 → AG-03 검수 → M-02 조립 → EditorBlock[].

저장 콘티(projects.storyboard)의 source='ai' 블록별로 AG-06 컷 이미지를 생성(실패 컷은 빈 슬롯,
전체 중단 없음·미차감), copywriting 이면 블록별 AG-02 카피 + 묶음 AG-03 검수, page_assembler(M-02)
로 EditorBlock[] 조립. 크레딧: 성공 컷 수 × storyboardPerCut 만 confirm(부분 성공). lease 펜스.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import replace
from io import BytesIO

from PIL import Image

from .. import facemarket, repo
from ..agents import (
    content_roles,
    copy_qc,
    copywriter,
    confirmed_gpt_runtime,
    cut_generator,
    cut_output_qc,
    cut_plan,
    feature_copy,
    image_qc,
    mannequin,
    page_assembler,
    page_output_qc,
    space_set_assets,
)
from ..agents.gemini_image import InlineImage
from ..agents.model_routing import resolve_detail_cut_model
from ..agents.vision_llm import VisionError
from ..r2 import IMMUTABLE_CACHE, ai_key, ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.detail_page_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
# 컷·카피 동시 생성 상한. 순차(블록 수 × ~40s)면 4컷에 2~3분 → 병렬로 단축. gemini 버스트
# 제한을 감안해 무제한이 아닌 소폭 동시성(429 시 이 값을 낮춘다).
_GEN_CONCURRENCY = 3
_WORN_CUT_TYPES = ("styling", "horizon", "mirror")


def _example_repeat_indexes(
    blocks: list[dict], clothing_type: str,
) -> list[int | None]:
    """같은 섹션·같은 all 예시의 반복 변주 지수를 저장값 없이 결정적으로 계산한다.

    반복 규칙(2026-08-14 오너 확정): 포즈 변주는 **같은 예시를 다른 색상으로 반복**할
    때만 적용한다(컬러웨이 반복 컷). ``None``은 대상 아님, ``0``은 변주 없음(첫 색상),
    1 이상은 n번째 다른 색상. 같은 예시·같은 색상의 반복(컷 복제)은 0 — 변주 없이
    ``_duplicate_source_indexes`` 가 생성 자체를 1장으로 접는다.
    클라이언트가 보낸 런타임 필드는 읽지 않고 정규화된 컷 계약만 판정한다.
    """

    color_orders: dict[tuple[str, str], dict[str, int]] = {}
    indexes: list[int | None] = []
    for block in blocks:
        repeat_index = None
        if isinstance(block, dict) and block.get("source") == "ai":
            safe_block = dict(block)
            for runtime_field in (
                "_exampleRepeatIndex",
                "_referenceDirectionCompatible",
                "_spaceSetContinuity",
                "_detailColorTransfer",
            ):
                safe_block.pop(runtime_field, None)
            try:
                spec = cut_generator.normalize_spec(
                    safe_block, clothing_type=clothing_type
                )
                spec = cut_generator.apply_reference_compatibility(spec)
            except ValueError:
                spec = None
            if (
                spec is not None
                and spec.get("cutType") in _WORN_CUT_TYPES
                and spec.get("exampleId")
                and not spec.get("spaceGroupId")
                and spec.get("refScope") == "all"
                and spec.get("pose") == "auto"
                and spec.get("_referenceDirectionCompatible") is not False
            ):
                section = block.get("sectionId") or block.get("section_id")
                if not section:
                    role = block.get("sectionRole") or block.get("section_role") or "unknown"
                    section = f"role:{role}"
                key = (str(section), str(spec["exampleId"]))
                color = str(spec.get("colorId") or "")
                orders = color_orders.setdefault(key, {})
                if color not in orders:
                    orders[color] = len(orders)
                repeat_index = orders[color]
        indexes.append(repeat_index)
    return indexes


def _duplicate_source_indexes(
    blocks: list[dict], clothing_type: str,
) -> list[int | None]:
    """생성 계약이 완전히 같은 뒤쪽 블록 → 앞쪽 원본 인덱스 매핑.

    같은 컷을 복제해 넣은 경우(같은 예시·같은 색상·같은 설정) 이미지 생성을 1번만
    하고 결과를 복제 위치에 그대로 복사한다(2026-08-14 오너 확정). 판정은 정규화된
    컷 계약 전체(주입된 ``_exampleRepeatIndex`` 포함)로 하므로, 색상이 다른 반복은
    변주 지수부터 달라 복제로 접히지 않는다. ``None`` = 원본(직접 생성).
    """

    first_by_key: dict[str, int] = {}
    sources: list[int | None] = []
    for index, block in enumerate(blocks):
        source = None
        if isinstance(block, dict) and block.get("source") == "ai":
            try:
                spec = cut_generator.normalize_spec(
                    dict(block), clothing_type=clothing_type
                )
                spec = cut_generator.apply_reference_compatibility(spec)
            except ValueError:
                spec = None
            if spec is not None and not spec.get("spaceGroupId"):
                key = json.dumps(
                    {k: v for k, v in spec.items() if k not in {"id", "blockId"}},
                    sort_keys=True, ensure_ascii=False, default=str,
                )
                if key in first_by_key:
                    source = first_by_key[key]
                else:
                    first_by_key[key] = index
        sources.append(source)
    return sources


def _dims(data: bytes):
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


async def _load_license_face(app, conn, project: dict) -> dict | None:
    """프로젝트에 잠긴 얼굴 라이선스의 얼굴 이미지 → {image, license_id, model_name}. 없으면 None.

    FM-31 "라이선스 얼굴이 실제 상세컷에 나오게" 의 입력 로더. **잠금이 없으면 쿼리조차 돌지
    않는다** — 라이선스 없는 기존 셀러 경로는 이 함수가 즉시 None 이라 완전 무변경.

    verify-before-use 재확인: 게이트(routes.generate_detail_page)는 **요청 시점**에만 검증하므로
    그 뒤 해지·만료된 라이선스가 큐에 남을 수 있다. 얼굴은 한 번 생성되면 공개 URL 로 나가
    회수가 불가능하므로 워커에서 status/만료를 한 번 더 본다(게이트와 같은 판정 함수 _is_expired).

    실패(r2_face 미설정·해지·만료·dangling 키)는 잡을 죽이지 않고 **얼굴 없이 생성**으로 강등한다:
    상세페이지는 부분 성공 계약이고, 얼굴 게이트(get_license_face)도 같은 상황을 404 로
    우아하게 강등한다. 강등 시 AI 고지도 기본 문구로 돌아가므로 허위 고지가 생기지 않는다.
    로그에 얼굴 바이트·R2 키·digest 를 남기지 않는다(PII 룰).
    """
    s = app.state.settings
    lic_id = project.get("facemarket_license_id") or project.get("facemarketLicenseId")
    if not s.facemarket_enabled or not lic_id:
        return None
    r2_face = getattr(app.state, "r2_face", None)
    if r2_face is None:  # 얼굴=생체 PII → 공개 버킷 폴백 금지(개인화 워커 선례)
        log.warning("facemarket face skipped (no face storage) license %s", lic_id)
        return None
    # 지연 import — facemarket 모듈과의 순환 참조 회피(정산 훅 선례와 동일).
    from ..facemarket import _EXT_TO_MIME, _is_expired, _mask_name

    async with conn.cursor() as cur:
        await cur.execute(
            """select l.face_image_key, l.status, l.license_valid_until, m.display_name
               from fm_licenses l join fm_models m on m.id = l.model_id
               where l.id = %s""",
            (str(lic_id),),
        )
        lic = await cur.fetchone()
    if not lic or not lic["face_image_key"]:
        return None
    if lic["status"] != "active" or _is_expired(lic):
        log.warning("facemarket face skipped (license %s status=%s)", lic_id, lic["status"])
        return None
    key = lic["face_image_key"]
    mime = _EXT_TO_MIME.get(key.rsplit(".", 1)[-1].lower())
    if not mime:  # 키 확장자 역매핑 실패(fm_licenses 에 mime 컬럼 부재) — 얼굴 없이 생성
        return None
    try:
        data = await asyncio.to_thread(r2_face.get_bytes, key)
    except Exception:  # 개인화 파기로 얼굴 객체만 지워진 dangling 키 등
        log.warning("facemarket face skipped (object unavailable) license %s", lic_id)
        return None
    return {
        "image": InlineImage(mime, data),
        "license_id": str(lic_id),
        "model_name": _mask_name(lic["display_name"] or ""),
    }


async def _gen_cuts(app, job, prepared, product, analysis):
    """준비된 블록별
    (block, images, manifest, has_face, product_images,
    space_set_plate, strict_space_scene_qc, passthrough, confirmed_packet)로 AG-06 컷 생성
    → (cut_results, cut_assets, face_cuts, garment_qcs, cut_qcs, page_qc, warnings).
    face_cuts = 라이선스 얼굴이 실제로 들어가고
    **성공까지 한** 컷 수 — AI 고지 문구 분기의 사실 근거(주입 0건이면 기본 문구).
    실패 컷은 건너뛴다(빈 슬롯은 assemble 이 처리) — 부분 성공. 스펙 위반(unknown cutType)도
    같은 경로(빈 슬롯) — 조용한 styling 대체 렌더는 하지 않는다(ADR-0004)."""
    s, gemini, r2 = app.state.settings, app.state.gemini, app.state.r2
    # AG-06만 상세컷 전용 모델을 사용한다. 공용 cut_generator의 기본 라우트를
    # 바꾸면 에디터의 '새 이미지'까지 함께 GPT로 전환되므로, 이 워커 안에서만
    # 불변 Settings 복사본의 image_high를 상세컷 snapshot으로 치환한다.
    detail_settings = replace(s, model_image_high=resolve_detail_cut_model(s))
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    suppress_preview_urls = bool(job.get("_suppress_detail_preview_urls"))
    # 동시성: 설정값(0=제한 없음 → 컷 수만큼). 구 상수 3은 429 실측 없는 보수적 추정이라
    # 오너 결정(2026-08-03)으로 전부 병렬 + 제출 간격(stagger) + 429 백오프가 기본이 됐다.
    _limit = getattr(s, "detail_cut_concurrency", 0) or max(1, len(prepared))
    sem = asyncio.Semaphore(_limit)
    page_input_unavailable = object()
    clothing_type = (
        product.get("clothing_type") or product.get("clothingType") or "top"
    )

    def _page_plan_item(item, output_index, product_truth_indexes) -> dict:
        block = item[0]
        normalized = cut_generator.normalize_spec(
            block, clothing_type=clothing_type
        )
        return {
            "outputIndex": output_index,
            "blockId": str(block.get("id")),
            "targetColor": normalized.get("colorId") or "base",
            "clothingType": clothing_type,
            "cutType": normalized["cutType"],
            "outerClosureState": normalized.get("outerClosureState"),
            "modelId": normalized.get("modelId"),
            "matchingIds": normalized.get("matchIds") or [],
            "spaceGroupId": normalized.get("spaceGroupId"),
            "productTruthIndexes": product_truth_indexes,
        }

    async def _one_impl(item):
        """컷 1개 생성+저장. 실패(빈 슬롯)면 None. 각 블록 독립이라 동시 실행 가능."""
        b, images, manifest, has_face, product_images = item[:5]
        space_set_plate = item[5] if len(item) > 5 else None
        strict_space_scene_qc = bool(item[6]) if len(item) > 6 else False
        passthrough = item[7] if len(item) > 7 else None
        confirmed_packet = item[8] if len(item) > 8 else None
        # 최신 main의 첫 화면 시그니처 컷은 자체 모델/폴백 계약을 가진다. AG-06 일반
        # 컷용 GPT 설정을 덮어씌우지 않고 원래 Settings를 써서 그 경계를 보존한다.
        generation_settings = (
            s if cut_generator.is_signature_cut(b) else detail_settings
        )
        # 원본 패스스루 — 미세 패턴(스트라이프·체크) 상품의 디테일 컷은 **생성하지 않고**
        # 셀러가 찍은 그 색상의 Detail 사진을 그대로 쓴다. 원단 매크로는 전신 컷 해상도로는
        # 재현이 불가능하다(2026-08-01 측정: 4K 에서도 줄 한 주기당 14px → 파란 2가닥을 그리려면
        # 가닥당 1px 미만). 있는 원본을 다시 그려 정보를 버리는 대신 그대로 싣는다.
        # 생성 호출도 그만큼 줄어든다. 여기서 하는 이유: gather 순서가 곧 블록 순서라
        # 나중에 합치면 컷 배열이 어긋난다.
        if passthrough is not None:
            page_item = None
            passthrough_warnings = []
            if s.page_output_qc_mode == "shadow":
                try:
                    page_item = InlineImage(
                        passthrough["mime_type"],
                        await asyncio.to_thread(r2.get_bytes, passthrough["r2_key"]),
                    )
                except Exception as e:
                    log.warning(
                        "AG-06 page QC passthrough input unavailable job %s block %s: %r",
                        job_id, b.get("id"), e,
                    )
                    page_item = page_input_unavailable
                    passthrough_warnings.append({"code": "page_output_qc_input_unavailable"})
            await _emit(app.state.pool, job_id, "step",
                        {"blockId": b.get("id"), "status": "cut_passthrough",
                         "assetId": passthrough["id"]})
            return (
                {"blockId": b.get("id"), "imageUrl": f"/v1/assets/{passthrough['id']}/file",
                 "width": passthrough.get("width"), "height": passthrough.get("height")},
                None,          # 새 asset 을 만들지 않는다 — 이미 존재하는 셀러 자산이다
                False, None, None, passthrough_warnings, page_item,
            )
        async with sem:
            if not images:  # 옷 근거(상품/마네킹) 없음 — 무드만으로는 동일성 보장 불가, 생성하지 않는다
                log.warning("AG-06 cut skipped (no garment-truth references) job %s block %s", job_id, b.get("id"))
                await _emit(app.state.pool, job_id, "step",
                            {"blockId": b.get("id"), "status": "cut_failed"})
                return None
            # 대기 화면의 "지금 그리는 중" 표시 근거 — 세마포어를 잡은 뒤에 쏴야
            # 큐 대기 중인 컷이 전부 '생성 중'으로 보이지 않는다(editor_wait_dev_spec §2-1).
            await _emit(app.state.pool, job_id, "step",
                        {"blockId": b.get("id"), "status": "cut_start"})
            if confirmed_packet is not None:
                generate_kwargs = {
                    "confirmed_prompt_input": confirmed_packet.prompt_input,
                }
            else:
                generate_kwargs = {"analysis": analysis, "manifest": manifest}
                if has_face:
                    generate_kwargs["has_face"] = True
            # 컷 생성 재시도 — 안전필터·응답 누락처럼 "다시 부르면 달라질 수 있는" 실패는
            # 한 번 더 시도한다. 빈 슬롯은 셀러에게 그냥 못 만든 페이지이고, 그 값은 우리가
            # 흡수해야 한다(오너 8/15). ValueError(잘못된 cutType 등)는 결정적이라 제외.
            # 재시도 예산(초). 잡 lease(job_lease_timeout_seconds)를 넘기면 sweeper 가 실행 중인
            # 잡을 회수해 같은 잡이 다시 도는 사고가 난다 — 앞선 시도가 이미 오래 걸렸으면
            # (예: 프로바이더 타임아웃 연쇄) 재시도하지 않고 그 컷만 포기한다.
            retry_budget_s = max(60, s.job_lease_timeout_seconds // 4)
            cut_started = time.monotonic()
            img = mime = None
            max_attempts = (
                1 if confirmed_packet is not None else max(1, s.detail_cut_max_attempts)
            )
            for attempt in range(1, max_attempts + 1):
                try:
                    img, mime = await cut_generator.generate(
                        generation_settings, gemini, b, product, images, **generate_kwargs)
                    break
                except ValueError as e:  # 입력 계약 위반 — 재시도해도 같다
                    log.warning("AG-06 cut invalid for job %s block %s: %r", job_id, b.get("id"), e)
                    await _emit(app.state.pool, job_id, "step",
                                {"blockId": b.get("id"), "status": "cut_failed"})
                    return None
                except Exception as e:  # GeminiError 등 — 마지막 시도에서만 빈 슬롯(미차감)
                    spent = time.monotonic() - cut_started
                    # 프로바이더가 이미 그렸을 수 있는 실패(읽기 타임아웃·502/504)는 여기서도
                    # 다시 보내지 않는다 — 아래층이 안 보내기로 한 이유가 위층에서 무효가 되면
                    # 같은 컷을 두 번 과금한다(2026-08-17 리뷰).
                    billable = bool(getattr(e, "billable", False))
                    if billable or attempt >= max_attempts or spent >= retry_budget_s:
                        log.warning("AG-06 cut failed for job %s block %s after %d attempts (%.0fs): %r",
                                    job_id, b.get("id"), attempt, spent, e)
                        await _emit(app.state.pool, job_id, "step",
                                    {"blockId": b.get("id"), "status": "cut_failed"})
                        return None
                    log.info("AG-06 cut retry %d for job %s block %s: %r",
                             attempt, job_id, b.get("id"), e)
                    if s.detail_cut_retry_delay_seconds > 0:
                        await asyncio.sleep(s.detail_cut_retry_delay_seconds)
            plate = space_set_plate
            # bg 편집 컷은 첫 첨부, 공간 세트는 별도 전달된 대표 plate를 같은 장소 QC 기준으로 쓴다.
            if (
                plate is None
                and b.get("refScope") == "bg"
                and manifest.startswith("1. EXAMPLE REFERENCE (scope: bg)")
            ):
                plate = images[0]
            # 장소일치 QC 게이트. 불일치면 재생성, 상한 초과면 이 컷만 빈 슬롯(부분 성공).
            # 일반 bg 편집은 기존 fail-open, 발행 공간세트는 QC 불능도 fail-closed다.
            if plate is not None:
                attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, plate, InlineImage(mime, img))
                    except VisionError as e:
                        if strict_space_scene_qc:
                            log.warning(
                                "AG-06 production space-set scene QC unavailable "
                                "job %s block %s: %r — fail-closed",
                                job_id,
                                b.get("id"),
                                e,
                            )
                            await _emit(
                                app.state.pool,
                                job_id,
                                "step",
                                {
                                    "blockId": b.get("id"),
                                    "status": "cut_failed",
                                },
                            )
                            return None
                        log.warning(
                            "AG-06 scene QC unavailable job %s block %s: %r — fail-open",
                            job_id,
                            b.get("id"),
                            e,
                        )
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if attempt >= max(1, s.bg_scene_qc_attempts):
                        log.warning("AG-06 scene mismatch after %d attempts job %s block %s: %s",
                                    attempt, job_id, b.get("id"), scene_qc["mismatches"][:3])
                        await _emit(app.state.pool, job_id, "step",
                                    {"blockId": b.get("id"), "status": "cut_failed"})
                        return None
                    attempt += 1
                    try:
                        img, mime = await cut_generator.generate(
                            generation_settings, gemini, b, product, images, **generate_kwargs)
                    except Exception as e:
                        log.warning("AG-06 scene retry generate failed job %s block %s: %r",
                                    job_id, b.get("id"), e)
                        await _emit(app.state.pool, job_id, "step",
                                    {"blockId": b.get("id"), "status": "cut_failed"})
                        return None
            candidate_scene_warnings = []

            async def _generate_candidate():
                candidate_img, candidate_mime = await cut_generator.generate(
                    generation_settings, gemini, b, product, images, **generate_kwargs)
                if plate is None:
                    return InlineImage(candidate_mime, candidate_img)

                candidate_attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, plate, InlineImage(candidate_mime, candidate_img))
                    except VisionError as e:
                        if strict_space_scene_qc:
                            raise RuntimeError(
                                "production space-set scene QC unavailable"
                            ) from e
                        log.warning(
                            "AG-06 candidate scene QC unavailable job %s block %s: %r — fail-open",
                            job_id, b.get("id"), e)
                        candidate_scene_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if candidate_attempt >= max(1, s.bg_scene_qc_attempts):
                        raise RuntimeError("candidate scene mismatch")
                    candidate_attempt += 1
                    candidate_img, candidate_mime = await cut_generator.generate(
                        generation_settings, gemini, b, product, images, **generate_kwargs)
                return InlineImage(candidate_mime, candidate_img)

            if confirmed_packet is not None:
                # The reviewed baseline was first-result-only.  Independent cut QC below
                # owns the optional single Stage-2 attempt; best-of is never inserted.
                chosen, garment_qc, garment_warnings = (
                    InlineImage(mime, img),
                    None,
                    [],
                )
            else:
                chosen, garment_qc, garment_warnings = await image_qc.best_of(
                    s,
                    product_images,
                    InlineImage(mime, img),
                    _generate_candidate,
                )
            img, mime = chosen.data, chosen.mime
            garment_warnings = [*candidate_scene_warnings, *garment_warnings]

            cut_qc = None
            if s.cut_output_qc_mode in {"shadow", "repair"}:
                try:
                    normalized_spec = cut_generator.normalize_spec(
                        b, clothing_type=clothing_type
                    )
                    normalized_spec = cut_generator.apply_reference_compatibility(
                        normalized_spec
                    )
                    plan = cut_plan.compile_cut_plan(
                        normalized_spec,
                        clothing_type,
                        fit_profile=(analysis or {}).get("fitProfile"),
                    )
                    qc_references = cut_output_qc.references_from_manifest(
                        manifest, images
                    )
                    authority_profile = (
                        "confirmed_gpt_v1"
                        if confirmed_packet is not None
                        else "generic_v1"
                    )
                    verdict_kwargs = (
                        {
                            "authority_profile": authority_profile,
                            "confirmed_prompt_input": confirmed_packet.prompt_input,
                        }
                        if confirmed_packet is not None
                        else {}
                    )
                    cut_qc = await cut_output_qc.verdict(
                        s,
                        plan,
                        qc_references,
                        chosen,
                        **verdict_kwargs,
                    )

                    if s.cut_output_qc_mode == "repair":
                        route = cut_output_qc.repair_route(cut_qc)
                        repair = {
                            "attempted": False,
                            "route": route,
                            "accepted": False,
                            "finalSource": "stage1",
                        }
                        if route in {"EDIT_STAGE1", "REGENERATE_FROM_SCRATCH"}:
                            instructions = cut_output_qc.repair_instructions(cut_qc)
                            repair["attempted"] = True
                            try:
                                if route == "EDIT_STAGE1":
                                    repair_kwargs = {"qc_corrections": instructions}
                                    if confirmed_packet is not None:
                                        repair_kwargs["confirmed_prompt_input"] = (
                                            confirmed_packet.prompt_input
                                        )
                                    repaired_img, repaired_mime = await cut_generator.repair(
                                        generation_settings,
                                        gemini,
                                        b,
                                        product,
                                        chosen,
                                        **repair_kwargs,
                                    )
                                else:
                                    repaired_img, repaired_mime = await cut_generator.generate(
                                        generation_settings,
                                        gemini,
                                        b,
                                        product,
                                        images,
                                        **generate_kwargs,
                                        qc_corrections=instructions,
                                    )
                                repaired = InlineImage(repaired_mime, repaired_img)
                                repaired_qc = await cut_output_qc.verdict(
                                    s,
                                    plan,
                                    qc_references,
                                    repaired,
                                    **verdict_kwargs,
                                )
                                comparison = cut_output_qc.compare_repair(
                                    cut_qc, repaired_qc
                                )
                                repair.update(comparison)
                                repair["stage2Qc"] = repaired_qc
                                if comparison["accepted"]:
                                    chosen = repaired
                                    img, mime = repaired.data, repaired.mime
                                    repair["finalSource"] = "stage2"
                            except Exception as e:
                                # Stage2는 정확히 한 번만 시도한다. timeout/응답 불명확을 여기서
                                # 다시 호출하면 중복 과금될 수 있으므로 1차를 보존하고 끝낸다.
                                log.warning(
                                    "AG-06 cut repair unavailable job %s block %s: %r — keep stage1",
                                    job_id,
                                    b.get("id"),
                                    e,
                                )
                                repair["error"] = "repair_unavailable"
                                garment_warnings.append({
                                    "code": "cut_output_qc_repair_unavailable"
                                })
                        cut_qc = {**cut_qc, "repair": repair}
                except Exception as e:
                    # QC/plan/manifest/provider 오류가 성공한 1차 이미지를 막지 않는다.
                    log.warning(
                        "AG-06 cut output QC unavailable job %s block %s: %r — keep stage1",
                        job_id,
                        b.get("id"),
                        e,
                    )
                    garment_warnings.append({"code": "cut_output_qc_unavailable"})

            ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            async with app.state.pool.connection() as conn:
                cleanup_intent_id = await repo.create_ai_output_cleanup_intent(
                    conn,
                    job_id=job_id,
                    r2_key=key,
                )
                await conn.commit()
            await asyncio.to_thread(r2.put_bytes, key, img, mime, cache=IMMUTABLE_CACHE)
            w, h = _dims(img)
            # 대기 화면 프리뷰 — asset 행은 finalize에서만 생기므로 /file 경로는 아직 404다.
            # REAL FaceMarket 컷은 최종 권한 펜스 전까지 출력 위치를 이벤트 원장에 남기지 않는다.
            step = {"blockId": b.get("id"), "status": "cut_done",
                    "width": w, "height": h}
            if not suppress_preview_urls:
                step["previewUrl"] = r2.preview_url(key)
            await _emit(app.state.pool, job_id, "step", step)
            return (
                # width/height 는 조립(M-02)이 요소 박스를 **이미지 비율대로** 잡는 근거다.
                # 없으면 page_assembler 가 기본 비율로 폴백한다(생성 실패·구 데이터 안전).
                {"blockId": b.get("id"), "imageUrl": f"/v1/assets/{asset_id}/file",
                 "width": w, "height": h},
                {"asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
                 "size": len(img), "width": w, "height": h,
                 "cleanup_intent_id": cleanup_intent_id},
                has_face,
                garment_qc,
                cut_qc,
                garment_warnings,
                chosen if s.page_output_qc_mode == "shadow" else None,
            )

    # 같은 설정 복제 컷은 생성에서 접는다 — 원본만 생성하고 결과를 복제 위치에 복사
    # (2026-08-14 오너 확정: "같은 컷 복제는 1장만 생성"). 진행 분모도 실제 생성 수.
    dup_sources = _duplicate_source_indexes(
        [item[0] for item in prepared], clothing_type
    )
    original_indexes = [i for i in range(len(prepared)) if dup_sources[i] is None]

    # 컷 1개가 끝날 때마다(성공·실패 무관) 진행 이벤트 — 대기 화면의 정직한 진행 근거.
    # 10분 잡에서 65%에 몇 분씩 멈춰 보이던 체크포인트 방식을 대체한다(editor_wait_dev_spec §2-1).
    _done_counter = {"n": 0}
    _total_cuts = max(1, len(original_indexes))
    _stagger_s = max(0, getattr(s, "detail_cut_stagger_ms", 0)) / 1000

    async def _one(idx, item):
        # 제출 간격 — i번째 컷을 i×간격 뒤에 시작해 순간 버스트를 평탄화한다(전부 병렬의 안전판).
        if idx and _stagger_s:
            await asyncio.sleep(idx * _stagger_s)
        r = await _one_impl(item)
        _done_counter["n"] += 1
        await _emit(app.state.pool, job_id, "progress",
                    {"progress": 20 + round(60 * _done_counter["n"] / _total_cuts),
                     "phase": "cut", "done": _done_counter["n"], "total": _total_cuts})
        return r

    # gather 는 입력 순서를 보존 — 원본 컷만 생성한 뒤 복제 컷 자리에 결과를 복사한다.
    cut_results, cut_assets, face_cuts = [], [], 0
    garment_qcs, cut_qcs, garment_warnings = [], [], []
    gathered = await asyncio.gather(*[
        _one(pos, prepared[i]) for pos, i in enumerate(original_indexes)
    ])
    outcome_by_index = dict(zip(original_indexes, gathered))
    outcomes = []
    for i in range(len(prepared)):
        src = dup_sources[i]
        if src is None:
            outcomes.append(outcome_by_index[i])
            continue
        base = outcome_by_index.get(src)
        block = prepared[i][0]
        if base is None:
            # 원본 생성 실패 → 복제 컷도 같은 빈 슬롯 처리(조립이 흡수).
            await _emit(app.state.pool, job_id, "step",
                        {"blockId": block.get("id"), "status": "cut_failed"})
            outcomes.append(None)
            continue
        step = {"blockId": block.get("id"), "status": "cut_done",
                "width": base[0].get("width"), "height": base[0].get("height")}
        if base[1] is not None and not suppress_preview_urls:
            step["previewUrl"] = r2.preview_url(base[1]["key"])
        await _emit(app.state.pool, job_id, "step", step)
        outcomes.append((
            # 같은 이미지 참조를 복제 블록 자리에 그대로 — 새 asset 없음(과금 없음),
            # 얼굴 컷 수·QC 는 원본에서 1회만 센다.
            {**base[0], "blockId": str(block.get("id"))},
            None,
            False,
            None,
            None,
            [],
            base[6],
        ))
    for r in outcomes:
        if r:
            cut_results.append(r[0])
            # 패스스루는 새 asset 이 없다(None). 이 목록의 길이가 **과금 단위**라 여기 넣으면
            # 이미지 모델을 부르지도 않은 컷에 크레딧이 붙는다.
            if r[1] is not None:
                cut_assets.append(r[1])
            face_cuts += 1 if r[2] else 0
            if r[3] is not None:
                garment_qcs.append({"blockId": r[0]["blockId"], **r[3]})
            if r[4] is not None:
                cut_qcs.append({"blockId": r[0]["blockId"], **r[4]})
            garment_warnings.extend(
                {"blockId": r[0]["blockId"], **warning} for warning in r[5])

    page_qc = None
    if s.page_output_qc_mode == "shadow" and prepared:
        # 원본 패스스루를 읽지 못한 경우에는 출력 자체가 빠진 것이 아니라 QC 입력만 없는 상태다.
        # 이를 completeness 실패로 오인시키지 않고 이번 page QC만 건너뛴다.
        passthrough_input_unavailable = any(
            r is not None and r[6] is page_input_unavailable for r in outcomes
        )
        if not passthrough_input_unavailable:
            try:
                product_truth_refs = []
                product_truth_index_by_hash = {}
                product_truth_indexes = []
                for item in prepared:
                    indexes = []
                    for image in item[4] if len(item) > 4 else []:
                        digest = hashlib.sha256(image.data).digest()
                        index = product_truth_index_by_hash.get(digest)
                        if (
                            index is None
                            and len(product_truth_refs) < page_output_qc.MAX_PRODUCT_REFS
                        ):
                            index = len(product_truth_refs)
                            product_truth_index_by_hash[digest] = index
                            product_truth_refs.append(image)
                        if index is not None and index not in indexes:
                            indexes.append(index)
                    product_truth_indexes.append(indexes)

                page_plan = [
                    _page_plan_item(
                        item, output_index, product_truth_indexes[output_index]
                    )
                    for output_index, item in enumerate(prepared)
                ]
                # 실패 컷은 같은 outputIndex 자리에 None으로 남긴다. page_output_qc가 계획 대비
                # 누락을 completeness 실패로 판정하며, 뒤 컷이 앞으로 당겨져 잘못 매핑되지 않는다.
                page_images = [r[6] if r is not None else None for r in outcomes]
                page_qc = await page_output_qc.judge(
                    s, page_plan, page_images, product_truth_refs=product_truth_refs,
                )
            except Exception as e:
                # shadow는 관측 전용이다. 매핑·정규화·provider의 예기치 않은 오류도 저장·정산을
                # 막아서는 안 된다.
                log.warning("AG-06 page output QC unavailable job %s: %r — shadow only", job_id, e)
                garment_warnings.append({"code": "page_output_qc_unavailable"})
    return (
        cut_results, cut_assets, face_cuts, garment_qcs, cut_qcs, page_qc,
        garment_warnings,
    )


async def _gen_copy(app, job, ai_blocks, product, analysis):
    """블록별 AG-02 카피와, 필요하면 첫 기존 호출에 묶은 상품명을 생성한다."""
    s = app.state.settings
    sem = asyncio.Semaphore(_GEN_CONCURRENCY)
    needs_name = not str(product.get("name") or "").strip() or str(product.get("name")).strip() == "새 상품"
    naming_block_id = ai_blocks[0].get("id") if needs_name and ai_blocks else None

    async def _one(b):
        """블록 1개 카피 생성. 실패면 None(카피는 게이트 아님, 블록 생략)."""
        async with sem:
            try:
                generated = await copywriter.generate(
                    s, content_role=b.get("contentRole"), section_role=b.get("sectionRole"),
                    cut_type=b.get("cutType"), product=product, analysis=analysis,
                    color_label=b.get("colorId"),
                    include_product_name=b.get("id") == naming_block_id)
            except Exception as e:  # VisionError 포함 — 카피는 게이트 아님, 실패 블록 생략
                log.warning("AG-02 copy failed for job %s block %s: %r", job["id"], b.get("id"), e)
                return None
            if isinstance(generated, dict):
                texts = generated.get("texts") or []
                product_name = generated.get("productName")
            else:
                texts, product_name = generated, None
            return (b.get("id"), texts, product_name) if texts or product_name else None

    # gather 는 순서 보존 — drafts 삽입 순서(=콘티 순서)를 유지한다.
    items, drafts, generated_name = [], {}, None
    for r in await asyncio.gather(*[_one(b) for b in ai_blocks]):
        if r:
            bid, texts, product_name = r
            generated_name = generated_name or product_name
            if texts:
                drafts[bid] = texts
            for t in texts:
                items.append({"blockId": bid, "text": t.get("text", "")})
    if not items:
        return [], generated_name
    # AG-03 검수 — revise면 수정 텍스트로 교체(첫 항목 role 유지). 실패 시 원문 유지.
    try:
        confirmed = {"materials": analysis.get("materials"),
                     "sellingPoints": analysis.get("sellingPoints"),
                     "measurementsKnown": not analysis.get("measurementsUnknown")}
        results = await copy_qc.review(s, items, confirmed)
        rev = {r["blockId"]: r for r in results if r.get("verdict") == "revise" and r.get("revisedText")}
    except Exception as e:  # VisionError 포함 — 검수 실패 시 원문 유지(게이트 아님)
        log.warning("AG-03 copy-qc failed for job %s: %r", job["id"], e)
        rev = {}
    copy_results = []
    for bid, texts in drafts.items():
        if bid in rev:  # 첫 텍스트를 검수 수정안으로 교체
            texts = [{"role": texts[0].get("role", "body"), "text": rev[bid]["revisedText"]}] + texts[1:]
        copy_results.append({"blockId": bid, "texts": texts})
    return copy_results, generated_name


_CATEGORY_NAMES = {
    "tshirt": "데일리 티셔츠", "sweatshirt": "데일리 맨투맨", "shirt": "데일리 셔츠",
    "knit": "데일리 니트", "cotton_pants": "코튼 팬츠", "training_pants": "트레이닝 팬츠",
    "jeans": "데일리 데님", "slacks": "데일리 슬랙스", "skirt": "데일리 스커트",
    "jacket": "데일리 재킷", "cardigan": "데일리 가디건", "padding": "데일리 패딩",
    "coat": "데일리 코트", "top": "데일리 상의", "bottom": "데일리 하의",
    "outer": "데일리 아우터", "dress": "데일리 원피스",
}


def _fallback_product_name(product: dict, analysis: dict) -> str:
    """카피 OFF/작명 출력 실패 시 추가 LLM 호출 없이 분석값으로 짓는다."""
    suggested = copywriter.validate_product_name(analysis.get("suggestedName"))
    if suggested:
        return suggested
    category = analysis.get("subCategory") or product.get("clothing_type") or product.get("clothingType")
    return _CATEGORY_NAMES.get(category, "데일리 웨어")


async def run_detail_page_job(app, job: dict) -> None:
    s, pool = app.state.settings, app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    payload = job.get("payload") or {}

    async def _fail(message: str, meta: dict, code: str = "generation_failed"):
        try:
            async with pool.connection() as conn:
                await repo.finalize_detail_page_failure(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, reserved=reserved, settle_key=settle_key,
                    message=message, metadata=meta, code=code)
                await conn.commit()
        except Exception:
            log.exception("detail_page finalize_failure error for job %s", job_id)

    async def _delete_output_candidate(c: dict) -> None:
        key = c.get("key") if isinstance(c, dict) else None
        if not key:
            return
        try:
            await asyncio.to_thread(app.state.r2.delete, key)
            head = getattr(app.state.r2, "head", None)
            if head is None:
                raise RuntimeError("R2 absence confirmation unavailable")
            if await asyncio.to_thread(head, key) is not None:
                raise RuntimeError("R2 object remained after delete")
        except Exception:
            log.warning("orphan R2 cleanup deferred after detail finalization rejection")
            return
        intent_id = c.get("cleanup_intent_id") if isinstance(c, dict) else None
        if intent_id:
            async with pool.connection() as conn:
                await repo.clear_ai_output_cleanup_intent(conn, intent_id)
                await conn.commit()

    try:
        # 1) 입력 로드 — 옷 레퍼런스 = (있으면) 선택 마네킹컷(핏·기장 기준, ADR-0004)
        #    + 블록 색상별 상품 슬롯 이미지 + 모든 착용컷의 매칭 의류 + 무드 레퍼런스
        async with pool.connection() as conn:
            project = await repo.get_project(conn, user_id, project_id) or {}
            storyboard = await repo.get_storyboard(conn, project_id)
            if not getattr(s, "genexample_bg_enabled", False) and any(
                isinstance(block, dict)
                and (block.get("refScope") or block.get("ref_scope")) == "bg"
                and bool(block.get("exampleId") or block.get("example_id"))
                and space_set_assets.parse_space_set_group_id(
                    block.get("spaceGroupId") or block.get("space_group_id")
                )
                is None
                for block in storyboard
            ):
                raise ValueError("genexample_bg_disabled")
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            # contentRole가 사용자 선택의 정본이다. 저장 입력을 여기서도 방어적으로
            # 정규화해 매칭 첨부·컷·카피·조립이 모두 같은 역할/레시피를 읽게 한다.
            storyboard = content_roles.canonicalize_storyboard(storyboard)
            clothing_type = (
                product.get("clothing_type")
                or product.get("clothingType")
                or "top"
            )
            space_set_bindings = space_set_assets.bind_storyboard_space_sets(
                storyboard,
                clothing_type=clothing_type,
                gender=mannequin.select_base_gender(
                    analysis, clothing_type
                ),
            )
            ai_blocks = [
                b
                for b in storyboard
                if isinstance(b, dict) and b.get("source") == "ai"
            ]
            uses_model_identity = any(
                block.get("cutType") in _WORN_CUT_TYPES for block in ai_blocks
            )
            example_repeat_indexes = _example_repeat_indexes(
                ai_blocks, clothing_type
            )
            selected_model_id = payload.get("modelId")
            try:
                uuid.UUID(str(selected_model_id))
            except (TypeError, ValueError):
                selected_is_real = False
            else:
                selected_is_real = True
            selected_is_virtual = bool(selected_model_id) and not selected_is_real
            from ..agents import identity_source
            license_row = None
            real_refs = None
            face_ref = None
            if selected_is_real and uses_model_identity:
                snapshot = payload.get("_facemarket")
                if (
                    not isinstance(snapshot, dict)
                    or str(snapshot.get("modelId") or "") != str(selected_model_id)
                    or not str(snapshot.get("licenseId") or "").strip()
                ):
                    raise facemarket._err(
                        "model_unavailable", "사용할 수 없는 모델입니다.", status=409
                    )
                license_row = await facemarket.resolve_model_license(
                    conn,
                    str(snapshot["modelId"]),
                    license_id=str(snapshot["licenseId"]),
                )
                await facemarket.verify_license(
                    app,
                    license_row,
                    model_id=str(snapshot["modelId"]),
                    brand_use_category=payload.get("brandUseCategory"),
                )
                real_refs = await identity_source.resolve_real_model_assets(
                    conn,
                    str(snapshot["modelId"]),
                    enrollment_id=str(license_row["current_enrollment_id"]),
                    evidence_version=str(license_row["match_policy_version"]),
                )
                if real_refs is None:
                    raise facemarket._err(
                        "model_assets_unavailable",
                        "현재 모델 자산을 사용할 수 없습니다.",
                        status=409,
                    )
            elif not selected_model_id and uses_model_identity:
                face_ref = await _load_license_face(app, conn, project)
            source = identity_source.select_source(
                selected_model_id=(selected_model_id if uses_model_identity else None),
                license_row=license_row,
                has_real_assets=real_refs is not None, has_license_face=face_ref is not None)
            # 관측 로그(PII 없음 — 소스 enum·플래그만). 데모·검증에서 REAL 주입 확인용.
            log.info("AG-06 identity source=%s job=%s hasReal=%s hasLicenseFace=%s",
                     source, job_id, real_refs is not None, face_ref is not None)
            if source == "REJECTED":
                raise facemarket._err(
                    "model_unavailable", "사용할 수 없는 모델입니다.", status=409
                )
            if source == "REAL" and license_row is not None:
                notice_ctx = {"model_name": license_row["model_name"], "license_id": license_row["id"]}
            elif source == "LEGACY" and face_ref is not None:
                notice_ctx = {"model_name": face_ref["model_name"], "license_id": face_ref["license_id"]}
            else:
                notice_ctx = None
            job["_suppress_detail_preview_urls"] = source == "REAL"

            mannequin_asset = None
            sel = project.get("selected_mannequin_id") or project.get("selectedMannequinId")
            if sel:
                for c in await repo.list_mannequin_cuts(conn, user_id, project_id):
                    if f"{c.get('candidate')}-{c.get('version')}" == sel and c.get("asset_id"):
                        asset_id = c.get("active_asset_id") or c["asset_id"]
                        mannequin_asset = await repo.get_asset_for_user(conn, user_id, str(asset_id))
                        break
            color_assets: dict = {}   # (colorId, detail 여부) → [asset(slot 포함)] — 블록 간 재사용
            detail_color_transfers: dict = {}  # 위 키 → 타색 Detail의 목표색 전환 정보|None
            match_assets: dict = {}   # matchingItemId → asset|None
            mood_assets: dict = {}    # refAssetId → asset|None (소유 검증 겸함)
            def _color_key(block: dict) -> str | None:
                value = block.get("colorId")
                return None if value is None else str(value)

            colors = product.get("colors") or []
            base_color = next(
                (color for color in colors if color.get("isBase")),
                colors[0] if colors else None,
            )
            base_color_id = (
                str(base_color.get("id"))
                if base_color is not None and base_color.get("id") is not None
                else None
            )

            def _uses_base_color(block: dict) -> bool:
                color_id = _color_key(block)
                return color_id is None or (
                    base_color_id is not None and color_id == base_color_id
                )

            def _is_detail(block: dict) -> bool:
                return block.get("cutType") == "product" and block.get("shot") == "detail"

            def _detail_direction(block: dict) -> str | None:
                """디테일 블록의 근거 방향 — 캐시 키·첨부 선택·패스스루가 공유한다(§5)."""
                if not _is_detail(block):
                    return None
                return "back" if block.get("direction") == "back" else "front"

            def _matching_ids(block: dict) -> list[str]:
                # normalize_spec 과 같은 최대 2개 계약을 입력 로드 단계에도 적용한다.
                # 블록 순서를 그대로 보존해야 manifest/image 위치 계약이 흔들리지 않는다.
                return [
                    str(match_id)
                    for match_id in (
                        block.get("matchIds") or block.get("match_ids") or []
                    )
                ][:2]

            # 미세 패턴 상품의 디테일 컷은 셀러 원본을 그대로 쓴다 → 생성 스킵.
            # 왜: 원단 매크로(줄 하나가 파란 실 2가닥 + 베이지 1가닥)는 전신·근접 어느 쪽이든
            # 생성 해상도로 재현이 안 된다(2026-08-01 측정: 4K 에서도 한 주기 14px → 요소당 2.3px).
            # 원본이 있는데 다시 그리면 있던 정보를 버리는 셈이고, 체크·스트라이프는 그 원단이
            # 곧 상품 정체성이라 셀러가 가장 먼저 알아본다. 무지는 생성도 잘 되므로 대상이 아니다.
            # 타색(그 색상에 Detail 원본이 없어 색 전환이 필요한 경우)은 원본이 없으니 기존 생성.
            fine_pattern = mannequin.has_fine_pattern(product, analysis)

            def _detail_passthrough(block: dict, asset_key) -> dict | None:
                if not (fine_pattern and _is_detail(block)):
                    return None
                if detail_color_transfers.get(asset_key):   # 타색 전환 = 그 색 원본이 없다
                    return None
                _slot = "BackDetail" if _detail_direction(block) == "back" else "Detail"
                for asset in color_assets.get(asset_key, []):
                    if asset.get("slot") == _slot:
                        return asset
                return None

            for b in ai_blocks:
                ckey = _color_key(b)
                # 디테일은 방향까지 키에 — 앞·뒤 디테일 블록이 같은 색이어도 첨부가 다르다(§5)
                asset_key = (ckey, _is_detail(b), _detail_direction(b))
                if asset_key not in color_assets:
                    rows = []
                    if asset_key[1]:
                        image_refs, transfer = cut_generator.detail_reference_images(
                            product, ckey, direction=asset_key[2])
                    else:
                        image_refs, transfer = cut_generator.color_images(product, ckey), None
                    for slot, aid in image_refs:
                        a = await repo.get_asset_for_user(conn, user_id, aid)
                        if a:
                            a["slot"] = slot
                            rows.append(a)
                    color_assets[asset_key] = rows
                    detail_color_transfers[asset_key] = transfer
                if b.get("cutType") in _WORN_CUT_TYPES:
                    for matching_id in _matching_ids(b):
                        if matching_id in match_assets:
                            continue
                        m_aid = await repo.get_matching_item_asset(
                            conn, matching_id, user_id, project_id
                        )
                        match_assets[matching_id] = (
                            await repo.get_asset_for_user(conn, user_id, m_aid)
                            if m_aid
                            else None
                        )
                for rid in (b.get("refAssetIds") or [])[:3]:
                    if str(rid) not in mood_assets:
                        mood_assets[str(rid)] = await repo.get_asset_for_user(conn, user_id, str(rid))

        # R2 바이트는 r2_key 캐시로 1회만 — 같은 색상 이미지가 블록마다 재다운로드되지 않게
        _img_cache: dict = {}

        async def _r2_img(k: str, mime: str, bucket: str = "public") -> InlineImage:
            # 실존 모델 그리드는 bucket='face' → 비공개 r2_face 에서 로드(공개 버킷 하드코딩 금지).
            cache_key = (bucket, k)
            if cache_key not in _img_cache:
                client = app.state.r2_face if bucket == "face" else app.state.r2
                if client is None:
                    raise RuntimeError("bucket client unavailable")
                _img_cache[cache_key] = InlineImage(
                    mime, await asyncio.to_thread(client.get_bytes, k))
            return _img_cache[cache_key]

        async def _img(a: dict) -> InlineImage:
            return await _r2_img(a["r2_key"], a["mime_type"])

        # 가상모델 얼굴+전신은 원자적인 한 쌍이다. 하나라도 manifest/R2 로드에 실패하면
        # 둘 다 빼고 기존 옷 레퍼런스만으로 계속 생성한다(상세페이지 부분 실패 정책과 같은 fail-open).
        _model_cache: dict[str, list[InlineImage] | None] = {}

        async def _model_images(spec: dict | None) -> list[InlineImage]:
            if not spec or spec.get("cutType") not in ("styling", "horizon", "mirror"):
                return []
            model_id = spec.get("modelId")
            if not model_id:
                return []
            if model_id not in _model_cache:
                try:
                    refs = cut_generator.resolve_virtual_model_assets(
                        spec, require_full_body=True
                    )
                    if refs is not None:
                        _model_cache[model_id] = [
                            await _r2_img(ref["key"], ref["mime"]) for ref in refs
                        ]
                    else:
                        _model_cache[model_id] = None
                except Exception as e:
                    log.warning(
                        "AG-06 virtual model assets unavailable for job %s model %s; "
                        "continuing without model references: %r", job_id, model_id, e)
                    _model_cache[model_id] = None
            return _model_cache[model_id] or []

        # Confirmed GPT uses the exact historical face-direction + full-body-direction
        # sheets, not the generic face_front/body_front pair.  Both bytes are verified
        # against the server manifest before either becomes usable.
        _confirmed_model_cache: dict[str, tuple[InlineImage, InlineImage]] = {}

        async def _confirmed_model_images(
            spec: dict,
        ) -> tuple[InlineImage, InlineImage]:
            model_id = str(spec.get("modelId") or "")
            if not model_id:
                raise ValueError("confirmed_gpt_model_id_required")
            if model_id not in _confirmed_model_cache:
                refs = cut_generator.resolve_confirmed_gpt_direction_sheets(spec)
                loaded: list[InlineImage] = []
                for ref in refs:
                    image = await _r2_img(
                        ref["key"], ref["mime"], ref.get("bucket", "public")
                    )
                    if (
                        len(image.data) != ref.get("byteLength")
                        or hashlib.sha256(image.data).hexdigest() != ref.get("sha256")
                    ):
                        raise ValueError("confirmed_gpt_direction_sheet_hash_mismatch")
                    loaded.append(image)
                if len(loaded) != 2:
                    raise ValueError("confirmed_gpt_direction_sheet_pair_required")
                _confirmed_model_cache[model_id] = (loaded[0], loaded[1])
            return _confirmed_model_cache[model_id]

        real_model_images: list[InlineImage] = []
        if source == "REAL":
            try:
                r2_face = getattr(app.state, "r2_face", None)
                if r2_face is None:
                    raise RuntimeError("face storage unavailable")
                real_model_images = [
                    InlineImage(
                        ref["mime"],
                        await asyncio.to_thread(r2_face.get_bytes, ref["key"]),
                    )
                    for ref in real_refs
                ]
            except Exception as exc:
                raise facemarket._err(
                    "model_assets_unavailable",
                    "현재 모델 자산을 사용할 수 없습니다.",
                    status=409,
                ) from exc

        # (runtime block, images, manifest, has_face, product_images,
        #  space_set_plate, strict_space_scene_qc, passthrough, confirmed_packet)
        # — images 순서는 manifest 계약과 동일. 대표 plate는 같은 세트에서 1회만 로드해 공유한다.
        prepared = []
        _example_cache: dict[str, InlineImage | None] = {}
        _space_plate_cache: dict[str, InlineImage] = {}
        _space_example_cache: dict[str, InlineImage] = {}
        example_warnings: list[dict] = []
        _virtual_ids: set[str] = set()
        fallback_model_id = s.detailpage_fallback_model_id
        if fallback_model_id and source == "VIRTUAL":
            try:
                _virtual_ids = set(cut_generator.load_virtual_model_registry())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                # 파일 부재/권한(OSError)·JSON 파손(JSONDecodeError)·깨진 인코딩(UnicodeDecodeError)
                # 모두 fail-open — 폴백만 건너뛰고 잡은 계속(폴백은 선택적 보정이지 잡 필수 입력 아님).
                log.warning(
                    "AG-06 virtual model manifest unavailable; skipping fallback substitution "
                    "for job %s: %r", job_id, e)
        _fallback_warned = False
        for b, example_repeat_index in zip(ai_blocks, example_repeat_indexes):
            cut_spec = dict(b)
            space_binding = space_set_bindings.get(id(b))
            # 저장/클라이언트가 런타임 전용 지시를 주입하지 못하게 매번 실제 선택 결과로 재구성한다.
            cut_spec.pop("_detailColorTransfer", None)
            cut_spec.pop("_spaceSetContinuity", None)
            cut_spec.pop("_referenceDirectionCompatible", None)
            cut_spec.pop("_exampleRepeatIndex", None)
            if example_repeat_index is not None:
                cut_spec["_exampleRepeatIndex"] = example_repeat_index
            if space_binding is not None:
                # 공간 세트의 pose/범위/변주 강도는 저장 payload가 아니라 발행 레지스트리가
                # 정본이다. 오래된 값이나 우회 클라이언트가 전용 pose·plate 계약을 바꾸지 못한다.
                cut_spec["refScope"] = "pose"
                cut_spec["pose"] = "auto"
                cut_spec["spaceVariation"] = space_binding["set"]["spaceVariation"]
            # 저장 콘티에 우연히 남은 비계약 필드가 프로젝트 선택 모델을 덮지 못하게 제거 후 주입한다.
            cut_spec.pop("modelId", None)
            cut_spec.pop("model_id", None)
            # 인물 일관성(AG-06): VIRTUAL 소스에서 선택 id 가 가상 registry 밖(facemarket off 상태의
            # 실존 UUID)이면 resolve_virtual_model_assets 가 None → 참조 0장 → 컷마다 인물 랜덤.
            # 결정적 가상모델로 폴백해 전 컷 동일 인물 보장. REAL/LEGACY 는 얼굴을 별도 경로로
            # 붙이므로 건드리지 않는다(인물 이중 첨부 방지).
            if source == "VIRTUAL":
                eff_model_id, _subbed = cut_generator.resolve_effective_model_id(
                    selected_model_id, fallback_model_id=fallback_model_id,
                    virtual_ids=_virtual_ids)
                if _subbed and not _fallback_warned:
                    log.warning(
                        "AG-06 selected model %s unresolvable as virtual (facemarket=%s) → fallback %s "
                        "for identity consistency (job %s)",
                        selected_model_id, s.facemarket_enabled, eff_model_id, job_id)
                    _fallback_warned = True
            else:
                eff_model_id = selected_model_id
            if eff_model_id:
                cut_spec["modelId"] = eff_model_id
            try:
                normalized = cut_generator.normalize_spec(cut_spec, clothing_type=clothing_type)
            except ValueError:
                normalized = None  # generate()가 블록 단위 실패로 처리하는 기존 경로 유지
            try:
                confirmed_requested = bool(
                    normalized is not None
                    and confirmed_gpt_runtime.resolve_profile_request(
                        cut_generator.apply_reference_compatibility(normalized),
                        identity_source=source,
                        selected_model_id=selected_model_id,
                        effective_model_id=eff_model_id,
                        uses_base_color=_uses_base_color(b),
                    )
                )
            except confirmed_gpt_runtime.ConfirmedGptRuntimeError as e:
                log.warning(
                    "AG-06 confirmed GPT profile unavailable — cut fail-closed "
                    "job %s block %s: %r",
                    job_id,
                    b.get("id"),
                    e,
                )
                prepared.append((cut_spec, [], "", False, [], None, False))
                continue
            is_product_cut = normalized is not None and normalized["cutType"] == "product"
            is_worn_cut = normalized is not None and normalized["cutType"] in _WORN_CUT_TYPES
            # PRODUCT 컷은 사람 없는 상품 단독 이미지다. 프로젝트에 선택 마네킹이 있어도 이 컷의
            # 옷 근거로 승격하지 않는다 — 상품 사진이 없으면 사람 이미지만으로 생성하지 않고 스킵한다.
            # 선택 마네킹컷은 기준색 상품 사진으로 만든 결과라 색상 메타가 따로 없다.
            # 사용자가 콘티에서 다른 색을 골랐을 때까지 붙이면 기준색 마네킹 픽셀과 목표색
            # 상품 픽셀이 충돌한다. 사용자 색상을 확실히 우선하도록 기준색 착용컷에만 쓴다.
            cut_mannequin_asset = (
                mannequin_asset
                if not is_product_cut and _uses_base_color(b)
                else None
            )
            asset_key = (_color_key(b), _is_detail(b), _detail_direction(b))
            prods = color_assets.get(asset_key, [])
            if detail_color_transfers.get(asset_key):
                cut_spec["_detailColorTransfer"] = detail_color_transfers[asset_key]
            # 옷 근거(상품 사진 또는 마네킹컷)가 없으면 생성 불가 — 무드/매칭만으로 진행하면
            # 모델이 레퍼런스 속 옷을 지어내거나 베낀다(ADR-0004 정확성 최우선). 스킵 표식.
            # 얼굴은 이 가드 **뒤에서만** 붙는다 — 여기 얼굴을 넣으면 images 가 비지 않아
            # _gen_cuts 의 `if not images` 스킵이 무력화되고 옷 근거 0으로 생성이 돌아간다.
            if cut_mannequin_asset is None and not prods:
                prepared.append((cut_spec, [], "", False, [], None, False))
                continue
            mids = normalized.get("matchIds", []) if is_worn_cut else []
            matching_assets = [match_assets.get(matching_id) for matching_id in mids]
            if mids and any(asset is None for asset in matching_assets):
                # 사용자가 확정한 매칭 의류는 전부 한 벌의 진실 근거다. 일부만 붙여 생성하면
                # 선택하지 않은 기본 의류로 나머지를 채우므로, 이 컷만 빈 슬롯으로 닫는다.
                log.warning(
                    "AG-06 matching asset unavailable — cut fail-closed job %s block %s",
                    job_id,
                    b.get("id"),
                )
                prepared.append((cut_spec, [], "", False, [], None, False))
                continue
            try:
                matching_images = [await _img(asset) for asset in matching_assets]
            except Exception as e:
                # 메타데이터는 있어도 실제 R2 객체가 없으면 동일하게 부분 첨부하지 않는다.
                log.warning(
                    "AG-06 matching image unavailable — cut fail-closed job %s block %s: %r",
                    job_id,
                    b.get("id"),
                    e,
                )
                prepared.append((cut_spec, [], "", False, [], None, False))
                continue
            moods = [mood_assets[str(r)] for r in (b.get("refAssetIds") or [])[:3] if mood_assets.get(str(r))]
            # 얼굴이 실제로 담기는 컷에만 첨부 — product(사람 금지)·거울샷 기본(폰이 가림)·
            # 뒷모습·머리가 프레임 밖인 샷은 제외(cut_generator.wants_face 가 단일 규칙).
            wants = cut_generator.wants_face(cut_spec, clothing_type)
            # MODEL FULL BODY는 진짜 전신 자산을 붙인 VIRTUAL 경로에만 선언한다.
            # REAL의 두 번째 이미지는 얼굴 시트이므로 체형 근거로 위장하지 않는다.
            model_has_full_body = False
            # 컷당 아이덴티티 소스 1개(codex [P1]) — 셋 중 하나만 컷에 들어간다:
            #  REAL    실존 모델 그리드(비공개 face 버킷) — 단일 라이선스 얼굴 미첨부
            #  LEGACY  라이선스 단일 얼굴(비공개) — 어떤 그리드도 미첨부
            #  VIRTUAL 가상모델 얼굴·시트·체형 묶음(공개 버킷) — 라이선스 불요
            # face_slot=단일 얼굴 슬롯(LEGACY만). has_identity=검증 얼굴이 실제 담기는 컷(REAL·LEGACY)
            # → face_cuts·검증 배지 근거. 세 소스가 한 컷에 겹치지 않아 인물 혼합·이중주입이 없다.
            if not is_worn_cut:
                # 상품컷에는 REAL/VIRTUAL 그리드와 LEGACY 단일 얼굴을 모두 구조적으로 차단한다.
                model_images = []
                has_identity = False
                face_slot = False
            elif source == "REAL":
                # 실존 모델 그리드는 얼굴 노출과 무관하게 모든 착용컷에 identity 앵커로 붙인다(A4).
                # wants(얼굴 노출)로만 게이트하면 mirror/back 이 참조 0장 → 그 컷만 인물 랜덤이 된다
                # (REAL 은 VIRTUAL 과 달리 mB 폴백도 없음). 배지(has_identity)만 wants 로 준다.
                attach_grid, _badge = cut_generator.real_identity_plan(
                    normalized.get("cutType") if normalized else None, wants_face=wants)
                model_images = real_model_images if attach_grid else []
                has_identity = _badge and len(model_images) == 2
                face_slot = False
            elif source == "LEGACY":
                model_images = []
                has_identity = wants
                face_slot = wants
            elif source == "VIRTUAL":
                try:
                    model_images = list(
                        await _confirmed_model_images(normalized)
                    ) if confirmed_requested else await _model_images(normalized)
                except Exception as e:
                    log.warning(
                        "AG-06 confirmed GPT direction sheets unavailable — cut "
                        "fail-closed job %s block %s: %r",
                        job_id,
                        b.get("id"),
                        e,
                    )
                    prepared.append((cut_spec, [], "", False, [], None, False))
                    continue
                model_has_full_body = len(model_images) == 2
                has_identity = False
                face_slot = False
            else:  # NONE — 얼굴 없이 생성
                model_images = []
                has_identity = False
                face_slot = False
            imgs = []
            product_images = []
            cut_mannequin_image = None
            if cut_mannequin_asset is not None:
                cut_mannequin_image = await _img(cut_mannequin_asset)
                imgs.append(cut_mannequin_image)
            imgs.extend(model_images)
            for a in prods:
                product_image = await _img(a)
                imgs.append(product_image)
                product_images.append(product_image)
            imgs.extend(matching_images)
            if face_slot:
                # 비공개 r2_face 바이트(LEGACY 단일 얼굴) — _img()(공개 버킷 하드코딩) 를 태우지 않는다.
                imgs.append(face_ref["image"])
            # 무드는 장면 자산보다 앞에 와야 하지만, all/bg/대표 plate가 장면·조명을 소유하면
            # 아예 첨부하지 않는다. 예시를 해석한 뒤 이 위치에 필요한 경우에만 삽입한다.
            scene_suffix_start = len(imgs)
            example_scope = None
            service_example_image = None
            space_set_plate = None
            has_space_set_plate = False
            example_id = b.get("exampleId") or b.get("example_id")
            if space_binding is not None:
                set_entry = space_binding["set"]
                pose_reference = space_binding["poseReference"]
                set_id = set_entry["setId"]
                plate_asset = set_entry["representativePlate"]
                if plate_asset is not None and set_id not in _space_plate_cache:
                    _space_plate_cache[set_id] = (
                        await space_set_assets.load_space_set_image(
                            s, plate_asset, role="대표 배경"
                        )
                    )
                pose_cache_key = (
                    f"{pose_reference['source']}:{pose_reference['exampleId']}"
                )
                if pose_cache_key not in _space_example_cache:
                    if pose_reference["source"] == "space-set":
                        pose_image = await space_set_assets.load_space_set_image(
                            s, pose_reference["asset"], role="포즈"
                        )
                    else:
                        pose_image = await cut_generator.load_example_image(
                            s,
                            pose_reference["exampleId"],
                            scope="pose",
                            clothing_type=clothing_type,
                        )
                        if pose_image is None:
                            raise space_set_assets.SpaceSetBindingError(
                                "space_set_pose_unavailable",
                                "공간 세트의 포즈 예시를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                            )
                    _space_example_cache[pose_cache_key] = pose_image
                space_set_plate = _space_plate_cache.get(set_id)
                if space_set_plate is not None:
                    imgs.append(space_set_plate)
                imgs.append(_space_example_cache[pose_cache_key])
                example_scope = "pose"
                has_space_set_plate = space_set_plate is not None
                cut_spec["_spaceSetContinuity"] = has_space_set_plate
            elif example_id:
                # 직접 포즈가 pose-scope 예시보다 우선한다는 기존 계약: 이미지 자체도 첨부하지 않아
                # 픽셀 조건이 텍스트 가드를 우회해 포즈를 되살리지 못하게 한다.
                pose_overrides_example = normalized is not None \
                    and normalized["pose"] != "auto" and normalized["refScope"] == "pose"
                if normalized is not None and not pose_overrides_example:
                    scope = normalized["refScope"]
                    if example_id.startswith("ss_") and not (
                        b.get("spaceGroupId") or b.get("space_group_id")
                    ):
                        example_reference = (
                            space_set_assets.resolve_published_example_reference(
                                normalized,
                                clothing_type=clothing_type,
                                gender=mannequin.select_base_gender(
                                    analysis, clothing_type
                                ),
                                scope=scope,
                            )
                        )
                        cut_spec["_referenceDirectionCompatible"] = example_reference.get(
                            "directionCompatible", True
                        )
                        cache_key = (
                            f"space-set:{example_reference['exampleId']}:{scope}"
                        )
                        if cache_key not in _space_example_cache:
                            _space_example_cache[cache_key] = (
                                await space_set_assets.load_space_set_image(
                                    s,
                                    example_reference["asset"],
                                    role="전체 예시" if scope == "all" else "포즈",
                                )
                            )
                        imgs.append(_space_example_cache[cache_key])
                        example_scope = scope
                        service_example_image = _space_example_cache[cache_key]
                    else:
                        status = cut_generator.example_asset_status(
                            example_id, clothing_type, scope)
                        if status in ("not_applicable", "variant_unpublished"):
                            example_warnings.append({
                                "code": "example_not_applicable"
                                if status == "not_applicable" else "example_variant_unpublished",
                                "blockId": b.get("id"),
                                "exampleId": example_id,
                                "clothingType": clothing_type,
                                "refScope": scope,
                            })
                            # 이미지 미첨부만으로는 all 범위의 레거시 EXNUANCE 해시가 남는다.
                            # 부적합/미발행 예시가 텍스트로도 영향을 주지 않게 런타임 사본에서 해제한다.
                            cut_spec["exampleId"] = None
                        elif scope == "pose" and not cut_generator.pose_direction_compatible(
                            example_id, normalized
                        ):
                            # v2 preflight: 호환되지 않는 포즈는 이미지 모델 호출 전에 이 컷만
                            # 빈 슬롯으로 닫는다. 배치의 다른 컷은 계속 생성한다.
                            example_warnings.append({
                                "code": "pose_direction_incompatible",
                                "blockId": b.get("id"),
                                "exampleId": example_id,
                                "direction": normalized.get("direction"),
                            })
                            prepared.append((cut_spec, [], "", False, [], None, False))
                            continue
                        else:
                            # 캐시 키에 scope 포함 — pose는 누끼 variant, all은 원본이라 자산이 다르다
                            cache_key = f"{example_id}:{scope}"
                            if cache_key not in _example_cache:
                                _example_cache[cache_key] = await cut_generator.load_example_image(
                                    s, example_id, scope=scope, clothing_type=clothing_type)
                            example_img = _example_cache[cache_key]
                            if example_img is None and scope in ("all", "pose", "bg"):
                                # 사용자가 고른 예시 자산 로드 실패 — 무음 강등 대신 이 컷만 빈 슬롯
                                # (ADR-0009 §2). all도 계속 생성하면 예시를 참고한 것처럼 보이면서
                                # 무드 사진으로 장면이 바뀔 수 있으므로 pose/bg와 똑같이 닫는다.
                                log.warning("AG-06 %s example unavailable — cut fail-closed job %s block %s",
                                            scope, job_id, b.get("id"))
                                prepared.append((cut_spec, [], "", False, [], None, False))
                                continue
                            if example_img is not None:
                                # bg 플레이트는 첫 첨부(에디터 경로와 동일) — 마지막 첨부는 컷 섹션의
                                # 배경 나열에 밀려 무시된다(2026-07-20 파일럿 실측).
                                if scope == "bg":
                                    imgs.insert(0, example_img)
                                else:
                                    imgs.append(example_img)
                                example_scope = scope
                                service_example_image = example_img
            # 정식 공간 세트는 대표 plate가 없는 회전/호리존 세트도 자체 장면 계약을 가진다.
            # 저장 payload에 남은 수동 무드가 세트 컷 사이로 새지 않게 binding 자체를 장면
            # 소유권으로 본다. 대표 plate 유무는 배경 이미지 첨부 여부일 뿐 권한이 아니다.
            authoritative_scene = (
                example_scope in ("all", "bg") or space_binding is not None
            )
            attached_mood_count = 0
            if not authoritative_scene:
                mood_images = [await _img(a) for a in moods]
                imgs[scene_suffix_start:scene_suffix_start] = mood_images
                attached_mood_count = len(mood_images)
            confirmed_packet = None
            if confirmed_requested:
                try:
                    confirmed_packet = confirmed_gpt_runtime.build_packet(
                        cut_generator.apply_reference_compatibility(normalized),
                        clothing_type=clothing_type,
                        identity_source=source,
                        selected_model_id=selected_model_id,
                        effective_model_id=eff_model_id,
                        uses_base_color=_uses_base_color(b),
                        mannequin_image=cut_mannequin_image,
                        face_direction_sheet=model_images[0],
                        full_body_direction_sheet=model_images[1],
                        seller_images=tuple(
                            (asset["slot"], image)
                            for asset, image in zip(prods, product_images, strict=True)
                        ),
                        matching_images=tuple(matching_images),
                        example_image=service_example_image,
                        evidence_contract=analysis.get(
                            "confirmedGptProductEvidence"
                        ),
                    )
                except Exception as e:
                    log.warning(
                        "AG-06 confirmed GPT packet invalid — cut fail-closed "
                        "job %s block %s: %r",
                        job_id,
                        b.get("id"),
                        e,
                    )
                    prepared.append((cut_spec, [], "", False, [], None, False))
                    continue
                imgs = list(confirmed_packet.images)
                manifest = confirmed_packet.manifest
            else:
                manifest = cut_generator.build_manifest(
                    prods, has_mannequin=cut_mannequin_asset is not None,
                    has_match=bool(matching_images), matching_count=len(matching_images),
                    matching_custom=[matching_id.startswith("custom_") for matching_id in mids],
                    mood_count=attached_mood_count,
                    has_model_face=len(model_images) == 2,
                    has_model_sheet=len(model_images) == 2 and not model_has_full_body,
                    has_model_full_body=model_has_full_body,
                    has_face=face_slot,
                    example_scope=example_scope,
                    example_is_product=normalized is not None and normalized["cutType"] == "product",
                    has_space_set_plate=has_space_set_plate,
                    reference_direction_compatible=cut_generator.apply_reference_compatibility(
                        cut_generator.normalize_spec(cut_spec, clothing_type=clothing_type)
                    )["_referenceDirectionCompatible"])
            # 4번째 = has_identity: 검증 얼굴(REAL 그리드·LEGACY 단일)이 실제 담긴 컷 → face_cuts 계수·
            # generate has_face·검증 배지 근거. VIRTUAL 그리드는 검증 얼굴이 아니므로 False.
            prepared.append(
                (
                    cut_spec,
                    imgs,
                    manifest,
                    has_identity,
                    product_images,
                    space_set_plate,
                    space_binding is not None and space_set_plate is not None,
                    _detail_passthrough(b, asset_key),
                    confirmed_packet,
                )
            )

        copywriting = bool(project.get("copywriting"))
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded",
                                               "aiCuts": len(ai_blocks)})

        # 2) 카피(선택) + 검수 — **컷보다 먼저**. 카피는 컷 이미지를 입력으로 쓰지 않아
        # (copywriter.generate: product·analysis·role만) 순서를 당길 수 있고, 셀러는 컷을
        # 기다리는 몇 분 동안 문구를 다듬을 수 있다(editor_wait_dev_spec §2-1).
        copy_results = []
        generated_name = None
        needs_name = not str(product.get("name") or "").strip() or str(product.get("name")).strip() == "새 상품"
        if copywriting:
            await _emit(pool, job_id, "progress", {"progress": 18, "phase": "copy",
                                                   "blocks": len(ai_blocks)})
            copy_results, generated_name = await _gen_copy(app, job, ai_blocks, product, analysis)
            for cr in copy_results:  # 검수(AG-03) 통과본만 내보낸다 — 선emit 후revise 금지
                await _emit(pool, job_id, "step",
                             {"blockId": cr.get("blockId"), "status": "copy_ready",
                             "texts": cr.get("texts")})
        if needs_name:
            generated_name = generated_name or _fallback_product_name(product, analysis)
            product["name"] = generated_name

            # 특징 포인트 설명 문구 — 에디터의 정보 블록이 프리필로 읽는다(analysis.featureCopy).
            # 컷 카피와 달리 블록 단위가 아니라 강조특징 단위라 1콜이면 끝난다.
            # 포인트 출처는 프론트(Editor.jsx buildInfoCtx)와 같은 우선순위 — 셀러가 칩을
            # 직접 채웠으면 그걸, 비워뒀으면 AI 제안을 쓴다. 다르면 제목(칩)과 설명이 어긋난다.
            points = analysis.get("sellingPoints") or analysis.get("aiSuggestedPoints") or []
            try:
                items = await feature_copy.generate(s, points, product, analysis)
            except Exception as e:  # 카피는 게이트 아님 — 상세페이지 생성을 막지 않는다
                log.warning("feature copy failed for job %s: %r", job_id, e)
                items = []
            if items:
                try:
                    async with pool.connection() as conn:
                        # 잡이 도는 동안 셀러가 분석을 고쳤을 수 있다. 잡 시작 때 읽은 사본으로
                        # 덮으면 그 사이 편집이 날아가므로, 여기서 다시 읽어 featureCopy 만 얹는다.
                        fresh = await repo.get_analysis(conn, project_id) or {}
                        await repo.save_analysis(conn, project_id, {**fresh, "featureCopy": items})
                        await conn.commit()
                except Exception as e:  # 카피는 게이트 아님 — 기록 실패가 생성을 죽이지 않는다
                    log.warning("feature copy persist failed for job %s: %r", job_id, e)

        # 3) 컷 생성 (부분 성공) — 컷 단위 progress(20→80)는 _gen_cuts 안에서 emit
        (
            cut_results,
            cut_assets,
            face_cuts,
            garment_qcs,
            cut_qcs,
            page_qc,
            garment_warnings,
        ) = await _gen_cuts(app, job, prepared, product, analysis)
        example_warnings.extend(garment_warnings)
        # 판정 기준은 **컷이 하나라도 나왔는가**(cut_results)다. cut_assets 로 보면 전 블록이
        # 원본 패스스루인 상세페이지가 "전멸"로 오인된다 — 그 경우 컷은 멀쩡히 있다.
        if ai_blocks and not cut_results:
            # AI 컷이 하나도 없는데 done으로 종결하면 빈 상세페이지가 완성본처럼 보이고
            # 완료 화면 가드와도 충돌한다. 예약 크레딧을 환불하는 실패 종결로 보낸다.
            await _fail(
                "이미지를 만들지 못했어요. 상품 사진과 컷 설정을 확인한 뒤 다시 시도해 주세요.",
                {"error": "all_cuts_failed", "requestedCuts": len(ai_blocks)},
                code="all_cuts_failed",
            )
            return

        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "assemble",
                                               "generated": len(cut_assets)})

        # 4) 조립(M-02) — 실패 컷은 빈 슬롯으로.
        # AI 고지 분기는 **얼굴이 실제로 들어간 컷이 성공했을 때만**(face_cuts > 0) —
        # 라이선스만 잠기고 주입이 실패(전 컷 실패·얼굴 로드 강등)했는데 '실제 모델' 이라
        # 쓰면 허위 고지가 된다. 라이선스 없는 경로는 face_ref=None → 항상 기본 문구.
        # 범위 주장 근거: totalCuts = **성공한 컷 수**(실패 컷은 빈 슬롯이라 인물이 없다).
        # face_cuts < totalCuts 면 얼굴 미첨부 컷(거울샷·뒷모습·하반신·상품컷)이 섞였다는 뜻이라
        # 페이지 전체를 '가상인물 아님' 으로 주장할 수 없다 → assembler 가 '일부 컷' 문구로 내린다.
        license_notice = None
        if notice_ctx is not None and face_cuts > 0:
            license_notice = {"modelName": notice_ctx["model_name"],
                              "licenseId": notice_ctx["license_id"],
                              "faceCuts": face_cuts,
                              "totalCuts": len(cut_assets)}
        assemble_kwargs = {"license_notice": license_notice} if license_notice is not None else {}
        assembly_product = {
            **product,
            "_matchClothing": (
                analysis.get("matchClothing")
                or analysis.get("matchCandidates")
                or []
            ),
        }
        editor_blocks = page_assembler.assemble(
            storyboard, cut_results, copy_results, assembly_product, copywriting, **assemble_kwargs)

        # 5) 성공 종결 (원자·lease 펜스). charge = 성공 컷 수 × **예약 시점 단가 스냅샷**
        # (job.metadata.perCutCost — routes.py가 예약과 같은 tx에서 기록). 실행 시점 설정을 쓰면
        # 배포 사이 단가 변경이 낀 잡이 견적과 다르게 정산되고, 예약액÷현재 블록 수 역산은 예약 후
        # 콘티 재저장으로 블록이 늘면 단가가 0으로 떨어져 무과금 생성이 된다 — 둘 다 금지.
        # 스냅샷 없는 legacy 잡만 실행 시점 단가로 폴백. min 캡 = 예약 초과 차감 최종 가드.
        per_cut = (job.get("metadata") or {}).get("perCutCost")
        if per_cut is None:  # legacy 잡(스냅샷 도입 전 큐 잔여분)
            per_cut = s.credit_cost_storyboard_per_cut
        charge = min(len(cut_assets) * per_cut, reserved)
        success_metadata = {
            "creditCostVersion": s.credit_cost_version,
            "generatedCuts": len(cut_assets),
        }
        if garment_qcs:
            success_metadata["garmentQc"] = garment_qcs
        if cut_qcs:
            success_metadata["cutQc"] = cut_qcs
        if page_qc is not None:
            success_metadata["pageQc"] = page_qc
        if example_warnings:
            success_metadata["warnings"] = example_warnings
        async with pool.connection() as conn:
            if source == "REAL" and license_row is not None:
                snapshot = payload.get("_facemarket") if isinstance(payload, dict) else None
                if not isinstance(snapshot, dict):
                    raise facemarket._err(
                        "model_unavailable", "사용할 수 없는 모델입니다.", status=409
                    )
                await repo.lock_facemarket_writer_boundary(conn)
                license_row = await facemarket.resolve_model_license(
                    conn,
                    str(snapshot.get("modelId") or ""),
                    license_id=str(snapshot.get("licenseId") or ""),
                    for_update=True,
                )
                facemarket.verify_license_local(
                    app,
                    license_row,
                    model_id=str(snapshot.get("modelId") or ""),
                    brand_use_category=payload.get("brandUseCategory"),
                )
            out = await repo.finalize_detail_page_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id, project_id=project_id,
                editor_blocks=editor_blocks, cut_assets=cut_assets, reserved=reserved, charge=charge,
                metadata=success_metadata, product_name=generated_name)
            await conn.commit()
        if out is None:  # lease 상실 → 방금 올린 R2 객체 best-effort 정리
            for c in cut_assets:
                await _delete_output_candidate(c)
        else:
            # FaceMarket 온체인 정산 훅(선택과제2). 이 잡이 얼굴 라이선스를 소비했으면
            # 성공 종결 지점에서 70/20/10 을 온체인 기록. 프로젝트에 과거 잠금이 남아도
            # 이번 잡의 소스가 VIRTUAL/NONE 이면 라이선스를 소비하지 않았으므로 기록하지 않는다.
            # best-effort: 정산 실패가 이미 완료된 상세페이지 생성을 되돌리지 않는다.
            if (
                source == "REAL"
                and license_row is not None
                and license_row.get("unit_price") is not None
                and getattr(app.state, "fm_chain", None) is not None
            ):
                try:
                    await facemarket.record_license_settlement(
                        app,
                        payment_key=f"job:{job_id}",
                        license_id=str(license_row["id"]),
                        model_id=str(license_row["model_id"]),
                        total=int(license_row["unit_price"]),
                        job_id=str(job_id),
                    )
                except Exception:
                    log.warning("facemarket settlement hook failed for job %s", job_id)
    except Exception as e:
        for c in locals().get("cut_assets") or ():
            await _delete_output_candidate(c)
        error = str(e)[:300]
        fm_detail = e.detail if isinstance(e, facemarket.HTTPException) else None
        fm_code = fm_detail.get("code") if isinstance(fm_detail, dict) else None
        fm_message = fm_detail.get("message") if isinstance(fm_detail, dict) else None
        is_space_set_error = isinstance(e, space_set_assets.SpaceSetBindingError)
        await _fail(
            (
                fm_message
                if fm_message
                else e.message
                if is_space_set_error
                else "배경만 생성예시는 현재 사용할 수 없어요. 콘티에서 해당 예시를 제거해 주세요."
                if error == "genexample_bg_disabled"
                else "상세페이지 생성에 실패했어요. 다시 시도해 주세요."
            ),
            {"error": error},
            code=(
                fm_code
                if fm_code
                else e.code
                if is_space_set_error
                else "genexample_bg_disabled"
                if error == "genexample_bg_disabled"
                else "generation_failed"
            ),
        )
