import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.agents.page_template_assembler import measure_text_width, wrap_text, estimate_text_height, korean_count, collect_overlay_regions
from app.agents.page_template_contrast import sample_overlay_style, overlay_text_metadata
from app.agents.page_templates import CATALOG, validate_catalog, walk_layout
from page_template_cases import case_input, assemble_input, section, elements, text

QUALITY = json.loads((Path(__file__).parents[2] / 'tests/fixtures/page_templates_quality.json').read_text())


def custom_layout(monkeypatch, nodes):
    original = CATALOG['designs']['T08-D01']
    monkeypatch.setitem(CATALOG['designs'], 'T08-D01', {**original, 'padding': 0, 'top': 0, 'bottom': 0, 'layout': nodes})
    value = case_input('emo-2-mono')
    value['storyboard'] = [card for card in value['storyboard'] if card['pageSectionKind'] == 'for_you']
    return value


@pytest.mark.parametrize('case', QUALITY['textCases'], ids=lambda c: c['name'])
def test_hug_text_and_panel_widths_are_fixed_in_both_engines(monkeypatch, case):
    value = custom_layout(monkeypatch, [dict(type='panel', hug=True, minW=80, maxW=case['maxW'], padding=[14, 22], fill='ink',
        nodes=[dict(type='text', literal=case['text'], style=case.get('style', 'body'), size=case.get('fontSize', 20), color='invertInk')])])
    els = elements(section(assemble_input(value), 'emo.for_you'))
    panel, txt = next(e for e in els if e['type'] == 'shape'), next(e for e in els if e['type'] == 'text')
    assert panel['w'] == case['boxW']
    assert txt['style']['size'] == case.get('size', case.get('fontSize', 20))
    assert txt['style']['tracking'] == case.get('tracking', 0)
    assert measure_text_width(txt['text'], txt['w'], txt['style']['size'], tracking=txt['style']['tracking']) == case['textW']
    assert (txt['x'], txt['y'], txt['w']) == (22, 14, panel['w'] - 44)
    assert panel['h'] == txt['h'] + 28


@pytest.mark.parametrize('mode,widths', [('each', [104, 171]), ('max', [171, 171])])
def test_list_hug_modes_and_alignment(monkeypatch, mode, widths):
    value = custom_layout(monkeypatch, [dict(type='list', copyRole='items.n', count=2, hug=True, hugMode=mode,
        minW=80, maxW=300, align='center', padding=[14, 22], fill='ink', gap=12, items=[dict(type='text', copyRole='items.n', size=20)])])
    value['copyResults'] = [dict(blockId=value['storyboard'][0]['id'], texts=[dict(role='items.0', text='가나다'), dict(role='items.1', text='가나다 라마바')])]
    shapes = [e for e in elements(section(assemble_input(value), 'emo.for_you')) if e['type'] == 'shape']
    assert [e['w'] for e in shapes] == widths
    assert all(e['x'] + e['w'] / 2 == 500 for e in shapes)


@pytest.mark.parametrize('field,value', [('padding', [1]), ('padding', [1, -2]), ('padding', [1, '2']), ('hugMode', 'all'), ('minW', -1), ('photoRadius', -1), ('layout', 'grid')])
def test_invalid_layout_extensions_are_rejected(field, value):
    from copy import deepcopy
    data = deepcopy(CATALOG)
    data['designs']['T08-D01']['layout'][-1][field] = value
    with pytest.raises(ValueError):
        validate_catalog(data)


def test_section_gaps_use_canvas_color_and_keep_photo_blocks():
    value = case_input('show-2-mono')
    value['storyboard'] = [{**card, 'pageDesignId': 'T06-COMMENT-01'} for card in value['storyboard'] if card['pageSectionKind'] == 'intro']
    blocks = section(assemble_input(value), 'show.intro')
    gap = blocks[-1]['elements'][-1]
    assert gap['pageSectionGap'] and gap['locked']
    assert gap['fill'] == CATALOG['canvasColor']
    assert gap['y'] + gap['h'] == blocks[-1]['h']
    assert len(blocks) == 1


