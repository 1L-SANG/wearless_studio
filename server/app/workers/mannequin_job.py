"""AG-04 마네킹 생성 워커 (요리사). dispatcher가 claim한 job 1건을 실행한다.

흐름: 입력 로드(베이스+상품사진+하의) → 단일 tier(기본 image_high=Gemini 3 Pro,
Flash·승격 없음) 생성 → QC(기본 shadow: 판정 로그만, 게이팅 시 같은 모델 재시도) → 통과본 R2 저장
→ finalize(에셋·컷·크레딧·done/error, 원자·lease 펜스). 생성/네트워크는 to_thread·async로 격리.
"""

import asyncio
import logging
import uuid
from contextlib import suppress
from io import BytesIO

from PIL import Image

log = logging.getLogger("wearless.mannequin_job")

from .. import repo
from ..agents import image_qc, mannequin
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.model_routing import resolve_model
from ..agents.prompts import load_prompt_template, render_mannequin_prompt
from ..r2 import ai_key, ext_for_mime
from ..services import qc
from ._common import emit_job_event as _emit  # 공용 헬퍼 (analyze_job과 공유)

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_GENERATION_PROGRESS_INTERVAL_SECONDS = 7.0
_GENERATION_PROGRESS_MAX = 84


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


# 첨부 이미지 슬롯 → 모델용 라벨. prompt ${imageManifest} 가 이 목록을 받는다.
_SLOT_LABEL = {
    "Front": "front view of the garment",
    "Back": "back view of the garment",
    "Detail": "detail close-up of the garment (texture, stitching, trims, print)",
    "Fit": "fit reference — the garment worn on a real person (true length & how it sits)",
}


def _build_manifest(prod_assets: list[dict], has_match: bool) -> str:
    """images=[base, *prod(slot순), match]와 동일 순서의 역할 목록 (모델이 어느 이미지가 무엇인지 알게).
    내용은 전부 고정 라벨(_SLOT_LABEL 룩업) — 셀러 데이터를 직접 끼우지 않는다(프롬프트 인젝션 방지).
    의류 종류는 sanitize된 ${clothingType}·PRODUCT CONTEXT로 따로 전달되므로 여기엔 넣지 않는다."""
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    i = 2
    for a in prod_assets:
        lines.append(f"{i}. {_SLOT_LABEL.get(a.get('slot'), 'view of the garment')}")
        i += 1
    if has_match:
        lines.append(f"{i}. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top")
    return "\n".join(lines)


def gate_decision(s, pillow_verdict_str: str, p2) -> tuple[bool, bool]:
    """생성 컷 게이팅 결정 (순수) → (pillow_reject, p2_reject).

    - Pillow QC(휴리스틱): mannequin_qc_enabled 且 판정!='pass' → reject.
    - AG-P2(vision 동일성): image_qc=='enforce' 且 p2.verdict=='retry' → reject.
      off/shadow 는 게이트 안 함(항상 통과 — 기존 동작 불변). p2 없음(키미설정·판정실패)도 통과.
    """
    pillow_reject = s.mannequin_qc_enabled and pillow_verdict_str != "pass"
    p2_reject = s.image_qc == "enforce" and isinstance(p2, dict) and p2.get("verdict") == "retry"
    return pillow_reject, p2_reject


