"""`editor_garment_mask` — produce the Tone Editor's garment mask for a generated cut.

Runs AFTER the mannequin cut is already persisted and already on the seller's screen. Nothing
here is on anyone's critical path: if SAM is unconfigured, down, slow, or simply cannot find the
garment, the cut stays exactly as good as it was and the only consequence is that the tone
sliders are unavailable for it.

That is why this worker reports `skipped`/`failed` states rather than raising, and why it is a
separate job rather than a step inside `mannequin`: a mask failure must never turn a paid,
successful generation into a red job badge.

No credits, no image generation, no Gemini, no VLM. One HTTP call to the internal SAM service.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app import repo
from app.agents import mannequin
from app.config import load_settings
from app.services import canonical_reference, editor_garment_mask, sam_client

log = logging.getLogger("wearless.editor_garment_mask")

SKIP_DISABLED = "tone_editor_disabled"
SKIP_SAM_UNCONFIGURED = "sam_not_configured"
SKIP_NO_CUT = "cut_unavailable"
SKIP_NO_BASE = "missing_base_reference"
#: 내준 마스크가 코디 의류 위에 앉았다 → 마스크를 기록하지 않는다. 셀러는 그 컷에서 색감
#: 조정을 못 하게 되지만, 파는 옷이 아닌 코디 옷의 색을 바꿔 발행할 일은 없다.
FAIL_ON_MATCHING = "mask_on_matching_garment"
#: 마스크를 읽지 못해 "코디 위가 아님"을 확인할 수 없었다 → 기록하지 않고 재시도로 넘긴다.
FAIL_UNVERIFIED = "mask_unverified"


def _base_gender(cut_metadata, analysis: dict, clothing_type) -> str:
    """Which base mannequin this cut was dressed on.

    The cut's own generation metadata is the truth when present — analysis can change afterwards
    and re-deriving would then pick a base the cut was never made from, which would make the
    difference map describe a different body.
    """
    if isinstance(cut_metadata, dict):
        g = cut_metadata.get("profileGender")
        if g in ("men", "women"):
            return g
    return mannequin.select_base_gender(analysis, clothing_type)


async def _product_reference_key(conn, *, project_id: str, product: dict) -> str | None:
    """이 상품의 현재 앞면 사진으로 만든 캐노니컬 컷아웃 키. 조회 실패는 None 으로 강등한다.

    앞면 하나만 쓴다. 마네킹컷은 정면 착장이라 뒷면 컷아웃은 같은 질문에 답하지 못한다.
    """
    front = next((aid for slot, aid in mannequin.base_color_images(product or {})
                  if slot == "Front" and aid), None)
    if not front:
        return None
    try:
        return await canonical_reference.current_key(
            conn, project_id=project_id, view="Front", source={"id": front, "hash": None})
    except Exception:  # noqa: BLE001 - 보조 근거 조회 실패가 마스크를 막지 않는다
        log.warning("product reference lookup failed project=%s", project_id, exc_info=True)
        return None


async def run_editor_garment_mask_job(app, job: dict) -> None:
    """Worker entrypoint. Signature matches the other kinds in `_WORKERS`."""
    pool = app.state.pool
    job_id, project_id = job["id"], job["project_id"]
    user_id, lease_token = job["user_id"], job["lease_token"]
    s = load_settings()
    payload = job.get("payload") or {}
    cut_id = payload.get("cutId")

    async def finish(status: str, detail: dict) -> None:
        async with pool.connection() as conn:
            await repo.finalize_uncharged_job(
                conn, job_id=job_id, lease_token=lease_token, status=status, result=detail)
            await conn.commit()
        log.info("editor_garment_mask job=%s project=%s cut=%s %s %s",
                 job_id, project_id, cut_id, status, detail.get("state"))

    async def skip(reason: str, extra: dict | None = None) -> None:
        # `done`, not `error`: not producing a mask is a degraded state, not a broken job.
        await finish("done", {"state": "skipped", "reason": reason, "cutId": cut_id,
                              **(extra or {})})

    if getattr(s, "mannequin_tone_editor", "off") != "on":
        await skip(SKIP_DISABLED)
        return
    if not sam_client.configured(s):
        await skip(SKIP_SAM_UNCONFIGURED)
        return
    if not isinstance(cut_id, str) or not cut_id:
        await skip(SKIP_NO_CUT)
        return

    async with pool.connection() as conn:
        cut = await repo.get_mannequin_cut_asset(conn, user_id, project_id, cut_id)
        product = await repo.get_product(conn, project_id) or {}
        analysis = await repo.get_analysis(conn, project_id) or {}
        clothing_type = (product.get("clothing_type") or product.get("clothingType")
                         or analysis.get("clothingType") or "top")
        # 라우트의 상태 판정과 **같은 함수**를 쓴다 — 갈리면 영구 재큐가 된다.
        matching_side = await editor_garment_mask.matching_side_for_project(
            conn, user_id=user_id, project_id=project_id)
        gender = _base_gender(payload.get("cutMetadata"), analysis, clothing_type)
        base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                         else s.base_mannequin_women_asset_id)
        base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                      if base_asset_id else None)
        # 셀러가 올린 주상품 앞면 사진의 배경 제거 컷아웃. 있으면 "파는 옷이 어떻게 생겼나"를
        # 채점이 위치가 아니라 생김새로 답할 수 있다. 없으면(전처리 미완·SAM 미설정·사진 교체
        # 직후) 그냥 없는 채로 간다 — 보조 근거를 기다리느라 마스크를 미루지 않는다.
        product_key = await _product_reference_key(conn, project_id=project_id, product=product)

    if cut is None or not cut.get("r2_key"):
        await skip(SKIP_NO_CUT, {"cutId": cut_id})
        return
    if base_asset is None or not base_asset.get("r2_key"):
        await skip(SKIP_NO_BASE, {"cutId": cut_id, "baseGender": gender})
        return

    t0 = time.monotonic()
    try:
        result = await sam_client.segment_worn_garment(
            s, source_key=cut["r2_key"], base_key=base_asset["r2_key"],
            clothing_type=clothing_type, sub_category=analysis.get("subCategory"),
            matching_side=matching_side, product_key=product_key)
    except sam_client.SamUnavailable as exc:
        # Bounded dispatcher retry covers transient outages. The mask key is deterministic, so a
        # retry after a partial failure is cheap — a finished mask is served from R2.
        await finish("error", {"state": "unavailable", "reason": str(exc), "cutId": cut_id})
        return

    latency = round(time.monotonic() - t0, 2)
    if not result.ready:
        await finish("done", {"state": "failed", "cutId": cut_id,
                              "code": result.code, "message": result.message,
                              "latencySeconds": latency})
        return

    # 코디 의류가 함께 착장된 컷이면, 내준 마스크가 정말 주상품 위인지 **여기서 픽셀로** 본다.
    # 서비스 쪽 채점·veto 와 겹치는 검사지만 겹쳐야 한다: SAM 서비스는 별도 배포라 구버전이
    # 돌 수 있고(그때는 matchingSide 를 아예 무시한다), 캐시 히트로 옛 규칙의 마스크가 그대로
    # 돌아올 수도 있다. 보장은 배포 순서와 캐시에 의존해선 안 된다.
    band = editor_garment_mask.matching_core_band(clothing_type, matching_side)
    match_share = None
    if band:
        try:
            png = await asyncio.to_thread(app.state.r2.get_bytes, result.mask_key)
        except Exception:  # noqa: BLE001 - 읽기 실패는 일시적일 수 있다 → 재시도로 넘긴다
            log.warning("mask fetch for guard failed project=%s cut=%s key=%s",
                        project_id, cut_id, result.mask_key, exc_info=True)
            png = b""
        match_share = editor_garment_mask.band_mass_fraction(png, band)
        if match_share is None:
            # 재볼 수 없는 마스크는 기록하지 않는다 — "검증했다"는 도장을 못 찍는 마스크를
            # 남기면 그 컷은 확인되지 않은 채 영구히 보장된 것으로 취급된다. error 로 끝내
            # 디스패처의 유한 재시도에 맡기고, 재시도가 다 떨어지면 화면은 failed 로 간다.
            await finish("error", {"state": "unverified", "cutId": cut_id,
                                   "code": FAIL_UNVERIFIED, "matchingSide": matching_side,
                                   "maskKey": result.mask_key,
                                   "latencySeconds": latency})
            return
        if match_share > editor_garment_mask.MATCH_ZONE_MAX:
            await finish("done", {"state": "failed", "cutId": cut_id,
                                  "code": FAIL_ON_MATCHING, "matchingSide": matching_side,
                                  "matchShare": match_share, "band": list(band),
                                  "maskKey": result.mask_key,
                                  "algorithmVersion": result.algorithm_version,
                                  "latencySeconds": latency})
            return

    async with pool.connection() as conn:
        row = await editor_garment_mask.record(
            conn, user_id=user_id, project_id=project_id, cut_id=cut_id,
            source_asset_id=str(cut.get("id") or ""), result=result,
            category=clothing_type, sub_category=analysis.get("subCategory"),
            matching_side=matching_side, match_share=match_share)
        await conn.commit()

    await finish("done", {"state": "ready", "cutId": cut_id,
                          "maskAssetId": (row or {}).get("id"),
                          "sourceHash": result.source_hash,
                          "algorithmVersion": result.algorithm_version,
                          "matchingSide": matching_side, "matchShare": match_share,
                          "selectedRank": result.selected_rank,
                          "vetoedAttempts": result.vetoed_attempts,
                          "productMatch": result.product_match,
                          "productKey": product_key,
                          "areaFrac": result.area_frac, "cached": result.cached,
                          "latencySeconds": latency})