@pytest.mark.parametrize('name,design_id,node_type', [('common-fit-album-location-medium', 'T07-D06', 'photoStack'), ('emo-2-mono', 'PT-EMO-01', 'pointRows'), ('emo-2-mono', 'CP-EMO-01', 'photoGrid')])
def test_repeats_keep_their_trailing_gap(monkeypatch, name, design_id, node_type):
    from copy import deepcopy
    design = deepcopy(CATALOG['designs'][design_id])
    monkeypatch.setitem(CATALOG['designs'], design_id, design)
    node = next(n for n in design['layout'] if n['type'] == node_type)
    if node_type == 'photoGrid':
        node['columns'] = 1  # Isolate trailing spacing from cell-width changes.
    design['layout'].insert(design['layout'].index(node) + 1, dict(type='text', literal='after repeat', size=20))
    node['gap'] = 0
    before = assemble_input(case_input(name))
    node['gap'] = 40
    after = assemble_input(case_input(name))
    get = lambda page: next(e for e in elements(page) if e.get('text') == 'after repeat')
    assert get(after)['y'] - get(before)['y'] == (0 if node.get('balancedRows') else 40)
    assert len(after) == len(before)


@pytest.mark.parametrize('layout', ['inline', 'columns', 'stacked'])
def test_fact_table_modes_without_an_invented_size(monkeypatch, layout):
    node = next(n for n in walk_layout(CATALOG['designs']['T06-D01']['layout']) if n['type'] == 'factTable' and n.get('layout') == 'columns')
    monkeypatch.setitem(node, 'layout', layout)
    value = case_input('common-2-mono'); value['product']['size'] = 'M'
    rows = text(section(assemble_input(value), 'emo.intro'), 'facts')
    assert all('SIZE' not in e['text'] for e in rows)
    if layout == 'inline':
        assert len(rows) == 1
    else:
        label = next(e for e in rows if e['factLabel'] == 'COLOR' and e['factPart'] == 'label')
        val = next(e for e in rows if e['factLabel'] == 'COLOR' and e['factPart'] == 'value')
        assert label['style']['size'] == 28
        assert label['style']['align'] == ('left' if layout == 'columns' else 'center')
        assert val['x'] - label['x'] == (180 if layout == 'columns' else 0)
        assert val['y'] == (label['y'] if layout == 'columns' else label['y'] + label['h'] + 4)


@pytest.mark.parametrize('marker', ['dot', 'square'])
def test_markers_are_geometry_at_first_line_center(monkeypatch, marker):
    value = custom_layout(monkeypatch, [dict(type='list', copyRole='items.n', count=1, marker=marker, items=[dict(type='text', literal='첫 줄\n둘째 줄', size=20, align='left')])])
    els = elements(section(assemble_input(value), 'emo.for_you'))
    dot, txt = next(e for e in els if e['type'] == 'shape'), next(e for e in els if e['type'] == 'text')
    assert (dot['w'], dot['h']) == (6, 6)
    assert txt['x'] - dot['x'] == 22
    assert dot['y'] + 3 == txt['y'] + txt['style']['lineHeight'] / 2
    assert dot['shape'] == ('circle' if marker == 'dot' else 'rect')


def test_point_rows_stay_centered_with_template_specific_photo_corners():
    for name, key in [('emo-2-point', 'emo.points'), ('common-2-point', 'emo.points_detail')]:
        block = section(assemble_input(case_input(name)), key)[-1]
        photo = next(e for e in block['elements'] if e['type'] == 'image')
        copy = [e for e in block['elements'] if e['type'] == 'text' and (e.get('copyRole', '').startswith('items.') or e.get('text', '').isdigit())]
        assert photo['radius'] == (16 if name.startswith('emo') else 0)
        assert (min(e['y'] for e in copy) + max(e['y'] + e['h'] for e in copy)) / 2 == photo['y'] + photo['h'] / 2


