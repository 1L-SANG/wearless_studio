"""Tone-adjusted mannequin renders: the seller's saturation/exposure applied to one cut.

The original generated cut is immutable. A tone edit produces a SEPARATE derived asset and
records the parameters that made it, so the editor can always reconstruct the adjustment from
the original rather than stacking filters on an already-filtered PNG (which would compound
banding and colour drift over repeated edits).

Resolution rule: the selected mannequin cut never changes identity. What changes is whether an
active tone render is attached to it. Everything user-facing — result preview, download, share,
export — resolves through `active_for_cut()`, so a seller who has applied an adjustment gets the
adjusted pixels everywhere without the cut itself being replaced.
"""

from __future__ import annotations

import logging
import uuid

from app import repo

log = logging.getLogger(__name__)

RENDER_KIND = "mannequinToneAdjusted"
#: Version of the pixel maths. Bumping it means an existing render's bytes are no longer what
#: the current renderer would produce from the same parameters, which is worth knowing when a
#: seller reports that reopening the editor looks different from the saved result.
RENDERER_VERSION = "tone-linear-rec709-v2"

#: Slider limits, mirrored in the frontend. Enforced here because an uploaded render arrives
#: with its parameters as a claim, and a claim outside these bounds is not something this
#: product can produce.
# v2(2026-08-13): ±30/±20 → ±100/±100. 슬라이더→계수 매핑이 달라졌으므로 저장된 v1
# 조정값(예: saturation 18)은 v2 에선 다른 결과를 낸다 — RENDERER_VERSION 으로 구분한다.
SATURATION_RANGE = 100
EXPOSURE_RANGE = 100


def clamp_params(saturation, exposure) -> tuple[int, int]:
    def _one(v, limit):
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            return 0
        return max(-limit, min(limit, n))
    return _one(saturation, SATURATION_RANGE), _one(exposure, EXPOSURE_RANGE)


def is_neutral(saturation: int, exposure: int) -> bool:
    """Zero adjustment. The render would be the original, so we do not store one."""
    return saturation == 0 and exposure == 0


def metadata_for(*, cut_id: str, source_asset_id: str, source_hash: str | None,
                 mask_asset_id: str | None, mask_algorithm_version: str | None,
                 saturation: int, exposure: int) -> dict:
    return {
        "type": RENDER_KIND,
        "sourceCutId": cut_id,
        "sourceAssetId": source_asset_id,
        "sourceHash": source_hash,
        "maskAssetId": mask_asset_id,
        "maskAlgorithmVersion": mask_algorithm_version,
        "rendererVersion": RENDERER_VERSION,
        "saturation": saturation,
        "exposure": exposure,
    }


def is_current(meta: dict, *, cut_id: str, source_hash: str | None = None) -> bool:
    if not isinstance(meta, dict) or meta.get("type") != RENDER_KIND:
        return False
    if str(meta.get("sourceCutId") or "") != str(cut_id):
        return False
    if source_hash and str(meta.get("sourceHash") or "") != str(source_hash):
        return False
    return True


async def list_for_project(conn, *, project_id: str) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata, created_at "
            "from assets where project_id = %s and deleted_at is null "
            "and metadata->>'type' = %s order by created_at desc",
            (project_id, RENDER_KIND))
        return list(await cur.fetchall())


async def active_for_cut(conn, *, project_id: str, cut_id: str,
                         source_hash: str | None = None) -> dict | None:
    """The newest render that still describes this cut, or None.

    Newest wins: re-applying is a new render derived from the original, not an edit of the
    previous one, so the most recent row is always the seller's current intent.
    """
    for row in await list_for_project(conn, project_id=project_id):
        if is_current(row.get("metadata") or {}, cut_id=cut_id, source_hash=source_hash):
            return row
    return None


async def record(conn, *, user_id: str, project_id: str, asset_id: str, cut_id: str,
                 source_asset_id: str, source_hash: str | None, mask_asset_id: str | None,
                 mask_algorithm_version: str | None, saturation: int, exposure: int) -> None:
    """Attach provenance to an already-uploaded render asset."""
    from psycopg.types.json import Json
    meta = metadata_for(cut_id=cut_id, source_asset_id=source_asset_id,
                        source_hash=source_hash, mask_asset_id=mask_asset_id,
                        mask_algorithm_version=mask_algorithm_version,
                        saturation=saturation, exposure=exposure)
    async with conn.cursor() as cur:
        await cur.execute("update assets set metadata = %s where id = %s and project_id = %s",
                          (Json(meta), asset_id, project_id))
    log.info("tone render recorded project=%s cut=%s asset=%s sat=%s exp=%s",
             project_id, cut_id, asset_id, saturation, exposure)


async def clear_for_cut(conn, *, project_id: str, cut_id: str) -> int:
    """Reset back to the original by retiring every render for this cut.

    Soft delete, not a metadata edit: the row stays for auditing but stops resolving, and the
    original cut asset is untouched either way.
    """
    rows = [r for r in await list_for_project(conn, project_id=project_id)
            if is_current(r.get("metadata") or {}, cut_id=cut_id)]
    if not rows:
        return 0
    async with conn.cursor() as cur:
        await cur.execute("update assets set deleted_at = now() where id = any(%s)",
                          ([uuid.UUID(r["id"]) for r in rows],))
    return len(rows)
