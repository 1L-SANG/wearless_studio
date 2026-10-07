"""`matching_cutout` 잡 이름을 유지하며 내 옷의 배경과 자세를 정리한다.

커스텀 매칭 등록이 커밋된 뒤 백그라운드로 돈다. 셀러 화면에는 이미 원본이 떠 있고,
이 잡은 그걸 시드 카탈로그 톤(회색 배경 컷)으로 조용히 교체한다. 아무것도 이 잡의
성공에 걸려 있지 않다. 실패해도 원본을 그대로 두고 등록과 선택은 계속 가능하다.
셀러 크레딧은 차감하지 않으며 Gemini 호출 실비는 image_usage에 기록한다.

MATCHING_FLATLAY on/full은 첫 원본 사진을 Gemini에 직접 넣어 한 장만 생성한다.
full이면 그 결과를 생성 입력 grid의 첫 칸에도 쓴다. 나머지 사진은 원본으로 유지한다.
실패하면 원본으로 끝내며 추가 생성이나 SAM 재시도는 없다.
off는 각 사진을 SAM으로 자르는 기존 방식이다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from app import repo
from app.services import garment_grid, matching_cutout, matching_flatlay, sam_client
from app.r2 import derived_key

log = logging.getLogger("wearless.matching_cutout")

SKIP_DISABLED = "matching_cutout_disabled"
SKIP_SAM = "sam_not_configured"
SKIP_NO_SOURCES = "no_source_assets"


class _CutoutFailed(Exception):
    """누끼 산출물이 없다. 원본을 그대로 두고 조용히 종결하기 위한 내부 신호."""


async def _clothing_type(pool, *, matching_item_id: str, user_id: str,
                         project_id: str) -> str | None:
    """플랫레이 프롬프트가 쓸 의류 종류(top|bottom). 조회 실패는 중립 명사로 흘린다.

    잡 payload 에 싣지 않고 여기서 읽는다 — 이 조회는 플래그가 켜졌을 때만 일어나므로
    off 경로는 쿼리 한 줄 늘지 않고, 플래그 이전에 큐잉된 잡도 그대로 처리된다.
    """
    try:
        async with pool.connection() as conn:
            row = await repo.get_matching_item_metadata(
                conn, matching_item_id, user_id, project_id)
        return (row or {}).get("clothing_type")
    except Exception:  # noqa: BLE001 - 명사 하나 때문에 재렌더를 포기하지 않는다
        log.warning("matching_flatlay clothing_type lookup failed item=%s",
                    matching_item_id, exc_info=True)
        return None


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
    flatlay_enabled = matching_flatlay.enabled(s)

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
    if not flatlay_enabled and not sam_client.configured(s):
        await skip(SKIP_SAM)
        return
    if not matching_item_id or not source_keys:
        await skip(SKIP_NO_SOURCES)
        return

    # 컷아웃~스왑 전 구간을 통째로 감싼다. R2·PIL·DB 어디서 터지든 잡이 running 으로
    # 고착되면 카드가 최대 30분(스테일 리스 회수 주기) 스켈레톤에 갇힌다 — 제약이
    # fail-open 이므로 무조건 종결시키고 원본을 그대로 둔다(2026-08-13 리뷰 I2).
    try:
        # on/full은 원본에서 Gemini 한 번으로 정리한다. off만 기존 SAM 누끼를 쓴다.
        cut_pngs: list[bytes] = []
        source_hashes: list[str] = []
        for key in source_keys:
            if flatlay_enabled:
                original = await asyncio.to_thread(r2.get_bytes, key)
                cut_pngs.append(original)
                source_hashes.append(hashlib.sha256(original).hexdigest())
                continue
            results = await sam_client.segment_garment(s, {"Front": key})
            view = results.get("Front")
            if view is None or not view.ready or not view.cutout_key:
                raise _CutoutFailed("no_cutout")
            # 신원의 뿌리는 소스다 — SAM 이 준 소스 해시, 없으면 소스 키.
            source_hashes.append(view.source_hash or key)
            cutout_bytes = await asyncio.to_thread(r2.get_bytes, view.cutout_key)
            flattened = await asyncio.to_thread(
                matching_cutout.flatten_on_bg, cutout_bytes)
            # 색 일치 게이트 — SAM 이 옷 대신 배경 조각을 딴 케이스(2026-08-15 실측:
            # 부츠컷 다리 사이 흰 문틈 → flat-lay 가 칼을 그림). 마스크 넓이로는 못
            # 거르고, 전경색이 원본 옷과 계열이 다르면 누끼 전체를 버리고 원본을
            # 유지한다(기존 fail-open 폴백 그대로 — 어떤 경우에도 등록은 살아 있다).
            original = await asyncio.to_thread(r2.get_bytes, key)
            agrees, color_metrics = await asyncio.to_thread(
                matching_cutout.cutout_color_agrees, original, flattened)
            if not agrees:
                log.warning("matching_cutout color gate rejected item=%s key=%s %s",
                            matching_item_id, key, color_metrics)
                raise _CutoutFailed("cutout_color_mismatch")
            cut_pngs.append(flattened)

        # 2) 파생 신원은 소스 지문 + 알고리즘 버전으로 결정론적으로 만든다 —
        #    재실행(스테일 리스 회수)이 같은 행·같은 R2 키로 수렴한다.
        fingerprint = matching_cutout.source_fingerprint(source_hashes)
        grid_asset_id = matching_cutout.derived_asset_id(
            role="grid", matching_item_id=matching_item_id, source_hash=fingerprint)
        grid_key = derived_key(user_id, project_id, grid_asset_id, "jpg")
        first_source_id = source_asset_ids[0] if source_asset_ids else None

        # 카드와 첫 생성 입력에 함께 쓸 펼친 사진을 한 번만 만든다.
        flat = None
        if flatlay_enabled:
            flat = await matching_flatlay.render_thumbnail(
                getattr(app.state, "gemini", None), settings=s, source_image=cut_pngs[0],
                clothing_type=await _clothing_type(
                    pool, matching_item_id=matching_item_id, user_id=user_id,
                    project_id=project_id))
            if flat is None:
                # 생성 실패를 SAM 대기나 추가 유료 생성으로 바꾸지 않는다.
                raise _CutoutFailed("flatlay_failed")
        if flat is not None:
            thumb_asset_id = matching_flatlay.derived_asset_id(
                matching_item_id=matching_item_id, source_hash=fingerprint)
            thumb_bytes = flat.image
            thumb_meta = matching_flatlay.metadata_for(
                source_hash=fingerprint, source_asset_id=first_source_id,
                matching_item_id=matching_item_id, model=flat.model)
        else:
            thumb_asset_id = matching_cutout.derived_asset_id(
                role="thumb", matching_item_id=matching_item_id, source_hash=fingerprint)
            thumb_bytes = await asyncio.to_thread(
                matching_cutout.encode_thumbnail, cut_pngs[0])
            thumb_meta = matching_cutout.metadata_for(
                source_hash=fingerprint, source_asset_id=first_source_id,
                matching_item_id=matching_item_id,
                purpose=matching_cutout.CUTOUT_PURPOSE)
        thumb_key = derived_key(user_id, project_id, thumb_asset_id, "jpg")

        # full은 첫 칸만 생성한 1K 원본으로 바꾼다. 카드용 512px를 넣지 않는다.
        # on은 원본 사진 grid, off는 SAM 누끼 grid다. 다른 칸은 다시 생성하지 않는다.
        grid_flatlay = flat is not None and matching_flatlay.grid_enabled(s)
        grid_inputs = [flat.raw, *cut_pngs[1:]] if grid_flatlay else cut_pngs
        grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid, grid_inputs)
        await asyncio.to_thread(r2.put_bytes, thumb_key, thumb_bytes, "image/jpeg")
        await asyncio.to_thread(r2.put_bytes, grid_key, grid_bytes, "image/jpeg")

        # 5) asset 행 생성 + 매칭 아이템 스왑 (원자적)
        if not origin_grid_asset_id:
            # 이 변경 이전에 큐잉된 잡의 payload 에는 gridAssetId 가 없다. 그때의 원본
            # grid 는 아이템의 현재 image_asset_id 다(스왑 전이므로) — 이월하지 않으면
            # "내 옷 삭제" 가 그 grid 행과 R2 객체를 영구히 남긴다(재리뷰 M-3).
            async with pool.connection() as conn:
                origin_grid_asset_id = await repo.get_matching_item_asset(
                    conn, matching_item_id, user_id, project_id)
        cleanup_ids = [*source_asset_ids]
        if origin_grid_asset_id and origin_grid_asset_id not in cleanup_ids:
            cleanup_ids.append(origin_grid_asset_id)
        if flatlay_enabled:
            # 썸네일 신원이 재렌더 성패로 갈리므로, 재실행이 다른 분기를 타면 이전 실행의
            # 썸네일은 아이템에서 떨어져 나간다. 삭제 경로는 현재 thumbnail_asset_id 와
            # 이 목록만 훑으므로(routes.py:1379-1381), 두 후보를 모두 실어 둬야 어느 쪽이
            # 남더라도 "내 옷 삭제" 가 회수한다(리뷰 I2).
            for candidate in (
                matching_flatlay.derived_asset_id(
                    matching_item_id=matching_item_id, source_hash=fingerprint),
                matching_cutout.derived_asset_id(
                    role="thumb", matching_item_id=matching_item_id,
                    source_hash=fingerprint),
            ):
                if candidate not in cleanup_ids:
                    cleanup_ids.append(candidate)
        async with pool.connection() as conn:
            await repo.create_asset(
                conn, asset_id=thumb_asset_id, user_id=user_id, project_id=project_id,
                source="derived", bucket=s.r2_bucket, key=thumb_key, mime="image/jpeg",
                size=len(thumb_bytes), original_filename=None, metadata=thumb_meta)
            # grid(=image_asset_id) 는 마네킹 생성 입력이자 Task 5 가 상태를 판정하는 asset —
            # metadata.type == "matchingCutout" 이 없으면 상태가 계속 미완료로 보인다.
            # sourceAssetIds 는 삭제 경로가 원본을 회수하는 유일한 통로다.
            grid_meta = matching_cutout.metadata_for(
                source_hash=fingerprint,
                source_asset_id=origin_grid_asset_id or first_source_id,
                matching_item_id=matching_item_id,
                purpose=matching_cutout.GRID_PURPOSE,
                source_asset_ids=cleanup_ids)
            if flatlay_enabled:
                grid_meta["producer"] = matching_flatlay.PRODUCER
                grid_meta["algorithmVersion"] = matching_flatlay.ALGORITHM_VERSION
            if grid_flatlay:
                # front 칸이 재렌더본임을 provenance 로 남긴다 — 착장 결과를 조사할 때
                # "생성이 뭘 보고 그렸나"를 asset 만 보고 답할 수 있어야 한다.
                grid_meta["flatlayFront"] = True
                grid_meta["flatlayModel"] = flat.model
            await repo.create_asset(
                conn, asset_id=grid_asset_id, user_id=user_id, project_id=project_id,
                source="derived", bucket=s.r2_bucket, key=grid_key, mime="image/jpeg",
                size=len(grid_bytes), original_filename=None,
                metadata=grid_meta)
            swapped = await repo.swap_matching_item_assets(
                conn, matching_item_id=matching_item_id, project_id=project_id,
                thumbnail_asset_id=thumb_asset_id, image_asset_id=grid_asset_id)
            if not swapped:
                # 누끼가 도는 사이 셀러가 "내 옷"을 지웠다. 그대로 커밋하면 아무도
                # 도달할 수 없는 파생 asset 2개가 남는다(재리뷰 M-4) — 커밋 전에
                # 트랜잭션째 버린다(예외가 커넥션 컨텍스트를 나가며 롤백).
                raise _CutoutFailed("item_gone")
            await conn.commit()
    except sam_client.SamUnavailable as exc:
        # done + unavailable — error 가 아니다. error 로 적으면 멱등키가 이 실패 잡에 묶여 그
        # 옷은 영영 누끼가 없다 — 마네킹 생성이 셀러 원본(접힌 사진)을 그대로 입력으로 쓴다
        # (2026-08-21). 다음 세대는 sam_retry_pusher 가 건다. 원본 자산은 여기서도 그대로다.
        await finish("done", {"state": "unavailable", "reason": str(exc),
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

    detail = {"state": "ready", "matchingItemId": matching_item_id,
              "thumbnailAssetId": thumb_asset_id, "imageAssetId": grid_asset_id}
    if flatlay_enabled:
        # 플래그가 켜진 동안만 적는다 — off 인 프로덕션의 결과 payload 는 오늘 그대로다.
        detail["flatlay"] = flat is not None
        detail["flatlayGrid"] = grid_flatlay
    await finish("done", detail)
