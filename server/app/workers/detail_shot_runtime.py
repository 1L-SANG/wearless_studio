"""Source resolution and durable attempt state for product/detail only."""
from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO

from psycopg.types.json import Jsonb
from PIL import Image, ImageOps

from .. import repo
from ..agents import cut_generator, detail_output_qc, detail_recommendations, mannequin
from ..agents.detail_shot_pipeline import DetailShotRejected, generate_verified, fingerprint
from ..agents.gemini_image import InlineImage
from ..r2 import PRIVATE_NO_STORE


class ProgressStore:
    """Each claim commits before a provider request; job lease guards every write.

    An interrupted in-flight request stays consumed. Candidate bytes are private
    checkpoints, not published assets. Existing cleanup intents expire them in 24h.
    Explicit seller retry creates a new job; lease recovery cannot reset this one.
    """
    def __init__(self, app, job, block_id):
        self.app, self.job = app, job
        self.key = hashlib.sha256(str(block_id).encode()).hexdigest()

    async def _change(self, mutate=None):
        async with self.app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select metadata from jobs where id = %s and user_id = %s "
                    "and project_id = %s and status = 'running' and locked_by = %s for update",
                    (self.job['id'], self.job['user_id'], self.job['project_id'], self.job['lease_token']))
                row = await cur.fetchone()
                if row is None:
                    raise DetailShotRejected('detail_job_lease_lost')
                state = dict(((row.get('metadata') or {}).get('detailShots') or {}).get(self.key) or {})
                if mutate:
                    state = mutate(state)
                    await cur.execute(
                        "update jobs set metadata = jsonb_set(coalesce(metadata, '{}'::jsonb), "
                        "'{detailShots}', coalesce(metadata->'detailShots', '{}'::jsonb) || %s::jsonb) "
                        "where id = %s and locked_by = %s and status = 'running'",
                        (Jsonb({self.key: state}), self.job['id'], self.job['lease_token']))
            await conn.commit()
        return state

    async def read(self):
        return await self._change()

    async def claim(self, fingerprint):
        def claim(state):
            attempts = state.get('attempts', 0)
            qc = state.get('qc') or {}
            judgments = qc.get('judgments') or []
            third_allowed = (state.get('phase') == 'retryable'
                             or (state.get('phase') == 'judged'
                                 and qc.get('attempts') == 2
                                 and [j.get('decision') for j in judgments]
                                 in (['FAIL'], ['FAIL', 'FAIL'])))
            if (state.get('phase') in {'generating', 'judging', 'rejudging', 'accepted', 'held'}
                    or attempts >= 3
                    or (attempts == 2 and not third_allowed)
                    or state and state.get('fingerprint') != fingerprint):
                raise DetailShotRejected('attempt_budget_exhausted')
            return {**state, 'fingerprint': fingerprint,
                    'attempts': attempts + 1, 'phase': 'generating'}
        return (await self._change(claim))['attempts']

    async def save_candidate(self, image, fingerprint, attempt, *, image_quality='medium'):
        sha = hashlib.sha256(image.data).hexdigest()
        job = self.job
        key = (f"users/{job['user_id']}/projects/{job['project_id']}/ai/{job['id']}/"
               f"detail-checkpoints/{self.key}/{attempt}-{sha}.bin")
        async with self.app.state.pool.connection() as conn:
            intent = await repo.create_ai_checkpoint_intent(
                conn, job_id=job['id'], r2_key=key, ttl_seconds=24 * 3600)
            await conn.commit()
        if not intent:
            raise DetailShotRejected('detail_checkpoint_unavailable')
        await asyncio.to_thread(self.app.state.r2.put_bytes, key, image.data, image.mime,
                                cache=PRIVATE_NO_STORE)

        def save(state):
            if (state.get('fingerprint') != fingerprint or state.get('attempts') != attempt
                    or state.get('phase') != 'generating'):
                raise DetailShotRejected('detail_attempt_state_changed')
            candidate = {'attempt': attempt, 'key': key, 'sha256': sha, 'mime': image.mime,
                         'imageQuality': image_quality}
            return {**state, 'phase': 'candidate', 'candidate': candidate,
                    'candidates': [*(state.get('candidates') or []), candidate]}
        await self._change(save)

    async def load_candidate(self, attempt=None):
        state = await self.read()
        candidate = (state.get('candidate') or {}) if attempt is None else next(
            (row for row in state.get('candidates') or [] if row.get('attempt') == attempt), {})
        job = self.job
        prefix = (f"users/{job['user_id']}/projects/{job['project_id']}/ai/{job['id']}/"
                  f"detail-checkpoints/{self.key}/")
        if not str(candidate.get('key', '')).startswith(prefix):
            return None
        try:
            data = await asyncio.to_thread(self.app.state.r2.get_bytes, candidate['key'])
        except Exception:
            return None
        if hashlib.sha256(data).hexdigest() != candidate.get('sha256'):
            return None
        return InlineImage(candidate['mime'], data)

    async def claim_judgment(self):
        def claim(state):
            if state.get('phase') != 'candidate':
                raise DetailShotRejected('detail_judgment_already_claimed')
            return {**state, 'phase': 'judging'}
        await self._change(claim)

    async def record_judgment(self, metadata):
        def record(state):
            if state.get('phase') not in {'judging', 'judged'}:
                raise DetailShotRejected('detail_judgment_state_changed')
            return {**state, 'phase': 'judged', 'qc': metadata}
        await self._change(record)

    async def claim_judgment_retry(self, fingerprint, attempt, candidate_hash):
        def claim(state):
            qc = state.get('qc') or {}
            judgments = qc.get('judgments') or []
            candidate = state.get('candidate') or {}
            retries = state.get('judgmentRetries') or []
            if (state.get('phase') != 'judged'
                    or state.get('fingerprint') != fingerprint
                    or state.get('attempts') != attempt
                    or qc.get('fingerprint') != fingerprint or qc.get('attempts') != attempt
                    or candidate.get('attempt') != attempt or candidate.get('sha256') != candidate_hash
                    or not judgments or judgments[-1].get('attempt') != attempt
                    or not detail_output_qc.is_transient_unknown(judgments[-1])
                    or any(row.get('attempt') == attempt for row in retries)):
                raise DetailShotRejected('detail_judgment_retry_unavailable')
            entry = {'attempt': attempt, 'fingerprint': fingerprint, 'candidateHash': candidate_hash,
                     'sourceHash': judgments[-1].get('sourceHash'),
                     'targetHash': judgments[-1].get('targetHash'),
                     'status': 'CLAIMED', 'initialJudgment': judgments[-1]}
            return {**state, 'phase': 'rejudging', 'judgmentRetries': [*retries, entry],
                    'judgmentRetryLease': self.job['lease_token']}
        return (await self._change(claim))['judgmentRetries']

    async def record_judgment_retry(self, metadata):
        def record(state):
            retries = list(state.get('judgmentRetries') or [])
            judgments = metadata.get('judgments') or []
            previous = (state.get('qc') or {}).get('judgments') or []
            candidate = state.get('candidate') or {}
            if (state.get('phase') != 'rejudging'
                    or state.get('judgmentRetryLease') != self.job['lease_token']
                    or metadata.get('fingerprint') != state.get('fingerprint')
                    or metadata.get('attempts') != state.get('attempts')
                    or not retries or retries[-1].get('status') != 'CLAIMED'
                    or retries[-1].get('attempt') != state.get('attempts')
                    or retries[-1].get('candidateHash') != candidate.get('sha256')
                    or len(judgments) != len(previous) or not judgments
                    or judgments[:-1] != previous[:-1]
                    or judgments[-1].get('attempt') != state.get('attempts')):
                raise DetailShotRejected('detail_judgment_retry_state_changed')
            retries[-1] = {**retries[-1], 'status': 'COMPLETE', 'judgment': judgments[-1]}
            return {**state, 'phase': 'judged', 'judgmentRetryLease': None,
                    'judgmentRetries': retries, 'qc': {**metadata, 'judgmentRetries': retries}}
        return (await self._change(record))['qc']

    async def recover_judgment_retry(self):
        def recover(state):
            retries = list(state.get('judgmentRetries') or [])
            if (state.get('phase') != 'rejudging'
                    or state.get('judgmentRetryLease') == self.job['lease_token']
                    or not retries or retries[-1].get('status') != 'CLAIMED'):
                raise DetailShotRejected('detail_judgment_retry_unavailable')
            # A lost response is consumed, even when the provider may not have received it.
            retries[-1] = {**retries[-1], 'status': 'INTERRUPTED'}
            return {**state, 'phase': 'judged', 'judgmentRetryLease': None,
                    'judgmentRetries': retries,
                    'qc': {**(state.get('qc') or {}), 'judgmentRetries': retries}}
        return await self._change(recover)

    async def record_retryable_failure(self, metadata):
        def record(state):
            if state.get('phase') != 'generating':
                raise DetailShotRejected('detail_attempt_state_changed')
            return {**state, 'phase': 'retryable', 'qc': metadata}
        await self._change(record)

    async def finish(self, metadata, *, selected_attempt=None):
        def finish(state):
            judgments = metadata.get('judgments') or []
            selected_judgment = next(
                (judgment for index, judgment in enumerate(judgments, 1)
                 if isinstance(judgment, dict)
                 and judgment.get('attempt', index) == selected_attempt), None)
            best_effort = (
                metadata.get('passed') is False
                and metadata.get('publishedBestEffort') is True
                and metadata.get('attempts') == 3
                and type(selected_attempt) is int
                and metadata.get('selectedAttempt') == selected_attempt
                and isinstance(judgments, list)
                and any(isinstance(j, dict) and j.get('decision') == 'FAIL' for j in judgments)
                and selected_judgment is not None
                and selected_judgment.get('errorCode') != 'source_photo_copy'
            )
            candidate = state.get('candidate')
            if selected_attempt is not None:
                if not best_effort:
                    raise DetailShotRejected('detail_checkpoint_unavailable')
                candidate = next((row for row in state.get('candidates') or []
                                  if row.get('attempt') == selected_attempt), None)
                if candidate is None:
                    raise DetailShotRejected('detail_checkpoint_unavailable')
            return {**state, 'qc': metadata, 'candidate': candidate,
                    'phase': 'accepted' if metadata.get('passed') is True or best_effort else 'held'}
        await self._change(finish)


