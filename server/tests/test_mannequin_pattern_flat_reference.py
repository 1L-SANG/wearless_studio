"""Offline request contracts for the optional, source-bound tidy reference."""
import asyncio
import copy
import hashlib
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from app import repo
from app.agents import mannequin
from app.agents.gemini_image import InlineImage
from app.agents.product_reference import ProductReference
from app.agents.prompts import load_prompt_template
from app.config import load_settings
from app.services import product_tidy
from app.services.qc import QcResult
from app.workers import mannequin_job as worker
from conftest import make_settings


FLAT_LABEL = ('FLAT REFERENCE: a clean flat-lay of the same sold garment; use it for the overall shape, '
              'proportions and length; take fabric texture, small details, logos and color from the product photos.')
SOURCE_HASH = hashlib.sha256(b'front').hexdigest()


def tidy_png():
    image = Image.new('RGBA', (768, 1024), (0, 0, 0, 0))
    image.putpixel((0, 0), (20, 40, 60, 128))
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def ready_row():
    return dict(id=product_tidy.asset_id('u', 'p', SOURCE_HASH), user_id='u', project_id='p',
                source='derived', r2_key='tidy', mime_type='image/png', metadata={
                    'canonicalType': 'product_tidy', 'view': 'Front', 'sourceAssetId': 'front',
                    'sourceHash': SOURCE_HASH, 'algorithmVersion': product_tidy.ALGORITHM_VERSION})


def test_switch_defaults_off_and_parses_on(monkeypatch):
    monkeypatch.delenv('MANNEQUIN_PATTERN_FLAT_REFERENCE', raising=False)
    assert load_settings().mannequin_pattern_flat_reference == 'off'
    assert make_settings().mannequin_pattern_flat_reference == 'off'
    monkeypatch.setenv('MANNEQUIN_PATTERN_FLAT_REFERENCE', ' ON ')
    assert load_settings().mannequin_pattern_flat_reference == 'on'
    monkeypatch.setenv('MANNEQUIN_PATTERN_FLAT_REFERENCE', 'invalid')
    assert load_settings().mannequin_pattern_flat_reference == 'off'


def test_checkerboard_name_is_a_fine_pattern():
    assert mannequin.has_fine_pattern({'name': '바둑판 니트'}, {})


@pytest.mark.parametrize('change', [None, {'canonicalType': 'canonical_cutout'},
    {'canonicalType': 'product_tidy_input'}, {'sourceAssetId': 'old-front'},
    {'sourceHash': 'old-hash'}, {'algorithmVersion': 'old-version'}, {'view': 'Back'}])
def test_ready_lookup_rejects_unrelated_or_stale_assets(monkeypatch, change):
    row = ready_row() if change is not None else None
    if row:
        row['metadata'].update(change)

    async def lookup(conn, uid, aid):
        assert (uid, aid) == ('u', product_tidy.asset_id('u', 'p', SOURCE_HASH))
        return row

    monkeypatch.setattr(repo, 'get_asset_for_user', lookup)
    result = asyncio.run(product_tidy.ready_front_asset(
        object(), user_id='u', project_id='p', source_id='front', source_hash=SOURCE_HASH))
    assert result is None


def test_ready_lookup_accepts_verified_upload_alias(monkeypatch):
    row = ready_row()
    row['metadata'].update(sourceAssetId='identical-old-upload', sourceAssetIds=['front'])

    async def lookup(*args):
        return row

    monkeypatch.setattr(repo, 'get_asset_for_user', lookup)
    assert asyncio.run(product_tidy.ready_front_asset(
        object(), user_id='u', project_id='p', source_id='front', source_hash=SOURCE_HASH)) == row


class Captured(BaseException):
    """Stop before a real provider, without being swallowed by worker error handling."""


