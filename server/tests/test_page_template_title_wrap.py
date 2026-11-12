"""WOQ6 fixed line decisions, geometry and source-pixel placement integration."""
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents.page_template_assembler import wrap_text
from app.agents.page_template_contrast import overlay_text_metadata
from app.agents.page_templates import CATALOG, validate_catalog
from PIL import Image
from page_template_cases import assemble_input, case_input, collect_input_regions, elements, section

CASES = json.loads((Path(__file__).parents[2] / 'tests/fixtures/page_templates_title_wrap.json').read_text())['cases']
KEY = 'emo.first:first.hero:center'


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
@pytest.mark.parametrize('position', ['center', 'top', 'bottom'])
def test_balanced_title_size_rules_and_position_share_the_actual_line_geometry(case, position):
    value = case_input('emo-2-mono')
    value['product']['name'] = case['title']
    value['cutResults'][0]['overlayTextPositions'] = {KEY: position}
    els = elements(section(assemble_input(value), 'emo.first'))
    title = next(e for e in els if e.get('copyRole') == 'name')
    rules = [e for e in els if e['type'] == 'shape']
    texts = [e for e in els if e['type'] == 'text']
    assert title['text'] == '\n'.join(case['lines'])
    assert title['style']['size'] == case['size']
    assert title['keepWords'] is True
    assert (title['x'], title['w']) == (150, 700)
    assert title['h'] == len(case['lines']) * math.ceil(case['size'] * 1.3)
    assert wrap_text(title['text'], title['w'], case['size'], tracking=title['style']['tracking']) == case['lines']
    assert [(r['x'], r['w'], r['h']) for r in rules] == [((1000 - case['ruleWidth']) / 2, case['ruleWidth'], 1.5)] * 2
    assert texts[0]['y'] - rules[0]['y'] - rules[0]['h'] == 18
    assert rules[1]['y'] - texts[-1]['y'] - texts[-1]['h'] == 18
    top, bottom = rules[0]['y'], rules[1]['y'] + rules[1]['h']
    if position == 'center':
        assert (top + bottom) / 2 == 625
    elif position == 'top':
        assert top == 100
    else:
        assert bottom == 1150
    plan = next(p for p in collect_input_regions(value) if p['key'] == KEY)
    lines = [line for line in plan['lines'] if line['key'].startswith(f'{KEY}:1:')]
    assert len(lines) == len(case['lines'])
    for i, line in enumerate(lines):
        assert line['region']['top'] == (title['y'] + i * title['style']['lineHeight']) / 1250
        assert (line['region']['right'] - line['region']['left']) * 1000 == pytest.approx(math.ceil(case['lineWidths'][i]))
    assert ''.join(title['text'].split()) == ''.join(case['title'].split())
    assert value['product']['name'] == case['title']


def test_name_alone_sets_rule_width_when_other_copy_is_wider():
    value = case_input('emo-2-mono')
    value['product']['name'] = 'I'
    for text in value['copyResults'][0]['texts']:
        text['text'] = 'A considerably longer line'
    els = elements(section(assemble_input(value), 'emo.first'))
    assert [e['w'] for e in els if e['type'] == 'shape'] == [48, 48]


@pytest.mark.parametrize('policy', [
    {'photoWidthRatio': 0}, {'sizes': [51, 38, 44]}, {'sizes': []},
    {'maxLines': 0}, {'minSizeMaxLines': 1},
])
def test_catalog_rejects_invalid_title_wrapping(policy):
    catalog = deepcopy(CATALOG)
    title = next(n for n in catalog['designs']['P01-D09']['layout'][0]['groups'][0]['nodes'] if n.get('copyRole') == 'name')
    title['titleWrap'] = {**title.get('titleWrap', {}), **policy}
    with pytest.raises(ValueError, match='title wrap'):
        validate_catalog(catalog)


@pytest.mark.parametrize('name', ['가' * 19, ' '.join(['가나다라마바사아자차'] * 7)])
def test_infeasible_copy_keeps_every_word_at_the_font_floor(name):
    value = case_input('emo-2-mono')
    value['product']['name'] = name
    title = next(e for e in elements(section(assemble_input(value), 'emo.first')) if e.get('copyRole') == 'name')
    assert title['style']['size'] == 38
    assert title['keepWords'] is True
    assert title['text'].split('\n') == name.split(' ')


@pytest.mark.parametrize('name', ['two-line-balance', 'size-44', 'size-38-three', 'bracket-boundary'])
@pytest.mark.parametrize('position,bad', [('bottom', ['center']), ('top', ['center', 'bottom'])])
def test_wrapped_name_relocation_samples_the_new_line_bounds(name, position, bad):
    case = next(c for c in CASES if c['name'] == name)
    value = case_input('emo-2-mono')
    value['product']['name'] = case['title']
    plan = next(p for p in collect_input_regions(value) if p['key'] == KEY)
    with Image.new('RGB', (1000, 1250), 'white') as image:
        for candidate in plan['positionCandidates']:
            if candidate['position'] not in bad:
                continue
            for line in candidate['lines']:
                if not line['key'].startswith(f'{KEY}:1:'):
                    continue
                region = line['region']
                image.paste('black', (0, math.floor(region['top'] * 1250), 500, math.ceil(region['bottom'] * 1250)))
        hero = next(c for c in value['storyboard'] if c['pageSectionKind'] == 'first')
        result = overlay_text_metadata(hero, image, product=value['product'], analysis=value['analysis'],
                                       storyboard=value['storyboard'], copy_results=value['copyResults'])
    assert result['overlayTextPositions'] == {KEY: position}
    assert result['overlayTextColors'] == {KEY: '#111111'}
    value['cutResults'][0].update(result)
    title = next(e for e in elements(section(assemble_input(value), 'emo.first')) if e.get('copyRole') == 'name')
    assert title['text'] == '\n'.join(case['lines'])
    assert [line['text'] for line in title['contrastLines']] == case['lines']
    assert all(line == {'text': text, 'color': '#111111', 'shade': False} for line, text in zip(title['contrastLines'], case['lines']))
    chosen = next(c for c in plan['positionCandidates'] if c['position'] == position)
    first_name_line = next(l for l in chosen['lines'] if l['key'] == f'{KEY}:1:0')
    assert title['y'] == first_name_line['region']['top'] * 1250
