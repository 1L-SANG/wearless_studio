"""Behavior tests for opt-in natural photos and ordered WOC6 photo flows."""
import pytest

from app.agents.page_template_assembler import assemble_v4
from app.agents.page_templates import CATALOG


def images(blocks):
    return [el for block in blocks for el in block['elements'] if el['type'] == 'image']


def render(monkeypatch, node, cards, cuts=None, kind='wear'):
    design = {'id': 'WOC6-ENGINE-TEST', 'kind': kind, 'padding': 60, 'top': 0, 'bottom': 0,
              'layout': [node], 'overflow': {'type': 'photoStack', 'aspect': 'source', 'gap': 40}}
    monkeypatch.setitem(CATALOG['designs'], design['id'], design)
    storyboard = [dict(id=f'photo-{i}', taxonomyVersion=4, source='ai', cutType='styling' if kind == 'wear' else 'product',
                       shot='full' if kind == 'wear' else 'detail', direction=['front', 'side', 'back'][i % 3],
                       pageSectionKey=f'emo.{"wear_styling" if kind == "wear" else "points"}', pageSectionKind=kind,
                       pageSectionUsage='styling', pageDesignId=design['id'], colorId='base',
                       slotKey=f'{"wear_styling" if kind == "wear" else "points"}.extra{i + 1}') | card
                  for i, card in enumerate(cards)]
    return assemble_v4(storyboard, cuts or [], [], {'colors': [{'id': 'base', 'name': '블랙', 'isBase': True}]}, True)


@pytest.mark.parametrize(('pattern', 'sizes'), [('twoColumn', [2, 2, 1, 2, 1]), ('heroPair', [1, 2, 2, 1, 2])])
def test_flow_keeps_five_plus_three_groups_and_global_caption_ids(monkeypatch, pattern, sizes):
    node = dict(type='photoFlow', slot='*', pattern=pattern, groupBy='spaceGroup', aspect='4/5', gap=24,
                captions=[{'type': 'text', 'copyRole': 'colorName.n'}, {'type': 'text', 'copyRole': 'cap.{direction}'}])
    blocks = [block for block in render(monkeypatch, node, [{'spaceGroupId': 'a' if i < 5 else 'b'} for i in range(8)]) if block.get('pageSectionKey')]
    assert [len(images([block])) for block in blocks] == sizes
    assert [el['sourceBlockId'] for el in images(blocks)] == [f'photo-{i}' for i in range(8)]
    assert [el['slotKey'] for el in images(blocks)] == [f'wear_styling.extra{i + 1}' for i in range(8)]
    assert [el['copyRole'] for block in blocks for el in block['elements'] if el.get('copyRole', '').startswith('colorName.')] == [f'colorName.{i}' for i in range(8)]
    assert [el['text'] for block in blocks for el in block['elements'] if el.get('copyRole', '').startswith('cap.')] == [''] * 8
    for block in blocks:
        assert all(image['w'] == (880 if len(images([block])) == 1 else 428) for image in images([block]))


def test_flow_never_merges_disjoint_groups_and_uses_loose_color_boundaries(monkeypatch):
    node = dict(type='photoFlow', slot='*', pattern='heroPair', groupBy='spaceGroup', aspect='4/5', gap=24, captions=[])
    cards = [{'spaceGroupId': 'a'}, {'spaceGroupId': 'a'}, {'spaceGroupId': 'b'}, {'spaceGroupId': 'a'},
             {'colorId': 'base'}, {'colorId': 'base'}, {'colorId': 'other'}, {'colorId': 'other'}, {'colorId': 'base'}]
    blocks = [block for block in render(monkeypatch, node, cards) if block.get('pageSectionKey')]
    assert [[el['sourceBlockId'] for el in images([block])] for block in blocks] == [
        ['photo-0', 'photo-1'], ['photo-2'], ['photo-3'], ['photo-4', 'photo-5'], ['photo-6', 'photo-7'], ['photo-8']]
    node['groupBy'] = 'none'
    assert [len(images([block])) for block in render(monkeypatch, node, cards) if block.get('pageSectionKey')] == [1, 2, 1, 2, 1, 2]


