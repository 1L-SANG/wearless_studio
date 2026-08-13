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

import logging
import time

from app import repo
from app.agents import mannequin
from app.config import load_settings
from app.services import editor_garment_mask, sam_client

log = logging.getLogger("wearless.editor_garment_mask")

SKIP_DISABLED = "tone_editor_disabled"
SKIP_SAM_UNCONFIGURED = "sam_not_configured"
SKIP_NO_CUT = "cut_unavailable"
SKIP_NO_BASE = "missing_base_reference"


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
        gender = _base_gender(payload.get("cutMetadata"), analysis, clothing_type)
        base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                         else s.base_mannequin_women_asset_id)
        base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                      if base_asset_id else None)

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
            clothing_type=clothing_type, sub_category=analysis.get("subCategory"))
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

    async with pool.connection() as conn:
        row = await editor_garment_mask.record(
            conn, user_id=user_id, project_id=project_id, cut_id=cut_id,
            source_asset_id=str(cut.get("id") or ""), result=result,
            category=clothing_type, sub_category=analysis.get("subCategory"))
        await conn.commit()

    await finish("done", {"state": "ready", "cutId": cut_id,
                          "maskAssetId": (row or {}).get("id"),
                          "sourceHash": result.source_hash,
                          "algorithmVersion": result.algorithm_version,
                          "areaFrac": result.area_frac, "cached": result.cached,
                          "latencySeconds": latency})
