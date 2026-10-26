"""Uncharged Cafe24 registration, checkpointed and fenced by the claimed lease."""

import asyncio
from datetime import datetime, timezone
from io import BytesIO

import httpx
from PIL import Image, ImageOps

from .. import repo
from ..config import is_allowed_mall, registration_enabled
from ..detail_publications import publication_files
from ..mall_connect import _external_code, derive_outcome
from ..services.cafe24_client import (
    ERROR_COPY, Cafe24ApiError, Cafe24Client, build_description_html, classify_error, price_fields,
)
from ..services.cafe24_tokens import call_with_token


_sleep = asyncio.sleep
_BATCH_BYTES = 20 * 1024 * 1024


class _Stopped(Exception):
    def __init__(self, code):
        self.code = code


async def _recheck(app, conn, job) -> str | None:
    payload, settings = job["payload"], app.state.settings
    if not job.get("lease_token") or not await repo.renew_job_lease(conn, job["id"], job["lease_token"]):
        return "lease_lost"
    if not registration_enabled(settings):
        return "cafe24_disabled"
    if not is_allowed_mall(settings, payload["mallId"]):
        return "cafe24_not_allowed"
    if await repo.user_account_purge_closed(conn, job["user_id"]):
        return "account_closed"
    row = await repo.get_cafe24_connection_by_id(conn, user_id=job["user_id"],
                                                 connection_id=payload["connectionId"])
    if (not row or row["id"] != payload["connectionId"] or row["status"] != "connected"
            or row["user_id"] != job["user_id"] or row["mall"] != "cafe24"
            or row["shop_id"] != payload["mallId"]):
        return "connection_changed"
    if not row.get("refresh_expires_at") or row["refresh_expires_at"] <= datetime.now(timezone.utc):
        return "reconnect_required"
    if await repo.is_job_cancelled(conn, job["id"]):
        return "cancelled"
    return None


async def _check(app, job):
    async with app.state.pool.connection() as conn:
        code = await _recheck(app, conn, job)
        await conn.commit()
    if code:
        raise _Stopped(code)


async def _patch(app, job, patch):
    async with app.state.pool.connection() as conn:
        changed = await repo.set_job_metadata_patch(conn, job_id=job["id"], patch=patch,
                                                    lease_token=job["lease_token"])
        await conn.commit()
    if not changed:
        raise _Stopped("lease_lost")
    job["metadata"].update(patch)