def test_native_counters_and_paragraph_bubbles():
    assert [korean_count(i) for i in range(1, 13)] == ['한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열', '11', '12']
    intro = section(assemble_input(case_input('emo-md-paragraphs')), 'emo.intro')
    body = text(intro, 'body'); bubbles = [e for e in elements(intro) if e['type'] == 'shape' and e['radius'] == 26]
    assert len(body) == len(bubbles) == 3
    assert '\n오래 입을' in body[-1]['text']
    assert [e['paragraphIndex'] for e in body] == [0, 1, 2]
    assert bubbles[1]['y'] - bubbles[0]['y'] - bubbles[0]['h'] == 12
    assert len(text(intro, 'title')) == 1


@pytest.mark.parametrize('fill,expected_x,shape', [('canvas', 0, False), ('ink', 22, True)])
def test_same_surface_removes_horizontal_padding_only(monkeypatch, fill, expected_x, shape):
    value = custom_layout(monkeypatch, [dict(type='panel', fill=fill, padding=[14, 22], nodes=[dict(type='text', literal='설명', size=20)])])
    els = elements(section(assemble_input(value), 'emo.for_you'))
    txt = next(e for e in els if e['type'] == 'text')
    assert (txt['x'], txt['y']) == (expected_x, 14)
    assert any(e['type'] == 'shape' for e in els) == shape


def test_swatch_dots_hidden_english_and_optional_opening_intro():
    for template in ('emo', 'show'):
        value = case_input(template + '-2-mono')
        page = assemble_input(value)
        captions = section(page, template + '.color_product')
        assert not text(captions, 'colors.0.en')
        for index in range(2):
            txt = text(captions, f'colors.{index}.name')[0]
            dot = next(e for e in elements(captions) if e['type'] == 'shape' and e['w'] == 26 and e['x'] == txt['x'] - 36)
            assert dot['fill'] == CATALOG['swatchHex'][value['product']['colors'][index]['swatchId']]
        if template != 'emo':
            assert not text(section(page, template + '.first'), 'intro')
            assert text(section(assemble_input(case_input(template + '-first-intro')), template + '.first'), 'intro')[0]['text']


PLAN = {'frame': {'w': 100, 'h': 100}, 'region': {'left': 0, 'top': 0, 'right': 1, 'bottom': 1}}


@pytest.mark.parametrize('color,expected', [('white', '#111111'), ('black', '#ffffff'), ((128, 128, 128), '#111111')])
def test_uniform_pixels_choose_unshaded_high_contrast(color, expected):
    with Image.new('RGB', (100, 100), color) as image:
        assert sample_overlay_style(image, PLAN) == dict(color=expected, shade=False)


@pytest.mark.parametrize('bad_rows,shade', [(5, False), (10, False), (11, True), (50, True)])
def test_lower_decile_and_ninety_percent_threshold(bad_rows, shade):
    with Image.new('RGB', (100, 100), 'white') as image:
        image.paste('black', (0, 0, 100, bad_rows))
        assert sample_overlay_style(image, PLAN) == dict(color='#111111', shade=shade)


def test_checkerboard_is_not_resized_into_an_average_gray():
    with Image.new('RGB', (100, 100)) as image:
        image.putdata([(255, 255, 255) if (x + y) % 2 else (0, 0, 0) for y in range(100) for x in range(100)])
        assert sample_overlay_style(image, PLAN)['shade'] is True


