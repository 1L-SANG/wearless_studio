"""B3 layout behavior, shared real-copy examples and both-engine fixture parity."""
import pytest
from page_template_cases import assemble_input, case_input, elements
from test_page_template_copy_v2 import intro_input


def test_hidden_intro_identity_survives_without_changing_empty_layout():
    block = assemble_input(intro_input(seller='', body=''))[0]
    hidden = [el for el in block['elements'] if el.get('hidden')]
    assert len(hidden) == 3
    assert next(el for el in hidden if el.get('copyRole') == 'who')['text'] == 'MD'
    assert len({el['groupId'] for el in hidden}) == 1
    assert block['h'] == 422


@pytest.mark.parametrize('template', ['emo', 'show'])
def test_missing_first_detail_preserves_all_photo_identities(template):
    value = case_input(f'{template}-2-mono')
    value['storyboard'] = [b for b in value['storyboard'] if b['pageSectionKind'] == 'points']
    value['analysis']['partDescriptionCopy'] = [dict(point='소매 단', label='소매 끝', desc='소매 끝을 잡아주는 골지 짜임')]
    value['analysis']['featureCopy'] = []
    value['copyResults'] = [dict(blockId=value['storyboard'][0]['id'], texts=[])]
    output = elements(assemble_input(value))
    assert [el['sourceBlockId'] for el in output if el['type'] == 'image'] == [f'{template}.points.detail1', f'{template}.points.detail2']
    assert next(el for el in output if el.get('copyRole') == 'items.1.name')['text'] == '소매 끝'
    assert next(el for el in output if el.get('copyRole') == 'items.0.desc').get('hidden')


@pytest.mark.parametrize('kind', ['faq', 'review', 'for_you'])
def test_completed_empty_lists_do_not_emit_cards(kind):
    value = case_input('emo-2-mono')
    value['storyboard'] = [b for b in value['storyboard'] if b['pageSectionKind'] == kind]
    value['copyResults'] = [dict(blockId=value['storyboard'][0]['id'], texts=[])]
    assert not any(el.get('copyRole', '').startswith('items.') for el in elements(assemble_input(value)))


def test_faq_answer_only_alignment_and_pair_compaction():
    value = case_input('emo-2-mono')
    value['storyboard'] = [b for b in value['storyboard'] if b['pageSectionKind'] == 'faq']
    # Comparison 20901f58 hydrogen: keep the washing pair, drop the orphan answer.
    value['copyResults'] = [dict(blockId=value['storyboard'][0]['id'], texts=[
        dict(role='items.1.a', text='도톰한 스웻 원단이라 비침 걱정 없이 입기 좋아요'),
        dict(role='items.2.q', text='세탁은 어떻게 하면 되나요?'),
        dict(role='items.2.a', text='단독으로 찬물 세탁하시고 건조기 사용은 피해 주세요')])]
    output = elements(assemble_input(value))
    items = [el for el in output if el.get('copyRole', '').startswith('items.')]
    assert [el['copyRole'] for el in items] == ['items.0.q', 'items.0.a']
    assert [el['style']['align'] for el in items] == ['center', 'left']


def test_explicit_empty_intro_completion_restores_seller_owned_claim():
    value = intro_input(seller='군살을 완벽하게 커버해요', body='')
    value['copyResults'][0]['texts'] = []
    assert next(el for el in elements(assemble_input(value)) if el.get('copyRole') == 'body')['text'] == value['analysis']['mdComment']
