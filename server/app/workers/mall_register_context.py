"""The sole owner of registration DB writes and guarded, token-aware calls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import httpx

from .. import repo
from ..config import is_allowed_mall, registration_enabled
from ..detail_publications import publication_files
from ..services.cafe24_client import Cafe24ApiError
from ..services.cafe24_tokens import call_with_token

_sleep = asyncio.sleep


class RegistrationStopped(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def recheck_registration(app, job: dict) -> None:
    payload, metadata, settings = job['payload'], job.get('metadata') or {}, app.state.settings
    async with app.state.pool.connection() as conn:
        if not job.get('lease_token') or not await repo.renew_job_lease(conn, job['id'], job['lease_token']):
            raise RegistrationStopped('lease_lost')
        code = None
        if payload['mall'] != 'cafe24':
            code = 'registration_preparing'
        elif not registration_enabled(settings):
            code = 'cafe24_disabled'
        elif not is_allowed_mall(settings, payload['shopId']):
            code = 'cafe24_not_allowed'
        elif await repo.user_account_purge_closed(conn, job['user_id']):
            code = 'account_closed'
        else:
            row = await repo.get_cafe24_connection_by_id(conn, user_id=job['user_id'], connection_id=payload['connectionId'])
            if (not row or row['id'] != payload['connectionId'] or row['status'] != 'connected'
                    or row['user_id'] != job['user_id'] or row['mall'] != payload['mall'] or row['shop_id'] != payload['shopId']):
                code = 'connection_changed'
            elif not row.get('refresh_expires_at') or row['refresh_expires_at'] <= datetime.now(timezone.utc):
                code = 'reconnect_required'
            elif await repo.is_job_cancelled(conn, job['id']):
                code = 'cancelled'
        checkpoints = metadata.get('checkpoints') or {}
        # Only require resources that still have to be consumed. Lookup recovery and
        # already copied detail images do not depend on the publication's lifetime.
        creating = not metadata.get('productId') and not (metadata.get('create') or {}).get('sentAt')
        if not code and creating and checkpoints.get('uploading_images', {}).get('status') != 'complete':
            publication = await repo.get_detail_publication(conn, user_id=job['user_id'], publication_id=payload['publicationId'])
            if (not publication or publication['status'] != 'ready' or publication['project_id'] != job['project_id']
                    or publication.get('editor_revision') != payload['editorRevision']):
                code = 'image_upload_mismatch'
            else:
                try:
                    if publication_files(publication) != payload['snapshot']['publication']['files']:
                        code = 'image_upload_mismatch'
                except ValueError:
                    code = 'image_upload_mismatch'
        if not code and not (checkpoints.get('activating') or {}).get('sentAt') and (creating or metadata.get('productId')):
            for index, image in enumerate(payload['snapshot']['images']):
                stage = 'uploading_thumbnail' if index == 0 else 'uploading_additional_images'
                if checkpoints.get(stage, {}).get('status') == 'complete':
                    continue
                asset = await repo.get_project_asset_for_user(conn, job['user_id'], job['project_id'], image['assetId'])
                if not asset or asset['r2_key'] != image['r2Key'] or asset['mime_type'] != image['mimeType']:
                    code = 'image_upload_mismatch'
                    break
        await conn.commit()
    if code:
        raise RegistrationStopped(code)


class RegistrationJobContext:
    def __init__(self, app, job, *, client, storage):
        self.app, self.job, self.client, self.storage = app, job, client, storage
        self.snapshot = job['payload']['snapshot']
        self.client.before_request = self.recheck

    async def recheck(self) -> None:
        await recheck_registration(self.app, self.job)

    async def checkpoint(self, patch: dict) -> None:
        async with self.app.state.pool.connection() as conn:
            changed = await repo.set_job_metadata_patch(conn, job_id=self.job['id'], patch=patch,
                                                        lease_token=self.job['lease_token'])
            if changed and patch.get('stage') == 'uploading_images' and (patch.get('checkpoints') or {}).get('uploading_images', {}).get('urls'):
                images = (patch.get('checkpoints') or {}).get('uploading_images') or {}
                progress = int(60 * len(images.get('urls') or []) / len(self.snapshot['publication']['files']))
                changed = await repo.set_job_progress_fenced(conn, job_id=self.job['id'], progress=progress,
                                                            lease_token=self.job['lease_token'])
            await conn.commit()
        if not changed:
            raise RegistrationStopped('lease_lost')
        self.job['metadata'].update(deepcopy(patch))

    async def mark_create_sent(self) -> None:
        await self.checkpoint(dict(stage='creating_product', create=dict(
            sentAt=datetime.now(timezone.utc).isoformat(),
            attempt=int((self.job['metadata'].get('create') or {}).get('attempt', 0)) + 1,
            leaseToken=self.job['lease_token'])))

    async def call(self, operation, *, effect: str):
        if effect not in {'read', 'image_copy', 'create', 'absolute_setup', 'activate'}:
            raise ValueError('invalid effect')
        for attempt in range(4):
            await self.recheck()

            async def guarded(token):
                # A token refresh can wait for the connection lock. Recheck after it,
                # and before every transmission, including the existing 401 rotation.
                await self.recheck()
                if effect == 'absolute_setup' and (self.job['metadata'].get('checkpoints', {}).get('activating') or {}).get('sentAt'):
                    raise Cafe24ApiError('remote_product_changed')
                if effect == 'create':
                    await self.mark_create_sent()
                    await self.recheck()
                try:
                    return await operation(token)
                except httpx.HTTPStatusError as exc:
                    if effect == 'create' and exc.response.status_code in (400, 422):
                        await self.checkpoint(dict(rejectedAt=datetime.now(timezone.utc).isoformat(), code='invalid_request'))
                        raise Cafe24ApiError('invalid_request') from None
                    if exc.response.status_code == 401:
                        await self.recheck()
                    raise

            try:
                return await call_with_token(self.app, user_id=self.job['user_id'],
                    connection_id=self.job['payload']['connectionId'], fn=guarded)
            except RegistrationStopped:
                raise
            except Exception as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                retryable = status == 429 or (status is not None and status >= 500) or isinstance(exc, (httpx.TransportError, TimeoutError))
                if effect not in {'read', 'image_copy', 'absolute_setup'} or not retryable or attempt == 3:
                    raise
                raw = exc.response.headers.get('Retry-After', '1') if status == 429 else ''
                delay = min(60, int(raw)) if raw.isascii() and raw.isdigit() else (1 if status == 429 else 2 ** attempt)
                await _sleep(delay)
