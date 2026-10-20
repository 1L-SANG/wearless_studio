"""Product flatlay for display and opt-in patterned mannequin generation.

Canonical files are globally shared; only the project-scoped tidy PNG is public.
Failures live in sam_preprocess.result.productTidy, independently of committed cutouts.
"""
from __future__ import annotations

import asyncio
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image

from app import image_usage, repo
from app.agents import mannequin
from app.r2 import derived_key
from app.services import canonical_reference, matching_cutout, qwen_flatlay, sam_retry
from app.services.segmentation_types import SamUnavailable

log = logging.getLogger(__name__)
ALGORITHM_VERSION = "product-tidy-v1-qwen"
ENQUEUE_VERSION = "required-v2"
ACTIVE_WINDOW_MINUTES = 10


def preprocess_key(project_id: str, product: dict, *, enabled: bool) -> str | None:
    key = canonical_reference.preprocess_idempotency_key(project_id, product)
    # Queue protocol is separate from image identity: v1 jobs could finish on an
    # incapable worker without producing tidy. Do not join those completed rows.
    return f"{key}:{ALGORITHM_VERSION}:{ENQUEUE_VERSION}" if key and enabled else key


def asset_id(user_id: str, project_id: str, source_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f"{user_id}:{project_id}:{source_hash}:{ALGORITHM_VERSION}"))


def board_asset_id(user_id: str, project_id: str, source_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f"{asset_id(user_id, project_id, source_hash)}:board"))


def source_is_bound(metadata: dict, source_id: str) -> bool:
    aliases = metadata.get("sourceAssetIds") or []
    return (metadata.get("sourceAssetId") == source_id
            or isinstance(aliases, list) and source_id in aliases)


async def ready_front_asset(conn, *, user_id: str, project_id: str,
                            source_id: str, source_hash: str) -> dict | None:
    """Read an already committed tidy asset bound to this exact Front upload.

    Never enqueue, render or wait for preprocessing. Generation supplies the hash
    of the loaded original bytes; display uses SAM's recorded source hash.
    """
    row = await repo.get_asset_for_user(conn, user_id, asset_id(user_id, project_id, source_hash))
    meta = (row or {}).get("metadata") or {}
    if (row and row.get("r2_key") and meta.get("canonicalType") == "product_tidy"
            and meta.get("view") == "Front"
            and source_is_bound(meta, source_id) and meta.get("sourceHash") == source_hash
            and meta.get("algorithmVersion") == ALGORITHM_VERSION):
        return row
    return None


async def bind_source(conn, *, user_id: str, asset_id: str, source_id: str,
                      source_hash: str) -> None:
    """Keep verified upload aliases without overwriting the asset's original provenance.

    SAM confirms the hash before this write. Append in SQL so concurrent jobs cannot
    lose one another's IDs. Canonical bindings commit before the optional Qwen stage.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "update assets set metadata = jsonb_set(metadata, '{sourceAssetIds}', "
            "coalesce(metadata->'sourceAssetIds', '[]'::jsonb) || jsonb_build_array(%s::text)) "
            "where id = %s and user_id = %s and metadata->>'sourceHash' = %s "
            "and metadata->>'sourceAssetId' is distinct from %s "
            "and not jsonb_exists(coalesce(metadata->'sourceAssetIds', '[]'::jsonb), %s)",
            (source_id, asset_id, user_id, source_hash, source_id, source_id))


async def load_front(conn, *, user_id: str, product: dict, project_id: str | None = None,
                     include_display_input: bool = False) -> tuple[dict | None, dict | None]:
    """Resolve the current upload ID and SAM's recorded hash using DB metadata only."""
    source_id = next((aid for view, aid in mannequin.base_color_images(product)
                      if view == "Front" and aid), None)
    if not source_id:
        return None, None
    source = await repo.get_asset_for_user(conn, user_id, source_id)
    if not source or not source.get("r2_key") or source.get("source") != "upload":
        return None, None
    kinds = "metadata->>'canonicalType' = 'canonical_cutout'"
    params = [user_id]
    if include_display_input:
        kinds = ("(" + kinds + " or (metadata->>'canonicalType' = 'product_tidy_input' "
                 "and project_id = %s))")
        params.append(project_id)
    params.extend((source_id, source_id))
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where user_id = %s and source = 'derived' and deleted_at is null "
            "and " + kinds + " "
            "and metadata->>'view' = 'Front' "
            "and (metadata->>'sourceAssetId' = %s or jsonb_exists(metadata->'sourceAssetIds', %s)) "
            "order by created_at desc limit 1", tuple(params))
        cutout = await cur.fetchone()
    return {"id": source_id, "key": source["r2_key"],
            "hash": ((cutout or {}).get("metadata") or {}).get("sourceHash")}, cutout


