"""`matching_cutout` — 커스텀 매칭 의류의 배경을 SAM2 로 제거한다.

커스텀 매칭 등록이 커밋된 뒤 백그라운드로 돈다. 셀러 화면에는 이미 원본이 떠 있고,
이 잡은 그걸 시드 카탈로그 톤(회색 배경 컷)으로 조용히 교체한다. 아무것도 이 잡의
성공에 걸려 있지 않다 — SAM 미설정·다운·오선택 어떤 경우도 원본을 그대로 두고,
매칭 등록·선택은 내내 가능하다. 무과금·이미지 생성 없음.

SAM 서비스는 view 를 Front/Back 으로 강제하므로, 각 매칭 원본을 `"Front"` 로 순차
호출해 우회한다(뷰 이름은 SAM 캐시 키의 일부일 뿐, 매칭에선 의미 없다).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app import repo
from app.services import garment_grid, matching_cutout, sam_client
from app.r2 import derived_key

log = logging.getLogger("wearless.matching_cutout")

SKIP_DISABLED = "matching_cutout_disabled"
SKIP_SAM = "sam_not_configured"
SKIP_NO_SOURCES = "no_source_assets"


async def run_matching_cutout_job(app, job: dict) -> None:
    pool = app.state.pool
    r2 = app.state.r2
    job_id, project_id = job["id"], job["project_id"]
    user_id, lease_token = job["user_id"], job["lease_token"]
    # app.state.settings (load_settings() 재호출 아님) — 디스패처가 앱 기동 시 로드한
    # 설정 그대로 써야 테스트·런타임 모두에서 주입된 설정을 실제로 존중한다.
    s = app.state.settings
    payload = job.get("payload") or {}
    matching_item_id = payload.get("matchingItemId")
    source_keys = payload.get("sourceKeys") or []

    async def finish(status: str, detail: dict) -> None:
        async with pool.connection() as conn:
            await repo.finalize_uncharged_job(
                conn, job_id=job_id, lease_token=lease_token, status=status, result=detail)
            await conn.commit()
        log.info("matching_cutout job=%s project=%s %s %s",
                 job_id, project_id, status, detail.get("state"))

    async def skip(reason: str) -> None:
        await finish("done", {"state": "skipped", "reason": reason,
                              "matchingItemId": matching_item_id})

    if getattr(s, "matching_cutout", "off") != "on":
        await skip(SKIP_DISABLED)
        return
    if not sam_client.configured(s):
        await skip(SKIP_SAM)
        return
    if not matching_item_id or not source_keys:
        await skip(SKIP_NO_SOURCES)
        return

    # 1) 각 원본을 누끼 → 회색배경 합성. view 는 Front 로 우회.
    cut_pngs: list[bytes] = []
    try:
        for key in source_keys:
            results = await sam_client.segment_garment(s, {"Front": key})
            view = results.get("Front")
            if view is None or not view.ready or not view.cutout_key:
                await finish("done", {"state": "failed", "reason": "no_cutout",
                                      "matchingItemId": matching_item_id})
                return
            cutout_bytes = await asyncio.to_thread(r2.get_bytes, view.cutout_key)
            cut_pngs.append(await asyncio.to_thread(
                matching_cutout.flatten_on_bg, cutout_bytes))
    except sam_client.SamUnavailable as exc:
        await finish("error", {"state": "unavailable", "reason": str(exc),
                               "matchingItemId": matching_item_id})
        return

    # 2) 회색배경 컷을 각각 derived asset 으로 저장 (썸네일 = 첫 장)
    source_assets: list[tuple[str, str]] = []
    for png in cut_pngs:
        asset_id = str(uuid.uuid4())
        key = derived_key(user_id, project_id, asset_id, "png")
        await asyncio.to_thread(r2.put_bytes, key, png, "image/png")
        source_assets.append((asset_id, key))
    thumb_asset_id, _thumb_key = source_assets[0]

    # 3) 누끼본으로 grid 재합성 (마네킹 생성 입력)
    grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid, cut_pngs)
    grid_asset_id = str(uuid.uuid4())
    grid_key = derived_key(user_id, project_id, grid_asset_id, "jpg")
    await asyncio.to_thread(r2.put_bytes, grid_key, grid_bytes, "image/jpeg")

    # 4) asset 행 생성 + 매칭 아이템 스왑 (원자적)
    async with pool.connection() as conn:
        for (asset_id, key), png in zip(source_assets, cut_pngs):
            await repo.create_asset(
                conn, asset_id=asset_id, user_id=user_id, project_id=project_id,
                source="derived", bucket=s.r2_bucket, key=key, mime="image/png",
                size=len(png), original_filename=None)
        # grid(=image_asset_id) 는 마네킹 생성 입력이자 Task 5 가 상태를 판정하는 asset —
        # metadata.type == "matchingCutout" 이 없으면 상태가 계속 미완료로 보인다.
        await repo.create_asset(
            conn, asset_id=grid_asset_id, user_id=user_id, project_id=project_id,
            source="derived", bucket=s.r2_bucket, key=grid_key, mime="image/jpeg",
            size=len(grid_bytes), original_filename=None,
            metadata=matching_cutout.metadata_for(
                source_hash=None, source_asset_id=grid_asset_id,
                matching_item_id=matching_item_id))
        await repo.swap_matching_item_assets(
            conn, matching_item_id=matching_item_id, project_id=project_id,
            thumbnail_asset_id=thumb_asset_id, image_asset_id=grid_asset_id)
        await conn.commit()

    await finish("done", {"state": "ready", "matchingItemId": matching_item_id,
                          "thumbnailAssetId": thumb_asset_id, "imageAssetId": grid_asset_id})
