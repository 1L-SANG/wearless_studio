"""AG-01 분석 워커 (PL-1). dispatcher가 claim한 analyze job 1건을 실행한다.

흐름: 입력 로드(상품명+색상 그룹 이미지, 서버 상태만 신뢰) → Gemini 3 Flash 구조화 JSON
(검증 실패 1회 피드백 재시도) → 후처리·분배(agents/analysis.postprocess) → M-01 매칭 →
finalize(원자·lease 펜스·지문 가드 — repo.finalize_analyze_success). 크레딧 없음.
(pl1_analysis_agent_spec §6.6)
"""

import asyncio
import logging

from pydantic import ValidationError

from .. import repo
from ..agents import analysis
from ..agents.gemini_image import InlineImage
from ..agents.gemini_text import GeminiTextError
from ..agents.model_routing import resolve_model
from ..services import matching
from ._common import emit as _emit

log = logging.getLogger("wearless.analyze_job")


async def run_analyze_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]

    async def _fail(message: str, meta: dict):
        async with pool.connection() as conn:
            await repo.finalize_analyze_failure(
                conn, job_id=job_id, lease_token=lease_token, message=message, metadata=meta)
            await conn.commit()

    try:
        if app.state.gemini_text is None:
            await _fail("분석 서버가 설정되지 않았어요. 잠시 후 다시 시도해 주세요.",
                        {"error": "gemini_text_unconfigured"})
            return

        # 1) 입력 로드 — 서버 상태만 신뢰 (클라이언트 값 불신, ai_pipeline_spec §3)
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            specs = analysis.collect_input_images(product)
            assets = []
            for spec in specs:
                a = await repo.get_asset_for_user(conn, user_id, spec["assetId"])
                if a:
                    assets.append((spec, a))
        if not assets:
            await _fail("상품 사진을 찾을 수 없어요. 정면 사진을 올렸는지 확인해 주세요.",
                        {"error": "no_product_images"})
            return
        actual_fp = analysis.input_fingerprint(product)  # 실제 분석 대상의 지문 —
        # finalize 지문 가드 비교 기준(§3.7 불변식) + finalize metadata에 실측값으로 기록.

        # 2) 바이트 다운로드 (to_thread — 이벤트 루프 비차단) + 프롬프트 조립
        images = [
            InlineImage(
                a["mime_type"],
                await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"]),
            )
            for _spec, a in assets
        ]
        manifest = analysis.build_manifest([spec for spec, _a in assets])
        user_text = analysis.build_user_text(manifest, product.get("name"))
        system = analysis.load_analysis_prompt(s)
        model = resolve_model(s, "text")
        await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded",
                                               "imageCount": len(images)})

        # 3) AG-01 호출 — 파싱/검증 실패는 사유를 피드백으로 붙여 1회 재시도 (§2.3)
        raw, usage, latency, attempts = None, None, 0, 0
        feedback = ""
        for attempt in range(1, s.analysis_max_attempts + 1):
            attempts = attempt
            try:
                res = await app.state.gemini_text.generate_json(
                    model, system, f"{user_text}{feedback}", images,
                    analysis.RESPONSE_SCHEMA,
                    thinking_level=s.analysis_thinking_level,
                    timeout=s.analysis_timeout_seconds,
                )
                raw = analysis.AnalysisRaw.model_validate(res.data)
                usage, latency = res.usage, res.latency_ms
                break
            except (GeminiTextError, ValidationError) as e:
                await _emit(pool, job_id, "step", {
                    "attempt": attempt, "model": model,
                    "status": "error", "message": str(e)[:200]})
                feedback = (
                    "\n\nPREVIOUS ATTEMPT WAS REJECTED: "
                    f"{str(e)[:300]}. Fix exactly this and return the full JSON again."
                )
        if raw is None:
            await _fail("상품 분석에 실패했어요. 다시 시도해 주세요.",
                        {"error": "agent_failed", "attempts": attempts, "model": model})
            return
        await _emit(pool, job_id, "progress", {"progress": 70, "phase": "agent_done"})

        # 4) 안전 게이트 + 후처리·분배 (§3.3)
        if not raw.garment_detected:
            await _fail("사진에서 의류를 인식하지 못했어요. 상품이 잘 보이는 사진으로 다시 시도해 주세요.",
                        {"error": "garment_not_detected", "model": model})
            return
        post = analysis.postprocess(raw, product)

        # 5) M-01 매칭 + 모델 기본 선택 → payload 완성 (§3.4·§3.5·§3.6)
        async with pool.connection() as conn:
            items = await repo.list_active_matching_items(conn)
        genders = post["payload_base"]["targetGenders"]
        ranked = matching.recommend(items, post["clothing_type"], genders)
        candidates = [c for c in
                      (matching.to_candidate(i, app.state.r2.public_url) for i in ranked)
                      if c]
        selections = [
            {"clothingId": c["id"], "role": role}
            for c, role in zip(candidates[:2], ("main", "sub"))
        ]
        payload = {
            **post["payload_base"],
            "selectedModelId": analysis.default_model_id(genders),
            "matchCandidates": candidates,
            "matchSelections": selections,
        }

        # 6) finalize (원자·lease 펜스·지문 가드 §3.7)
        async with pool.connection() as conn:
            out = await repo.finalize_analyze_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, clothing_type=post["clothing_type"],
                swatch_suggestions=post["swatch_suggestions"], payload=payload,
                actual_fingerprint=actual_fp,
                metadata={"agentId": "AG-01", "tier": "text", "model": model,
                          "promptVersion": s.analysis_prompt_version,
                          "fingerprint": actual_fp,
                          "latencyMs": latency, "usage": usage, "attempts": attempts,
                          "styleTags": post["style_tags"]})
            await conn.commit()
        # out is None = ① lease 상실(부수효과 0) 또는 ② 지문 가드 폐기(finalize가 스스로
        # error 종결 완료). 어느 쪽이든 워커는 그냥 종료 (R2 산출물 없어 정리 불필요).
        if out is None:
            log.info("analyze job %s discarded (lease lost or stale input)", job_id)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        log.exception("analyze job %s failed", job_id)
        await _fail("분석 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