def test_different_text_lines_are_sampled_independently():
    value = case_input('emo-2-mono')
    plan = next(plan for plan in collect_overlay_regions(value['storyboard'], value['cutResults'], value['copyResults'], value['product'], True, analysis=value['analysis']) if plan['key'] == 'emo.first:first.hero:center')
    with Image.new('RGB', (1000, 1250), 'white') as image:
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(plan['lines']):
            region = line['region']
            draw.rectangle((int(region['left'] * 1000), int(region['top'] * 1250), int(region['right'] * 1000) + 1, int(region['bottom'] * 1250) + 1), fill='black' if index % 2 == 0 else 'white')
        for index, line in enumerate(plan['lines']):
            assert sample_overlay_style(image, {**plan, 'region': line['region']}) == dict(color='#ffffff' if index % 2 == 0 else '#111111', shade=False)


def test_line_contrast_metadata_is_carried_into_saved_editor_elements():
    page = assemble_input(case_input('emo-line-contrast'))
    lines = [e for e in elements(page) if e.get('contrastLines')]
    assert lines and any(line['shade'] for e in lines for line in e['contrastLines'])
    for e in lines:
        assert e['contrastFor']['text'] == e['text']
        assert e['contrastFor']['style'] == e['style']


def test_max_hug_without_background_keeps_the_longest_box(monkeypatch):
    value = custom_layout(monkeypatch, [dict(type='list', count=2, copyRole='items.n', hug=True, hugMode='max', minW=80,
        maxW=300, padding=[14, 22], gap=12, items=[dict(type='text', copyRole='items.n', size=20)])])
    value['copyResults'] = [dict(blockId=value['storyboard'][0]['id'], texts=[dict(role='items.0', text='가나다'), dict(role='items.1', text='가나다 라마바')])]
    assert [e['w'] for e in text(section(assemble_input(value), 'emo.for_you'), 'items.0') + text(section(assemble_input(value), 'emo.for_you'), 'items.1')] == [127, 127]


@pytest.mark.parametrize('value', ['면 100%', ''])
def test_text_width_rule_uses_actual_copy_and_hides_when_empty(monkeypatch, value):
    page_input = custom_layout(monkeypatch, [dict(type='text', literal=value, size=20), dict(type='rule', w='text', h=2)])
    shapes = [e for e in elements(section(assemble_input(page_input), 'emo.for_you')) if e['type'] == 'shape']
    assert [e['w'] for e in shapes] == ([measure_text_width(value, 1000, 20)] if value else [])


@pytest.mark.parametrize('name', ['민트', 'Mint'])
def test_english_color_role_does_not_use_swatch_family(monkeypatch, name):
    from copy import deepcopy
    design = deepcopy(CATALOG['designs']['CP-EMO-01'])
    monkeypatch.setitem(CATALOG['designs'], 'CP-EMO-01', design)
    grid = next(n for n in design['layout'] if n['type'] == 'photoGrid')
    grid['captions'].append(dict(type='text', copyRole='colors.n.en', size=20, hideEmpty=True))
    value = case_input('emo-2-mono');value['product']['colors'][0].update(name=name,swatchId='green')
    assert [e['text'] for e in text(assemble_input(value),'colors.0.en')] == ['MINT', 'MINT']


@pytest.mark.parametrize('case', QUALITY['metricCases'], ids=lambda c: c['name'])
def test_r2_fixed_uppercase_and_tracking_metrics(case):
    args = case['text'], case['width'], case['size']
    assert wrap_text(*args, tracking=case['tracking']) == case['lines']
    assert measure_text_width(*args, tracking=case['tracking']) == case['textW']
    assert estimate_text_height(*args, 32, tracking=case['tracking']) == len(case['lines']) * 32


