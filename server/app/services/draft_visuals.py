"""Optional pre-confirmation visuals. Jobs are also the durable cost/promotion ledger."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app import repo
from app.agents import mannequin
from app.r2 import derived_key
from app.services import canonical_reference, fal_api, fal_cutout, garment_grid, matching_cutout, product_tidy, qwen_flatlay

VERSION = 'draft-visuals-v1-qwen-sam3'
MODE = 'draft_visuals'
DAILY_LIMIT = 20
CONCURRENT_LIMIT = 2
DELIVERY_ATTEMPT_LIMIT = 3


def idempotency_key(user_id, role, hashes):
    digest = hashlib.sha256('|'.join(hashes).encode()).hexdigest()
    return f'{user_id}:{MODE}:{role}:{VERSION}:{digest}'


def current_sources(slot, role, local_id=None):
    payload = slot.get('payload') or {}
    if role == 'product':
        return [aid for view, aid in mannequin.base_color_images(payload.get('product') or {})
                if view == 'Front'][:1]
    custom = payload.get('customMatch') or {}
    if role != 'custom' or custom.get('localId') != local_id:
        return []
    return [source['id'] for source in custom.get('sources') or [] if source.get('id')]


def reference(role, source_asset_ids, local_id=None):
    return {'role': role, 'sourceAssetIds': source_asset_ids,
            **({'localId': local_id} if role == 'custom' else {})}


async def owned_slot(conn, user_id, token):
    slot = await repo.lock_draft_slot(conn, user_id)
    if not slot or slot.get('expires_at') <= datetime.now(timezone.utc):
        raise HTTPException(409, detail={'code': 'token_mismatch', 'message': '임시저장 작업권이 만료됐어요.'})
    if not token or token != slot.get('active_token'):
        raise HTTPException(409, detail={'code': 'token_mismatch', 'message': '다른 기기에서 이어서 작업을 시작했어요.'})
    return slot


def presentation(app, job, *, role, source_asset_ids, local_id=None):
    result = (job or {}).get('result') or {}
    state = ('processing' if job and job.get('status') in ('pending', 'running') else
             'ready' if result.get('state') == 'ready' else 'failed')
    out = {'state': state, 'jobId': (job or {}).get('id'), 'sourceAssetIds': source_asset_ids}
    url = lambda asset: app.state.r2.public_url(asset['key']) if asset else None
    if role == 'product':
        tidy, cutout = result.get('tidy'), result.get('cutout')
        display = (result.get('board') or tidy) if tidy else None
        out['front'] = {'tidyStatus': 'ready' if tidy else state, 'tidyUrl': url(display),
                        'cutoutStatus': 'ready' if cutout else state, 'cutoutUrl': url(cutout),
                        'width': (display or cutout or {}).get('width', 0),
                        'height': (display or cutout or {}).get('height', 0)}
    else:
        out.update(localId=local_id, boardUrl=url(result.get('board')),
                   thumbnailUrl=url(result.get('thumb')), cutoutStatus=state)
    return out


async def current_presentation(app, conn, job, *, user_id, role, source_asset_ids, local_id=None):
    # Jobs outlive the seven-day objects. A retained idempotency key is not proof
    # that its former output is still an owned, live asset.
    current = {**job, 'result': dict(job.get('result') or {})}
    result = current['result']
    for part in ('cutout', 'tidy', 'thumb', 'grid', 'board'):
        asset = result.get(part)
        if not asset:
            continue
        row = await repo.get_asset_for_user(conn, user_id, asset['assetId'])
        if (not row or row.get('source') != 'derived' or row.get('r2_key') != asset.get('key')
                or row.get('deleted_at')):
            result.pop(part, None)
    if result.get('state') == 'ready' and not result.get('tidy' if role == 'product' else 'board'):
        result['state'] = 'failed'
    return presentation(app, current, role=role, source_asset_ids=source_asset_ids, local_id=local_id)


async def prepare(app, conn, *, user_id, token, role, source_asset_ids, local_id=None):
    off = {'state': 'off', 'jobId': None, 'sourceAssetIds': source_asset_ids}
    if getattr(app.state.settings, 'draft_visuals', 'off') != 'on':
        return off
    slot = await owned_slot(conn, user_id, token)
    if not source_asset_ids or current_sources(slot, role, local_id) != source_asset_ids:
        raise HTTPException(404, detail={'code': 'source_changed', 'message': '현재 사진을 다시 확인해 주세요.'})
    sources = []
    for aid in source_asset_ids:
        asset = await repo.get_asset_for_user(conn, user_id, aid)
        if not asset or asset.get('source') != 'upload' or (asset.get('metadata') or {}).get('purpose') != 'draft_slot':
            raise HTTPException(404, detail={'code': 'not_found', 'message': '사진을 찾을 수 없어요.'})
        raw = await asyncio.to_thread(app.state.r2.get_bytes, asset['r2_key'])
        sources.append({'id': aid, 'key': asset['r2_key'], 'hash': hashlib.sha256(raw).hexdigest()})
    key = idempotency_key(user_id, role, [a['hash'] for a in sources])
    await repo.lock_draft_visual_budget(conn)
    job = await repo.find_draft_visual_job(conn, user_id, key)
    ref = reference(role, source_asset_ids, local_id)
    if job:
        await repo.bind_draft_visual_reference(conn, job['id'], ref)
        return await current_presentation(app, conn, job, user_id=user_id, role=role,
                                          source_asset_ids=source_asset_ids, local_id=local_id)
    usage = await repo.draft_visual_usage(conn, user_id)
    # Conservative reservation covers all source masks, Qwen, output mask and BiRefNet fallback.
    reserve = .05 + .005 * (len(sources) - 1)
    budget = float(getattr(app.state.settings, 'draft_visuals_daily_budget_usd', 5))
    if (not math.isfinite(budget) or usage['daily'] >= DAILY_LIMIT or usage['active'] >= CONCURRENT_LIMIT
            or float(usage['reserved']) + reserve > budget):
        return {**off, 'reason': 'limit'}
    job, _ = await repo.create_job(conn, user_id=user_id, project_id=None,
        kind='sam_preprocess' if role == 'product' else 'matching_cutout',
        payload={'mode': MODE, 'role': role, 'version': VERSION, 'sources': sources,
                 'sourceAssetIds': source_asset_ids, 'references': [ref]},
        idempotency_key=key, credits_reserved=0, metadata={'reservedUsd': reserve})
    return presentation(app, job, role=role, source_asset_ids=source_asset_ids, local_id=local_id)


async def visuals(app, conn, *, user_id, token):
    if getattr(app.state.settings, 'draft_visuals', 'off') != 'on':
        return {'state': 'off', 'product': None, 'custom': None}
    slot = await owned_slot(conn, user_id, token)
    out = {'state': 'ready', 'product': None, 'custom': None}
    local_id = ((slot.get('payload') or {}).get('customMatch') or {}).get('localId')
    for role in ('product', 'custom'):
        ids = current_sources(slot, role, local_id)
        if not ids:
            continue
        job = await repo.get_draft_visual_reference(conn, user_id, reference(role, ids, local_id))
        if job:
            out[role] = await current_presentation(app, conn, job, user_id=user_id, role=role,
                                                   source_asset_ids=ids, local_id=local_id)
    return out


async def store(app, conn, job, part, data, metadata, *, project_id=None, asset_id=None, mime='image/png'):
    uid = job['user_id']
    aid = asset_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f'{uid}:{job["id"]}:{part}:{VERSION}'))
    key = derived_key(uid, project_id or 'draft-slot', aid, 'jpg' if mime == 'image/jpeg' else 'png')
    await asyncio.to_thread(app.state.r2.put_bytes, key, data, mime)
    meta = {**metadata, 'r2Key': key, 'draftVisualJobId': job['id'], 'purpose': 'draft_visuals' if project_id is None
            else metadata.get('purpose', 'product_tidy')}
    await repo.create_asset(conn, asset_id=aid, user_id=uid, project_id=project_id, source='derived',
        bucket=app.state.settings.r2_bucket, key=key, mime=mime, size=len(data),
        original_filename=None, metadata=meta)
    return {'assetId': aid, 'key': key, 'mime': mime, 'metadata': meta,
            'width': meta.get('width', 0), 'height': meta.get('height', 0)}


async def apply_promotions(app, conn, job, result, *, targets=None):
    promotions = (job.get('metadata') or {}).get('promotions') or []
    if result.get('state') != 'ready':
        return
    for target in promotions if targets is None else targets:
        if target.get('applied'):
            continue
        pid, ids = target['projectId'], target['sourceAssetIds']
        if not await repo.get_project(conn, job['user_id'], pid):
            continue
        if job['payload']['role'] == 'product':
            product = await repo.get_product(conn, pid) or {}
            current = [aid for view, aid in mannequin.base_color_images(product) if view == 'Front'][:1]
            if current and current != ids:
                continue
            project_cutout_id = None
            project_board_id = None
            digest = job['payload']['sources'][0]['hash']
            tidy_id = product_tidy.asset_id(job['user_id'], pid, digest)
            for part in ('cutout', 'board', 'tidy'):
                source = result.get(part)
                if not source:
                    continue
                aid = (tidy_id if part == 'tidy' else
                       product_tidy.board_asset_id(job['user_id'], pid, digest) if part == 'board' else
                       str(uuid.uuid5(uuid.NAMESPACE_URL, f'{job["user_id"]}:{pid}:canonical-front:{digest}:{VERSION}')))
                data = await asyncio.to_thread(app.state.r2.get_bytes, source['key'])
                meta = {**source['metadata'], 'sourceAssetId': ids[0], 'sourceAssetIds': ids,
                        'sourceHash': digest, 'view': 'Front'}
                if part == 'tidy':
                    meta['cutoutAssetId'] = project_cutout_id
                    if project_board_id:
                        meta['boardAssetId'] = project_board_id
                elif part == 'board':
                    meta.update(tidyAssetId=tidy_id, cutoutAssetId=project_cutout_id)
                copied = await store(app, conn, job, part, data, meta, project_id=pid, asset_id=aid)
                if part == 'cutout':
                    project_cutout_id = copied['assetId']
                elif part == 'board':
                    project_board_id = copied['assetId']
        else:
            item_id = target.get('matchingItemId')
            if not item_id:
                continue  # Source-only promotion records are bound when the item is registered.
            original_grid = await repo.get_matching_item_asset(conn, item_id, job['user_id'], pid)
            if not original_grid:
                continue
            copies = {}
            for part in ('thumb', 'grid', 'board'):
                source = result.get(part)
                if not source:
                    continue
                data = await asyncio.to_thread(app.state.r2.get_bytes, source['key'])
                aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{job["id"]}:{pid}:{item_id}:{part}:{VERSION}'))
                meta = {**source['metadata'], 'matchingItemId': item_id,
                        'sourceAssetId': original_grid, 'sourceAssetIds': [*ids, original_grid],
                        'purpose': matching_cutout.GRID_PURPOSE if part == 'grid' else matching_cutout.CUTOUT_PURPOSE}
                if part == 'board':
                    meta.update(sourceAssetId=copies['grid']['assetId'], thumbnailAssetId=copies['thumb']['assetId'])
                copies[part] = await store(app, conn, job, part, data, meta, project_id=pid,
                                          asset_id=aid, mime=source['mime'])
            if not await repo.swap_matching_item_assets(conn, matching_item_id=item_id, project_id=pid,
                    thumbnail_asset_id=copies['thumb']['assetId'], image_asset_id=copies['grid']['assetId']):
                continue
        target['applied'] = True
        target['deliveryState'] = 'applied'
    await repo.update_draft_visual_job(conn, job['id'], metadata={'promotions': promotions})


async def result_assets_live(conn, job):
    result = job.get('result') or {}
    primary = 'board' if (job.get('payload') or {}).get('role') == 'custom' else 'tidy'
    if result.get('state') != 'ready' or not result.get(primary):
        return False
    for part in ('cutout', 'tidy', 'thumb', 'grid', 'board'):
        asset = result.get(part)
        if not asset:
            continue
        if not asset.get('assetId') or not asset.get('key'):
            return False
        row = await repo.get_asset_for_user(conn, job['user_id'], asset['assetId'])
        if (not row or row.get('source') != 'derived' or row.get('r2_key') != asset.get('key')
                or row.get('deleted_at')):
            return False
    return True


async def promotion_covers(conn, job, project_id, source_asset_ids, matching_item_id=None):
    """Only delivered output or durable, recoverable work suppresses the project pipeline."""
    target = next((p for p in ((job or {}).get('metadata') or {}).get('promotions', [])
                   if p['projectId'] == project_id and p['sourceAssetIds'] == source_asset_ids
                   and p.get('matchingItemId') == matching_item_id), None)
    if not target:
        return False
    if job.get('status') in ('pending', 'running') and (job.get('result') or {}).get('state') != 'ready':
        return True
    if not await result_assets_live(conn, job):
        return False
    return (target.get('applied') or target.get('deliveryState') == 'pending'
            and target.get('deliveryAttempts', 0) < DELIVERY_ATTEMPT_LIMIT)


async def deliver(app, conn, job, *, result=None):
    # The durable target is written outside this savepoint. Storage/DB failure
    # rolls back copies only. The periodic consumer retries delivery, never models.
    result = result if result is not None else job.get('result') or {}
    if result.get('state') != 'ready':
        return
    promotions = (job.get('metadata') or {}).get('promotions') or []
    targets = [target for target in promotions if not target.get('applied')
               and target.get('deliveryAttempts', 0) < DELIVERY_ATTEMPT_LIMIT]
    if not targets:
        return
    for target in targets:
        target['deliveryAttempts'] = target.get('deliveryAttempts', 0) + 1
    # Persist attempts before the copy savepoint so failed copies still spend one.
    await repo.update_draft_visual_job(conn, job['id'], metadata={'promotions': promotions})
    try:
        async with conn.transaction():
            await apply_promotions(app, conn, job, result, targets=targets)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('draft promotion deferred job=%s', job['id'])
        # apply_promotions may have mutated the snapshot before the savepoint failed.
        current = await repo.lock_draft_visual_job(conn, job['user_id'], job['id'])
        job.update(current)


async def join_project(app, conn, *, user_id, project_id, source_asset_ids, role='product', matching_item_id=None):
    """Discover lost handoffs from owned project bytes, including the processing version."""
    try:
        # A failed optional SQL lookup must not leave the project queue transaction aborted.
        async with conn.transaction():
            if not source_asset_ids or not await repo.get_project(conn, user_id, project_id):
                return None
            assets = await repo.get_uploaded_assets_for_project(conn, user_id, project_id, source_asset_ids)
            by_id = {a['id']: a for a in assets}
            if len(by_id) != len(source_asset_ids):
                return None
            hashes = [hashlib.sha256(await asyncio.to_thread(app.state.r2.get_bytes, by_id[aid]['r2_key'])).hexdigest()
                      for aid in source_asset_ids]
            job = await repo.find_draft_visual_job(conn, user_id, idempotency_key(user_id, role, hashes))
            if not job:
                return None
            promoted = await promote(app, conn, user_id=user_id, job_id=job['id'], project_id=project_id,
                source_asset_ids=source_asset_ids, matching_item_id=matching_item_id)
            return await repo.lock_draft_visual_job(conn, user_id, job['id']) if promoted['joined'] else None
    except Exception:
        import logging
        logging.getLogger(__name__).warning('draft join unavailable project=%s', project_id, exc_info=True)
        return None


async def retry_project_deliveries(app, conn, *, user_id=None, project_id=None):
    if project_id is not None and not await repo.get_project(conn, user_id, project_id):
        return
    for job in await repo.pending_draft_visual_deliveries(
            conn, user_id, project_id, max_attempts=DELIVERY_ATTEMPT_LIMIT):
        for target in job['metadata']['promotions']:
            if target['projectId'] == project_id and not target.get('applied'):
                target['deliveryState'] = 'pending'
        await deliver(app, conn, job)


async def promote(app, conn, *, user_id, job_id, project_id, source_asset_ids, matching_item_id=None):
    # Same row lock as completion: whichever arrives second observes the other's write.
    job = await repo.lock_draft_visual_job(conn, user_id, job_id)
    if not job or not await repo.get_project(conn, user_id, project_id):
        raise HTTPException(404, detail={'code': 'not_found', 'message': '정돈 작업을 찾을 수 없어요.'})
    if ((job.get('payload') or {}).get('role') == 'custom') != bool(matching_item_id):
        raise HTTPException(409, detail={'code': 'source_changed', 'message': '정돈 대상이 달라요.'})
    if (job.get('result') or {}).get('state') == 'ready' and not await result_assets_live(conn, job):
        return {'joined': False, 'jobId': job['id']}
    sources = (job.get('payload') or {}).get('sources') or []
    assets = await repo.get_uploaded_assets_for_project(conn, user_id, project_id, source_asset_ids)
    by_id = {a['id']: a for a in assets}
    if len(sources) != len(source_asset_ids) or len(by_id) != len(source_asset_ids):
        raise HTTPException(404, detail={'code': 'source_changed', 'message': '현재 사진을 다시 확인해 주세요.'})
    for original, aid in zip(sources, source_asset_ids):
        raw = await asyncio.to_thread(app.state.r2.get_bytes, by_id[aid]['r2_key'])
        if hashlib.sha256(raw).hexdigest() != original['hash']:
            raise HTTPException(409, detail={'code': 'source_changed', 'message': '사진이 바뀌어 정돈 결과를 건너뛰었어요.'})
    target = {'projectId': project_id, 'sourceAssetIds': source_asset_ids, 'deliveryState': 'pending',
              **({'matchingItemId': matching_item_id} if matching_item_id else {})}
    promotions = (job.get('metadata') or {}).get('promotions') or []
    existing = next((p for p in promotions if p['projectId'] == project_id and p['sourceAssetIds'] == source_asset_ids
                     and p.get('matchingItemId') == matching_item_id), None)
    if not existing:
        promotions.append(target)
    elif not existing.get('applied'):
        existing['deliveryState'] = 'pending'
    job['metadata'] = {**(job.get('metadata') or {}), 'promotions': promotions}
    await repo.update_draft_visual_job(conn, job['id'], metadata={'promotions': promotions})
    await deliver(app, conn, job)
    return {'joined': True, 'jobId': job['id']}


async def run(app, job):
    """One paid attempt per durable job, including lease recovery and provider errors."""
    pool = app.state.pool
    uid, jid, lease = job['user_id'], job['id'], job['lease_token']

    async def guard():
        async with pool.connection() as conn:
            current = await repo.lock_draft_visual_job(conn, uid, jid)
            if (not current or current.get('lease_token') != lease or current.get('status') != 'running'
                    or getattr(app.state.settings, 'draft_visuals', 'off') != 'on'
                    or not await repo.draft_visual_is_current(conn, current)):
                raise RuntimeError('draft_source_no_longer_current')

    async def finish(result):
        async with pool.connection() as conn:
            current = await repo.lock_draft_visual_job(conn, uid, jid)
            if not current or current.get('lease_token') != lease or current.get('status') != 'running':
                return
            # A promotion/storage failure cannot discard paid outputs or leave a running job.
            try:
                async with conn.transaction():
                    await deliver(app, conn, current, result=result)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('draft promotion deferred job=%s', jid)
            await repo.finalize_uncharged_job(conn, job_id=jid, lease_token=lease,
                status='done', result=result, product_tidy_capable=True)
            await conn.commit()

    async with pool.connection() as conn:
        # Same global lock order as preparation: budget, then job. A queued job
        # from yesterday must reserve today's budget BEFORE any provider call.
        await repo.lock_draft_visual_budget(conn)
        current = await repo.lock_draft_visual_job(conn, uid, jid)
        if not current or current.get('lease_token') != lease or current.get('status') != 'running':
            return
        already_started = (current.get('metadata') or {}).get('paidStarted')
        budget_exceeded = False
        if not already_started:
            usage = await repo.draft_visual_usage(conn, uid, exclude_job_id=jid)
            budget = float(getattr(app.state.settings, 'draft_visuals_daily_budget_usd', 5))
            reserve = float((current.get('metadata') or {}).get('reservedUsd', .05))
            budget_exceeded = not math.isfinite(budget) or float(usage['reserved']) + reserve > budget
            if not budget_exceeded:
                await repo.update_draft_visual_job(conn, jid,
                    metadata={'paidStarted': True, 'budgetDate': usage['budget_day']})
            await conn.commit()  # Durable BEFORE any paid POST, even when its response is lost.
    if budget_exceeded:
        await finish({'state': 'failed', 'reason': 'daily_budget'})
        return
    if already_started:
        await finish({'state': 'failed', 'reason': 'uncertain_paid_attempt'})
        return
    try:
        await guard()
        payload = job['payload']
        if payload.get('version') != VERSION:
            raise ValueError('unsupported_draft_version')
        cuts, canonical = [], None
        with fal_api.draft_request_scope(guard):
            for source in payload['sources']:
                raw = await asyncio.to_thread(app.state.r2.get_bytes, source['key'])
                if hashlib.sha256(raw).hexdigest() != source['hash']:
                    raise ValueError('source_changed')
                views = await fal_cutout.segment_garment(app.state.settings, app.state.r2, {'Front': source['key']})
                view = views.get('Front')
                if not view or not view.ready or view.source_hash != source['hash']:
                    raise ValueError('cutout_unavailable')
                cut = await asyncio.to_thread(app.state.r2.get_bytes, view.cutout_key)
                flat = await asyncio.to_thread(matching_cutout.flatten_on_bg, cut)
                agrees, _ = await asyncio.to_thread(matching_cutout.cutout_color_agrees, raw, flat)
                if not agrees:
                    raise ValueError('cutout_color_mismatch')
                cuts.append((cut, flat))
                if canonical is None:
                    meta = canonical_reference.metadata_for('Front', view, source['id'])
                    async with pool.connection() as conn:
                        canonical = await store(app, conn, job, 'cutout', cut, meta)
                        await conn.commit()
            async def checkpoint(raw):
                async with pool.connection() as conn:
                    await store(app, conn, job, 'qwen-raw', raw, {'canonicalType': 'draft_qwen_raw'})
                    await conn.commit()
            tidy_png = await qwen_flatlay.render(app.state.settings, cuts[0][0], transparent=True,
                                                on_generated=checkpoint)
        width, height = await asyncio.to_thread(product_tidy._png_size, tidy_png)
        meta = {'canonicalType': 'product_tidy', 'view': 'Front', 'sourceAssetId': payload['sources'][0]['id'],
                'sourceHash': payload['sources'][0]['hash'], 'cutoutAssetId': canonical['assetId'],
                'algorithmVersion': product_tidy.ALGORITHM_VERSION, 'width': width, 'height': height}
        async with pool.connection() as conn:
            result = {'state': 'ready', 'cutout': canonical}
            if payload['role'] == 'product':
                board = await asyncio.to_thread(matching_cutout.encode_board_cutout, tidy_png)
                board_width, board_height = await asyncio.to_thread(product_tidy._png_size, board)
                result['board'] = await store(app, conn, job, 'board', board,
                    {**meta, 'canonicalType': 'product_tidy_board', 'width': board_width, 'height': board_height})
                result['tidy'] = await store(app, conn, job, 'tidy', tidy_png, meta)
            else:
                grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid,
                    [await asyncio.to_thread(matching_cutout.flatten_on_bg, tidy_png), *[flat for _, flat in cuts[1:]]])
                thumb = await asyncio.to_thread(matching_cutout.encode_thumbnail,
                    await asyncio.to_thread(matching_cutout.flatten_on_bg, tidy_png))
                board = await asyncio.to_thread(matching_cutout.encode_board_cutout, tidy_png)
                meta.update(type=matching_cutout.CUTOUT_KIND, canonicalType='custom_board_cutout')
                result['thumb'] = await store(app, conn, job, 'thumb', thumb, meta, mime='image/jpeg')
                result['grid'] = await store(app, conn, job, 'grid', grid_bytes, {**meta, 'canonicalType': 'custom_grid'}, mime='image/jpeg')
                result['board'] = await store(app, conn, job, 'board', board, meta)
            await conn.commit()
        await finish(result)
    except Exception as exc:
        # Includes uncertain provider POSTs: terminal and excluded from the retry pusher.
        # Only known codes are safe to log; arbitrary messages can contain source keys or credentials.
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in (
            'unsupported_draft_version', 'source_changed', 'cutout_unavailable', 'cutout_color_mismatch') else None
        logging.getLogger(__name__).warning('draft preparation failed job=%s exception=%s code=%s',
                                           jid, type(exc).__name__, code)
        await finish({'state': 'failed', 'reason': 'preparation_failed'})