@pytest.mark.parametrize(('meta', 'height'), [
    ({'width': 600, 'height': 900}, 1320), ({'width': 6000, 'height': 300}, 44),
    ({'width': 300, 'height': 6000}, 17600), ({'width': 1024, 'height': 1365, 'dimensionsKnown': False}, 1320),
    ({'width': 6000, 'height': 1}, 1),
    ({}, 1320), ({'width': 0, 'height': 100}, 1320),
])
def test_natural_photo_ignores_estimates_and_never_clamps_or_crop_tops(monkeypatch, meta, height):
    node = {'type': 'photoStack', 'slot': '*', 'items': [{'type': 'photo', 'slot': '$current', 'fit': 'contain', 'aspect': 'source', 'fallbackAspect': '2/3', 'cropTop': .2}]}
    image = images(render(monkeypatch, node, [{}], [{'blockId': 'photo-0', 'imageUrl': '/detail.webp', **meta}], 'points'))[0]
    assert (image['w'], image['h']) == (880, height)
    assert 'crop' not in image


def test_legacy_source_photo_keeps_height_clamp_and_crop_top(monkeypatch):
    node = {'type': 'photoStack', 'slot': '*', 'items': [{'type': 'photo', 'slot': '$current', 'aspect': 'source', 'cropTop': .2}]}
    image = images(render(monkeypatch, node, [{}], [{'blockId': 'photo-0', 'imageUrl': '/legacy.webp', 'width': 300, 'height': 6000}]))[0]
    assert image['h'] == 2400
    assert image['crop']


@pytest.mark.parametrize('pattern', ['twoColumn', 'heroPair'])
def test_flow_keeps_null_slot_photo_at_its_group_position_with_caption(monkeypatch, pattern):
    node = dict(type='photoFlow', slot='*', pattern=pattern, groupBy='spaceGroup', aspect='4/5', gap=24,
                captions=[{'type': 'text', 'copyRole': 'colorName.n'}, {'type': 'text', 'copyRole': 'cap.{direction}'}])
    cards = [{'spaceGroupId': 'a'}, {'spaceGroupId': 'a', 'slotKey': None}, {'spaceGroupId': 'b'}, {'spaceGroupId': 'b'}]
    blocks = [block for block in render(monkeypatch, node, cards) if block.get('pageSectionKey')]
    assert [[el['sourceBlockId'] for el in images([block])] for block in blocks] == [['photo-0', 'photo-1'], ['photo-2', 'photo-3']]
    assert images(blocks)[1]['slotKey'] == 'wear_styling.extra2'
    assert [el['copyRole'] for block in blocks for el in block['elements'] if el.get('copyRole', '').startswith('colorName.')] == ['colorName.0', 'colorName.1', 'colorName.2', 'colorName.3']


def test_color_column_uses_source_color_index_for_name_and_swatch_after_front_removal():
    board = [dict(id=f'photo-{i}', taxonomyVersion=4, source='ai', cutType='product', shot='ghost',
                  pageSectionKey='emo.color_product', pageSectionKind='color_product', pageDesignId='CP-COLUMN-01',
                  colorId=color, direction=direction, slotKey=f'color_product.{direction}.{color}')
             for i, (color, direction) in enumerate([('white', 'front'), ('black', 'back')])]
    product = {'colors': [{'id': 'black', 'name': '블랙', 'swatchId': 'black', 'isBase': True}, {'id': 'white', 'name': '화이트', 'swatchId': 'white'}]}
    elements = [el for block in assemble_v4(board, [], [], product, True) if block.get('pageSectionKey') for el in block['elements']]
    captions = [el for el in elements if el.get('copyRole', '').endswith('.name')]
    assert [(el['copyRole'], el['text']) for el in captions] == [('colors.1.name', '화이트'), ('colors.0.name', '블랙')]
    assert [el['fill'] for el in elements if el.get('shape') == 'circle' and el['w'] == 10] == ['#ffffff', '#15141a']