async def _load(app, user_id, refs):
    assets = []
    async with app.state.pool.connection() as conn:
        for slot, asset_id in refs:
            asset = await repo.get_asset_for_user(conn, user_id, asset_id)
            if asset is None:
                raise DetailShotRejected('detail_source_unavailable')
            assets.append((slot, asset))
    async def image(asset):
        return InlineImage(asset['mime_type'], await asyncio.to_thread(app.state.r2.get_bytes, asset['r2_key']))
    images = await asyncio.gather(*(image(asset) for _, asset in assets))
    return images, [slot for slot, _ in assets]


async def resolve_sources(app, user_id, product, analysis, spec):
    direction = 'back' if spec.get('direction') == 'back' else 'front'
    valid_slots = {'Back', 'BackDetail'} if direction == 'back' else {'Front', 'Detail'}
    target_id = spec.get('detailTargetId')
    target = None
    framing_source_index = 0
    if target_id:
        colors = product.get('colors') or []
        base = next((c for c in colors if c.get('isBase')), colors[0] if colors else {})
        if spec.get('colorId') and str(spec['colorId']) != str(base.get('id')):
            raise DetailShotRejected('detail_target_color_changed')
        all_images, slots = await _load(app, user_id, mannequin.base_color_images(product))
        target = detail_recommendations.resolve_target(
            analysis, target_id, source_images=[(i.data, i.mime) for i in all_images],
            slots=slots, direction=direction)
        selected = next((i for i in all_images if hashlib.sha256(i.data).hexdigest() == target['sourceSha256']), None)
        if selected is None:
            raise DetailShotRejected('detail_target_source_changed')
        bound = {(row['sourceSlot'], row['sourceSha256'])
                 for row in analysis[detail_recommendations.PERSISTED_KEY]['binding']}
        sources = [selected, *(i for i, slot in zip(all_images, slots)
                               if slot in valid_slots and i is not selected
                               and (slot, hashlib.sha256(i.data).hexdigest()) in bound)]
    else:
        refs, transfer = cut_generator.detail_reference_images(product, spec.get('colorId'), direction=direction)
        refs = [(slot, aid) for slot, aid in refs if slot in valid_slots]
        sources, slots = await _load(app, user_id, refs)
        detail_slot = 'BackDetail' if direction == 'back' else 'Detail'
        framing_source_index = next((index for index, slot in enumerate(slots)
                                     if slot == detail_slot), 0)
        if transfer:
            # Existing cross-color closeups: chosen-color whole photo owns COLOR,
            # another color's detail owns construction only (ADR-0010).
            whole_slot = 'Back' if direction == 'back' else 'Front'
            color_image = next((i for i, slot in zip(sources, slots) if slot == whole_slot), None)
            detail_image = next((i for i, slot in zip(sources, slots) if slot == detail_slot), None)
            if color_image is None or detail_image is None:
                raise DetailShotRejected('detail_reference_required')
            target = {'kind': 'construction', 'direction': direction, 'colorTransfer': True,
                      'sourceSha256': hashlib.sha256(detail_image.data).hexdigest(),
                      'colorSourceSha256': hashlib.sha256(color_image.data).hexdigest()}
    if not sources:
        raise DetailShotRejected('detail_reference_required')
    if (analysis or {}).get('sourceMirrored') is True:
        # Preserve the existing AG-01 mirror contract. Verification above binds the
        # uploaded originals first; generation AND QC then see the same upright,
        # horizontally corrected pixels, so correct lettering isn't judged wrong.
        sources, target = await asyncio.to_thread(normalize_mirrored_sources, sources, target)
    return sources, target, direction, framing_source_index


