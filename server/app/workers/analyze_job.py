"""AG-01 상품 분석 워커. dispatcher가 claim한 kind='analyze' job 1건을 실행한다.

흐름: 기준 색상 이미지(bytes) 로드 → product_analyst.analyze(프롬프트→vision_llm 폴백→검증→분배)
→ finalize(analyses 저장 + clothingType→products + job done/result 봉투, 원자·lease 펜스).
무과금(ai_agent_modules §3) — reserve/confirm/refund 경로 없음. 비전 입력은 bytes(URL 아님).
"""

import asyncio
import logging
from io import BytesIO

from PIL import Image, ImageOps

from .. import repo
from ..services import outfit, outfit_jobs
from ..agents import (
    feature_extractor,
    input_consistency,
    product_analyst,
    product_evidence_contract,
    detail_recommendations,
)
from ..agents.gemini_image import InlineImage
from ..agents.vision_llm import VisionError
from ._common import emit_job_event as _emit  # 공용 헬퍼(mannequin_job과 공유). 테스트가 이 이름을 monkeypatch

log = logging.getLogger("wearless.analyze_job")

# 분석 비전 입력 축소 (2026-07-07 속도 개선) — 판정(종류·핏·색·특징)엔 원본 해상도가
# 불필요한데 셀러 원본은 수 MB라 base64 전송·인코딩이 지연의 큰 몫을 차지한다.
# 마네킹/컷 '생성' 입력은 디테일 재현이 필요해 축소하지 않는다(이 함수는 분석 전용).
_VISION_MAX_DIM = 1024
_VISION_SKIP_BYTES = 400_000  # 이보다 작으면 그대로 (재인코딩 이득 없음)


async def _judge_input_consistency(settings, images, slots) -> dict | None:
    """AG-IC 판정 — 플래그 off 거나 레퍼런스가 정면이 아니면 아예 호출하지 않는다.

    첫 장이 Front 가 아니면(정면 미업로드) 판정을 건너뛴다. 뒷면을 레퍼런스로 삼으면
    나머지 사진이 전부 '앞면과 다르다'로 몰려 오탐이 된다.
    """
    if settings.input_consistency == "off":
        return None
    if not slots or slots[0] != "Front":
        return None
    return await input_consistency.judge(settings, images, slots)


def shrink_for_vision(data: bytes, mime: str) -> tuple[bytes, str]:
    """최장변 1024px JPEG(q82)로 축소. 작거나 실패하면 원본 그대로 (안전 폴백)."""
    try:
        im = Image.open(BytesIO(data))
        rotated = im.getexif().get(274, 1) != 1
        im = ImageOps.exif_transpose(im)
        if len(data) <= _VISION_SKIP_BYTES and not rotated:
            return data, mime
        if max(im.size) > _VISION_MAX_DIM:
            im.thumbnail((_VISION_MAX_DIM, _VISION_MAX_DIM))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=82)
        out = buf.getvalue()
        if rotated or len(out) < len(data):
            return out, "image/jpeg"
        return data, mime
    except Exception:  # 손상 파일 등 — 축소 실패가 분석을 막지 않게
        return data, mime


async def _prepare_analysis_images(source_images):
    loaded = await asyncio.gather(*(
        asyncio.to_thread(shrink_for_vision, data, mime)
        for data, mime in source_images
    ))
    return loaded, [InlineImage(mime, data) for data, mime in loaded]


