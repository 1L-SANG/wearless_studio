"""WOC23 assembly values and common design behavior, without providers or DB."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agents.page_templates import CATALOG, validate_catalog, walk_layout
from app.agents.page_template_assembler import balanced_photo_rows, wrap_text
from page_template_cases import assemble_input, case_input


def elements(blocks):
    return [el for block in blocks for el in block['elements']]


def copy(blocks, role):
    return next(el for el in elements(blocks) if el.get('copyRole') == role)


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('design', ['P01-SHOW-01', 'P01-SHOW-COMMENT-01'])
@pytest.mark.parametrize('photo', ['photo', 'ghost'])
@pytest.mark.parametrize('intro', ['moved', 'added'])
def test_selected_linked_opening_assembles_intro_immediately_after_saved_first_section(template, design, photo, intro):
    value = case_input(f'woc23-{template}-opening-order-{design}-{photo}-{intro}')
    saved_order = list(dict.fromkeys(card['pageSectionKey'] for card in value['storyboard']))
    assert saved_order[:2] == [f'{template}.first', f'{template}.intro']
    page = assemble_input(value)
    assert list(dict.fromkeys(block['pageSectionKey'] for block in page if block.get('pageSectionKey'))) == saved_order
    assert all(block['pageDesignId'] == CATALOG['designs'][design]['introDesignId'] for block in page if block.get('pageSectionKind') == 'intro')


COLOR_PRODUCTS = json.loads((Path(__file__).parents[2] / 'tests/fixtures/page_templates_color_products.json').read_text())['cases']


def color_product_input(template, design, case):
    value = case_input(f'woc23-{template}-{design}-1')
    value['product']['colors'] = [dict(id=f'c{index}', name=name, swatchId='blue', isBase=index == 0) for index, name in enumerate(case['names'])]
    value['storyboard'] = [{**value['storyboard'][0], 'id': f'color-{index}', 'slotKey': f'color_product.front.c{index}', 'colorId': f'c{index}'}
                           for index in case.get('renderColorIndexes', range(len(case['names'])))]
    return value


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('design', ['CP-COLUMN-01', 'CP-EMO-01'])
@pytest.mark.parametrize('case', COLOR_PRODUCTS, ids=lambda case: case['id'])
def test_product_color_language_is_uniform_with_matching_tracking(template, design, case):
    value = color_product_input(template, design, case)
    labels = [el for el in elements(assemble_input(value)) if el.get('copyRole', '').startswith('colors.') and el['copyRole'].endswith('.name')]
    indices = range(len(case['names'])) if design == 'CP-COLUMN-01' else case.get('renderColorIndexes', range(len(case['names'])))
    expected = []
    for index in indices:
        name = case['names'][index]
        english = case['english'][index] if case['english'] else ''
        expected.append((english or name) if template == 'show' else (english if name.upper() == english else f'{name} {english}') if english else name)
    assert [el['text'] for el in labels] == expected
    for label in labels:
        assert label['style']['size'] == (28 if template == 'show' else 30)
        assert label['style']['font'] == 'Pretendard'
        assert label['style']['weight'] == (400 if template == 'show' else 600)
        assert label['style']['tracking'] == (3.36 if template == 'show' and case['english'] else 0)


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('case_id', ['all-known', 'one-unknown'])
def test_direct_english_slots_share_product_language(template, case_id, monkeypatch):
    design = CATALOG['designs']['CP-COLUMN-01']
    monkeypatch.setitem(design, 'layout', [{'type': 'text', 'copyRole': 'colors.n.en', 'style': 'body', 'hideEmpty': True}, *design['layout']])
    case = next(item for item in COLOR_PRODUCTS if item['id'] == case_id)
    labels = [el['text'] for el in elements(assemble_input(color_product_input(template, 'CP-COLUMN-01', case))) if el.get('copyRole') == 'colors.0.en']
    assert labels == (['BLACK'] if case['english'] else [])


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_product_color_language_keeps_color_fact_row(template):
    value = case_input(f'woc23-{template}-T06-D01')
    value['product']['colors'] = [dict(id=f'c{index}', name=name, swatchId='blue', isBase=index == 0)
                                  for index, name in enumerate(next(item for item in COLOR_PRODUCTS if item['id'] == 'one-unknown')['names'])]
    value['storyboard'] = [card for card in value['storyboard'] if card['pageSectionKind'] in ('first', 'intro')]
    assert next(el['text'] for el in elements(assemble_input(value)) if el.get('factLabel') == 'COLOR' and el.get('factPart') == 'value') == '블랙, 아이보리, 자두, 그레이'


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('design', ['T06-D01', 'T06-COMMENT-01'])
def test_opening_boards_follow_caption_free_photo_and_use_product_name(template, design):
    value = case_input(f'woc23-{template}-{design}')
    page = assemble_input(value)
    first = next(block for block in page if block.get('pageSectionKind') == 'first')
    intro = [block for block in page if block.get('pageSectionKind') == 'intro']
    photo = next(el for el in first['elements'] if el['type'] == 'image')
    assert first['h'] == photo['h']
    assert not any(el['type'] == 'text' for el in first['elements'])
    assert copy(intro, 'name')['text'] == value['product']['name']
    assert copy(intro, 'name')['style']['size'] == (54 if template == 'show' else 51)
    assert copy(intro, 'body')['style']['size'] == (30 if template == 'show' else 32)
    if design == 'T06-COMMENT-01':
        assert copy(intro, 'label')['text'].upper() == 'COMMENT'
        assert intro[0]['bg'] != CATALOG['canvasColor']


ROWS = [(1, [1]), (2, [2]), (3, [3]), (4, [2, 2]), (5, [2, 3]), (6, [3, 3]), (7, [2, 2, 3]), (8, [2, 3, 3])]


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('count,rows', ROWS)
def test_balanced_color_rows_use_individual_widths(template, count, rows):
    page = [block for block in assemble_input(case_input(f'woc23-{template}-CP-EMO-01-{count}'))
            if block.get('pageSectionKind') == 'color_product']
    assert balanced_photo_rows(count) == rows
    assert len(page) == len(rows)
    for block, columns in zip(page, rows):
        photos = [el for el in block['elements'] if el['type'] == 'image']
        width = (888 - 24 * (columns - 1)) // columns
        assert [el['w'] for el in photos] == [width] * columns
        assert [el['x'] for el in photos] == [56 + index * (width + 24) for index in range(columns)]
    names = [el for el in elements(page) if el.get('copyRole', '').endswith('.name')]
    assert len(names) == count
    assert all(el['style']['size'] == (28 if template == 'show' else 30) for el in names)


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_legend_precedes_full_width_photos_without_detached_captions(template):
    page = assemble_input(case_input(f'woc23-{template}-CP-COLUMN-01-5'))
    page = [block for block in page if block.get('pageSectionKind') == 'color_product']
    photos = [el for el in elements(page) if el['type'] == 'image']
    names = [el for el in elements(page) if el.get('copyRole', '').endswith('.name')]
    assert len(names) == 5
    assert all(el['y'] < photos[0]['y'] for el in names)
    assert all(el['x'] == 0 and el['w'] == 1000 for el in photos)
    assert not any(el.get('copyRole', '').startswith('cap.') for el in elements(page))


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_wear_is_full_width_with_template_gap_and_lookbook(template):
    page = assemble_input(case_input(f'woc20-{template}-wear-sets'))
    wear = [block for block in page if block.get('pageSectionKind') == 'wear']
    for key in {block['pageSectionKey'] for block in wear}:
        group = [block for block in wear if block['pageSectionKey'] == key]
        label = copy(group, 'label')
        assert label['text'].upper() == ('LOOKBOOK' if 'fit' in key or template == 'show' else 'DAILY LOOK')
        assert label['x'] == 56
        assert label['style']['size'] == (42 if template == 'show' else 48)
        for block in group[:-1]:
            photo = next(el for el in block['elements'] if el['type'] == 'image')
            assert (photo['x'], photo['w']) == (0, 1000)
            assert block['h'] - photo['y'] - photo['h'] == (40 if template == 'show' else 0)


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_detail_and_fabric_use_template_type_outside_the_photo(template):
    value = case_input(f'woc11-{template}-PT-TEXT-PHOTO-01-template-mono')
    for part in value['analysis']['partDescriptionCopy']:
        part['desc'] = '세탁 뒤에도 뜨지 않게 눌러 박은 카라 끝'
    points = [block for block in assemble_input(value) if block.get('pageSectionKind') == 'points']
    assert copy(points, 'items.0.name')['style']['size'] == (46 if template == 'show' else 48)
    desc = copy(points, 'items.0.desc')
    assert (desc['style']['size'], desc['style']['weight']) == (32, 400)
    assert desc['h'] == desc['style']['lineHeight']
    value['storyboard'] = [{**card, 'pageDesignId': 'T03-D06'} for card in value['storyboard'] if card['pageSectionKind'] == 'fabric']
    fabric = assemble_input(value)
    photo = next(el for el in elements(fabric) if el['type'] == 'image')
    assert copy(fabric, 'title')['y'] == photo['y'] + photo['h'] + 44
    assert copy(fabric, 'mix')['style']['size'] == 28
    assert copy(fabric, 'body')['style']['size'] == 32


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[2] / 'tests/fixtures/page_templates_color_names.json').read_text())['cases'], ids=lambda case: case['id'])
def test_color_name_language_rules(template, case):
    value = case_input(f'woc23-{template}-CP-COLUMN-01-1')
    value['product']['colors'][0].pop('name', None)
    value['product']['colors'][0].update({key: case[key] for key in ('name', 'label', 'swatchId') if key in case})
    name, english = (case.get('expectedName') or case.get('name')).strip(), case['english']
    text = copy(assemble_input(value), 'colors.0.name')['text']
    assert text == ((english or name) if template == 'show' else (english if name.upper() == english else f'{name} {english}') if english else name)


def test_all_saved_designs_remove_small_detached_direction_and_wear_color_names():
    for design in CATALOG['designs'].values():
        for node in walk_layout(design['layout']):
            assert not node.get('copyRole', '').startswith('cap.')
            assert not (design['kind'] == 'wear' and node.get('copyRole', '').startswith('colorName.'))


@pytest.mark.parametrize('mutation', ['english', 'type', 'rows'])
def test_invalid_catalog_extensions_are_rejected(mutation):
    catalog = deepcopy(CATALOG)
    if mutation == 'english':
        del catalog['colorNameEnglish']
    elif mutation == 'type':
        catalog['templates'][0]['typography']['body']['size'] = 0
    else:
        catalog['designs']['CP-EMO-01']['layout'][1]['balancedRows'] = 'yes'
    with pytest.raises(ValueError):
        validate_catalog(catalog)
