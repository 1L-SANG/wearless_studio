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
        (309, 498, 382, 1.5, color), (309, 750.5, 382, 1.5, color)]
    texts = [e for e in els if e.get('copyRole')]
    assert texts[0]['y'] - rules[0]['y'] - rules[0]['h'] == 18
    assert rules[1]['y'] - texts[-1]['y'] - texts[-1]['h'] == 18
    assert all(e['style']['color'] == color for e in texts)


@pytest.mark.parametrize('position,label_y,rule_y', [('center', 517.5, 498), ('top', 119.5, 100), ('bottom', 915.5, 896)])
def test_saved_position_moves_copy_rules_and_sampling_together(position, label_y, rule_y):
    value = case_input('emo-2-mono')
    value['cutResults'][0]['overlayTextPositions'] = {KEY: position}
    els = first(value)
    assert next(e for e in els if e.get('copyRole') == 'label')['y'] == label_y
    assert next(e for e in els if e['type'] == 'shape')['y'] == rule_y
    plan = next(p for p in collect_input_regions(value) if p['key'] == KEY)
    assert plan['lines'][0]['region']['top'] == label_y / 1250
    assert [p['position'] for p in plan['positionCandidates']] == ['center', 'top', 'bottom']
    assert [p['lines'][0]['region']['top'] for p in plan['positionCandidates']] == [.414, .0956, .7324]


@pytest.mark.parametrize('position', [None, 'left', {'y': 0}, 10])
def test_missing_or_invalid_position_stays_centered(position):
    value = case_input('emo-2-mono')
    value['cutResults'][0]['overlayTextPositions'] = {KEY: position}
    assert next(e for e in first(value) if e.get('copyRole') == 'label')['y'] == 517.5


def paint_mixed(image, top, bottom, low=0, high=255):
    image.paste((low,) * 3, (0, top, 500, bottom))
    image.paste((high,) * 3, (500, top, 1000, bottom))


@pytest.mark.parametrize('bad,position', [([], 'center'), (['center'], 'top'), (['center', 'top'], 'bottom')])
@pytest.mark.parametrize('background,color', [('white', '#111111'), ('black', '#ffffff')])
def test_first_passing_position_uses_exact_line_pixels(bad, position, background, color):
    value = case_input('emo-2-mono')
    bands = {'center': (517, 734), 'top': (119, 336), 'bottom': (915, 1132)}
    with Image.new('RGB', (1000, 1250), background) as image:
        for place in bad:
            paint_mixed(image, *bands[place])
        result = metadata(value, image)
    assert result.get('overlayTextPositions') == {KEY: position}
    assert result['overlayTextColors'] == {KEY: color}
    assert len(result['overlayTextLines']) == 4
    assert all(style == {'color': color, 'shade': False} for style in result['overlayTextLines'].values())
    value['cutResults'][0].update(result)
    assert next(e for e in first(value) if e.get('copyRole') == 'label')['y'] == {'center': 517.5, 'top': 119.5, 'bottom': 915.5}[position]
    assert all(e['fill'] == color for e in first(value) if e['type'] == 'shape')


@pytest.mark.parametrize('best', ['center', 'top', 'bottom'])
def test_all_positions_fail_choose_highest_worst_line_and_shade_only_bad_line(best):
    value = case_input('emo-2-mono')
    name_bands = {'center': (568, 637), 'top': (170, 239), 'bottom': (966, 1035)}
    with Image.new('RGB', (1000, 1250), 'white') as image:
        for place, bounds in name_bands.items():
            paint_mixed(image, *bounds, low=60 if place == best else 0, high=210 if place == best else 255)
        result = metadata(value, image)
    assert result.get('overlayTextPositions') == {KEY: best}
    assert result['overlayTextColors'] == {KEY: '#111111'}
    assert [result['overlayTextLines'][f'{KEY}:{i}:0'] for i in range(4)] == [
        {'color': '#111111', 'shade': False}, {'color': '#111111', 'shade': True},
        {'color': '#111111', 'shade': False}, {'color': '#111111', 'shade': False}]


def test_all_failed_ties_prefer_center():
    with Image.new('RGB', (1000, 1250)) as image:
        paint_mixed(image, 0, 1250)
        result = metadata(case_input('emo-2-mono'), image)
    assert result.get('overlayTextPositions') == {KEY: 'center'}


def test_opposite_uniform_lines_cannot_pass_as_one_group_color():
    with Image.new('RGB', (1000, 1250), 'white') as image:
        image.paste('black', (0, 517, 1000, 557))
        result = metadata(case_input('emo-2-mono'), image)
    assert result.get('overlayTextPositions') == {KEY: 'top'}
    assert result['overlayTextColors'] == {KEY: '#111111'}


@pytest.mark.parametrize('bad_center,expected', [(False, 'center'), (True, 'top')])
def test_first_passing_position_wins_even_when_later_position_has_more_contrast(bad_center, expected):
    with Image.new('RGB', (1000, 1250), 'white') as image:
        image.paste((128,) * 3, (0, 119, 1000, 336))
        if bad_center:
            paint_mixed(image, 517, 734)
        else:
            image.paste((128,) * 3, (0, 517, 1000, 734))
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
def test_invalid_image_defaults_center_white_without_shade(image):
    result = metadata(case_input('emo-2-mono'), image)
    assert result.get('overlayTextPositions') == {KEY: 'center'}
    assert result['overlayTextColors'] == {KEY: '#ffffff'}
    assert all(style == {'color': '#ffffff', 'shade': False} for style in result['overlayTextLines'].values())


@pytest.mark.parametrize('blank', ['', ' ', '\t', ' \t '])
def test_blank_wrapped_line_does_not_invalidate_visible_line_measurements(blank):
    value = case_input('emo-2-mono')
    value['copyResults'][0]['texts'][0]['text'] = f'Daily\n{blank}\ncotton'
    with Image.new('RGB', (1000, 1250), 'white') as image:
        # At center the blank label row occupies y=517.5..556.5. A stripe
        # behind no visible glyph must not make the shared ink fail there.
        image.paste('black', (0, 518, 1000, 556))
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