async def analyze_core_image_bytes(
    settings,
    source_images: list[tuple[bytes, str]],
    *,
    product: dict | None = None,
    slots: list[str] | None = None,
    on_prepared=None,
    persist_confirmed_evidence: bool = False,
    _prepared=None,
) -> dict:
    """AG-01과 사진 일치만 실행한다. 사진 일치에만 기존 보조 예산을 적용한다."""
    product = product or {}
    slots = slots or ["Front", *(["Detail"] * max(0, len(source_images) - 1))]

    loaded, images = _prepared if _prepared is not None else await _prepare_analysis_images(source_images)
    analysis_product = product
    if persist_confirmed_evidence:
        evidence_binding = product_evidence_contract.build_input_binding(
            source_images, loaded, slots
        )
        analysis_product = {
            **product,
            product_evidence_contract.INTERNAL_BINDING_KEY: evidence_binding,
        }
    if on_prepared is not None:
        await on_prepared(sum(len(image.data) for image in images))
    analyze_res, consistency_res = await asyncio.gather(
        product_analyst.analyze(settings, analysis_product, images, slots=slots),
        asyncio.wait_for(
            _judge_input_consistency(settings, images, slots),
            timeout=settings.analysis_aux_timeout_seconds),
        return_exceptions=True,
    )
    if isinstance(analyze_res, BaseException):
        raise analyze_res

    distributed, provider = analyze_res
    consistency = None
    if isinstance(consistency_res, BaseException):
        log.warning("AG-IC input consistency failed: %r", consistency_res)
    elif consistency_res:
        consistency = consistency_res

    analysis_payload = distributed["analysis"]
    # tags·swatch 기본 랭킹은 분석 완료 직후 match-candidates 요청과 재진입/새로고침 후
    # GET /analysis에서도 같은 값을 읽어야 한다. AG-01 distribute 단계에서는
    # intermediate이지만 서버 추천 입력으로 쓰이므로 저장 payload에 승격한다.
    analysis_payload["styleTags"] = distributed["intermediate"]["styleTags"]
    analysis_payload["swatchSuggestions"] = distributed["intermediate"]["swatchSuggestions"]
    if (
        consistency
        and settings.input_consistency == "warn"
        and consistency["verdict"] == "mismatch"
    ):
        analysis_payload["inputConsistency"] = consistency
    clothing_type = distributed["product"]["clothingType"]
    # The evidence contract is server-owned generation input. It stays in
    # analyses.payload but is not duplicated into the client-facing analysis job result.
    public_analysis_payload = {
        key: value
        for key, value in analysis_payload.items()
        if key != product_evidence_contract.PERSISTED_KEY
    }
    result_data = {
        **public_analysis_payload,
        "clothingType": clothing_type,
        "styleTags": analysis_payload["styleTags"],
        "swatchSuggestions": distributed["intermediate"]["swatchSuggestions"],
        "measurements": [],
    }
    return {
        "result_data": result_data,
        "analysis_payload": analysis_payload,
        "clothing_type": clothing_type,
        "provider": provider,
        "feature_provider": None,
        "consistency": consistency,
        "bytes_out": sum(len(image.data) for image in images),
    }


async def extract_image_features(settings, source_images, *, product=None, slots=None, _prepared=None):
    """AG-08과 원본에 묶인 디테일 계약. 공급자 상한 외의 시간 제한은 두지 않는다."""
    slots = slots or ["Front", *(["Detail"] * max(0, len(source_images) - 1))]
    _loaded, images = _prepared if _prepared is not None else await _prepare_analysis_images(source_images)
    points, provider = await feature_extractor.extract(settings, product or {}, images, slots=slots)
    contract = detail_recommendations.build_contract(
        getattr(points, "detail_candidates", None), source_images, slots
    )
    return {"points": points, "contract": contract, "provider": provider}