def _png_size(image: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image)) as decoded:
        decoded.load()
        if (decoded.format != "PNG" or decoded.mode != "RGBA"
                or decoded.getchannel("A").getbbox() is None):
            raise ValueError("invalid tidy PNG")
        return decoded.size


async def _owned_display_input(app, job: dict, source: dict, cutout: dict) -> dict:
    """Copy a verified shared SAM result without borrowing another seller's asset row.

    This metadata type is display-only: canonical generation consumers cannot load it.
    """
    uid, pid = job["user_id"], job["project_id"]
    aid = str(uuid.uuid5(uuid.NAMESPACE_URL,
                        f"{uid}:{pid}:display-input:{source['hash']}:{ALGORITHM_VERSION}"))
    async with app.state.pool.connection() as conn:
        existing = await repo.get_asset_for_user(conn, uid, aid)
    if existing:
        return existing
    key = derived_key(uid, pid, aid, "png")
    image = await asyncio.to_thread(app.state.r2.get_bytes, cutout["r2_key"])
    width, height = await asyncio.to_thread(_png_size, image)
    metadata = {"canonicalType": "product_tidy_input", "view": "Front",
                "sourceAssetId": source["id"], "sourceHash": source["hash"],
                "algorithmVersion": ALGORITHM_VERSION, "r2Key": key,
                "width": width, "height": height}
    await asyncio.to_thread(app.state.r2.put_bytes, key, image, "image/png")
    async with app.state.pool.connection() as conn:
        row = await repo.create_asset(
            conn, asset_id=aid, user_id=uid, project_id=pid, source="derived",
            bucket=app.state.settings.r2_bucket, key=key, mime="image/png", size=len(image),
            original_filename=None, metadata=metadata)
        await conn.commit()
    return {**row, "metadata": metadata}


async def produce(app, job: dict, *, source: dict, cutout: dict) -> dict:
    """Called only AFTER the canonical transaction commits. Ready assets are reused."""
    pool, r2 = app.state.pool, app.state.r2
    uid, pid = job["user_id"], job["project_id"]
    aid = asset_id(uid, pid, source["hash"])
    key = derived_key(uid, pid, aid, "png")
    detail = {"sourceHash": source["hash"], "algorithmVersion": ALGORITHM_VERSION}
    try:
        if not cutout.get("id"):
            cutout = await _owned_display_input(app, job, source, cutout)
        # Separate from canonical.record's transaction: an optional binding failure
        # must never roll back Front/Back, and a Qwen failure must not lose this binding.
        async with pool.connection() as conn:
            await bind_source(conn, user_id=uid, asset_id=cutout["id"],
                              source_id=source["id"], source_hash=source["hash"])
            existing = await repo.get_asset_for_user(conn, uid, aid)
            cached = existing and (existing.get("metadata") or {}).get("canonicalType") == "product_tidy"
            if cached and not source_is_bound(existing["metadata"], source["id"]):
                await bind_source(conn, user_id=uid, asset_id=aid,
                                  source_id=source["id"], source_hash=source["hash"])
            await conn.commit()
    except Exception:
        return {**detail, "state": "unavailable", "reason": "binding_unavailable"}
    if cached:
        return {**detail, "state": "ready", "assetId": aid}
    try:
        try:
            stored = await asyncio.to_thread(r2.head, key)
            image = await asyncio.to_thread(r2.get_bytes, key) if stored else None
            if image is None:
                cutout_png = await asyncio.to_thread(r2.get_bytes, cutout["r2_key"])
        except Exception:
            return {**detail, "state": "unavailable", "reason": "storage_unavailable"}
        if image is None:
            # The dispatcher supplies job scope; explicit IDs cover direct invocations too.
            with image_usage.job_scope(job_id=job["id"], user_id=uid, stage="product_tidy"):
                image = await qwen_flatlay.render(app.state.settings, cutout_png, transparent=True)
        width, height = await asyncio.to_thread(_png_size, image)
        metadata = {
            "canonicalType": "product_tidy", "view": "Front", "sourceAssetId": source["id"],
            "sourceHash": source["hash"], "cutoutAssetId": cutout["id"],
            "producer": "fal-qwen-product-tidy", "algorithmVersion": ALGORITHM_VERSION,
            "r2Key": key, "width": width, "height": height,
        }
        if not stored:
            try:
                await asyncio.to_thread(r2.put_bytes, key, image, "image/png")
            except Exception:
                return {**detail, "state": "unavailable", "reason": "storage_unavailable"}
        # Keep the full canvas at its existing identity for mannequin references.
        # Persist it first so a board/storage/DB retry never repeats Qwen.
        board = await asyncio.to_thread(matching_cutout.encode_board_cutout, image)
        board_width, board_height = await asyncio.to_thread(_png_size, board)
        bid = board_asset_id(uid, pid, source["hash"])
        board_key = derived_key(uid, pid, bid, "png")
        board_meta = {**metadata, "canonicalType": "product_tidy_board", "tidyAssetId": aid,
                      "r2Key": board_key, "width": board_width, "height": board_height}
        metadata["boardAssetId"] = bid
        try:
            if not await asyncio.to_thread(r2.head, board_key):
                await asyncio.to_thread(r2.put_bytes, board_key, board, "image/png")
        except Exception:
            return {**detail, "state": "unavailable", "reason": "storage_unavailable"}
        try:
            async with pool.connection() as conn:
                await repo.create_asset(conn, asset_id=bid, user_id=uid, project_id=pid,
                                        source="derived", bucket=app.state.settings.r2_bucket,
                                        key=board_key, mime="image/png", size=len(board),
                                        original_filename=None, metadata=board_meta)
                await repo.create_asset(conn, asset_id=aid, user_id=uid, project_id=pid,
                                        source="derived", bucket=app.state.settings.r2_bucket,
                                        key=key, mime="image/png", size=len(image),
                                        original_filename=None, metadata=metadata)
                await conn.commit()
        except Exception:
            return {**detail, "state": "unavailable", "reason": "asset_record_unavailable"}
        return {**detail, "state": "ready", "assetId": aid}
    except SamUnavailable:
        return {**detail, "state": "unavailable", "reason": "provider_unavailable"}
    except Exception:
        log.exception("product tidy failed project=%s", pid)
        return {**detail, "state": "failed", "reason": "tidy_failed"}


