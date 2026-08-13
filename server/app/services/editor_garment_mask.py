"""Editor garment masks: record them, and resolve them for the Tone Editor.

The mask says which pixels of a GENERATED mannequin cut are the sold garment. It exists so the
seller can nudge saturation and exposure on the garment alone; nothing else in the frame may
move. It is produced once, asynchronously, after the cut is already on screen.

Two halves, deliberately separate — the same split canonical references use:

  PRODUCER  `editor_garment_mask` job -> SAM service -> R2 object -> `record()` writes an asset
  CONSUMER  `resolve()` reads asset rows and answers ready / processing / failed

`resolve()` never calls SAM and never enqueues. A missing mask is a normal state (preprocessing
may still be running), and the editor shows a disabled control rather than an error.

Storage reuses the existing `assets` model. A mask is a derived asset whose metadata records the
cut it belongs to AND that cut's content hash — which is what makes staleness detectable. A
regenerated cut produces different bytes, so the old mask no longer matches and is refused
instead of being painted onto a different garment.
"""

from __future__ import annotations

import logging
import uuid

from app import repo

log = logging.getLogger(__name__)

#: Marks an asset row as one of ours. Never mixed with `canonical_cutout`: that one is a cutout
#: of a RAW product photograph, this one is a mask over a generated image.
MASK_KIND = "editorGarmentMask"
PRODUCER = "sam2-worn-garment"

STATUS_READY = "ready"
STATUS_PROCESSING = "processing"
STATUS_FAILED = "failed"


def metadata_for(result, *, cut_id: str, source_asset_id: str, category: str | None,
                 sub_category: str | None) -> dict:
    """Provenance stored on the derived asset. Enough to answer 'what made this, from what'."""
    return {
        "type": MASK_KIND,
        "sourceCutId": cut_id,
        "sourceAssetId": source_asset_id,
        "sourceHash": result.source_hash,
        "producer": PRODUCER,
        "modelVersion": result.model_version,
        "algorithmVersion": result.algorithm_version,
        "selectorVersion": result.selector_version,
        "grid": result.grid,
        "m2m": result.m2m,
        "r2Key": result.mask_key,
        "checksum": result.checksum,
        "width": result.width,
        "height": result.height,
        "areaFrac": result.area_frac,
        "category": category,
        "subCategory": sub_category,
        "status": STATUS_READY,
    }


def is_current(meta: dict, *, cut_id: str, source_hash: str | None,
               algorithm_version: str | None = None) -> bool:
    """Does this mask describe the cut being edited RIGHT NOW?

    The cut id alone is not enough. A seller who regenerates gets a new image under a new cut
    id, but an id can also be reused by an edit path, so the content hash is checked whenever
    the caller knows it. Painting a previous garment's mask onto a new cut would recolour
    whatever happens to be under those pixels.
    """
    if not isinstance(meta, dict) or meta.get("type") != MASK_KIND:
        return False
    if str(meta.get("sourceCutId") or "") != str(cut_id):
        return False
    if source_hash and str(meta.get("sourceHash") or "") != str(source_hash):
        return False
    if algorithm_version and str(meta.get("algorithmVersion") or "") != str(algorithm_version):
        return False
    return True


async def record(conn, *, user_id: str, project_id: str, cut_id: str, source_asset_id: str,
                 result, category: str | None = None,
                 sub_category: str | None = None) -> dict | None:
    """Create — or reuse — the asset row for one produced mask. Idempotent.

    Idempotency is lookup-before-create on (project, r2Key): the service derives that key
    deterministically from the cut's content hash plus the model and algorithm versions, so a
    dispatcher retry that re-produces the same mask finds the same key and reuses the row.
    """
    if not result.ready or not result.mask_key:
        return None
    existing = await find_by_key(conn, project_id=project_id, r2_key=result.mask_key)
    if existing:
        return existing

    asset_id = str(uuid.uuid4())
    row = await repo.create_asset(
        conn, asset_id=asset_id, user_id=user_id, project_id=project_id,
        source="derived", bucket="", key=result.mask_key, mime="image/png",
        size=result.byte_size, original_filename=None)
    await set_metadata(conn, asset_id=asset_id, metadata=metadata_for(
        result, cut_id=cut_id, source_asset_id=source_asset_id,
        category=category, sub_category=sub_category))
    log.info("editor garment mask recorded project=%s cut=%s asset=%s key=%s",
             project_id, cut_id, asset_id, result.mask_key)
    return row


async def find_by_key(conn, *, project_id: str, r2_key: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where project_id = %s and r2_key = %s and deleted_at is null limit 1",
            (project_id, r2_key))
        return await cur.fetchone()


async def set_metadata(conn, *, asset_id: str, metadata: dict) -> None:
    from psycopg.types.json import Json
    async with conn.cursor() as cur:
        await cur.execute("update assets set metadata = %s where id = %s",
                          (Json(metadata), asset_id))


async def list_for_project(conn, *, project_id: str) -> list[dict]:
    """Every editor mask row for a project, newest first."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata, created_at "
            "from assets where project_id = %s and source = 'derived' and deleted_at is null "
            "and metadata->>'type' = %s order by created_at desc",
            (project_id, MASK_KIND))
        return list(await cur.fetchall())


async def find_for_cut(conn, *, project_id: str, cut_id: str,
                       source_hash: str | None = None) -> dict | None:
    """The current mask row for one cut, or None. Stale rows are skipped, never returned."""
    for row in await list_for_project(conn, project_id=project_id):
        if is_current(row.get("metadata") or {}, cut_id=cut_id, source_hash=source_hash):
            return row
    return None