def normalize_mirrored_sources(sources, target):
    hashes, normalized = {}, []
    for source in sources:
        with Image.open(BytesIO(source.data)) as raw:
            im = ImageOps.mirror(ImageOps.exif_transpose(raw)).convert('RGBA')
            out = BytesIO()
            im.save(out, format='PNG')
        data = out.getvalue()
        hashes[hashlib.sha256(source.data).hexdigest()] = hashlib.sha256(data).hexdigest()
        normalized.append(InlineImage('image/png', data))
    if target is not None:
        target = {**target, 'originalSourceSha256': target['sourceSha256'],
                  'sourceSha256': hashes[target['sourceSha256']]}
        if target.get('colorSourceSha256'):
            target['colorSourceSha256'] = hashes[target['colorSourceSha256']]
        if target.get('region'):
            region = target['region']
            target['region'] = {**region, 'x': max(0, 1 - region['x'] - region['w'])}
    return normalized, target


def recipe(spec):
    """Trusted provenance for subsequent editor edits of newly checked details."""
    return {'version': 1, 'shot': 'detail',
            'direction': 'back' if spec.get('direction') == 'back' else 'front',
            'colorId': spec.get('colorId'), 'detailTargetId': spec.get('detailTargetId'),
            'exampleId': spec.get('exampleId')}


