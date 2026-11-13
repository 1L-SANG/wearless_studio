"""WOQ5: real shared layouts, synthetic source pixels, and generation metadata."""
from copy import deepcopy

import pytest
from PIL import Image

from app.agents.page_template_contrast import overlay_text_metadata
from app.agents.page_templates import CATALOG, validate_catalog
from page_template_cases import assemble_input, case_input, collect_input_regions, elements, section

KEY = 'emo.first:first.hero:center'


def first(value):
    return elements(section(assemble_input(value), 'emo.first'))


def metadata(value, image):
    hero = next(c for c in value['storyboard'] if c['pageSectionKind'] == 'first')
    return overlay_text_metadata(hero, image, product=value['product'], analysis=value['analysis'],
                                 storyboard=value['storyboard'], copy_results=value['copyResults'])


@pytest.mark.parametrize('color', ['#ffffff', '#111111'])
def test_rules_frame_copy_and_follow_group_ink(color):
    value = case_input('emo-2-mono')
    value['cutResults'][0]['overlayTextColors'] = {KEY: color}
    els = first(value)
    rules = [e for e in els if e['type'] == 'shape']
    assert [(e['x'], e['y'], e['w'], e['h'], e['fill']) for e in rules] == [
        (309, 522, 382, 1.5, color), (309, 726.5, 382, 1.5, color)]
    texts = [e for e in els if e.get('copyRole')]
    assert texts[0]['y'] - rules[0]['y'] - rules[0]['h'] == 18
    assert rules[1]['y'] - texts[-1]['y'] - texts[-1]['h'] == 18
    assert all(e['style']['color'] == color for e in texts)


@pytest.mark.parametrize('position,label_y,rule_y', [('center', 541.5, 522), ('top', 119.5, 100), ('bottom', 963.5, 944)])
def test_saved_position_moves_copy_rules_and_sampling_together(position, label_y, rule_y):
    value = case_input('emo-2-mono')
    value['cutResults'][0]['overlayTextPositions'] = {KEY: position}
    els = first(value)
    assert next(e for e in els if e.get('copyRole') == 'label')['y'] == label_y
    assert next(e for e in els if e['type'] == 'shape')['y'] == rule_y
    plan = next(p for p in collect_input_regions(value) if p['key'] == KEY)
    assert plan['lines'][0]['region']['top'] == label_y / 1250
    assert [p['position'] for p in plan['positionCandidates']] == ['center', 'top', 'bottom']
    assert [p['lines'][0]['region']['top'] for p in plan['positionCandidates']] == [.4332, .0956, .7708]


@pytest.mark.parametrize('position', [None, 'left', {'y': 0}, 10])
def test_missing_or_invalid_position_stays_centered(position):
    value = case_input('emo-2-mono')
    value['cutResults'][0]['overlayTextPositions'] = {KEY: position}
    assert next(e for e in first(value) if e.get('copyRole') == 'label')['y'] == 541.5


def paint_mixed(image, top, bottom, low=0, high=255):
    image.paste((low,) * 3, (0, top, 500, bottom))
    image.paste((high,) * 3, (500, top, 1000, bottom))


@pytest.mark.parametrize('bad,position', [([], 'center'), (['center'], 'bottom'), (['center', 'top'], 'bottom')])
@pytest.mark.parametrize('background,color', [('white', '#111111'), ('black', '#ffffff')])
def test_first_passing_position_uses_exact_line_pixels(bad, position, background, color):
    value = case_input('emo-2-mono')
    bands = {'center': (541, 709), 'top': (119, 287), 'bottom': (963, 1131)}
    with Image.new('RGB', (1000, 1250), background) as image:
        for place in bad:
            paint_mixed(image, *bands[place])
        result = metadata(value, image)
    assert result.get('overlayTextPositions') == {KEY: position}
    assert result['overlayTextColors'] == {KEY: color}
    assert len(result['overlayTextLines']) == 3
    assert all(style == {'color': color, 'shade': False} for style in result['overlayTextLines'].values())
    value['cutResults'][0].update(result)
    assert next(e for e in first(value) if e.get('copyRole') == 'label')['y'] == {'center': 541.5, 'top': 119.5, 'bottom': 963.5}[position]
    assert all(e['fill'] == color for e in first(value) if e['type'] == 'shape')


@pytest.mark.parametrize('best', ['center', 'top', 'bottom'])
def test_all_failed_positions_use_plate_instead_of_shading_a_better_but_failing_line(best):
    value = case_input('emo-2-mono')
    plans = next(p for p in collect_input_regions(value) if p['key'] == KEY)['positionCandidates']
    with Image.new('RGB', (1000, 1250), 'white') as image:
        for candidate in plans:
            for line in candidate['lines']:
                if line['key'].startswith(f'{KEY}:1:'):
                    r = line['region']; paint_mixed(image, int(r['top']*1250), int(r['bottom']*1250)+1, low=30 if candidate['position']==best else 0)
        result = metadata(value, image)
    assert result['overlayTextPositions'] == {KEY: 'center'}
    assert result['overlayTextPlates'] == {KEY: True}
    assert all(v == {'color': '#ffffff', 'shade': False} for v in result['overlayTextLines'].values())


