"""`sam_preprocess` — produce canonical (background-removed) Front/Back cutouts.

Runs independently of `analyze`. It needs only the source photographs, not category,
subCategory, materials or anything else analysis produces, so the two are enqueued together and
neither waits on the other.

Nothing here is on a seller's critical path. Every failure mode — SAM unconfigured, SAM down,
SAM timing out, one view failing — leaves the product exactly as usable as it was; generation
simply proceeds on RAW references. That is why this worker reports `skipped`/`partial` states
rather than raising: a red job badge for an optional optimisation would be a worse outcome than
no cutout.
"""

from __future__ import annotations

import logging

from app import repo
from app.agents import mannequin
from app.config import load_settings
from app.services import canonical_reference, sam_client

log = logging.getLogger(__name__)

#: Only these views get a cutout. Detail is a macro close-up with no garment silhouette to
#: isolate, and Fit is a photograph of a person.
ELIGIBLE_VIEWS = ("Front", "Back")


async def run_sam_preprocess_job(app, job: dict) -> None:
    """Worker entrypoint. Signature matches the other kinds in `_WORKERS`."""
    pool = app.state.pool
    job_id, project_id = job["id"], job["project_id"]
    user_id, lease_token = job["user_id"], job["lease_token"]
    s = load_settings()

    async def finish(status: str, detail: dict) -> None:
        async with pool.connection() as conn:
            await repo.finalize_uncharged_job(
                conn, job_id=job_id, lease_token=lease_token, status=status, result=detail)
            await conn.commit()
        log.info("sam_preprocess job=%s project=%s %s %s", job_id, project_id, status, detail)

    if not sam_client.configured(s):
        # Not an error: an environment without SAM configured simply has no canonical path.
        await finish("done", {"state": "skipped", "reason": "sam_not_configured"})
        return

    async with pool.connection() as conn:
        product = await repo.get_product(conn, project_id) or {}
        assets = {}
        for slot, asset_id in mannequin.base_color_images(product):
            if slot in ELIGIBLE_VIEWS and slot not in assets:
                row = await repo.get_asset_for_user(conn, user_id, asset_id)
                if row and row.get("r2_key"):
                    assets[slot] = {"id": asset_id, "key": row["r2_key"]}

    if not assets:
        await finish("done", {"state": "skipped", "reason": "no_eligible_source_assets"})
        return

    try:
        results = await sam_client.segment_garment(
            s, {slot: a["key"] for slot, a in assets.items()})
    except sam_client.SamUnavailable as exc:
        # Bounded dispatcher retry handles transient outages. Deterministic cutout keys make a
        # retry cheap: views that already succeeded are served from R2 without re-inferring.
        await finish("error", {"state": "unavailable", "reason": str(exc)})
        return

    recorded, failed = {}, {}
    async with pool.connection() as conn:
        for slot, result in results.items():
            if not result.ready:
                failed[slot] = result.code or "failed"
                continue
            row = await canonical_reference.record(
                conn, user_id=user_id, project_id=project_id, view=slot,
                result=result, source_asset_id=assets[slot]["id"])
            if row:
                recorded[slot] = {"assetId": row["id"], "cutoutKey": result.cutout_key,
                                  "cached": result.cached}
        await conn.commit()

    # Front and Back are independent all the way through: a failed Back never discards a
    # recorded Front.
    state = ("ready" if recorded and not failed
             else "partial" if recorded
             else "failed")
    await finish("done" if recorded else "error",
                 {"state": state, "recorded": recorded, "failed": failed})
