"""컷 생성 워커 (ADR-0004) — dispatcher가 claim한 editor_image job 1건을 실행한다.

흐름: 스펙 로드 → 옷 레퍼런스(선택 마네킹컷 + 해당 색상 상품사진) + 매칭/무드 레퍼런스 로드
→ 프롬프트 렌더(cut_generate_v1.txt 섹션 조립) → 단일 tier 1회 생성 → R2 저장
→ finalize(asset·wardrobe·크레딧·done/error, 원자·lease 펜스).

QC 게이팅 없음(의도) — 컷은 "싼 재생성 루프"가 안전망이라 실패 비용을 낮추는 쪽을 택한다(ADR-0004).
"""

import asyncio
import logging
import uuid
from io import BytesIO

from PIL import Image

log = logging.getLogger("wearless.cut_job")

from .. import repo
from ..agents import cut as cut_agent
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.model_routing import resolve_model
from ..r2 import ai_key, ext_for_mime
from ._common import emit as _emit

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


async def _load_asset_bytes(app, asset: dict) -> InlineImage:
    return InlineImage(asset["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, asset["r2_key"]))


async def run_cut_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    metadata = {"creditCostVersion": s.credit_cost_version, "promptVersion": s.cut_prompt_version}

    async def _fail(message: str, meta: dict):
        async with pool.connection() as conn:
            await repo.finalize_cut_failure(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, reserved=reserved, settle_key=settle_key,
                message=message, metadata={**metadata, **meta})
            await conn.commit()

    try:
        spec = cut_agent.normalize_spec((job.get("payload") or {}).get("spec") or {})
    except ValueError:
        await _fail("컷 설정이 올바르지 않아요. 콘티를 다시 확인해 주세요.", {"error": "invalid_spec"})
        return

    try:
        # 1) 입력 로드 — 옷 레퍼런스 = (있으면) 선택 마네킹컷 + 해당 색상 상품사진
        async with pool.connection() as conn:
            project = await repo.get_project(conn, user_id, project_id) or {}
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id)
            prod_assets = []
            for slot, aid in cut_agent.color_images(product, spec["colorId"]):
                a = await repo.get_asset_for_user(conn, user_id, aid)
                if a:
                    a["slot"] = slot
                    prod_assets.append(a)
            mannequin_asset = None
            sel = project.get("selected_mannequin_id") or project.get("selectedMannequinId")
            if sel:
                for c in await repo.list_mannequin_cuts(conn, user_id, project_id):
                    if f"{c.get('candidate')}-{c.get('version')}" == sel and c.get("asset_id"):
                        mannequin_asset = await repo.get_asset_for_user(conn, user_id, str(c["asset_id"]))
                        break
            match_asset = None
            if spec["matchIds"] and spec["cutType"] in ("styling", "mirror"):
                m_aid = await repo.get_matching_item_asset(conn, spec["matchIds"][0])
                if m_aid:
                    match_asset = await repo.get_asset_for_user(conn, user_id, m_aid)
            mood_assets = []
            for rid in spec["refAssetIds"]:
                a = await repo.get_asset_for_user(conn, user_id, rid)  # 소유 검증 겸함
                if a:
                    mood_assets.append(a)

        if not prod_assets and mannequin_asset is None:
            await _fail("상품 사진을 찾을 수 없어요. 사진을 올렸는지 확인해 주세요.", {"error": "no_product_images"})
            return

        # 2) 바이트 다운로드 + 프롬프트 렌더
        images: list[InlineImage] = []
        if mannequin_asset is not None:
            images.append(await _load_asset_bytes(app, mannequin_asset))
        for a in prod_assets:
            images.append(await _load_asset_bytes(app, a))
        if match_asset is not None:
            images.append(await _load_asset_bytes(app, match_asset))
        for a in mood_assets:
            images.append(await _load_asset_bytes(app, a))
        manifest = cut_agent.build_manifest(
            prod_assets, has_mannequin=mannequin_asset is not None,
            has_match=match_asset is not None, mood_count=len(mood_assets))
        clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
        template = cut_agent.load_cut_template(s)
        prompt = cut_agent.render_cut_prompt(template, spec, product, analysis or {}, clothing_type, manifest)
        await _emit(pool, job_id, "progress", {
            "progress": 20, "phase": "inputs_loaded",
            "cutType": spec["cutType"], "images": len(images)})

        # 3) 단일 tier 1회 생성 (QC 게이팅 없음 — 재생성 루프가 안전망)
        model = resolve_model(s, s.cut_tier)
        try:
            res = await app.state.gemini.generate_content_image(
                model, prompt, images, s.mannequin_image_size,
                aspect_ratio=s.mannequin_aspect_ratio)
        except GeminiError as e:
            log.warning("cut job %s gemini error: %s", job_id, e)
            await _fail("이미지 생성에 실패했어요. 다시 시도해 주세요.",
                        {"error": "gemini_error", "detail": str(e)[:200]})
            return

        # 4) R2 저장 + 성공 종결
        ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
        asset_id = str(uuid.uuid4())
        key = ai_key(user_id, project_id, job_id, asset_id, ext)
        await asyncio.to_thread(app.state.r2.put_bytes, key, res.image, res.mime)
        w, h = _image_dims(res.image)
        async with pool.connection() as conn:
            out = await repo.finalize_cut_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id,
                image={"asset_id": asset_id, "bucket": s.r2_bucket, "key": key,
                       "mime": res.mime, "size": len(res.image), "width": w, "height": h},
                color_id=spec["colorId"], cut_type=spec["cutType"],
                reserved=reserved, charge=reserved, metadata=metadata)  # 성공 = 예약액 전액 확정 (설정 단가 추종, 예약 초과 차감 금지)
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            log.warning("cut job %s lost lease at finalize — dropped", job_id)
            try:
                await asyncio.to_thread(app.state.r2.delete, key)
            except Exception:
                log.warning("orphan R2 cleanup failed: %s", key)
    except Exception:
        log.exception("cut job %s crashed", job_id)
        await _fail("이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": "unexpected"})