def _recent(value, *, minutes: int) -> bool:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > datetime.now(timezone.utc) - timedelta(minutes=minutes)


def active(job: dict | None) -> bool:
    if not job:
        return False
    if job.get("status") in ("pending", "running"):
        return _recent(job.get("created_at"), minutes=ACTIVE_WINDOW_MINUTES)
    return (job.get("status") in sam_retry.TERMINAL_STATUSES
            and sam_retry.job_is_retryable(job) and sam_retry.budget_left(job)
            and sam_retry.within_revive_window(job))


async def visuals(conn, r2, settings, *, user_id: str, project_id: str, product: dict) -> dict:
    enabled = getattr(settings, "product_tidy", "off") == "on"
    key = preprocess_key(project_id, product, enabled=enabled)
    job = await repo.get_latest_job_generation(conn, user_id, key) if key else None
    if getattr(settings, "draft_visuals", "off") == "on":
        front_ids = [aid for view, aid in mannequin.base_color_images(product) if view == "Front"][:1]
        draft_job = await repo.get_draft_visual_promotion(conn, user_id, project_id, front_ids, "product") if front_ids else None
        if draft_job:
            job = draft_job
            enabled = True
    processing = active(job)
    front = {"tidyStatus": "processing" if processing else "failed",
             "tidyUrl": None, "cutoutStatus": "processing" if processing else "failed" if job else "none",
             "cutoutUrl": None, "width": 0, "height": 0}
    source, cutout = await load_front(conn, user_id=user_id, product=product,
                                     project_id=project_id, include_display_input=enabled)
    if cutout:
        meta = cutout.get("metadata") or {}
        front.update(cutoutStatus="ready", width=meta.get("width") or 0, height=meta.get("height") or 0)
    if not enabled:
        front["tidyStatus"] = "off"
        return {"front": front}
    if source and source["hash"]:
        row = await ready_front_asset(conn, user_id=user_id, project_id=project_id,
                                      source_id=source["id"], source_hash=source["hash"])
        meta = (row or {}).get("metadata") or {}
        if row:
            if meta.get("boardAssetId"):
                board = await repo.get_asset_for_user(conn, user_id, meta["boardAssetId"])
                board_meta = (board or {}).get("metadata") or {}
                if (board and board.get("source") == "derived"
                        and board.get("id") == board_asset_id(user_id, project_id, source["hash"])
                        and board.get("r2_key") and not board.get("deleted_at")
                        and board_meta.get("canonicalType") == "product_tidy_board"
                        and board_meta.get("tidyAssetId") == row["id"]):
                    row, meta = board, board_meta
            front.update(tidyStatus="ready", tidyUrl=r2.public_url(row["r2_key"]),
                         width=meta.get("width") or 0, height=meta.get("height") or 0)
        else:
            verdict = (((job or {}).get("result") or {}).get("productTidy")
                       or ((job or {}).get("payload") or {}).get("_productTidy") or {})
            if (verdict.get("state") == "failed" and verdict.get("sourceHash") == source["hash"]
                    and verdict.get("algorithmVersion") == ALGORITHM_VERSION):
                front["tidyStatus"] = "failed"
    return {"front": front}