def capture(monkeypatch, *, switch='on', patterned=True, ready=True, has_match=True,
            path='fresh', payload=None, retry=False, knit=False, style=False, mutation=None,
            broken_storage=False, adjusted=False, row_change=None, job_kind='mannequin',
            broken_lookup=False, missing_front=False):
    settings = make_settings(mannequin_max_attempts=2, image_qc='enforce' if retry else 'off')
    if switch is not None:
        object.__setattr__(settings, 'mannequin_pattern_flat_reference', switch)
    state = SimpleNamespace(row=ready_row() if ready else None, png=tidy_png(), reads=[],
                            lookups=[], events=[], calls=[])
    if row_change:
        state.row['metadata'].update(row_change)

    @asynccontextmanager
    async def connection():
        yield object()

    async def lookup(conn, uid, aid):
        state.lookups.append((uid, aid))
        if broken_lookup:
            raise OSError('unavailable asset DB')
        return copy.deepcopy(state.row)

    def read(key):
        state.reads.append(key)
        if broken_storage:
            raise OSError('unavailable tidy object')
        return state.png

    async def emit(pool, job_id, kind, event):
        state.events.append(event)

    class Provider:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            state.calls.append(dict(images=tuple(images), prompt=prompt, model=model, size=size, **kwargs))
            if mutation:
                mutation(state)
            if not retry or len(state.calls) == 2:
                raise Captured
            return SimpleNamespace(image=b'generated', mime='image/png')

    async def observe(**kwargs):
        return None, None

    async def edits(**kwargs):
        return kwargs['res'], kwargs['p2'], kwargs['calls_spent']

    async def series(**kwargs):
        return {'consistency': 40, 'inconsistencies': ['studio mismatch']}

    monkeypatch.setattr(repo, 'get_asset_for_user', lookup)
    monkeypatch.setattr(worker, '_emit', emit)
    monkeypatch.setattr(worker, '_observe_generation_qc', observe)
    monkeypatch.setattr(worker, '_apply_edits', edits)
    monkeypatch.setattr(worker, '_apply_series_qc', series)
    monkeypatch.setattr(worker.qc, 'evaluate_mannequin_qc', lambda _: QcResult('pass'))
    app = SimpleNamespace(state=SimpleNamespace(settings=settings,
        pool=SimpleNamespace(connection=connection), r2=SimpleNamespace(get_bytes=read), gemini=Provider()))
    refs = [ProductReference(slot, aid, InlineImage('image/png', data))
            for slot, aid, data in [('Front', 'front', b'front'), ('Back', 'back', b'back')]]
    if missing_front:
        refs = refs[1:]
    match = InlineImage('image/png', b'match') if has_match else None
    manifest = worker._build_manifest([{'slot': r.slot} for r in refs], has_match, 'top')
    if style:
        manifest += f'\n{len(refs) + 2 + int(has_match)}. STYLE REFERENCE, look only, not product construction.'
    with pytest.raises(Captured):
        asyncio.run(worker._run_candidate(app=app,
            job={'id': 'j', 'user_id': 'u', 'project_id': 'p', 'payload': payload or {}, 'kind': job_kind},
            candidate='A', base_fit='regular', base_gender='women',
            base_img=InlineImage('image/png', b'base'), prod_imgs=[r.image for r in refs], match_img=match,
            product_count=len(refs) + int(has_match), template=load_prompt_template(settings),
            product={'name': '체크 니트' if patterned else '무지 셔츠', 'clothing_type': 'top'},
            analysis={}, clothing_type='top', image_manifest=manifest, product_refs=refs,
            generation_path=path, parent_cut_img=InlineImage('image/png', b'parent') if path == 'edit' else None,
            adjust_directives='keep identity' if path == 'edit' or adjusted else '',
            ref_imgs=[InlineImage('image/png', b'style')] if style else (),
            canonical_refs={slot: ProductReference(slot, slot, InlineImage('image/png', slot.encode()))
                            for slot in ('CanonicalFront', 'CanonicalBack')} if knit else {}))
    return state, manifest


def verdict(state):
    events = [e['patternFlatReference'] for e in state.events if 'patternFlatReference' in e]
    assert len(events) == 1
    return events[0]


@pytest.mark.parametrize('has_match,prompt_hash', [
    (False, '554a9b455aee44c2e9ece114fac5795f06ff9b2cc5461121a78b7111cc829047'),
    (True, 'ea23bdb7dc37fc4e7eee6d02a14031c25567eda39fedd72785544aaf7973c02b'),
])
def test_switch_off_preserves_request_bytes_and_avoids_storage(monkeypatch, has_match, prompt_hash):
    # Reviewed 2026-10-17 baseline: shared studio policy and manifest-based matching role.
    off, _ = capture(monkeypatch, switch='off', has_match=has_match)
    assert hashlib.sha256(off.calls[0]['prompt'].encode()).hexdigest() == prompt_hash
    assert [image.data for image in off.calls[0]['images']] == [
        b'base', b'front', b'back', *([b'match'] if has_match else [])]
    assert off.reads == off.lookups == []
    assert verdict(off) == {'status': 'skipped', 'reason': 'switch_off'}


