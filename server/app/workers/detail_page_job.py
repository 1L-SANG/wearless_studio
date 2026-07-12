"""PL-4 상세페이지 생성 워커. AG-06 컷 → AG-02 카피 → AG-03 검수 → M-02 조립 → EditorBlock[].

저장 콘티(projects.storyboard)의 source='ai' 블록별로 AG-06 컷 이미지를 생성(실패 컷은 빈 슬롯,
전체 중단 없음·미차감), copywriting 이면 블록별 AG-02 카피 + 묶음 AG-03 검수, page_assembler(M-02)
로 EditorBlock[] 조립. 크레딧: 성공 컷 수 × storyboardPerCut 만 confirm(부분 성공). lease 펜스.
"""

import asyncio
import logging
import uuid
from io import BytesIO

from PIL import Image

from .. import repo
from ..agents import copy_qc, copywriter, cut_generator, page_assembler
from ..agents.gemini_image import InlineImage
from ..r2 import ai_key, ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.detail_page_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
# 컷·카피 동시 생성 상한. 순차(블록 수 × ~40s)면 4컷에 2~3분 → 병렬로 단축. gemini 버스트
# 제한을 감안해 무제한이 아닌 소폭 동시성(429 시 이 값을 낮춘다).
_GEN_CONCURRENCY = 3


def _dims(data: bytes):
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


async def _gen_cuts(app, job, prepared, product, analysis):
    """준비된 블록별 (block, images, manifest)로 AG-06 컷 생성 → (cut_results, cut_assets).
    실패 컷은 건너뛴다(빈 슬롯은 assemble 이 처리) — 부분 성공. 스펙 위반(unknown cutType)도
    같은 경로(빈 슬롯) — 조용한 styling 대체 렌더는 하지 않는다(ADR-0004)."""
    s, gemini, r2 = app.state.settings, app.state.gemini, app.state.r2
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    sem = asyncio.Semaphore(_GEN_CONCURRENCY)

    async def _one(item):
        """컷 1개 생성+저장. 실패(빈 슬롯)면 None. 각 블록 독립이라 동시 실행 가능."""
        b, images, manifest = item
        async with sem:
            if not images:  # 옷 근거(상품/마네킹) 없음 — 무드만으로는 동일성 보장 불가, 생성하지 않는다
                log.warning("AG-06 cut skipped (no garment-truth references) job %s block %s", job_id, b.get("id"))
                await _emit(app.state.pool, job_id, "step",
                            {"blockId": b.get("id"), "status": "cut_failed"})
                return None
            try:
                img, mime = await cut_generator.generate(
                    s, gemini, b, product, images, analysis=analysis, manifest=manifest)
            except Exception as e:  # GeminiError·ValueError 포함 — 실패 컷 = 빈 슬롯, 미차감(부분 성공)
                log.warning("AG-06 cut failed for job %s block %s: %r", job_id, b.get("id"), e)
                await _emit(app.state.pool, job_id, "step",
                            {"blockId": b.get("id"), "status": "cut_failed"})
                return None
            ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(r2.put_bytes, key, img, mime)
            w, h = _dims(img)
            return (
                {"blockId": b.get("id"), "imageUrl": f"/v1/assets/{asset_id}/file"},
                {"asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
                 "size": len(img), "width": w, "height": h},
            )

    # gather 는 입력 순서를 보존 — 콘티 블록 순서대로 컷을 배열한다.
    cut_results, cut_assets = [], []
    for r in await asyncio.gather(*[_one(item) for item in prepared]):
        if r:
            cut_results.append(r[0])
            cut_assets.append(r[1])
    return cut_results, cut_assets


async def _gen_copy(app, job, ai_blocks, product, analysis):
    """copywriting 시 블록별 AG-02 카피 → 묶음 AG-03 검수(revise 채택). 실패 블록은 카피 생략."""
    s = app.state.settings
    sem = asyncio.Semaphore(_GEN_CONCURRENCY)

    async def _one(b):
        """블록 1개 카피 생성. 실패면 None(카피는 게이트 아님, 블록 생략)."""
        async with sem:
            try:
                texts = await copywriter.generate(
                    s, block_kind=b.get("kind"), cut_type=b.get("cutType"),
                    product=product, analysis=analysis, color_label=b.get("colorId"))
            except Exception as e:  # VisionError 포함 — 카피는 게이트 아님, 실패 블록 생략
                log.warning("AG-02 copy failed for job %s block %s: %r", job["id"], b.get("id"), e)
                return None
            return (b.get("id"), texts) if texts else None

    # gather 는 순서 보존 — drafts 삽입 순서(=콘티 순서)를 유지한다.
    items, drafts = [], {}
    for r in await asyncio.gather(*[_one(b) for b in ai_blocks]):
        if r:
            bid, texts = r
            drafts[bid] = texts
            for t in texts:
                items.append({"blockId": bid, "text": t.get("text", "")})
    if not items:
        return []
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
    return copy_results