@pytest.mark.parametrize('case', QUALITY['gridCases'], ids=lambda c: c['name'])
def test_r4_fixed_grid_widths_and_remaining_row_placement(case):
    from app.agents.page_template_assembler import balanced_photo_rows
    value = case_input(case['name'])
    template = value['storyboard'][0]['pageSectionKey'].split('.')[0]
    blocks = section(assemble_input(value), template + '.color_product')
    photos = [el for el in elements(blocks) if el['type'] == 'image']
    design = CATALOG['designs'][next(card['pageDesignId'] for card in value['storyboard'] if card['pageSectionKind'] == 'color_product')]
    if (design.get('choiceAxes') or {}).get('layout', {}).get('id') == 'column':
        assert all(el['x'] == 0 and el['w'] == 1000 for el in photos)
    else:
        offset = 0
        for count in balanced_photo_rows(len(photos)):
            width = (888 - 24 * (count - 1)) // count
            assert [(el['x'], el['w']) for el in photos[offset:offset + count]] == [(56 + index * (width + 24), width) for index in range(count)]
            offset += count
    assert [el['sourceBlockId'] for el in photos] == [card['id'] for card in value['storyboard'] if card['pageSectionKind'] == 'color_product' and card['source'] != 'text']


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('color_mode', ['mono', 'point'])
def test_r1_circular_swatches_and_light_color_borders(template, color_mode):
    from app.agents.page_templates import resolve_page_tokens
    value = case_input(template + '-light-swatches')
    value['pageStyle'] = {**value['analysis']['pageStyle'], 'templateId': template, 'colorMode': color_mode}
    page = assemble_input(value)
    dots = [e for e in elements(section(page, template + '.color_product')) if e['type'] == 'shape' and e['w'] == e['h'] == 26]
    assert len(dots) >= 3
    tokens = resolve_page_tokens(value['pageStyle'], value['product']['colors'][0]['swatchId'])
    for dot in dots:
        assert dot['shape'] == 'circle'
        light = dot['fill'] in (CATALOG['swatchHex']['white'], CATALOG['swatchHex']['ivory'])
        assert dot.get('strokeWidth') == (1 if light else None)
        assert dot.get('stroke') == (tokens['colors']['accentLine'] if light else None)
    assert all(e.get('shape') != 'ellipse' for e in elements(page))


@pytest.mark.parametrize('template,widths', [('show', [91, 73, 73]), ('emo', [67, 55, 55])])
def test_r2_fit_pills_stay_on_one_line_with_padding(template, widths):
    blocks = section(assemble_input(case_input(template + '-2-mono')), template + '.wear_fit')
    assert not any(el.get('copyRole', '').startswith('cap.') for el in elements(blocks))
    assert all(el['x'] == 0 and el['w'] == 1000 for el in elements(blocks) if el['type'] == 'image')
    assert text(blocks, 'label')[0]['text'].upper() == 'LOOKBOOK'


def test_r3_showroom_facts_center_and_common_columns_stay_left():
    for template in ('show', 'emo'):
        fixture_name = 'show-2-mono' if template == 'show' else 'common-2-mono'
        facts = text(section(assemble_input(case_input(fixture_name)), template + '.intro'), 'facts')
        assert len(facts) == 6
        assert all(fact['style']['align'] == 'left' for fact in facts)


def test_r6_showroom_summary_dots_follow_the_first_line_and_center_the_group():
    els = elements(section(assemble_input(case_input('show-2-mono')), 'show.intro'))
    assert not any(el.get('copyRole', '').startswith('summary.') for el in els)
    assert next(el for el in els if el.get('copyRole') == 'name')['style']['size'] == 54
    assert next(el for el in els if el.get('copyRole') == 'body')['style']['size'] == 30
    assert len([el for el in els if el.get('factPart') == 'label']) == 3


@pytest.mark.parametrize('template,height', [('emo', 8133), ('show', 9645)])
def test_versioned_preview_file_is_a_complete_webp_at_twice_the_card_resolution(template, height):
    import re
    preview = next(t['preview'] for t in CATALOG['templates'] if t['id'] == template)
    assert re.fullmatch(rf'/templates/previews/preview-{template}-v[1-9]\d*\.webp', preview['src'])
    filename = Path(__file__).parents[2] / 'public' / preview['src'].lstrip('/')
    with Image.open(filename) as image:
        assert image.format == 'WEBP'
        assert image.size == (440, height)
        assert image.width >= 219 * 2
        image.load()
