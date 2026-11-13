from copy import deepcopy

import pytest

from app.agents.page_template_assembler import assemble_v4
from app.agents.page_templates import CATALOG, get_template, validate_catalog

CARD, PLAIN = 'PT-PHOTO-CARD-01', 'PT-TEXT-PHOTO-01'


def point_input(template_id='show', design_id=PLAIN):
    key = f'{template_id}.points'
    common = {'taxonomyVersion': 4, 'pageSectionKey': key, 'pageSectionKind': 'points', 'pageDesignId': design_id}
    return {'storyboard': [{**common, 'id': 'lead', 'source': 'text'},
                           {**common, 'id': 'detail', 'source': 'ai', 'cutType': 'product', 'shot': 'detail', 'slotKey': None, 'detailTargetId': 'snap'}],
            'cut_results': [{'blockId': 'detail', 'imageUrl': 'detail:snap', 'width': 1000, 'height': 1500}],
            'copy_results': [], 'product': {}, 'copywriting': True,
            'analysis': {'detailRecommendations': {'candidates': [{'id': 'snap', 'label': '스냅 단추'}]},
                         'partDescriptionCopy': [{'point': '스냅 단추', 'desc': '은은한 메탈 스냅 단추로\n빈티지한 분위기를 더해요'}]},
            'page_style': {'templateId': template_id, 'fontStyle': 'plainGothic', 'colorMode': 'mono'}}


def point_elements(value):
    return [element for block in assemble_v4(**value) if block.get('pageSectionKind') == 'points' for element in block['elements']]


@pytest.mark.parametrize('template_id,expected', [('emo', CARD), ('show', PLAIN)])
def test_woc11_new_defaults_and_old_design_visibility(template_id, expected):
    template = get_template(template_id)
    assert next(section for section in template['sections'] if section['kind'] == 'points')['designId'] == expected
    assert template['addPlacements']['points']['designId'] == expected
    assert sorted(d['id'] for d in CATALOG['designs'].values() if d['kind'] == 'points' and not d.get('pickerHidden')) == sorted([CARD, PLAIN])


@pytest.mark.parametrize('prop,value', [('weight', 'thin'), ('weight', 300), ('numberPrefix', '1.'), ('trackingEm', 'tight'), ('trackingEm', -1), ('lineHeight', 0), ('lineHeight', True), ('copyPolicy', 'feature')])
def test_woc11_reject_malformed_text_opt_ins(prop, value):
    catalog = deepcopy(CATALOG)
    catalog['designs']['P06-D01']['layout'][0][prop] = value
    with pytest.raises(ValueError, match='text weight|number prefix|text tracking|text line height|copy policy'):
        validate_catalog(catalog)


@pytest.mark.parametrize('design_id,size,weight,width,height', [(CARD, 35, 400, 880, 1100), (PLAIN, 37, 300, 920, 1150)])
def test_woc11_loose_photo_and_two_line_copy_render(design_id, size, weight, width, height):
    value = point_input(design_id=design_id)
    elements = point_elements(value)
    desc = next(element for element in elements if element.get('copyRole') == 'items.0.desc')
    image = next(element for element in elements if element['type'] == 'image')
    assert (desc['style']['size'], desc['style']['weight']) == (size, weight)
    assert '\n' in desc['text']
    assert (image['w'], image['h']) == (width, height)
    assert image['h'] / image['crop']['ih'] == pytest.approx(5 / 6, abs=.001)
    assert image['crop']['oy'] > 0


@pytest.mark.parametrize('saved', ['', '옛 한 줄 문장', '첫 줄\n\n마지막 줄', '첫 줄\r\n둘째 줄'])
def test_woc11_part_only_copy_does_not_reuse_legacy_one_line_or_seller_copy(saved):
    value = point_input()
    value['analysis']['partDescriptionCopy'][0]['desc'] = saved
    value['analysis']['featureCopy'] = [{'point': '스냅 단추', 'desc': '판매 특징 문장'}]
    value['copy_results'] = [{'blockId': 'lead', 'texts': [{'role': 'items.0.desc', 'text': '기존 역할 문장'}]}]
    original = deepcopy(value)
    desc = next(el for el in point_elements(value) if el.get('copyRole') == 'items.0.desc')
    assert desc['text'] == ('첫 줄\n둘째 줄' if '\r\n' in saved else '')
    assert value == original