async def run_detail_page_job(app, job: dict) -> None:
    s, pool = app.state.settings, app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"

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

    try:
        # 1) 입력 로드 — 옷 레퍼런스 = (있으면) 선택 마네킹컷(핏·기장 기준, ADR-0004)
        #    + 블록 색상별 상품 슬롯 이미지 + (styling·mirror) 매칭 의류 + 무드 레퍼런스
        async with pool.connection() as conn:
            project = await repo.get_project(conn, user_id, project_id) or {}
            storyboard = await repo.get_storyboard(conn, project_id)
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id)
            ai_blocks = [b for b in storyboard if isinstance(b, dict) and b.get("source") == "ai"]

            mannequin_asset = None
            sel = project.get("selected_mannequin_id") or project.get("selectedMannequinId")
            if sel:
                for c in await repo.list_mannequin_cuts(conn, user_id, project_id):
                    if f"{c.get('candidate')}-{c.get('version')}" == sel and c.get("asset_id"):
                        mannequin_asset = await repo.get_asset_for_user(conn, user_id, str(c["asset_id"]))
                        break
            color_assets: dict = {}   # colorId → [asset(slot 포함)] — 블록 간 재사용
            match_assets: dict = {}   # matchingItemId → asset|None
            mood_assets: dict = {}    # refAssetId → asset|None (소유 검증 겸함)
            for b in ai_blocks:
                ckey = b.get("colorId") or None
                if ckey not in color_assets:
                    rows = []
                    for slot, aid in cut_generator.color_images(product, ckey):
                        a = await repo.get_asset_for_user(conn, user_id, aid)
                        if a:
                            a["slot"] = slot
                            rows.append(a)
                    color_assets[ckey] = rows
                mids = b.get("matchIds") or []
                if mids and b.get("cutType") in ("styling", "mirror") and str(mids[0]) not in match_assets:
                    m_aid = await repo.get_matching_item_asset(conn, str(mids[0]))
                    match_assets[str(mids[0])] = (
                        await repo.get_asset_for_user(conn, user_id, m_aid) if m_aid else None)
                for rid in (b.get("refAssetIds") or [])[:3]:
                    if str(rid) not in mood_assets:
                        mood_assets[str(rid)] = await repo.get_asset_for_user(conn, user_id, str(rid))

        # R2 바이트는 r2_key 캐시로 1회만 — 같은 색상 이미지가 블록마다 재다운로드되지 않게
        _img_cache: dict = {}

        async def _img(a: dict) -> InlineImage:
            k = a["r2_key"]
            if k not in _img_cache:
                _img_cache[k] = InlineImage(
                    a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, k))
            return _img_cache[k]

        prepared = []  # (block, images[마네킹?, *상품, 매칭?, *무드], manifest) — 순서=매니페스트
        for b in ai_blocks:
            prods = color_assets.get(b.get("colorId") or None, [])
            # 옷 근거(상품 사진 또는 마네킹컷)가 없으면 생성 불가 — 무드/매칭만으로 진행하면
            # 모델이 레퍼런스 속 옷을 지어내거나 베낀다(ADR-0004 정확성 최우선). 스킵 표식.
            if mannequin_asset is None and not prods:
                prepared.append((b, [], ""))
                continue
            mids = b.get("matchIds") or []
            match_a = match_assets.get(str(mids[0])) if mids and b.get("cutType") in ("styling", "mirror") else None
            moods = [mood_assets[str(r)] for r in (b.get("refAssetIds") or [])[:3] if mood_assets.get(str(r))]
            imgs = []
            if mannequin_asset is not None:
                imgs.append(await _img(mannequin_asset))
            for a in prods:
                imgs.append(await _img(a))
            if match_a is not None:
                imgs.append(await _img(match_a))
            for a in moods:
                imgs.append(await _img(a))
            manifest = cut_generator.build_manifest(
                prods, has_mannequin=mannequin_asset is not None,
                has_match=match_a is not None, mood_count=len(moods))
            prepared.append((b, imgs, manifest))

        copywriting = bool(project.get("copywriting"))
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded",
                                               "aiCuts": len(ai_blocks)})

        # 2) 컷 생성 (부분 성공)
        cut_results, cut_assets = await _gen_cuts(app, job, prepared, product, analysis)
        await _emit(pool, job_id, "progress", {"progress": 65, "phase": "cuts",
                                               "generated": len(cut_assets)})

        # 3) 카피(선택) + 검수
        copy_results = await _gen_copy(app, job, ai_blocks, product, analysis) if copywriting else []
        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "copy"})

        # 4) 조립(M-02) — 실패 컷은 빈 슬롯으로
        editor_blocks = page_assembler.assemble(storyboard, cut_results, copy_results, product, copywriting)

        # 5) 성공 종결 (원자·lease 펜스). charge = 성공 컷 수 × **예약 시점 단가 스냅샷**
        # (job.metadata.perCutCost — routes.py가 예약과 같은 tx에서 기록). 실행 시점 설정을 쓰면
        # 배포 사이 단가 변경이 낀 잡이 견적과 다르게 정산되고, 예약액÷현재 블록 수 역산은 예약 후
        # 콘티 재저장으로 블록이 늘면 단가가 0으로 떨어져 무과금 생성이 된다 — 둘 다 금지.
        # 스냅샷 없는 legacy 잡만 실행 시점 단가로 폴백. min 캡 = 예약 초과 차감 최종 가드.
        per_cut = (job.get("metadata") or {}).get("perCutCost")
        if per_cut is None:  # legacy 잡(스냅샷 도입 전 큐 잔여분)
            per_cut = s.credit_cost_storyboard_per_cut
        charge = min(len(cut_assets) * per_cut, reserved)
        async with pool.connection() as conn:
            out = await repo.finalize_detail_page_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id, project_id=project_id,
                editor_blocks=editor_blocks, cut_assets=cut_assets, reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version, "generatedCuts": len(cut_assets)})
            await conn.commit()
        if out is None:  # lease 상실 → 방금 올린 R2 객체 best-effort 정리
            for c in cut_assets:
                try:
                    await asyncio.to_thread(app.state.r2.delete, c["key"])
                except Exception:
                    log.warning("orphan R2 cleanup failed: %s", c["key"])
    except Exception as e:
        await _fail("상세페이지 생성에 실패했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