async def generate(app, job, spec, product, analysis, *, style_reference=None, final_cache=None):
    sources, target, direction, framing_source_index = await resolve_sources(
        app, job['user_id'], product, analysis, spec)
    if style_reference is None and spec.get('exampleId'):
        style_reference = await cut_generator.load_example_image(
            app.state.settings, spec['exampleId'], scope='all',
            clothing_type=product.get('clothing_type') or product.get('clothingType') or 'top')
        if style_reference is None:
            raise DetailShotRejected('example_asset_unavailable')
    progress = ProgressStore(app, job, spec.get('id') or 'editor-detail')
    cache_entry = None
    if final_cache is not None:
        fp = fingerprint(app.state.settings, sources, target, direction, style_reference,
                         framing_source_index=framing_source_index)
        cache_entry = await final_cache.for_verified_detail(fp, spec.get('id'))
        if cache_entry.final is not None:
            await progress.read()  # Even a reused final must respect this job's lease.
            saved = cache_entry.final
            return InlineImage(saved.mime, saved.image), {**saved.meta['cutQc'], 'checkpointReused': True}
    result, report = await generate_verified(
        app.state.settings, app.state.gemini, sources, target=target, direction=direction,
        progress=progress,
        style_reference=style_reference, framing_source_index=framing_source_index)
    if cache_entry is not None:
        await cache_entry.save_final(result.data, result.mime, {
            'cutQc': report, 'warnings': [], 'neckRepair': None, 'facePass': {},
        })
    return result, report
