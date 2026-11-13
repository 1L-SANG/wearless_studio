from copy import deepcopy

import pytest

from app.agents.page_templates import CATALOG, resolve_page_tokens, section_tokens
from page_template_cases import case_input, assemble_input


def test_existing_showroom_panel_nodes_follow_point_palette():
    value = case_input('woc5-show-P01-SHOW-01-reuse')
    value['pageStyle'] = {**value['analysis']['pageStyle'], 'colorMode': 'point'}
    first = [block for block in assemble_input(value) if block.get('pageSectionKind') == 'first']
    assert any(element.get('fill') == CATALOG['pointPalette']['blue']['accentBg'] for block in first for element in block['elements'])


@pytest.mark.parametrize('template_id', ['emo', 'show'])
@pytest.mark.parametrize('swatch', [None, 'null', 'white', 'gray', 'black', 'unknown'])
def test_neutral_saved_point_is_effectively_mono_without_mutating_style(template_id, swatch):
    style = {'templateId': template_id, 'colorMode': 'point'}
    before = deepcopy(style)
    point = resolve_page_tokens(style, swatch)
    assert point['colorMode'] == 'mono'
    assert point['colors'] == resolve_page_tokens({**style, 'colorMode': 'mono'}, swatch)['colors']
    assert style == before


@pytest.mark.parametrize('template_id', ['emo', 'show'])
@pytest.mark.parametrize('swatch', ['blue', 'beige', 'green', 'pink'])
def test_point_tints_pale_panels_and_preserves_dark_neutral_roles(template_id, swatch):
    point = resolve_page_tokens({'templateId': template_id, 'colorMode': 'point'}, swatch)
    mono = resolve_page_tokens({'templateId': template_id, 'colorMode': 'mono'}, swatch)
    assert point['colors']['accentBg'] == CATALOG['pointPalette'][swatch]['accentBg']
    assert point['colors']['ink'] == mono['colors']['ink']
    assert point['colors']['invertBg'] == mono['colors']['invertBg']
    if template_id == 'show':
        assert section_tokens(point, {'block': {'invert': True}})['colors'] == section_tokens(mono, {'block': {'invert': True}})['colors']
