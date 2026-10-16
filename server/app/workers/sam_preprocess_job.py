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
from app.services import canonical_reference, fal_api, fal_cutout, product_tidy, sam_client

log = logging.getLogger(__name__)

#: One definition, in the service, because the enqueue sites build the job's identity from the
#: same list (`canonical_reference.preprocess_idempotency_key`). Two copies drifting apart would
#: mean a key that promises a view this worker then refuses to segment.
ELIGIBLE_VIEWS = canonical_reference.ELIGIBLE_VIEWS


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

    use_fal = getattr(s, "sam_preprocess_provider", "sam2") == "fal"
    tidy_enabled = getattr(s, "product_tidy", "off") == "on"
    configured = fal_api.configured(s) if use_fal else sam_client.configured(s)
    if not configured and not tidy_enabled:
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

    recorded, failed = {}, {}
    front_source, front_cutout = None, None
    if tidy_enabled:
        async with pool.connection() as conn:
            front_source, front_cutout = await product_tidy.load_front(
                conn, user_id=user_id, product=product, project_id=project_id,
                include_display_input=True)
        if front_cutout:
            recorded["Front"] = {"assetId": front_cutout["id"],
                                 "cutoutKey": front_cutout["r2_key"], "cached": True}

    views = {slot: a["key"] for slot, a in assets.items() if slot not in recorded}
    results = {}
    if views and not configured:
        if not front_cutout:
            await finish("done", {"state": "skipped", "reason": "sam_not_configured"})
            return
    elif views:
        try:
            if use_fal:
                results = await fal_cutout.segment_garment(s, app.state.r2, views)
            else:
                results = await sam_client.segment_garment(s, views)
        except sam_client.SamUnavailable as exc:
            if not front_cutout:
                await finish("done", {"state": "unavailable", "reason": str(exc)})
                return
            failed.update({slot: "unavailable" for slot in views})

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
            if tidy_enabled and slot == "Front":
                front_source = {"id": assets[slot]["id"], "hash": result.source_hash}
                # A foreign owner may hold the shared canonical key. The optional display
                # path copies that verified result to this project, never their asset row.
                front_cutout = {**(row or {}), "r2_key": result.cutout_key}
        await conn.commit()

    tidy = None
    if tidy_enabled and front_cutout and front_source:
        previous = (job.get("payload") or {}).get("_productTidy") or {}
        if (previous.get("state") == "failed"
                and previous.get("sourceHash") == front_source["hash"]
                and previous.get("algorithmVersion") == product_tidy.ALGORITHM_VERSION):
            tidy = previous
        else:
            tidy = await product_tidy.produce(app, job, source=front_source, cutout=front_cutout)

    # Front and Back are independent all the way through: a failed Back never discards a
    # recorded Front.
    # PRODUCT_TIDY=off keeps the original provider-specific unavailable contract.
    # fal은 뷰별 장애를 반환한다. 성공분은 위에서 커밋하고, 나머지는 기존 푸셔가
    # 재시도하도록 unavailable을 유지한다. SAM2의 기존 partial/failed 판정은 그대로다.
    unavailable = (use_fal and "unavailable" in failed.values())
    if tidy_enabled:
        unavailable = (unavailable or "unavailable" in failed.values()
                       or bool(tidy and tidy["state"] == "unavailable"))
    has_result = bool(recorded) or bool(tidy and tidy["state"] == "ready")
    state = ("unavailable" if unavailable
             else "ready" if has_result and not failed
             else "partial" if has_result
             else "failed")
    detail = {"state": state, "recorded": recorded, "failed": failed}
    if tidy is not None:
        detail["productTidy"] = tidy
    await finish("done" if has_result or unavailable else "error", detail)