async def analyze_image_bytes(
    settings,
    source_images: list[tuple[bytes, str]],
    *,
    product: dict | None = None,
    slots: list[str] | None = None,
    on_prepared=None,
    persist_confirmed_evidence: bool = False,
) -> dict:
    """로그인 job과 이전 공개 분석의 병렬 실행·보조 예산·반환 계약을 유지한다."""
    product = product or {}
    slots = slots or ["Front", *(["Detail"] * max(0, len(source_images) - 1))]
    prepared = await _prepare_analysis_images(source_images)
    if on_prepared is not None:
        await on_prepared(sum(len(image.data) for image in prepared[1]))
    # 기존 경로는 AG-08도 부가 분석 예산으로 자른다. 축소 입력은 두 함수가 공유한다.
    core, feature_res = await asyncio.gather(
        analyze_core_image_bytes(
            settings, source_images, product=product, slots=slots,
            persist_confirmed_evidence=persist_confirmed_evidence, _prepared=prepared,
        ),
        asyncio.wait_for(
            extract_image_features(settings, source_images, product=product, slots=slots, _prepared=prepared),
            timeout=settings.analysis_aux_timeout_seconds,
        ),
        return_exceptions=True,
    )
    if isinstance(core, BaseException):
        raise core
    if isinstance(feature_res, BaseException):
        log.warning("AG-08 feature extract failed: %r", feature_res)
        contract = detail_recommendations.build_contract(None, source_images, slots)
    else:
        core["feature_provider"] = feature_res["provider"]
        if feature_res["points"]:
            core["analysis_payload"]["aiSuggestedPoints"] = feature_res["points"]
            core["result_data"]["aiSuggestedPoints"] = feature_res["points"]
        contract = feature_res["contract"]
    core["analysis_payload"][detail_recommendations.PERSISTED_KEY] = contract
    core["result_data"][detail_recommendations.PERSISTED_KEY] = detail_recommendations.public_summary(contract)
    return core


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
        # 1) 입력 로드 — 기준 색상 이미지 asset (마네킹과 동일 소스). slot(Front/Back/Detail/BackDetail)은
        #    AG-08 관찰 가이드용으로 보존한다(디테일 컷 집중 지시 — 2026-07-13).
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            assets, slots = [], []
            for slot, aid in detail_recommendations.analysis_source_entries(product):
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
        async def _load_one(a: dict) -> tuple[bytes, str]:
            raw = await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])
            return raw, a["mime_type"]

        loaded = await asyncio.gather(*(_load_one(a) for a in assets))
        bytes_in = sum(len(data) for data, _ in loaded)

        async def _inputs_loaded(bytes_out: int) -> None:
            await _emit(pool, job_id, "progress", {
                "progress": 30, "phase": "inputs_loaded",
                "bytesIn": bytes_in, "bytesOut": bytes_out,
            })

        core = await analyze_image_bytes(
            s,
            loaded,
            product=product,
            slots=slots,
            on_prepared=_inputs_loaded,
            persist_confirmed_evidence=True,
        )

        previous_analysis, items = {}, []
        try:
            async with pool.connection() as conn:
                previous_analysis = await repo.get_analysis(conn, project_id) or {}
                items = await outfit.load_items(conn, user_id, project_id)
        except Exception as exc:
            log.warning("outfit catalog unavailable during analysis: %s", type(exc).__name__)
        outfit_analysis = {**core["analysis_payload"]}
        if previous_analysis.get("outfitSelections"):
            outfit_analysis["outfitSelections"] = previous_analysis["outfitSelections"]
        elif previous_analysis.get("matchClothing"):
            outfit_analysis["matchClothing"] = previous_analysis["matchClothing"]
        fields = outfit.prepare_recommendation(s, app.state.r2, {**product, "clothingType": core["clothing_type"]},
                                         outfit_analysis, loaded, items, slots=slots)
        core["analysis_payload"].update(fields)
        core["result_data"].update(outfit.read_fields(fields))

        provider = core["provider"]
        feature_provider = core["feature_provider"]
        consistency = core["consistency"]
        if consistency:
            log.info("AG-IC job=%s verdict=%s confidence=%.2f offending=%s mode=%s",
                     job_id, consistency["verdict"], consistency["confidence"],
                     [o["slot"] for o in consistency["offending"]], s.input_consistency)

        await _emit(pool, job_id, "progress", {"progress": 80, "phase": "analyzed",
                                               "provider": provider,
                                               "featureProvider": feature_provider})

        analysis_payload = core["analysis_payload"]
        clothing_type = core["clothing_type"]
        result_data = core["result_data"]

        # 4) 성공 종결 (원자·lease 펜스, 무과금)
        async with pool.connection() as conn:
            latest_analysis, latest_items = previous_analysis, items
            try:
                latest_analysis = await repo.get_analysis_for_update(conn, project_id) or {}
                latest_items = await outfit.load_items(conn, user_id, project_id)
            except Exception as exc:
                log.warning("outfit catalog refresh unavailable: %s", type(exc).__name__)
            locks = latest_analysis.get("outfitSelections")
            if locks:
                selections = outfit.merge_selections(analysis_payload["outfitSelections"], locks, clothing_type)
                updated = outfit.sync_legacy(analysis_payload, selections, latest_items, app.state.r2)
                analysis_payload.update(updated)
                result_data.update(updated)
            # Refresh the key after merging a seller edit that arrived during core analysis.
            fields = outfit.prepare_recommendation(s, app.state.r2, {**product, "clothingType": clothing_type},
                analysis_payload, loaded, latest_items, slots=slots)
            analysis_payload.update(fields)
            result_data.update(outfit.read_fields(fields))
            out = await repo.finalize_analyze_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, clothing_type=clothing_type,
                analysis_payload=analysis_payload, result={"data": result_data},
                metadata={"provider": provider, "featureProvider": feature_provider,
                          "promptVersion": "v1",
                          # 판정 원문 보존처(match 도 포함) — 사후에 오탐률을 되짚을 때 유일한 근거.
                          **({"inputConsistency": consistency} if consistency else {})})
            await conn.commit()
        if out is None:  # lease 상실(복구·재클레임) → 결과 폐기
            log.warning("analyze job %s lost lease", job_id)
        elif fields["outfitRecommendation"]["status"] == "pending":
            outfit_jobs.start_project(app, user_id, project_id, {**product, "clothingType": clothing_type},
                analysis_payload, loaded, latest_items, slots=slots)
    except VisionError as e:
        await _fail(str(e), {"error": "vision_failed"}, code="analysis_failed")
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("분석 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
