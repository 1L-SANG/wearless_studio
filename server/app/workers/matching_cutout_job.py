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

from app import repo
from app.services import garment_grid, matching_cutout, sam_client
from app.r2 import derived_key

log = logging.getLogger("wearless.matching_cutout")

SKIP_DISABLED = "matching_cutout_disabled"
SKIP_SAM = "sam_not_configured"
SKIP_NO_SOURCES = "no_source_assets"


class _CutoutFailed(Exception):
    """누끼 산출물이 없다. 원본을 그대로 두고 조용히 종결하기 위한 내부 신호."""


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
    # 스왑 뒤에도 "내 옷 삭제" 가 원본을 회수할 수 있게, 원본 업로드·원본 grid id 를
    # 파생 grid metadata 에 그대로 이월한다(삭제 경로는 image asset metadata 만 본다).
    source_asset_ids = [a for a in (payload.get("sourceAssetIds") or []) if a]
    origin_grid_asset_id = payload.get("gridAssetId")

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

    # 컷아웃~스왑 전 구간을 통째로 감싼다. R2·PIL·DB 어디서 터지든 잡이 running 으로
    # 고착되면 카드가 최대 30분(스테일 리스 회수 주기) 스켈레톤에 갇힌다 — 제약이
    # fail-open 이므로 무조건 종결시키고 원본을 그대로 둔다(2026-08-13 리뷰 I2).
    try:
        # 1) 각 원본을 누끼 → 회색배경 합성. view 는 Front 로 우회.
        cut_pngs: list[bytes] = []
        source_hashes: list[str] = []
        for key in source_keys:
            results = await sam_client.segment_garment(s, {"Front": key})
            view = results.get("Front")
            if view is None or not view.ready or not view.cutout_key:
                raise _CutoutFailed("no_cutout")
            # 신원의 뿌리는 소스다 — SAM 이 준 소스 해시, 없으면 소스 키.
            source_hashes.append(view.source_hash or key)
            cutout_bytes = await asyncio.to_thread(r2.get_bytes, view.cutout_key)
            cut_pngs.append(await asyncio.to_thread(
                matching_cutout.flatten_on_bg, cutout_bytes))

        # 2) 파생 신원은 소스 지문 + 알고리즘 버전으로 결정론적으로 만든다 —
        #    재실행(스테일 리스 회수)이 같은 행·같은 R2 키로 수렴한다.
        fingerprint = matching_cutout.source_fingerprint(source_hashes)
        thumb_asset_id = matching_cutout.derived_asset_id(
            role="thumb", matching_item_id=matching_item_id, source_hash=fingerprint)
        grid_asset_id = matching_cutout.derived_asset_id(
            role="grid", matching_item_id=matching_item_id, source_hash=fingerprint)
        thumb_key = derived_key(user_id, project_id, thumb_asset_id, "jpg")
        grid_key = derived_key(user_id, project_id, grid_asset_id, "jpg")

        # 3) 카드용 축소 JPEG + 누끼본 grid 재합성(마네킹 생성 입력).
        #    원본 해상도 컷은 grid 입력으로만 쓰고 저장하지 않는다 — 130px 카드에
        #    2048px 무압축 PNG 를 내려보내지 않는다(리뷰 I6).
        thumb_bytes = await asyncio.to_thread(
            matching_cutout.encode_thumbnail, cut_pngs[0])
        grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid, cut_pngs)
        await asyncio.to_thread(r2.put_bytes, thumb_key, thumb_bytes, "image/jpeg")
        await asyncio.to_thread(r2.put_bytes, grid_key, grid_bytes, "image/jpeg")

        # 4) asset 행 생성 + 매칭 아이템 스왑 (원자적)
        cleanup_ids = [*source_asset_ids]
        if origin_grid_asset_id and origin_grid_asset_id not in cleanup_ids:
            cleanup_ids.append(origin_grid_asset_id)
        first_source_id = source_asset_ids[0] if source_asset_ids else None
        async with pool.connection() as conn:
            await repo.create_asset(
                conn, asset_id=thumb_asset_id, user_id=user_id, project_id=project_id,
                source="derived", bucket=s.r2_bucket, key=thumb_key, mime="image/jpeg",
                size=len(thumb_bytes), original_filename=None,
                metadata=matching_cutout.metadata_for(
                    source_hash=fingerprint, source_asset_id=first_source_id,
                    matching_item_id=matching_item_id,
                    purpose=matching_cutout.CUTOUT_PURPOSE))
            # grid(=image_asset_id) 는 마네킹 생성 입력이자 Task 5 가 상태를 판정하는 asset —
            # metadata.type == "matchingCutout" 이 없으면 상태가 계속 미완료로 보인다.
            # sourceAssetIds 는 삭제 경로가 원본을 회수하는 유일한 통로다.
            await repo.create_asset(
                conn, asset_id=grid_asset_id, user_id=user_id, project_id=project_id,
                source="derived", bucket=s.r2_bucket, key=grid_key, mime="image/jpeg",
                size=len(grid_bytes), original_filename=None,
                metadata=matching_cutout.metadata_for(
                    source_hash=fingerprint,
                    source_asset_id=origin_grid_asset_id or first_source_id,
                    matching_item_id=matching_item_id,
                    purpose=matching_cutout.GRID_PURPOSE,
                    source_asset_ids=cleanup_ids))
            await repo.swap_matching_item_assets(
                conn, matching_item_id=matching_item_id, project_id=project_id,
                thumbnail_asset_id=thumb_asset_id, image_asset_id=grid_asset_id)
            await conn.commit()
    except sam_client.SamUnavailable as exc:
        await finish("error", {"state": "unavailable", "reason": str(exc),
                               "matchingItemId": matching_item_id})
        return
    except _CutoutFailed as exc:
        await finish("done", {"state": "failed", "reason": str(exc),
                              "matchingItemId": matching_item_id})
        return
    except Exception as exc:  # noqa: BLE001 - 어떤 실패도 카드를 스켈레톤에 가두지 않는다
        log.exception("matching_cutout failed job=%s project=%s item=%s",
                      job_id, project_id, matching_item_id)
        await finish("done", {"state": "failed", "reason": "unexpected_error",
                              "error": type(exc).__name__,
                              "matchingItemId": matching_item_id})
        return

    await finish("done", {"state": "ready", "matchingItemId": matching_item_id,
                          "thumbnailAssetId": thumb_asset_id, "imageAssetId": grid_asset_id})