async def _run_candidate(
    *, app, job, candidate, base_fit, base_gender, base_img, prod_imgs, match_img,
    product_count, template, product, analysis, clothing_type, image_manifest="", fit_profile=None,
) -> dict | None:
    """후보 1개 생성. 통과 시 R2 저장 후 finalize용 dict 반환, 실패 시 None."""
    s = app.state.settings
    pool, r2, gemini = app.state.pool, app.state.r2, app.state.gemini
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    images = [base_img, *prod_imgs] + ([match_img] if match_img else [])
    ctx = mannequin.prompt_context(
        clothing_type=clothing_type, product_count=product_count,
        base_gender=base_gender, image_manifest=image_manifest, fit_profile=fit_profile,
    )
    base_prompt = render_mannequin_prompt(
        template, ctx, product, analysis,
        seller_canon=s.seller_text_canonicalize, knowledge=s.retrieval_knowledge,
    )
    # AG-04는 처음부터 단일 tier(기본 image_high=Pro, 사용자 결정 — Flash·승격 없음).
    # QC 게이팅 시 같은 모델로 재시도(re-roll + 교정 피드백). shadow면 첫 결과 채택.
    model = resolve_model(s, s.mannequin_tier)
    feedback = ""
    for attempt in range(1, s.mannequin_max_attempts + 1):
        prompt = f"{feedback}\n\n{base_prompt}" if feedback else base_prompt
        try:
            res = await gemini.generate_content_image(
                model, prompt, images, s.mannequin_image_size,
                aspect_ratio=s.mannequin_aspect_ratio)
        except GeminiError as e:
            await _emit(pool, job_id, "step", {
                "candidate": candidate, "model": model, "attempt": attempt,
                "status": "error", "message": str(e)[:200]})
            continue
        verdict = qc.evaluate_mannequin_qc(res.image)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "model": model, "attempt": attempt, "status": "generated",
            # metrics 도 남긴다 — shadow 재캘리브(임계 튜닝)의 실측 근거. verdict/reasons 만으론
            # 왜 걸렸는지(bboxBottom·aspect·하단비율) 모른다.
            "qc": {"verdict": verdict.verdict, "reasons": verdict.reasons, "metrics": verdict.metrics}})
        # AG-P2 이미지 동일성 검수 — shadow(로그만)·enforce(게이트) 시 판정. off면 skip.
        # vision 실패(키미설정 등)는 삼켜 p2=None → 게이트 미적용(생성 자체 안 막음).
        p2 = None
        if s.image_qc in ("shadow", "enforce") and prod_imgs:
            try:
                p2 = await image_qc.verdict(s, prod_imgs, InlineImage(res.mime, res.image))
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "image_qc", "imageQc": p2})
            except Exception as e:
                log.warning("AG-P2 image_qc failed for job %s: %r", job_id, e)
        # 게이팅: Pillow QC + AG-P2. 둘 다 통과면 채택(off/shadow 는 항상 통과 — 기존 동작 불변).
        pillow_reject, p2_reject = gate_decision(s, verdict.verdict, p2)
        if not pillow_reject and not p2_reject:
            ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(r2.put_bytes, key, res.image, res.mime)
            w, h = _image_dims(res.image)
            return {
                "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": res.mime,
                "size": len(res.image), "width": w, "height": h,
                "candidate": candidate, "base_fit": base_fit,
            }
        # reject → 재시도 프롬프트에 교정 피드백 주입(Pillow 사유 + AG-P2 correctionPrompt).
        parts = []
        if pillow_reject:
            parts.append(qc.format_qc_feedback(verdict))
        if p2_reject and isinstance(p2, dict) and p2.get("correctionPrompt"):
            parts.append("CORRECTION (generate the SAME garment as the product photos): "
                         + p2["correctionPrompt"])
        feedback = "\n\n".join(parts)
    return None  # max_attempts 내 통과본 없음 → 이 후보 드롭(부분 성공 허용)


