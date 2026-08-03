"""Phase 9 deterministic export worker.

Export is provider-free: it renders a caller-supplied editor snapshot into a long PNG
and, optionally, a ZIP package. Storage/finalize remain lease-fenced like AI jobs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from .. import repo
from ..r2 import IMMUTABLE_CACHE, export_key
from ..services import export_render
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.export_job")
async def run_export_job(app, job: dict) -> None:
    job_id = job["id"]
    user_id = job["user_id"]
    project_id = job["project_id"]
    lease_token = job.get("lease_token")
    payload = job.get("payload") or {}
    export_id = payload.get("exportId")
    pool = app.state.pool
    r2 = app.state.r2
    settings = app.state.settings
    uploaded_keys: list[str] = []

    async def _cleanup() -> None:
        for key in uploaded_keys:
            try:
                await asyncio.to_thread(r2.delete, key)
            except Exception as exc:
                log.warning("export cleanup failed job=%s error_type=%s", job_id,
                            type(exc).__name__)

    async def _fail(message: str, metadata: dict | None = None, code: str = "export_failed"):
        async with pool.connection() as conn:
            await repo.finalize_export_failure(
                conn,
                job_id=job_id,
                lease_token=lease_token,
                project_id=project_id,
                export_id=export_id,
                message=message,
                metadata=metadata or {},
                code=code,
            )
            await conn.commit()

    if getattr(settings, "export_backend", "off") != "on":
        await _fail(
            "내보내기 기능이 비활성화됐어요.",
            {"reason": "export_disabled"},
            "export_disabled",
        )
        return

    if not export_id:
        await _fail(
            "내보내기 요청이 손상됐어요. 다시 시도해 주세요.",
            {"reason": "missing_export_id"},
            "invalid_export_job",
        )
        return

    try:
        snapshot = payload.get("snapshot") or {}
        options = payload.get("options") or {}
        body = payload.get("body") or {}
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "snapshot_loaded"})
        asset_ids = export_render.referenced_asset_ids(snapshot)
        asset_map: dict[str, bytes] = {}
        async with pool.connection() as conn:
            asset_rows = await repo.list_project_assets_by_ids(
                conn, user_id=user_id, project_id=project_id, asset_ids=asset_ids)
            await conn.commit()
        if {row["id"] for row in asset_rows} != set(asset_ids):
            await _fail(
                "내보내기에 필요한 이미지가 없거나 접근할 수 없어요.",
                {"reason": "export_asset_unavailable"},
                "export_asset_unavailable",
            )
            return
        for asset in asset_rows:
            asset_map[asset["id"]] = await asyncio.to_thread(r2.get_bytes, asset["r2_key"])

        try:
            export_render.verify_snapshot_hash(snapshot, payload.get("snapshotHash") or "")
            rendered = await asyncio.to_thread(
                export_render.render,
                snapshot=snapshot,
                body=body,
                options=options,
                asset_bytes=lambda aid: asset_map.get(aid),
            )
        except export_render.ExportRenderError as exc:
            await _fail(exc.message, {"reason": exc.code}, exc.code)
            return

        await _emit(pool, job_id, "progress", {"progress": 55, "phase": "rendered"})
        files = []
        for item in rendered.files:
            asset_id = str(uuid.uuid4())
            ext = "zip" if item["mime"] == "application/zip" else "png"
            key = export_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(
                r2.put_bytes, key, item["bytes"], item["mime"], IMMUTABLE_CACHE)
            uploaded_keys.append(key)
            files.append({
                **{k: v for k, v in item.items() if k != "bytes"},
                "asset_id": asset_id,
                "bucket": settings.r2_bucket,
                "key": key,
                "size": len(item["bytes"]),
                "metadata": {"exportId": export_id, "rendererVersion": export_render.RENDERER_VERSION},
            })

        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "stored"})
        provenance = {
            **rendered.manifest,
            "requestBody": {
                "snapshotHash": payload.get("snapshotHash"),
                "body": body,
                "options": options,
            },
        }
        async with pool.connection() as conn:
            out = await repo.finalize_export_success(
                conn,
                job_id=job_id,
                lease_token=lease_token,
                user_id=user_id,
                project_id=project_id,
                export_id=export_id,
                files=files,
                provenance=provenance,
                metadata={"rendererVersion": export_render.RENDERER_VERSION},
            )
            await conn.commit()
        if out is None:
            log.warning("export job %s lost lease during finalize", job_id)
            await _cleanup()
    except Exception as exc:
        await _cleanup()
        log.error("export job failed job=%s error_type=%s", job_id, type(exc).__name__)
        await _fail(
            "내보내기에 실패했어요. 잠시 후 다시 시도해 주세요.",
            {"reason": "unexpected_error"},
            "export_failed",
        )