async def _call(app, job, fn, *, before=None):
    # Token acquisition can await a refresh. Check again inside fn, including every 401 retry.
    await _check(app, job)

    async def guarded(token):
        await _check(app, job)
        if before:
            await before()
            await _check(app, job)
        try:
            return await fn(token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # Stop before call_with_token can refresh and retry after a changed gate.
                await _check(app, job)
            raise

    return await call_with_token(app, user_id=job["user_id"],
                                 connection_id=job["payload"]["connectionId"], fn=guarded)


async def _upload_batch(app, job, client, batch):
    for attempt in range(4):
        try:
            return await _call(app, job, lambda token: client.upload_description_images(token, batch))
        except _Stopped:
            raise
        except Exception as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            retry = status == 429 or (status is not None and status >= 500) or isinstance(
                exc, (httpx.TransportError, TimeoutError))
            if not retry or attempt == 3:
                raise
            if status == 429:
                raw = exc.response.headers.get("Retry-After", "1")
                delay = min(60, int(raw)) if raw.isascii() and raw.isdigit() else 1
            else:
                delay = 2 ** attempt
            await _sleep(delay)


async def _upload_images(app, job, client):
    payload = job["payload"]
    await _check(app, job)
    async with app.state.pool.connection() as conn:
        publication = await repo.get_detail_publication(conn, user_id=job["user_id"],
                                                        publication_id=payload["publicationId"])
        await conn.commit()
    if not publication or publication["status"] != "ready" or publication["project_id"] != job["project_id"]:
        raise Cafe24ApiError("image_upload_mismatch")
    files = publication_files(publication)
    urls = list((job["metadata"].get("images") or {}).get("urls") or [])
    if len(urls) > len(files):
        raise Cafe24ApiError("image_upload_mismatch")
    await _patch(app, job, {"stage": "uploading_images"})
    # Keep at most one batch and one lookahead image in memory; use actual encoded sizes.
    pending = None
    while len(urls) < len(files):
        await _check(app, job)
        batch, size = [], 0
        for file in files[len(urls):len(urls) + 8]:
            image = pending if pending is not None else await asyncio.to_thread(
                app.state.r2.get_bytes, file["r2_key"])
            pending = None
            encoded_size = 4 * ((len(image) + 2) // 3)
            if encoded_size > _BATCH_BYTES:
                raise Cafe24ApiError("image_upload_mismatch")
            if size + encoded_size > _BATCH_BYTES:
                pending = image
                break
            batch.append(image)
            size += encoded_size
        uploaded = await _upload_batch(app, job, client, batch)
        if len(uploaded) != len(batch):
            raise Cafe24ApiError("image_upload_mismatch")
        urls.extend(uploaded)
        await _patch(app, job, {"images": {"urls": urls}})
        async with app.state.pool.connection() as conn:
            changed = await repo.set_job_progress_fenced(conn, job_id=job["id"],
                progress=int(70 * len(urls) / len(files)), lease_token=job["lease_token"])
            await conn.commit()
        if not changed:
            raise _Stopped("lease_lost")
    return urls


async def _lookup(app, job, client):
    try:
        matches = await _call(app, job, lambda token: client.find_products_by_custom_code(
            token, job["metadata"]["externalCode"]))
    except _Stopped:
        raise
    except Exception:
        return "unknown"
    if len(matches) == 1:
        await _patch(app, job, {"productNo": matches[0]})
        return "created"
    return "duplicate_suspected" if len(matches) > 1 else "unknown"


async def _create(app, job, client, urls):
    payload = job["payload"]
    await _check(app, job)
    await _patch(app, job, {"stage": "price_setting"})
    try:
        setting = await _call(app, job, client.product_setting)
    except _Stopped:
        raise
    except Exception:
        raise Cafe24ApiError("price_setting_unavailable") from None
    description = build_description_html(urls, payload["productName"], payload["width"])
    request = dict(product_name=payload["productName"], supply_price=payload["supplyPrice"],
        display=payload["display"], selling=payload["selling"], tax_type=payload["taxType"],
        product_condition="N", has_option="F", shipping_fee_by_product="F",
        custom_product_code=job["metadata"]["externalCode"], description=description, mobile_description=description,
        **price_fields(setting, payload["price"]))
    if payload.get("categoryNo"):
        request["add_category_no"] = [{"category_no": payload["categoryNo"], "recommend": "F", "new": "F"}]

    async def mark_sent():
        attempt = int((job["metadata"].get("create") or {}).get("attempt", 0)) + 1
        await _patch(app, job, {"stage": "creating_product", "create": {
            "sentAt": datetime.now(timezone.utc).isoformat(), "attempt": attempt, "leaseToken": job["lease_token"]}})

    async def send(token):
        try:
            return await client.create_product(token, {"shop_no": 1, "request": request})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 422):
                # Only a rejection of this POST is definite, never a token-refresh response.
                await _patch(app, job, {"rejectedAt": datetime.now(timezone.utc).isoformat(), "code": "invalid_request"})
                raise Cafe24ApiError("invalid_request") from None
            raise

    try:
        no = await _call(app, job, send, before=mark_sent)
    except _Stopped:
        raise
    except Exception as exc:
        if job["metadata"].get("rejectedAt"):
            raise Cafe24ApiError("invalid_request") from None
        # Authentication/permission errors are classified, but sentAt still controls the outcome.
        if classify_error(exc) in ("reconnect_required", "app_removed", "missing_permission", "connection_changed"):
            raise Cafe24ApiError(classify_error(exc)) from None
        if not (job["metadata"].get("create") or {}).get("sentAt"):
            raise Cafe24ApiError(classify_error(exc)) from None
        return await _lookup(app, job, client)
    await _patch(app, job, {"productNo": no})
    return "created"


def _thumbnail_jpeg(data):
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue()


async def _complete_created(app, job, client):
    thumbnail, warnings = "none", list(job["metadata"].get("warnings") or [])
    if job["payload"].get("thumbnailAssetId"):
        # A successful thumbnail checkpoint also survives a crash before finalization.
        thumbnail = job["metadata"].get("thumbnail") or "skipped"
        if thumbnail not in ("ok", "failed"):
            await _check(app, job)
            await _patch(app, job, {"stage": "uploading_thumbnail"})
            try:
                await _check(app, job)
                async with app.state.pool.connection() as conn:
                    asset = await repo.get_project_asset_for_user(conn, job["user_id"], job["project_id"],
                                                                 job["payload"]["thumbnailAssetId"])
                    await conn.commit()
                if not asset:
                    raise Cafe24ApiError("image_upload_mismatch")
                raw = await asyncio.to_thread(app.state.r2.get_bytes, asset["r2_key"])
                jpeg = await asyncio.to_thread(_thumbnail_jpeg, raw)
                await _call(app, job, lambda token: client.upload_product_image(token, job["metadata"]["productNo"], jpeg))
                thumbnail = "ok"
            except _Stopped:
                raise
            except Exception as exc:
                thumbnail = "failed"
                warnings.append(ERROR_COPY[classify_error(exc)])
            await _patch(app, job, {"thumbnail": thumbnail, "warnings": warnings})
    base = f'https://{job["payload"]["mallId"]}.cafe24.com'
    try:
        store = await _call(app, job, client.store)
        base = store.get("mall_url") or base
    except _Stopped:
        raise
    except Exception:
        pass
    url = f'{base.rstrip("/")}/product/detail.html?product_no={job["metadata"]["productNo"]}'
    await _finish(app, job, thumbnail=thumbnail, warnings=warnings, product_url=url)


async def _finish(app, job, *, code=None, outcome=None, thumbnail=None, warnings=None, product_url=None):
    if code == "lease_lost":
        return
    outcome = outcome or derive_outcome(job)
    metadata = job["metadata"]
    warnings = list(warnings if warnings is not None else metadata.get("warnings") or [])
    if outcome == "created" and code:
        warnings.append(ERROR_COPY[code])
    if outcome == "created" and not product_url:
        product_url = f'https://{job["payload"]["mallId"]}.cafe24.com/product/detail.html?product_no={metadata["productNo"]}'
    result = dict(outcome=outcome, productNo=metadata.get("productNo"), productUrl=product_url,
        imageCount=len((metadata.get("images") or {}).get("urls") or []),
        thumbnail=thumbnail or metadata.get("thumbnail") or ("skipped" if outcome == "created" else None),
        externalCode=metadata.get("externalCode") or _external_code(job["id"]), warnings=warnings)
    if code:
        result["code"] = code
    async with app.state.pool.connection() as conn:
        # Keep the job row locked through the connection timestamp and finalization.
        if not await repo.renew_job_lease(conn, job["id"], job["lease_token"]):
            return
        if outcome == "created":
            async with conn.cursor() as cur:
                await cur.execute("""update mall_connections set last_registered_at = now()
                    where id = %s and user_id = %s and mall = 'cafe24' and status = 'connected'
                    and exists (select 1 from jobs where id = %s and locked_by = %s and status = 'running')""",
                    (job["payload"]["connectionId"], job["user_id"], job["id"], job["lease_token"]))
        await repo.finalize_uncharged_job(conn, job_id=job["id"], lease_token=job["lease_token"],
            status="error" if outcome == "failed" else "done", result=result,
            error_message=ERROR_COPY[code or "unknown"] if outcome == "failed" else None)
        await conn.commit()


async def run_mall_register_job(app, job):
    job["metadata"] = dict(job.get("metadata") or {})
    try:
        await _check(app, job)
        await _patch(app, job, {"mall": "cafe24", "externalCode": _external_code(job["id"])})
        settings = app.state.settings
        client = Cafe24Client(job["payload"]["mallId"], client_id=settings.cafe24_client_id,
            client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)
        if job["metadata"].get("productNo"):
            await _complete_created(app, job, client)
            return
        if job["metadata"].get("rejectedAt"):
            await _finish(app, job, code=job["metadata"].get("code", "invalid_request"))
            return
        if (job["metadata"].get("create") or {}).get("sentAt"):
            outcome = await _lookup(app, job, client)
        else:
            urls = await _upload_images(app, job, client)
            outcome = await _create(app, job, client, urls)
        if outcome == "created":
            await _complete_created(app, job, client)
        else:
            await _finish(app, job, outcome=outcome)
    except _Stopped as exc:
        await _finish(app, job, code=exc.code)
    except Exception as exc:
        await _finish(app, job, code=classify_error(exc))
