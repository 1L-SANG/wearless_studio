"""Per-line, lower-decile contrast over v4 photographs (no averaged pixels)."""
from __future__ import annotations

import math
from collections import Counter
from io import BytesIO

from PIL import Image

from .page_template_assembler import collect_overlay_regions
from .page_templates import CATALOG


def _luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * coefficient for value, coefficient in zip(channels, (.2126, .7152, .0722)))


def _pixel_counts(image, plan):
    width, height = image.size
    frame, region = plan['frame'], plan['region']
    fw, fh = frame['w'], frame['h']
    if min(width, height, fw, fh) <= 0:
        return Counter()
    left, top, right, bottom = [min(1, max(0, region[key])) for key in ('left', 'top', 'right', 'bottom')]
    if frame.get('crop'):
        crop = frame['crop']
        x1, x2 = [(crop['ox'] + point * fw) * width / crop['iw'] for point in (left, right)]
        y1, y2 = [(crop['oy'] + point * fh) * height / crop['ih'] for point in (top, bottom)]
    else:
        aspect = fw / fh
        visible_w, visible_h = min(width, height * aspect), min(height, width / aspect)
        x1, x2 = [(width - visible_w) / 2 + point * visible_w for point in (left, right)]
        y1, y2 = [(height - visible_h) / 2 + point * visible_h for point in (top, bottom)]
    bounds = (max(0, math.floor(x1)), max(0, math.floor(y1)), min(width, math.ceil(x2)), min(height, math.ceil(y2)))
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return Counter()
    # Resizing would average a checkerboard into gray and erase low-contrast pixels.
    with image.crop(bounds) as band:
        raw = band.tobytes()
        return Counter(zip(raw[0::3], raw[1::3], raw[2::3]))


def _contrast_scores(image, plan):
    """Lower-decile ratios for both inks over the original, unaveraged pixels."""
    light, dark = CATALOG['overlayColors']['light'], CATALOG['overlayColors']['dark']
    try:
        counts = _pixel_counts(image, plan)
        total = sum(counts.values())
        if not total:
            return None
        luminances = [(_luminance(pixel), count) for pixel, count in counts.items()]
        scores = {}
        for color in (light, dark):
            ink = _luminance(tuple(int(color[index:index + 2], 16) for index in (1, 3, 5)))
            ratios = sorted(((max(value, ink) + .05) / (min(value, ink) + .05), count) for value, count in luminances)
            rank, seen = math.floor(total * .1), 0
            for ratio, count in ratios:
                seen += count
                if seen > rank:
                    scores[color] = ratio
                    break
        return scores
    except Exception:
        return None


def sample_overlay_style(image, plan):
    """Choose the best lower 10% ratio; shade only if neither reaches 3:1 on 90%."""
    light, dark = CATALOG['overlayColors']['light'], CATALOG['overlayColors']['dark']
    scores = _contrast_scores(image, plan)
    if not scores:
        return {'color': light, 'shade': False}
    chosen = dark if scores[dark] >= scores[light] else light
    return {'color': chosen, 'shade': scores[chosen] < 3}


def sample_overlay_color(image, plan):
    return sample_overlay_style(image, plan)['color']


def _plate_position(plan):
    policy = CATALOG['firstTitlePlate']
    start, end = plan.get('garmentBand') or policy['fallbackGarmentBand']
    candidates = {item['position']: item['region'] for item in plan.get('plateCandidates', [])}
    overlaps = {}
    for position in policy['platePreference']:
        region = candidates.get(position)
        if not region:
            continue
        overlap = max(0, min(end, region['bottom']) - max(start, region['top']))
        overlaps[position] = round(overlap / max(1e-9, region['bottom'] - region['top']), 12)
        if overlaps[position] < policy['maxGarmentOverlap'] - 1e-9:
            return position
    return min(overlaps, key=overlaps.get) if overlaps else 'center'


def _first_title_metadata(image, plan):
    """Score every position; near-best ties prefer center/bottom/top, else plate."""
    light, dark = CATALOG['overlayColors']['light'], CATALOG['overlayColors']['dark']
    policy = CATALOG['firstTitlePlate']
    candidates = []
    for candidate in plan['positionCandidates']:
        lines = [(line['key'], _contrast_scores(image, {'frame': plan['frame'], 'region': line['region']})
                  if line['region']['right'] > line['region']['left'] else {}) for line in candidate['lines']]
        visible = [scores for _, scores in lines if scores]
        if not visible or any(scores is None for _, scores in lines):
            continue
        scores = {color: min(line_scores[color] for line_scores in visible) for color in (light, dark)}
        chosen = dark if scores[dark] >= scores[light] else light
        candidates.append((scores[chosen], candidate['position'], chosen, lines))
    passing = [candidate for candidate in candidates if candidate[0] >= policy['threshold']]
    if passing:
        highest = max(candidate[0] for candidate in passing)
        near = [candidate for candidate in passing if candidate[0] >= highest * policy['nearBestRatio']]
        score, position, chosen, lines = min(near, key=lambda candidate: policy['preference'].index(candidate[1]))
        return position, chosen, {key: {'color': chosen, 'shade': False} for key, _ in lines}, False
    # Missing pixels are also unreadable, so use the same conservative plate.
    return _plate_position(plan), light, {line['key']: {'color': light, 'shade': False} for line in plan['lines']}, True


def overlay_text_metadata(block, image, *, product=None, analysis=None, storyboard=None,
                          copy_results=None, copywriting=True, profile=None, source_aliases=None):
    """Evaluate exact final-layout lines, including direct and reused appearances."""
    if block.get('taxonomyVersion') != 4:
        return {}
    opened = rgb = None
    try:
        try:
            if isinstance(image, (bytes, bytearray)):
                opened = Image.open(BytesIO(image))
                image = opened
            rgb = image.convert('RGB')
        except Exception:
            pass
        cut = {'blockId': block['id'], 'imageUrl': None}
        if rgb is not None:
            cut.update(width=rgb.width, height=rgb.height)
        sources = {block['id']}
        sources.update(alias for alias, original in (source_aliases or {}).items() if original == block['id'])
        cuts = [{**cut, 'blockId': source} for source in sorted(sources)]
        plans = collect_overlay_regions(storyboard or [block], cuts, copy_results or [], product or {}, copywriting,
                                        analysis=analysis, profile=profile)
        plans = [plan for plan in plans if plan['sourceBlockId'] in sources]
        if not plans:
            return {}
        colors, lines, positions, plates = {}, {}, {}, {}
        for plan in plans:
            if plan.get('positionCandidates'):
                position, color, measured_lines, plate = _first_title_metadata(rgb, plan)
                if plate:
                    plates[plan["key"]] = True
                positions[plan['key']] = position
                colors[plan['key']] = color
                lines.update(measured_lines)
            else:
                colors[plan['key']] = sample_overlay_color(rgb, plan)
                lines.update({line['key']: sample_overlay_style(rgb, {'frame': plan['frame'], 'region': line['region']})
                              for line in plan['lines']})
        return {'overlayTextColors': colors, 'overlayTextLines': lines,
                **({'overlayTextPositions': positions} if positions else {}),
                **({'overlayTextPlates': plates} if plates else {})}
    except Exception:
        return {}
    finally:
        if rgb is not None:
            rgb.close()
        if opened is not None:
            opened.close()