@pytest.mark.parametrize('has_match', [False, True])
def test_ready_tidy_only_changes_manifest_and_adds_white_rgb(monkeypatch, has_match):
    off, old_manifest = capture(monkeypatch, switch='off', has_match=has_match)
    on, _ = capture(monkeypatch, has_match=has_match)
    call = on.calls[0]
    images = call['images']
    index = -2 if has_match else -1
    flat = images[index]
    with Image.open(io.BytesIO(flat.data)) as image:
        assert image.mode == 'RGB' and image.size == (768, 1024)
        assert image.getpixel((1, 1)) == (255, 255, 255)
        assert image.getpixel((0, 0)) == (137, 147, 157)
    assert flat.mime == 'image/png'
    assert tuple(im for im in images if im is not flat) == off.calls[0]['images']
    assert [hashlib.sha256(im.data).hexdigest() for im in images if im is not flat] == [
        hashlib.sha256(im.data).hexdigest() for im in off.calls[0]['images']]
    lines = old_manifest.splitlines()
    new_lines = [*lines[:3], f'4. {FLAT_LABEL}']
    if has_match:
        new_lines.append('5. ' + lines[3].split('. ', 1)[1])
        assert images[-1].data == b'match'
    new_manifest = '\n'.join(new_lines)
    assert call['prompt'] == off.calls[0]['prompt'].replace(old_manifest, new_manifest, 1)
    assert verdict(on)['status'] == 'used'
    rendered = next(e for e in on.events if e.get('status') == 'prompt_rendered')
    assert rendered['prompt_hash'] == hashlib.sha256(call['prompt'].encode()).hexdigest()


@pytest.mark.parametrize('options,reason', [({'patterned': False}, 'not_patterned'),
    ({'ready': False}, 'tidy_not_ready'), ({'broken_storage': True}, 'tidy_not_ready'),
    ({'broken_lookup': True}, 'tidy_not_ready'), ({'missing_front': True}, 'tidy_not_ready'),
    ({'row_change': {'sourceAssetId': 'old-front'}}, 'tidy_not_ready'),
    ({'row_change': {'sourceHash': 'old-hash'}}, 'tidy_not_ready'),
    ({'path': 'edit'}, 'edit_path'), ({'payload': {'mode': 'regenerate'}}, 'edit_path'),
    ({'job_kind': 'mannequin_adjust'}, 'edit_path'), ({'job_kind': 'editor_image'}, 'edit_path'),
    ({'adjusted': True}, 'edit_path')])
def test_skips_preserve_original_request(monkeypatch, options, reason):
    baseline, _ = capture(monkeypatch, switch='off', **options)
    actual, _ = capture(monkeypatch, **options)
    assert actual.calls == baseline.calls
    assert verdict(actual) == {'status': 'skipped', 'reason': reason}
    if reason != 'tidy_not_ready':
        assert actual.lookups == actual.reads == []


@pytest.mark.parametrize('initial_ready', [False, True])
def test_qc_retry_freezes_first_decision_and_bytes(monkeypatch, initial_ready):
    def change_tidy(state):
        state.row = None if initial_ready else ready_row()
        state.png = b'changed-after-first-attempt'

    state, _ = capture(monkeypatch, ready=initial_ready, retry=True, mutation=change_tidy)
    assert state.calls[0]['images'] == state.calls[1]['images']
    assert len(state.lookups) == 1
    assert len(state.reads) == int(initial_ready)
    assert verdict(state)['status'] == ('used' if initial_ready else 'skipped')


@pytest.mark.parametrize('has_match', [False, True])
@pytest.mark.parametrize('style', [False, True])
def test_knit_retry_keeps_flat_immediately_before_last_match(monkeypatch, has_match, style):
    state, _ = capture(monkeypatch, retry=True, knit=True, has_match=has_match, style=style)
    first, second = state.calls
    expected_prefix = [b'base', b'front', b'back'] + ([b'style'] if style else [])
    flat = first['images'][-2 if has_match else -1]
    assert [im.data for im in second['images']] == [*expected_prefix, b'CanonicalFront', b'CanonicalBack',
                                                   flat.data, *([b'match'] if has_match else [])]
    labels = [line for line in second['prompt'].splitlines()
              if line.split('. ', 1)[0].isdigit()]
    assert len(labels) == len(second['images'])
    assert [int(line.split('.')[0]) for line in labels] == list(range(1, len(labels) + 1))
    assert labels[-2 if has_match else -1].endswith(FLAT_LABEL)
    if has_match:
        assert 'matching BOTTOM' in labels[-1]
