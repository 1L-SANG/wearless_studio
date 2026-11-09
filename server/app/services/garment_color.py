"""Internal garment masters share final output ownership, fencing and cleanup."""
from __future__ import annotations

import asyncio
import hashlib
import uuid

from .. import repo
from ..r2 import PRIVATE_NO_STORE, ai_key, ext_for_mime


async def stage_master(app, job: dict, image, *, cut_type: str, real_derived: bool,
                       stored_outputs=None) -> dict:
    asset_id = str(uuid.uuid4())
    key = ai_key(job["user_id"], job["project_id"], job["id"], asset_id,
                 ext_for_mime(image.mime) or "png")
    async with app.state.pool.connection() as conn:
        intent = await repo.create_ai_output_cleanup_intent(conn, job_id=job["id"], r2_key=key)
        await conn.commit()
    candidate = {"asset_id": asset_id, "key": key, "cleanup_intent_id": intent,
                 "bucket": app.state.settings.r2_bucket, "mime": image.mime,
                 "size": len(image.data), "sha256": hashlib.sha256(image.data).hexdigest(),
                 "metadata": {"internalGarmentMaster": True, "cut_type": cut_type,
                              "facemarket_real_derived": real_derived}}
    # Register before upload so cancellation cannot leave an untracked object.
    if stored_outputs is not None:
        stored_outputs.append(candidate)
    upload = asyncio.create_task(asyncio.to_thread(
        app.state.r2.put_bytes, key, image.data, image.mime, cache=PRIVATE_NO_STORE))
    try:
        await asyncio.shield(upload)
    except asyncio.CancelledError:
        # A canceled to_thread await does not stop PUT. Settle it before callers
        # can DELETE, confirm absence, and clear the only cleanup intent.
        settled = asyncio.gather(upload, return_exceptions=True)
        while not settled.done():
            try:
                await asyncio.shield(settled)
            except asyncio.CancelledError:
                continue
        raise
    return candidate


async def persist_masters(conn, *, job: dict, outputs: list[dict]) -> None:
    """Call only after successful lease-fenced finalization, before its transaction commits.

    Internal rows never enter cut_assets or wardrobe_images, so they do not change
    the requested final-image count, output previews, or credit settlement.
    """
    for output in outputs:
        for master in output.get("internal_masters") or []:
            metadata = {**master["metadata"], "sha256": master["sha256"]}
            await repo.create_asset(conn, asset_id=master["asset_id"], user_id=job["user_id"],
                project_id=job["project_id"], source="ai", bucket=master["bucket"], key=master["key"],
                mime=master["mime"], size=master["size"], original_filename=None, metadata=metadata)
            if master.get("cleanup_intent_id"):
                await repo.clear_ai_output_cleanup_intent(conn, master["cleanup_intent_id"])
            provenance = output.get("provenance")
            if provenance:
                async with conn.cursor() as cur:
                    await repo.insert_output_records(cur, records=[{
                        "asset_id": master["asset_id"], "job_id": job["id"],
                        "license_id": provenance["license_id"], "license_ref": provenance["license_id"],
                        "model_id": provenance["model_id"], "seller_id": job["user_id"],
                        "image_sha256": master["sha256"], "byte_size": master["size"],
                    }])
