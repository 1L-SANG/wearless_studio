from pathlib import Path

import pytest
from PIL import ImageFont

from page_template_cases import assemble_input, case_input, elements, section


@pytest.mark.parametrize('mode', ['mono', 'point'])
@pytest.mark.parametrize('display_name,initial', [(None, 'MD'), ('', 'MD'), ('김민지', '김'), ('Wearless', 'W'), ('MD', 'M'), ('🧵옷가게', '🧵')])
def test_md_avatar_fits_one_centered_line_in_the_bundled_font(mode, display_name, initial):
    value = case_input('emo-2-mono')
    value['pageStyle'] = {**value['analysis']['pageStyle'], 'colorMode': mode}
    if display_name is not None:
        value['profile'] = {'display_name': display_name}
    els = elements(section(assemble_input(value), 'emo.intro'))
    avatar = next(e for e in els if e['type'] == 'text' and not e.get('copyRole') and e['text'] == initial)
    circle = next(e for e in els if e['type'] == 'shape' and e.get('radius') == 999 and e['x'] <= avatar['x'] and e['y'] <= avatar['y'])
    assert (circle['w'], circle['h']) == (98, 98)
    assert avatar['w'] == 50
    assert avatar['style']['size'] == 24
    assert avatar['style']['align'] == 'center'
    assert avatar['h'] == avatar['style']['lineHeight']
    assert avatar['x'] + avatar['w'] / 2 == circle['x'] + circle['w'] / 2
    assert avatar['y'] + avatar['h'] / 2 == circle['y'] + circle['h'] / 2

    font_path = Path(__file__).parents[2] / 'src/fonts/PretendardVariable.woff2'
    font = ImageFont.truetype(str(font_path), size=avatar['style']['size'])
    font.set_variation_by_axes([avatar['style']['weight']])
    width = font.getlength(avatar['text']) + len(avatar['text']) * avatar['style']['tracking']
    assert width + 4 <= avatar['w'], 'the real font must fit with room for pixel rounding'