async def run_mannequin_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"

    async def _fail(message: str, meta: dict):
        async with pool.connection() as conn:
            await repo.finalize_mannequin_failure(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, reserved=reserved, settle_key=settle_key,
                message=message, metadata=meta)
            await conn.commit()

    try:
        # 1) 입력 로드
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            gender = mannequin.select_base_gender(analysis)
            base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                             else s.base_mannequin_women_asset_id)
            base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                          if base_asset_id else None)
            prod_assets = []
            for slot, aid in mannequin.base_color_images(product):
                a = await repo.get_asset_for_user(conn, user_id, aid)
                if a:
                    a["slot"] = slot  # Front/Back/Detail/Fit — 매니페스트 라벨용
                    prod_assets.append(a)
            match_asset = None
            match_id = mannequin.main_match_item_id(analysis)
            if match_id:
                m_aid = await repo.get_matching_item_asset(conn, match_id)
                if m_aid:
                    match_asset = await repo.get_asset_for_user(conn, user_id, m_aid)

        if base_asset is None:
            await _fail("마네킹 베이스가 설정되지 않았어요. 잠시 후 다시 시도해 주세요.",
                        {"error": "base_mannequin_missing", "gender": gender})
            return
        if not prod_assets:
            await _fail("상품 사진을 찾을 수 없어요. 정면 사진을 올렸는지 확인해 주세요.",
                        {"error": "no_product_images"})
            return

        # 2) 바이트 다운로드 (to_thread)
        base_img = InlineImage(base_asset["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, base_asset["r2_key"]))
        prod_imgs = [InlineImage(a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])) for a in prod_assets]
        match_img = None
        if match_asset:
            match_img = InlineImage(match_asset["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, match_asset["r2_key"]))
        product_count = len(prod_imgs) + (1 if match_img else 0)
        template = load_prompt_template(s)
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded",
                                               "withBottom": match_img is not None})

        # 3) A/B 이원 후보 생성(원 UI 계약: A=현재 핏, B=슬림 변형) — 병렬. 부분 성공 허용
        #    (한쪽 실패 시 성공분만 저장·차감). 크레딧 예약(2)도 2컷 기준.
        clothing_type = product.get("clothing_type") or "상의"
        manifest = _build_manifest(prod_assets, match_img is not None)
        fit_profile = mannequin.generation_spec(analysis)
        legacy_base_fit = analysis.get("fit") or "regular"
        slim_profile = mannequin.slim_variant_profile(fit_profile, clothing_type, gender)
        await _emit(pool, job_id, "progress", {"progress": 35, "phase": "generating"})

        # A/B gemini 생성은 이 job 에서 가장 긴 구간(20~60s)이라, 후보가 하나씩 끝날 때마다
        # 중간 progress 를 쏜다(35→60→85). 실제 Gemini 호출이 더 길어질 때는 ticker 가 84까지
        # 천천히 올려 폴링 UI 가 "멈춤/실패"처럼 보이지 않게 한다.
        _done = 0
        _reported_generation_progress = 35
        _progress_lock = asyncio.Lock()
        _generation_done = asyncio.Event()

        async def _emit_generation_progress(next_progress: int, *, estimated: bool = False):
            nonlocal _reported_generation_progress
            next_progress = min(85, max(35, int(next_progress)))
            async with _progress_lock:
                if next_progress <= _reported_generation_progress:
                    return
                _reported_generation_progress = next_progress
                payload = {"progress": next_progress, "phase": "generating"}
                if estimated:
                    payload["estimated"] = True
                await _emit(pool, job_id, "progress", payload)

        async def _tick_generation_progress():
            while not _generation_done.is_set():
                try:
                    await asyncio.wait_for(
                        _generation_done.wait(), timeout=_GENERATION_PROGRESS_INTERVAL_SECONDS)
                    return
                except asyncio.TimeoutError:
                    await _emit_generation_progress(
                        min(_GENERATION_PROGRESS_MAX, _reported_generation_progress + 1),
                        estimated=True)

        async def _cand(letter, base_fit, profile):
            nonlocal _done
            try:
                r = await _run_candidate(
                    app=app, job=job, candidate=letter, base_fit=base_fit, base_gender=gender,
                    base_img=base_img, prod_imgs=prod_imgs, match_img=match_img,
                    product_count=product_count, template=template, product=product,
                    analysis=analysis, clothing_type=clothing_type, image_manifest=manifest,
                    fit_profile=profile)
            except Exception as e:
                log.warning("job %s candidate %s failed: %r", job_id, letter, e)
                r = None
            async with _progress_lock:
                _done += 1
                # 2개 완료 기준 35→60→(85 은 아래 finalizing 이 덮음). 마지막은 85 로 클램프.
                next_progress = min(85, 35 + _done * 25)
            await _emit_generation_progress(next_progress)
            return r

        progress_task = asyncio.create_task(_tick_generation_progress())
        try:
            results = await asyncio.gather(
                _cand("A", legacy_base_fit, fit_profile),
                _cand("B", "slim", slim_profile))
        finally:
            _generation_done.set()
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
        passed = [r for r in results if isinstance(r, dict)]

        if not passed:
            await _fail("마네킹컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": "all_candidates_failed"})
            return
        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "finalizing"})

        # 4) 성공 종결 (원자·lease 펜스). charge = 성공 컷 수 (부분 성공 미차감 — codex/계약).
        charge = len(passed)
        async with pool.connection() as conn:
            out = await repo.finalize_mannequin_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, candidates=passed, reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version,
                          "promptVersion": s.mannequin_prompt_version, "gender": gender})
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            for c in passed:
                try:
                    await asyncio.to_thread(app.state.r2.delete, c["key"])
                except Exception:
                    log.warning("orphan R2 cleanup failed: %s", c["key"])
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
