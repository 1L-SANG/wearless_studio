"""Product-only closeups: original evidence, prepared presentation, two attempts.

Progress is durable and lease-fenced by the worker. A lost response is not permission
to make another paid request. Only a fully checked candidate can leave this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from io import BytesIO

from PIL import Image, ImageOps

from . import detail_output_qc
from .gemini_image import InlineImage

PROMPT_VERSION = 'product-detail-prepared-v2'
_SUBJECTS = {
    'neckline': 'neckline and its connection to the surrounding bodice',
    'closure': 'fastener construction and surrounding placket',
    'pocket': 'pocket opening, attachment and surrounding panel',
    'waist': 'waist construction and connected upper hip area',
    'cuff': 'cuff and connected sleeve',
    'hem': 'hem finish and adjacent body panel',
    'construction': 'distinctive garment construction with neighboring seams',
    'surface': 'surface decoration with enough garment context to locate it',
    'fabric': 'visible fabric surface at the scale actually resolved in the source',
    'label': 'permanent sewn label and its garment attachment',
}


class DetailShotRejected(RuntimeError):
    def __init__(self, reason, metadata=None):
        super().__init__(reason)
        self.reason = reason
        self.metadata = metadata or {}


def build_prompt(target, direction, corrections=()):
    subject = _SUBJECTS.get((target or {}).get('kind'),
                            'the clearest useful construction in the supplied closeup')
    view = 'back' if direction == 'back' else 'front'
    text = f'''Make ONE polished fashion e-commerce PRODUCT DETAIL photograph.
The seller originals are the sole authority for the actual garment. Show its {view} side.
Subject: {subject}. Show one meaningful closeup, not the entire garment and not an
isolated meaningless sliver. Include the adjacent seam/panel as a location anchor.
If a TARGET CROP is attached, it is a pixel crop of the source, not another garment.
Keep that subject recognizable, with breathing room and context; do not enlarge
unresolved fibers or lettering into fabricated detail. Fabric shots show visible
weave/knit and natural drape, never claim microscopic structure from a distant photo.

PHOTOGRAPHIC PREPARATION: gently steam incidental shipping/shooting wrinkles,
smooth and re-lay the garment, tidy collar edges and folds for a professional flat
lay or supported product closeup. Preserve intentional pleats, pintucks, shirring,
lace holes, knit structure, material weight, seams, panel connections, neckline
depth and real garment proportions. Keep fastener count/type and permanent sewn
labels/patches. Do not invent readable text when the original text is unreadable.
Do not copy accidental camera distortion or untidy photographed arrangement.

Clean softly lit neutral studio background, warm off-white to pale neutral gray,
with gentle separation for light fabric and faithful garment color. Restrained
natural contact shadow. No model/body/hands/mannequin/hanger/props, added graphics,
text overlays, commercial hangtags, price stickers, clips or removable tag strings.
Remove temporary photography artifacts without inventing hidden garment structure.
Permanent labels outside this contextual crop need not be forced into the photo.
The result must show the same product, attractively prepared, not a redesigned item.
'''
    if corrections:
        text += '\nCORRECT THE PREVIOUS CANDIDATE against the seller originals; '
        text += 'preserve everything already faithful. Last image is the previous candidate.\n'
        text += '\n'.join(corrections)
    return text


def target_crop(source, target):
    region = (target or {}).get('region')
    if not region:
        return None
    with Image.open(BytesIO(source.data)) as raw:
        im = ImageOps.exif_transpose(raw).convert('RGB')
        x, y, w, h = (float(region[k]) for k in ('x', 'y', 'w', 'h'))
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
                and x + w <= 1.000001 and y + h <= 1.000001):
            raise DetailShotRejected('invalid_detail_region')
        box = (round(x * im.width), round(y * im.height),
               round((x + w) * im.width), round((y + h) * im.height))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise DetailShotRejected('invalid_detail_region')
        out = BytesIO()
        im.crop(box).save(out, format='PNG')
        return InlineImage('image/png', out.getvalue())


def provider_reference(image):
    """Lossless upright pixels; iPhone MPO-as-JPEG is rejected by image APIs.

    Do not downsize. Original bytes still own the source binding and QC audit;
    this derivative shares the coordinate system of the target crop.
    """
    try:
        with Image.open(BytesIO(image.data)) as raw:
            im = ImageOps.exif_transpose(raw)
            if im.mode not in {'RGB', 'RGBA', 'L', 'LA'}:
                im = im.convert('RGB')
            out = BytesIO()
            im.save(out, format='PNG')
        return InlineImage('image/png', out.getvalue())
    except (OSError, ValueError) as exc:
        raise DetailShotRejected('detail_reference_unavailable') from exc


def fingerprint(settings, sources, target, direction, style_reference=None):
    value = {'prompt': PROMPT_VERSION, 'qc': detail_output_qc.POLICY_VERSION,
             'model': settings.model_product_detail, 'size': settings.detail_cut_image_size,
             'coreJudge': getattr(settings, 'model_detail_core', None),
             'coreTimeout': getattr(settings, 'detail_core_timeout_seconds', None),
             'specialist': getattr(settings, 'model_detail_specialist', None),
             'specialistTimeout': getattr(settings, 'detail_specialist_timeout_seconds', None),
             'source': [hashlib.sha256(s.data).hexdigest() for s in sources],
             'style': hashlib.sha256(style_reference.data).hexdigest() if style_reference else None,
             'target': target, 'direction': direction}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def generate_verified(settings, client, sources, *, target, direction, progress, style_reference=None):
    if not sources:
        raise DetailShotRejected('detail_reference_required')
    fp = fingerprint(settings, sources, target, direction, style_reference)
    state = await progress.read()
    if state and (state.get('fingerprint') != fp or state.get('phase') in {'generating', 'judging', 'held'}):
        raise DetailShotRejected('detail_attempt_unavailable', state.get('qc'))
    candidate = await progress.load_candidate() if state.get('phase') in {'candidate', 'judged', 'accepted'} else None
    if state.get('phase') == 'accepted':
        report = state.get('qc') or {}
        if candidate is not None and report.get('passed') is True:
            return candidate, report
        raise DetailShotRejected('detail_checkpoint_unavailable')
    if state and candidate is None:
        raise DetailShotRejected('detail_checkpoint_unavailable')

    report = {'policyVersion': detail_output_qc.POLICY_VERSION,
              'promptVersion': PROMPT_VERSION, 'fingerprint': fp,
              'requestedGenerationModel': settings.model_product_detail,
              'passed': False, 'attempts': state.get('attempts', 0),
              'judgments': list((state.get('qc') or {}).get('judgments') or [])}
    resumed_judgment = report['judgments'][-1] if state.get('phase') == 'judged' and report['judgments'] else None
    generation_sources = await asyncio.to_thread(lambda: [provider_reference(i) for i in sources])
    crop = None
    if target:
        # Runtime puts the hash-matched target original first, irrespective of its
        # original analysis index. Whole + crop preserves both context and detail.
        crop = await asyncio.to_thread(target_crop, sources[0], target)
        if crop:
            generation_sources.append(crop)
    style_index = None
    if style_reference:
        generation_sources.append(await asyncio.to_thread(provider_reference, style_reference))
        style_index = len(generation_sources)
    corrections = ()
    report['providerInputHashes'] = [hashlib.sha256(i.data).hexdigest() for i in generation_sources]
    while True:
        if candidate is None:
            attempt = await progress.claim(fp)
            report['attempts'] = attempt
            prompt = build_prompt(target, direction, corrections)
            if crop:
                prompt += f'\nImages 1–{len(sources)}: SELLER ORIGINALS. Image {len(sources)+1}: TARGET CROP.\n'
            if style_index:
                prompt += (f'\nImage {style_index} is PHOTOGRAPHY STYLE ONLY, not the product. '
                           'Borrow its lighting, background tone and attractive arrangement. '
                           'Never borrow its garment shape, material, details or letters. '
                           'The selected subject, direction and contextual crop above remain authoritative.\n')
            if (target or {}).get('colorTransfer') is True:
                color_index = next((i + 1 for i, source in enumerate(sources)
                                    if hashlib.sha256(source.data).hexdigest() == target.get('colorSourceSha256')), None)
                if color_index is None:
                    raise DetailShotRejected('detail_color_reference_required')
                prompt += (f'\nCOLORWAY TRANSFER: Image {color_index} alone owns target garment color. '
                           'Other-color closeup(s) explain the SAME construction only. '
                           'Keep their geometry, seams and visible texture, applying the target color.\n')
            try:
                response = await client.generate_content_image(
                    settings.model_product_detail, prompt, generation_sources,
                    settings.detail_cut_image_size, aspect_ratio='2:3',
                    openai_preserve_input_bytes=True)
                candidate = InlineImage(response.mime, response.image)
                await progress.save_candidate(candidate, fp, attempt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                report.update(decision='UNKNOWN', reason='generation_unavailable',
                              errorType=type(exc).__name__, billable=getattr(exc, 'billable', None))
                await progress.finish(report)
                raise DetailShotRejected('detail_generation_unavailable', report) from exc
        if resumed_judgment is not None:
            judgment, resumed_judgment = resumed_judgment, None
        else:
            await progress.claim_judgment()
            try:
                judgment = await detail_output_qc.verdict(
                    settings, sources, candidate, target=target, direction=direction)
            except asyncio.CancelledError:
                raise
            except Exception:
                judgment = {'passed': False, 'decision': 'UNKNOWN', 'reason': 'judge_unavailable'}
            report['judgments'].append(judgment)
        report['decision'] = judgment.get('decision', 'UNKNOWN')
        await progress.record_judgment(report)
        if judgment.get('passed') is True and judgment.get('decision') == 'PASS':
            report['passed'] = True
            await progress.finish(report)
            return candidate, report
        if judgment.get('decision') != 'FAIL' or report['attempts'] >= 2:
            await progress.finish(report)
            raise DetailShotRejected('detail_quality_unconfirmed', report)
        corrections = tuple(detail_output_qc.repair_instructions(judgment)) or (
            'Restore all source garment facts and the requested contextual detail framing.',)
        # Stage 2 sees originals + crop + rejected candidate, not the candidate alone.
        generation_sources = [*generation_sources, candidate]
        candidate = None
