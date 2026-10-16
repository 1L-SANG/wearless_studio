"""Display-only product flatlay. Never a reference for mannequin/detail generation.

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
from app.services import canonical_reference, qwen_flatlay, sam_client, sam_retry

log = logging.getLogger(__name__)
ALGORITHM_VERSION = "product-tidy-v1-qwen"
ACTIVE_WINDOW_MINUTES = 10


def preprocess_key(project_id: str, product: dict, *, enabled: bool) -> str | None:
    key = canonical_reference.preprocess_idempotency_key(project_id, product)
    # Completed off-mode jobs must not swallow the first on-mode request.
    return f"{key}:{ALGORITHM_VERSION}" if key and enabled else key


def asset_id(user_id: str, project_id: str, source_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         f"{user_id}:{project_id}:{source_hash}:{ALGORITHM_VERSION}"))


def source_is_bound(metadata: dict, source_id: str) -> bool:
    aliases = metadata.get("sourceAssetIds") or []
    return (metadata.get("sourceAssetId") == source_id
            or isinstance(aliases, list) and source_id in aliases)


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


async def load_front(conn, *, user_id: str, product: dict) -> tuple[dict | None, dict | None]:
    """Resolve the current upload ID and SAM's recorded hash using DB metadata only."""
    source_id = next((aid for view, aid in mannequin.base_color_images(product)
                      if view == "Front" and aid), None)
    if not source_id:
        return None, None
    source = await repo.get_asset_for_user(conn, user_id, source_id)
    if not source or not source.get("r2_key") or source.get("source") != "upload":
        return None, None
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where user_id = %s and source = 'derived' and deleted_at is null "
            "and metadata->>'canonicalType' = 'canonical_cutout' "
            "and metadata->>'view' = 'Front' "
            "and (metadata->>'sourceAssetId' = %s or jsonb_exists(metadata->'sourceAssetIds', %s)) "
            "order by created_at desc limit 1", (user_id, source_id, source_id))
        cutout = await cur.fetchone()
    return {"id": source_id, "key": source["r2_key"],
            "hash": ((cutout or {}).get("metadata") or {}).get("sourceHash")}, cutout


async def produce(app, job: dict, *, source: dict, cutout: dict) -> dict:
    """Called only AFTER the canonical transaction commits. Ready assets are reused."""
    pool, r2 = app.state.pool, app.state.r2
    uid, pid = job["user_id"], job["project_id"]
    aid = asset_id(uid, pid, source["hash"])
    key = derived_key(uid, pid, aid, "png")
    detail = {"sourceHash": source["hash"], "algorithmVersion": ALGORITHM_VERSION}
    try:
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
            cutout_png = await asyncio.to_thread(r2.get_bytes, cutout["r2_key"])
        except Exception:
            return {**detail, "state": "unavailable", "reason": "storage_unavailable"}
        # The dispatcher already supplies job scope; explicit IDs also cover direct invocations.
        with image_usage.job_scope(job_id=job["id"], user_id=uid, stage="product_tidy"):
            image = await qwen_flatlay.render(app.state.settings, cutout_png, transparent=True)
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.mode != "RGBA" or decoded.getchannel("A").getbbox() is None:
                raise ValueError("invalid tidy PNG")
            width, height = decoded.size
        metadata = {
            "canonicalType": "product_tidy", "view": "Front", "sourceAssetId": source["id"],
            "sourceHash": source["hash"], "cutoutAssetId": cutout["id"],
            "producer": "fal-qwen-product-tidy", "algorithmVersion": ALGORITHM_VERSION,
            "r2Key": key, "width": width, "height": height,
        }
        try:
            await asyncio.to_thread(r2.put_bytes, key, image, "image/png")
        except Exception:
            return {**detail, "state": "unavailable", "reason": "storage_unavailable"}
        async with pool.connection() as conn:
            await repo.create_asset(conn, asset_id=aid, user_id=uid, project_id=pid,
                                    source="derived", bucket=app.state.settings.r2_bucket,
                                    key=key, mime="image/png", size=len(image),
                                    original_filename=None, metadata=metadata)
            await conn.commit()
        return {**detail, "state": "ready", "assetId": aid}
    except sam_client.SamUnavailable:
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
    processing = active(job)
    front = {"tidyStatus": "processing" if processing else "failed",
             "tidyUrl": None, "cutoutStatus": "processing" if processing else "failed" if job else "none",
             "cutoutUrl": None, "width": 0, "height": 0}
    source, cutout = await load_front(conn, user_id=user_id, product=product)
    if cutout:
        meta = cutout.get("metadata") or {}
        front.update(cutoutStatus="ready", width=meta.get("width") or 0, height=meta.get("height") or 0)
    if not enabled:
        front["tidyStatus"] = "off"
        return {"front": front}
    if source and source["hash"]:
        row = await repo.get_asset_for_user(conn, user_id, asset_id(user_id, project_id, source["hash"]))
        meta = (row or {}).get("metadata") or {}
        if (row and row.get("r2_key") and meta.get("canonicalType") == "product_tidy"
                and source_is_bound(meta, source["id"])
                and meta.get("sourceHash") == source["hash"]
                and meta.get("algorithmVersion") == ALGORITHM_VERSION):
            front.update(tidyStatus="ready", tidyUrl=r2.public_url(row["r2_key"]),
                         width=meta.get("width") or 0, height=meta.get("height") or 0)
        else:
            verdict = (((job or {}).get("result") or {}).get("productTidy")
                       or ((job or {}).get("payload") or {}).get("_productTidy") or {})
            if (verdict.get("state") == "failed" and verdict.get("sourceHash") == source["hash"]
                    and verdict.get("algorithmVersion") == ALGORITHM_VERSION):
                front["tidyStatus"] = "failed"
    return {"front": front}