def test_all_failed_ties_use_garment_avoidance_before_contrast_preference():
    with Image.new('RGB', (1000, 1250)) as image:
        paint_mixed(image, 0, 1250)
        result = metadata(case_input('emo-2-mono'), image)
    assert result['overlayTextPositions'] == {KEY: 'center'}
    assert result['overlayTextPlates'] == {KEY: True}


def test_opposite_uniform_lines_cannot_pass_as_one_group_color():
    with Image.new('RGB', (1000, 1250), 'white') as image:
        image.paste('black', (0, 541, 1000, 581))
        result = metadata(case_input('emo-2-mono'), image)
    assert result.get('overlayTextPositions') == {KEY: 'bottom'}
    assert result['overlayTextColors'] == {KEY: '#111111'}


@pytest.mark.parametrize('bad_center,expected', [(False, 'bottom'), (True, 'bottom')])
def test_highest_passing_position_wins_even_when_earlier_positions_pass(bad_center, expected):
    with Image.new('RGB', (1000, 1250), 'white') as image:
        image.paste((128,) * 3, (0, 119, 1000, 336))
        if bad_center:
            paint_mixed(image, 541, 709)
        else:
            image.paste((128,) * 3, (0, 541, 1000, 709))
        result = metadata(case_input('emo-2-mono'), image)
    assert result['overlayTextPositions'] == {KEY: expected}


@pytest.mark.parametrize('crop_top,source_h,offset', [(None, 2500, 625), (.2, 2000, 400)])
def test_fallback_candidates_map_cover_and_explicit_crop_to_source_pixels(monkeypatch, crop_top, source_h, offset):
    node = CATALOG['designs']['P01-D09']['layout'][0]
    if crop_top:
        monkeypatch.setitem(node, 'cropTop', crop_top)
    with Image.new('RGB', (1000, source_h), 'white') as image:
        paint_mixed(image, 517 + offset, 734 + offset)
        image.paste('black', (0, 119 + offset, 1000, 336 + offset))
        result = metadata(case_input('emo-2-mono'), image)
    assert result['overlayTextPositions'] == {KEY: 'top'}
    assert result['overlayTextColors'] == {KEY: '#ffffff'}
    assert all(style == {'color': '#ffffff', 'shade': False} for style in result['overlayTextLines'].values())


@pytest.mark.parametrize('image', [None, b'invalid'])
def test_invalid_image_uses_conservative_white_plate_without_shade(image):
    result = metadata(case_input('emo-2-mono'), image)
    assert result.get('overlayTextPositions') == {KEY: 'center'}
    assert result['overlayTextPlates'] == {KEY: True}
    assert result['overlayTextColors'] == {KEY: '#ffffff'}
    assert all(style == {'color': '#ffffff', 'shade': False} for style in result['overlayTextLines'].values())


@pytest.mark.parametrize('blank', ['', ' ', '\t', ' \t '])
def test_blank_wrapped_line_does_not_invalidate_visible_line_measurements(blank):
    value = case_input('emo-2-mono')
    value['copyResults'][0]['texts'][0]['text'] = f'Daily\n{blank}\ncotton'
    with Image.new('RGB', (1000, 1250), 'white') as image:
        p = next(p for p in collect_input_regions(value) if p['key'] == KEY)
        blank_line = next(line for line in p['lines'] if line['key'] == f'{KEY}:0:1')['region']
        image.paste('black', (0, int(blank_line['top']*1250)+1, 1000, int(blank_line['bottom']*1250)))
        result = metadata(value, image)
    assert result['overlayTextPositions'] == {KEY: 'center'}
    assert result['overlayTextColors'] == {KEY: '#111111'}
    assert result['overlayTextLines'][f'{KEY}:0:1'] == {'color': '#111111', 'shade': False}
    assert all(style == {'color': '#111111', 'shade': False} for style in result['overlayTextLines'].values())


def test_other_overlays_keep_their_original_geometry_without_position_candidates():
    value = case_input('show-2-mono')
    before = assemble_input(value)
    for cut in value['cutResults']:
        cut['overlayTextPositions'] = {'show.fabric:fabric.fabric:heading': 'bottom', 'show.fabric:fabric.fabric:description': 'top'}
    assert assemble_input(value) == before
    assert all('positionCandidates' not in p for p in collect_input_regions(value))


@pytest.mark.parametrize('policy', [
    {'anchors': ['top', 'center', 'bottom'], 'edgeInsetRatio': .08},
    {'anchors': ['center', 'top', 'bottom'], 'edgeInsetRatio': -.1},
    {'anchors': ['center', 'top', 'bottom'], 'edgeInsetRatio': .6},
])
def test_catalog_rejects_invalid_position_policy(policy):
    catalog = deepcopy(CATALOG)
    catalog['designs']['P01-D09']['layout'][0]['groups'][0]['positionFallback'] = policy
    with pytest.raises(ValueError, match='overlay position'):
        validate_catalog(catalog)
