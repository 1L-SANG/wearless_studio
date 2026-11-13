import pytest

from app.agents import page_templates
from app.agents.page_style import default_page_style, validate_page_style
from app.agents.page_templates import CATALOG, get_template, resolve_page_tokens, section_definition, section_tokens


def contrast(a, b):
    def luminance(value):
        rgb = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(rgb, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


def test_only_shipping_templates_remain_with_shared_guide_designs():
    assert [template['id'] for template in CATALOG['templates']] == ['emo', 'show']
    assert get_template('guide') is None
    assert all(key in CATALOG['designs'] for key in ['P01-D11', 'CP-GUIDE-01', 'T03-GUIDE-01', 'T05-D07', 'T07-D06'])
    assert CATALOG['kinds']['points']['name'] == '디테일 요소'
    assert [kind['kind'] for kind in CATALOG['kinds'].values() if kind['repeatable']] == ['wear']


def test_analysis_style_rejects_removed_guide_selection():
    with pytest.raises(ValueError, match='invalid_page_style'):
        validate_page_style({**default_page_style(), 'templateId': 'guide'})


def test_show_studio_recipe_and_added_life_definition_do_not_share_a_recipe():
    show = get_template('show')
    original = section_definition(show, 'show.wear_styling', 'wear', 'styling')
    assert original['usage'] == 'fit'
    assert original['photoSlots'][0]['recipe']['set'] == 'sequence'
    added = section_definition(show, 'show.wear_styling_2', 'wear', 'styling')
    assert added['designId'] == 'P02-D01'
    assert added['photoSlots'][0]['recipe']['cutType'] == 'styling'
    assert added['photoSlots'][0]['count'] == 2


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('swatch', ['white', 'gray', 'black', 'unknown', None])
def test_neutral_clothing_point_colors_equal_mono(template, swatch):
    mono = resolve_page_tokens({'templateId': template, 'colorMode': 'mono'})['colors']
    assert resolve_page_tokens({'templateId': template, 'colorMode': 'point'}, swatch)['colors'] == mono


@pytest.mark.parametrize('template', ['emo', 'show'])
@pytest.mark.parametrize('mode', ['mono', 'point'])
def test_all_text_roles_have_accessible_opaque_surface_contrast(template, mode):
    for swatch in CATALOG['pointPalette']:
        tokens = resolve_page_tokens({'templateId': template, 'colorMode': mode}, swatch)
        for role in ['ink', 'muted', 'accentInk']:
            for surface in ['canvas', 'panel', 'accentBg', 'summaryBg', 'pointBg']:
                assert contrast(tokens['colors'][role], tokens['colors'][surface]) >= 4.5, (template, mode, swatch, role, surface)
        if template == 'show':
            inverted = section_tokens(tokens, {'block': {'invert': True}})['colors']
            assert all(contrast(inverted[role], inverted['panel']) >= 4.5 for role in ['ink', 'muted', 'accentInk'])


def test_stale_template_token_resolution_uses_current_default():
    tokens = resolve_page_tokens({'templateId': 'guide', 'colorMode': 'point'}, 'blue')
    assert tokens['template']['id'] == 'emo'


@pytest.mark.parametrize('cut_type,stored_usage,want_usage,want_name', [
    ('styling', 'fit', 'styling', '일상 착용컷'), ('horizon', 'styling', 'fit', '스튜디오 착용컷'),
])
def test_saved_wear_name_follows_physical_type_without_changing_title(cut_type, stored_usage, want_usage, want_name):
    assert hasattr(page_templates, 'wear_usage_for_block')
    assert hasattr(page_templates, 'template_section_name')
    block = {'pageSectionKind': 'wear', 'pageSectionKey': 'emo.wear_fit', 'pageSectionUsage': stored_usage,
             'pageDesignId': 'P04-D01', 'cutType': cut_type, 'sectionTitle': '이전 제목'}
    assert page_templates.wear_usage_for_block(block) == want_usage
    assert page_templates.template_section_name(block) == want_name
    assert block['sectionTitle'] == '이전 제목'
