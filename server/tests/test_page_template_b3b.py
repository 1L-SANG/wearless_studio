"""B3b empty rows and scrim regression coverage, no browsers/providers."""
import re
import pytest
from page_template_cases import assemble_input, case_input, elements
from test_page_template_copy_v2 import intro_input
from app.agents.page_templates import CATALOG


@pytest.mark.parametrize('seller', ['', '셀러가 쓴 본문이에요'])
def test_empty_intro_title_collapses_above_populated_body(seller):
    value = intro_input(seller=seller, body='본문이 있는 말풍선이에요')
    value['copyResults'][0]['texts'] = [{'role': 'body', 'text': '본문이 있는 말풍선이에요'}]
    els = elements(assemble_input(value))
    body = next(el for el in els if el.get('copyRole') == 'body')
    panel = next(el for el in els if el['type'] == 'shape' and el.get('groupId') == body['groupId'])
    title = next(el for el in els if el.get('copyRole') == 'title')
    assert title.get('hidden')
    assert body['y'] - panel['y'] == 22
    assert panel['y'] + panel['h'] - body['y'] - body['h'] == 22
    assert body['placeholder'] == 'MD 코멘트'


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_review_keeps_text_and_collapses_only_missing_author(template):
    value = case_input('emo-2-mono')
    value['storyboard'] = [{**b, 'pageSectionKey': f'{template}.review'} for b in value['storyboard'] if b['pageSectionKind'] == 'review']
    value['pageStyle'] = {**CATALOG['defaults'], 'templateId': template}
    value['copyResults'] = [{'blockId': value['storyboard'][0]['id'], 'texts': [{'role': 'items.0.text', 'text': '골이 촘촘해서 몸에 붙지 않고 편해요'}]}]
    els = elements(assemble_input(value))
    text = next(el for el in els if el.get('copyRole') == 'items.0.text')
    author = next((el for el in els if el.get('copyRole') == 'items.0.who'), None)
    assert not author or author.get('hidden')
    panel = next(el for el in els if el['type'] == 'shape' and el.get('groupId') == text['groupId'])
    assert panel['y'] + panel['h'] - text['y'] - text['h'] == 16


def test_retained_review_scrim_protects_first_text_on_white_photo():
    value = case_input('copy-b-emo-I09-SCRIM-01-static-stars')
    block = assemble_input(value)[0]
    scrim = next(el for el in block['elements'] if el.get('fill', '').startswith('linear-gradient'))
    opacities = [float(alpha) for alpha in re.findall(r'rgba\(0,\s*0,\s*0,\s*([0-9.]+)\)', scrim['fill'])]
    assert opacities == [0, .56, .72]
    first = min(el['y'] for el in block['elements'] if el['type'] == 'text' and (el.get('copyRole') == 'lead' or el.get('copyRole', '').startswith('items.')) and not el.get('hidden'))
    assert first >= scrim['y']
    # Worst-case sRGB white photo under black scrim; all later positions are darker.
    channel = 1 - opacities[1]
    luminance = channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
    assert 1.05 / (luminance + .05) >= 4.5


def test_hidden_title_measurement_does_not_inflate_short_body_block():
    value = intro_input(seller='', body='짧아요')
    block = assemble_input(value)[0]
    body = next(el for el in block['elements'] if el.get('copyRole') == 'body')
    panel = next(el for el in block['elements'] if el['type'] == 'shape' and el.get('groupId') == body['groupId'])
    assert block['h'] == panel['y'] + panel['h'] + 140
