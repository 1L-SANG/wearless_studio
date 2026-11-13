"""WOC6B frame count, selection and natural-size contracts."""
from copy import deepcopy

import pytest

from app.agents import page_template_assembler as engine
from app.agents.page_templates import CATALOG, validate_catalog


def input_for(count=6, layout='COLUMN', frames=2):
    return [dict(id=f'photo-{i}', taxonomyVersion=4, source='ai', cutType='styling',
                 shot='medium' if i % 3 else 'full', direction='front', colorId='base', spaceGroupId='a' if i < 3 else 'b',
                 pageSectionKey='emo.wear_styling', pageSectionKind='wear', pageSectionUsage='styling',
                 pageDesignId=f'WEAR-STYLING-FRAME-{layout}-0{frames}', slotKey=f'wear_styling.extra{i + 1}')
            for i in range(count)]


def render(board, cuts=None, profile=None, shop_name=None):
    return [block for block in engine.assemble_v4(board, cuts or [], [], {'name': '코튼 니트', 'colors': [{'id': 'base', 'isBase': True}]},
                                                True, profile=profile, shop_name=shop_name) if block.get('pageSectionKey')]


def images(blocks, backdrop=False):
    return [el for block in blocks for el in block['elements'] if el['type'] == 'image' and ('blur' in el) == backdrop]


@pytest.mark.parametrize(('layout', 'pattern', 'minimum'), [('COLUMN', 'column', 1), ('2COL', 'twoColumn', 2), ('HERO', 'heroPair', 3)])
@pytest.mark.parametrize('count', [1, 2, 3])
def test_frame_designs_and_original_photo_consumption(layout, pattern, minimum, count):
    board = input_for(8, layout, count)
    design = CATALOG['designs'][board[0]['pageDesignId']]
    assert design['framePhotos'] == count
    assert design['minPhotos'] == max(count, minimum)
    assert design['choiceAxes']['decoration'] == {'id': 'frame', 'name': '액자'}
    assert next(node for node in design['layout'] if node['type'] == 'photoFrame')['pattern'] == pattern
    blocks = render(board, shop_name='몰 이름')
    selection = engine.select_frame_photos(board, count)
    assert [el['sourceBlockId'] for el in images(blocks)] == [card['id'] for card in selection['frame'] + selection['remaining']]
    assert len(images(blocks)) == 8
    backdrop = images(blocks, True)
    assert len(backdrop) == 1 and backdrop[0]['blur'] == 48 and not backdrop[0].get('locked')
    assert backdrop[0]['sourceBlockId'] == selection['background']['id']
    assert all((el['w'], el['h']) == (560, 747) and 'crop' not in el for el in images([blocks[0]]))
    assert validate_catalog(CATALOG) is CATALOG


def test_selection_is_contiguous_stable_and_group_scoped_for_backdrop():
    cards = input_for(5)
    for card, group, shot in zip(cards, ['a', 'a', 'b', 'b', 'a'], ['medium', 'full', 'medium', 'medium', 'medium']):
        card.update(spaceGroupId=group, shot=shot)
    before = deepcopy(cards)
    chosen = engine.select_frame_photos(cards, 2)
    assert [card['id'] for card in chosen['frame']] == ['photo-2', 'photo-3']
    assert chosen['background']['id'] == 'photo-2'
    assert [[card['id'] for card in group] for group in chosen['remainingGroups']] == [['photo-0', 'photo-1'], ['photo-4']]
    chosen = engine.select_frame_photos(cards, 3)
    assert [card['id'] for card in chosen['frame']] == ['photo-2', 'photo-3', 'photo-0']
    assert cards == before


@pytest.mark.parametrize(('meta', 'height'), [({}, 747), ({'width': 6000, 'height': 300}, 28),
    ({'width': 300, 'height': 6000}, 11200), ({'width': 6000, 'height': 1}, 1),
    ({'width': 1024, 'height': 1365, 'dimensionsKnown': False}, 747)])
def test_single_frame_natural_height_missing_footer_and_no_remainder(meta, height):
    blocks = render(input_for(1, frames=1), [{'blockId': 'photo-0', 'imageUrl': '/single.webp', **meta}])
    assert len(blocks) == 1
    image, backdrop = images(blocks)[0], images(blocks, True)[0]
    assert (image['w'], image['h']) == (560, height) and 'crop' not in image
    assert image['sourceBlockId'] == backdrop['sourceBlockId']
    assert not any(el.get('copyRole') == 'who' for el in blocks[0]['elements'])
    assert blocks[0]['h'] == backdrop['y'] + backdrop['h'] + CATALOG['templates'][0]['spacing']['sectionGap']


@pytest.mark.parametrize('patch', [{'framePhotos': 4}, {'framePhotos': 1}, {'usage': 'fit'}, {'minPhotos': 1}])
def test_catalog_rejects_inconsistent_frame_contract(patch):
    value = deepcopy(CATALOG)
    value['designs']['WEAR-STYLING-FRAME-COLUMN-02'].update(patch)
    with pytest.raises(ValueError, match='[Ff]rame'):
        validate_catalog(value)


@pytest.mark.parametrize(('shop_name', 'expected'), [(None, []), ('', []), ('  ', []), (123, []),
    (False, []), ({'name': '몰'}, []), (['몰'], []), ('  공개 상점  ', ['공개 상점'])])
def test_frame_footer_only_accepts_explicit_trimmed_shop_name(shop_name, expected):
    blocks = render(input_for(1, frames=1), profile={'display_name': '개인 계정 실명'}, shop_name=shop_name)
    all_elements = [el for block in blocks for el in block['elements']]
    assert [el['text'] for el in all_elements if el.get('copyRole') == 'who'] == expected
    assert not any(el.get('text') == '개인 계정 실명' for el in all_elements)


def test_shop_name_does_not_replace_legacy_profile_initial_or_seller_name():
    board = input_for(1, frames=1)
    board[0].update(id='intro', source='text', pageSectionKey='emo.intro', pageSectionKind='intro', pageDesignId='T01-D07')
    elements = [el for block in render(board, profile={'display_name': '김민지'}, shop_name='공개 상점') for el in block['elements']]
    assert any(el['type'] == 'text' and not el.get('copyRole') and el['text'] == '김' for el in elements)
    assert any(el.get('copyRole') == 'who' and el['text'] == '김민지' for el in elements)
    assert not any(el.get('text') == '공개 상점' for el in elements)


def test_catalog_declares_public_shop_name_and_retains_legacy_profile_fact():
    assert 'mall_connections.shop_name' in CATALOG['factPaths']
    assert 'profiles.display_name' in CATALOG['factPaths']
    assert validate_catalog(CATALOG) is CATALOG
    value = deepcopy(CATALOG)
    next(slot for slot in value['kinds']['intro']['textSlots'] if slot['copyRole'] == 'who')['factPath'] = 'mall_connections.shop_name'
    assert validate_catalog(value) is value
