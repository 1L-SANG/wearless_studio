"""B3 regression evidence and synthetic boundaries; no real provider calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agents import page_copy as pc
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, ANALYSIS, board, response_for, slot

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')
FIXTURE = json.loads((Path(__file__).parent/'fixtures/page_copy_b3.json').read_text())


def validate_effect(text, facts):
    s = slot('feature', 'desc', target='feature')
    return pc.validate(response_for([s], [text]), [s], facts, {'slots': {}})


@pytest.mark.parametrize('text', ['날씬해 보여요', '허리가 잡혀 보여요', '허리가 가늘어 보여요',
    '날씬해 보이고 단정해요', '날씬해 보이는 옷이에요'])
@pytest.mark.parametrize('facts,source', [({'name': '허리 절개 원피스'}, 'name'),
    ({'parts': [{'name': '허리 밴딩', 'evidence': '주름진 밴드'}]}, 'parts.0.name'),
    ({'parts': [{'name': '앞쪽 마감', 'evidence': '허리끈이 있음'}]}, 'parts.0.evidence'),
    ({'sellingPoints': ['벨트']}, 'sellingPoints.0'),
    ({'sellerComment': '허리에 스트링이 있어요'}, 'sellerComment')])
def test_body_effect_requires_appearance_and_records_evidence_source(text, facts, source):
    result = validate_effect(text, facts)
    assert result['accepted']
    evidence = [w for w in result['warnings'] if w['reason'] == 'body_effect_evidence']
    assert evidence and any(e['source'] == source for w in evidence for e in w['evidence'])


@pytest.mark.parametrize('facts', [{}, {'sellingPoints': ['와이드']}, {'name': '후드 스트링'},
    {'sellingPoints': ['밑단 스트링']}, {'sellerComment': '허리 밴딩이 없어요'},
    {'parts': [{'name': '허리 밴딩', 'evidence': '밴딩 없이 지퍼로 여며요'}]},
    {'sellingPoints': ['벨트 없음']}, {'name': '하이웨이스트가 아닌 바지'}])
def test_unrelated_or_negated_evidence_does_not_allow_body_effect(facts):
    assert 'body_effect' in validate_effect('날씬해 보여요', facts)['rejected']['S1:desc']


@pytest.mark.parametrize('facts,allowed', [({'name': '하이웨이스트 팬츠'}, True),
    ({'sellingPoints': ['부츠컷']}, True), ({'axes': {'length': '롱'}}, False),
    ({'axes': {'length': '발목 덮음'}}, True), ({'axes': {'length': '복숭아뼈 덮음'}}, True),
    ({'sellingPoints': ['와이드'], 'axes': {'length': '발목 아래'}}, True),
    ({'sellingPoints': ['와이드'], 'axes': {'length': '발목 위'}}, False),
    ({'name': '롱이 아닌 와이드 팬츠'}, False), ({'name': '와이드 팬츠'}, False)])
def test_leg_effect_observes_long_wide_guard(facts, allowed):
    result = validate_effect('다리가 길어 보여요', facts)
    assert bool(result['accepted']) is allowed
    if allowed:
        assert any(w['reason'] == 'body_effect_evidence' for w in result['warnings'])


@pytest.mark.parametrize('text', ['날씬해요', '허리가 잡혀요', '허리가 가늘어요', '다리가 길어요',
    '-5kg 날씬해 보여요', '5cm 다리가 길어 보여요', '완벽하게 날씬해 보여요',
    '확실히 날씬해 보여요', '감쪽같이 날씬해 보여요', '커버되어 날씬해 보여요',
    '군살 없이 날씬해 보여요', '뱃살', '교정', '보정', '다이어트',
    '날씬해 보여요. 다리가 길어요'])
def test_assertions_numbers_absolutes_and_hard_bans_require_seller_claim_support(text):
    s = slot('intro', 'body', target='section')
    facts = {'sellerComment': '허리 밴딩과 하이웨이스트가 있어요', 'sellingPoints': ['허리 밴딩', '하이웨이스트']}
    assert 'body_effect' in pc._slot_reasons(text, s, facts, {})


def test_real_r5_and_benchmark_text_are_preserved_without_inventing_source_claims():
    r5 = next(r for r in FIXTURE['r5'] if r['line'] == 977)
    assert 'body_effect' not in pc._slot_reasons(r5['text'], slot(), {}, {})
    row = next(r for r in FIXTURE['comparison'] if r['item']['id'].startswith('20901f58'))
    text = next(text for text in row['item']['reference'] if '다리가 길어' in text)
    s = slot('intro', 'body', target='section')
    facts = {'sellingPoints': ['와이드']}
    assert 'body_effect' in pc._slot_reasons(text, s, facts, {})
    assert 'body_effect' not in pc._slot_reasons(text, s, {**facts, 'axes': {'length': '발목 아래'}}, {})


def faq_slots(index=0):
    return [slot('faq', f'items.{index}.{ending}', target='section') for ending in ('q', 'a')]


@pytest.mark.parametrize('texts', [['', '실측표를 확인해 주세요'], ['사이즈는 어떻게 고르나요?', ''],
    ['어떤 색상인가요?', '실측표를 확인해 주세요']])
def test_faq_validation_rejects_both_halves_when_partner_is_missing_or_invalid(texts):
    slots = faq_slots()
    result = pc.validate(response_for(slots, texts), slots, {}, {'slots': {}})
    assert not result['accepted']
    assert all('faq_pair_incomplete' in result['rejected'][s['id']] for s in slots)


@pytest.mark.parametrize('reverse', [False, True])
def test_valid_faq_pair_is_committed_atomically_regardless_of_slot_order(reverse):
    slots = faq_slots()
    if reverse: slots.reverse()
    async def fake(req, timeout):
        return {'response': response_for(req['slots'], [
            '사이즈는 어떻게 고르나요?' if s['role'].endswith('.q') else '실측표를 확인해 주세요' for s in req['slots']])}
    result = asyncio.run(pc.generate_slots(make_settings(), slots, PRODUCT, {}, seed=1,
        overrides={'caller': fake, 'rewrite_slots': ['faq']}))
    assert len(result['log']['calls']) == 1
    assert {t['role'] for t in result['copyResults'][0]['texts']} == {'items.0.q', 'items.0.a'}


@pytest.mark.parametrize('second_fails', [False, True])
def test_one_faq_failure_rewrites_both_once_and_never_keeps_an_orphan(second_fails):
    slots = faq_slots(1); requests = []
    async def fake(req, timeout):
        requests.append(req)
        return {'response': response_for(req['slots'], [
            ('어떤 색상인가요?' if len(requests) == 1 or second_fails else '사이즈는 어떻게 고르나요?')
            if s['role'].endswith('.q') else '실측표를 확인해 주세요' for s in req['slots']])}
    result = asyncio.run(pc.generate_slots(make_settings(), slots, PRODUCT, {}, seed=1,
        overrides={'caller': fake, 'rewrite_slots': ['faq.a']}))
    assert [r['stage'] for r in requests] == ['single', 'rewrite']
    assert {s['id'] for s in requests[1]['slots']} == {'S1:items.1.q', 'S1:items.1.a'}
    assert result['copyResults'][0]['texts'] == ([] if second_fails else [
        {'role': 'items.0.q', 'text': '사이즈는 어떻게 고르나요?', 'draft': False},
        {'role': 'items.0.a', 'text': '실측표를 확인해 주세요', 'draft': False}])
    assert {r['slotId'] for r in result['log']['slots']} == {'S1:items.1.q', 'S1:items.1.a'}


def test_compaction_groups_reviews_and_faq_and_preserves_metadata_and_input():
    rows = [{'role': 'title', 'text': '후기'}, {'role': 'items.0.who', 'text': '고아'},
        {'role': 'items.2.who', 'text': '민지'}, {'role': 'items.2.text', 'text': '좋아요', 'draft': True},
        {'role': 'items.5.text', 'text': '편해요'}, {'role': 'items.7.text', 'text': '  '}]
    original = deepcopy(rows)
    assert pc.compact_section_texts(rows, kind='review') == [rows[0],
        {'role': 'items.0.who', 'text': '민지'}, {'role': 'items.0.text', 'text': '좋아요', 'draft': True},
        {'role': 'items.1.text', 'text': '편해요'}]
    assert rows == original
    faq = [{'role': 'items.1.a', 'text': '고아'}, {'role': 'items.3.q', 'text': '질문?'},
        {'role': 'items.3.a', 'text': '답변요'}]
    assert pc.compact_section_texts(faq, kind='faq') == [
        {'role': 'items.0.q', 'text': '질문?'}, {'role': 'items.0.a', 'text': '답변요'}]
    assert pc.compact_section_texts(faq, kind='points') == faq


def configure_required(directory, required, rewrite):
    path = directory/'settings_v2.json'
    config = json.loads(path.read_text())
    config.update(required_slots=required, rewrite_slots=rewrite)
    path.write_text(json.dumps(config))


def test_operational_generate_reads_top_level_required_settings_without_overrides(page_copy_prompt_dir, monkeypatch):
    configure_required(page_copy_prompt_dir, ['intro.title'], [])
    requests = []
    async def fake(settings, request, timeout, metadata):
        requests.append(request)
        return {'response': {} if request['stage'] == 'single' else response_for(request['slots'], ['넓은 목선'])}
    monkeypatch.setattr(pc, '_call', fake)
    result = asyncio.run(pc.generate(make_settings(gemini_api_key='fixture-key'), board(), PRODUCT, ANALYSIS,
        seed=1, timeout_seconds=2))
    assert [r['stage'] for r in requests] == ['single', 'rewrite']
    assert {s['kind']+'.'+s['role'] for s in requests[1]['slots']} == {'intro.title'}
    assert any(t['text'] == '넓은 목선' for row in result['copyResults'] for t in row['texts'])


def test_default_required_rewrite_obeys_same_total_deadline(page_copy_prompt_dir, monkeypatch):
    configure_required(page_copy_prompt_dir, ['intro.title'], [])
    requests = []; clock = [100.0]
    monkeypatch.setattr(pc.time, 'monotonic', lambda: clock[0])
    async def fake(settings, request, timeout, metadata):
        requests.append(request); clock[0] += 3
        return {'response': {}}
    monkeypatch.setattr(pc, '_call', fake)
    result = asyncio.run(pc.generate(make_settings(gemini_api_key='fixture-key'), board(), PRODUCT, ANALYSIS,
        seed=1, timeout_seconds=2))
    assert len(requests) == 1
    assert {'reason': 'deadline', 'stage': 'rewrite'} in result['log']['warnings']


def test_benchmark_same_ledger_key_collision_prevents_actual_rewrite_send(page_copy_prompt_dir):
    configure_required(page_copy_prompt_dir, ['intro.title'], [])
    saved = {}; actual_sends = []; ledger_attempts = []
    class Stop(RuntimeError): pass
    async def benchmark_caller(request, timeout):
        # Synthetic reproduction of run_bench.py:72-75 / ledger.py:77-82.
        key = 'case/emo/argon'; ledger_attempts.append(request['stage'])
        fingerprint = json.dumps(request, ensure_ascii=False, sort_keys=True)
        if key in saved:
            if saved[key] != fingerprint: raise Stop('이어 할 요청이 저장된 요청과 다릅니다.')
            return {'response': {}}
        saved[key] = fingerprint; actual_sends.append(request['stage'])
        return {'response': {}}
    result = asyncio.run(pc.generate(make_settings(), board(), PRODUCT, ANALYSIS, seed=1,
        overrides={'caller': benchmark_caller}))
    assert ledger_attempts == ['single', 'rewrite'] and actual_sends == ['single']
    assert [r['status'] for r in result['log']['calls']] == ['ok', 'error']
    assert result['log']['calls'][1]['error'] == 'Stop'
    assert any('provider_error' in r['reasons'] for r in result['log']['slots'])


def test_completed_empty_section_survives_output_conversion():
    s = slot('for_you', 'items.0.text', target='section')
    async def fake(req, timeout): return {'response': {}}
    result = asyncio.run(pc.generate_slots(make_settings(), [s], PRODUCT, {}, seed=1,
        overrides={'caller': fake}))
    assert result['copyResults'] == [{'blockId': 'block', 'texts': []}]
    assert pc.copy_results_from_result(result) == [{'blockId': 'block', 'texts': []}]


def test_bare_recommendation_roles_compact_from_first_surviving_item():
    texts = [{'role': 'items.0', 'text': ''}, {'role': 'items.2', 'text': '단추를 좋아하는 분'},
             {'role': 'items.4', 'text': '넓은 목선을 찾는 분'}]
    assert pc.compact_section_texts(texts, kind='for_you') == [
        {'role': 'items.0', 'text': '단추를 좋아하는 분'}, {'role': 'items.1', 'text': '넓은 목선을 찾는 분'}]


@pytest.mark.parametrize('seller', ['군살을 감쪽같이 보정해요', '허리 밴딩 없이 날씬해 보여요'])
def test_seller_fallback_keeps_seller_body_claim_under_b4_owner_policy(seller):
    async def fake(req, timeout): return {'response': {}}
    result = asyncio.run(pc.generate_slots(make_settings(), [slot('intro', 'body', target='section')],
        PRODUCT, {'mdComment': seller}, seed=1, overrides={'caller': fake}))
    assert result['copyResults'] == [{'blockId': 'block', 'texts': [{'role': 'body', 'text': seller, 'draft': False}]}]
    assert result['log']['slots'][0]['source'] == 'seller_original'


def test_real_benchmark_orphan_answer_is_dropped_with_original_role_in_audit():
    row = next(r for r in FIXTURE['comparison'] if r['item']['id'].startswith('20901f58'))
    orphan = next(r for r in row['item']['B'] if r['role'] == 'items.1.a')
    slots = faq_slots(1)
    facts = pc.facts_bundle({'name': row['item']['facts']['seller']['name']}, {}, slots)
    result = pc.validate({'sections': [{'key': 'S1', 'texts': [orphan]}]}, slots, facts, {'slots': {}})
    assert result['accepted'] == {}
    assert 'faq_pair_incomplete' in result['rejected']['S1:items.1.a']
    assert next(r for r in result['rows'] if r['slotId'] == 'S1:items.1.a')['text'] == orphan['text']


def test_points_only_generation_emits_completion_marker_without_changing_point_identity():
    s = slot()
    async def fake(req, timeout):
        return {'response': {'partDescriptionCopy': [{'point': '오픈 카라', 'desc': '깃을 넓게 펼쳐 입어요'}]}}
    result = asyncio.run(pc.generate_slots(make_settings(), [s], PRODUCT, ANALYSIS, seed=1,
        overrides={'caller': fake}))
    assert result['copyResults'] == [{'blockId': 'block', 'texts': []}]
    assert result['partDescriptionCopy'] == [{'point': '오픈 카라', 'desc': '깃을 넓게 펼쳐 입어요'}]


def test_fact_fallback_keeps_explicit_seller_feature_claim_under_b4_policy():
    async def fake(req, timeout): return {'response': {}}
    result = asyncio.run(pc.generate_slots(make_settings(), [slot('intro', 'body', target='section')],
        PRODUCT, {'sellingPoints': ['군살 보정']}, seed=1, overrides={'caller': fake}))
    assert result['copyResults'][0]['texts'][0]['text'] == '군살 보정이 포인트인 상의예요.'
    assert result['log']['slots'][0]['source'] == 'fact_fallback'


def test_fit_profile_length_source_is_available_to_leg_effect_check():
    facts = pc.facts_bundle({'name': '와이드 팬츠', 'clothingType': 'bottom'},
        {'fitProfile': {'category': 'pants', 'gender': 'women', 'axes': {'length': 'below_ankle'},
                        'axisSources': {'length': 'seller'}}}, [])
    result = validate_effect('다리가 길어 보여요', facts)
    assert result['accepted']
    assert any(e['source'] == 'axes.length' for w in result['warnings'] if w['reason'] == 'body_effect_evidence' for e in w['evidence'])


def test_allowed_seller_fallback_keeps_body_effect_evidence_warning():
    async def fake(req, timeout): return {'response': {}}
    result = asyncio.run(pc.generate_slots(make_settings(), [slot('intro', 'body', target='section')],
        PRODUCT, {'mdComment': '벨트가 있어서 날씬해 보여요'}, seed=1, overrides={'caller': fake}))
    assert result['copyResults'][0]['texts'][0]['text'] == '벨트가 있어서 날씬해 보여요'
    assert any(w['reason'] == 'body_effect_evidence' for w in result['log']['warnings'])

@pytest.mark.parametrize('facts', [
    {'sellerComment': '허리는 일자이고 밑단 스트링으로 조절해요'},
    {'name': '벨트 미포함 원피스'},
    {'parts': [{'name': '허리 밴딩', 'evidence': '허리에는 밴딩이 들어가지 않아요'}]},
])
def test_review_negated_or_nonwaist_structure_is_not_evidence(facts):
    # Explicit synthetic boundary cases from independent review, not source quotes.
    from app.agents.page_copy_body_effects import check_body_effect
    assert check_body_effect('날씬해 보여요', facts)[0]


@pytest.mark.parametrize('structure,allowed', [('허리 절개', True), ('허리 밴딩', True),
    ('소매 밴딩', False), ('벨트 고리', False)])
def test_owner_defined_waist_and_slim_phrase_uses_current_rules(structure, allowed):
    result = validate_effect('허리가 잡혀 날씬해 보여요', {'sellingPoints': [structure]})
    assert bool(result['accepted']) is allowed


@pytest.mark.parametrize('text', ['5kg 빠져 보여요', '살 빠져 보여요'])
def test_owner_weight_loss_phrases_are_forbidden(text):
    assert not validate_effect(text, {'sellingPoints': ['허리 절개', '하이웨이스트']})['accepted']


@pytest.mark.parametrize('facts,allowed', [({}, False), ({'sellingPoints': ['하이웨이스트']}, True),
    ({'sellingPoints': ['부츠컷']}, True), ({'sellingPoints': ['긴 기장']}, True),
    ({'axes': {'length': '롱'}}, False)])
def test_owner_taller_appearance_needs_supported_structure(facts, allowed):
    assert bool(validate_effect('키가 커 보여요', facts)['accepted']) is allowed
