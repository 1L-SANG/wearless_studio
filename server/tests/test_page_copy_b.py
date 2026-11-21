"""Approved B behavior; all requests use the A5 synthetic prompt fixture."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from app.agents import page_copy as pc, feature_copy
from app.config import load_settings
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, ANALYSIS, slot, response_for

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')


def run(slots, caller, analysis=None, **options):
    return asyncio.run(pc.generate_slots(make_settings(), slots, PRODUCT,
        ANALYSIS if analysis is None else analysis, seed=1, timeout_seconds=.2,
        overrides={'caller': caller, **options}))


@pytest.mark.parametrize('failure', ['error', 'timeout', 'shape'])
def test_same_provider_fallback_then_rewrite_stays_on_successful_model(failure, caplog):
    s = slot('intro', 'title', target='section'); calls = []
    async def fake(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            if failure == 'timeout': await asyncio.sleep(1)
            if failure == 'shape': return {'response': {'sections': 'invalid'}}
            raise RuntimeError('fake failure')
        return {'response': response_for([s], ['완벽한 선택' if len(calls) == 2 else '넓은 목선']),
                'usage': {'input_tokens': 10, 'output_tokens': 5}}
    out = run([s], fake, rewrite_slots=['intro.title'])
    assert [r['model'] for r in calls] == ['gemini-4-argon', 'gemini-4-hydrogen', 'gemini-4-hydrogen']
    assert [r['stage'] for r in calls] == ['single', 'single', 'rewrite']
    assert out['model'] == 'gemini-4-hydrogen'
    assert out['copyResults'][0]['texts'][0]['text'] == '넓은 목선'
    assert out['log']['calls'][-1]['usage'] == {'input_tokens': 10, 'output_tokens': 5}
    assert all('seconds' in r and 'rewrite' in r for r in out['log']['calls'])


def test_new_route_order_and_no_provider_key_are_callable():
    settings = make_settings(page_copy_routes='gemini:second,gemini:first', gemini_api_key='')
    assert not pc.has_provider_key(settings)
    assert pc.has_provider_key(make_settings(page_copy_routes='gemini:second', gemini_api_key='fake'))
    calls = []
    async def fake(req, timeout):
        calls.append(req['model']); return {'response': {}}
    run([slot()], fake, routes='gemini:second,gemini:first')
    assert calls == ['second']


def test_routes_env_beats_legacy_and_warns(monkeypatch, caplog):
    monkeypatch.setenv('PAGE_COPY_ROUTES', 'gemini:second,gemini:first')
    monkeypatch.setenv('MODEL_ORDER_PAGE_COPY', 'gpt')
    monkeypatch.setenv('MODEL_ROUTING_TEXT_GEMINI_PAGE_COPY', 'old-model')
    settings = load_settings()
    assert settings.page_copy_routes == 'gemini:second,gemini:first'
    assert settings.page_copy_timeout_seconds == 90
    assert settings.page_copy_prompt_version == 'v2'
    assert any('deprecated' in r.message and 'PAGE_COPY_ROUTES' in r.message for r in caplog.records)


@pytest.mark.parametrize('label,keep', [('넓은 목선', True), ('가나다라마바사아자', False), ('최고의 카라', False), ('앞판', False)])
def test_labels_drop_independently_of_valid_description(label, keep):
    s = slot()
    async def fake(req, timeout):
        return {'response': {'partDescriptionCopy': [{'point': s['point'], 'desc': '깃이 넓게 펼쳐져요', 'label': label}]}}
    out = run([s], fake)
    assert out['partDescriptionCopy'][0]['desc'] == '깃이 넓게 펼쳐져요'
    assert ('label' in out['partDescriptionCopy'][0]) is keep
    schema = pc.copy_schema()['properties']['partDescriptionCopy']['items']
    assert 'label' in schema['properties'] and 'label' not in schema['required']


@pytest.mark.parametrize('template,expected', [('emo', '자수가 포인트인 상의예요.'), ('show', '자수가 포인트인 상의입니다.')])
def test_empty_md_uses_fact_sentence_after_failed_generation(template, expected):
    async def failure(req, timeout): raise RuntimeError('fake outage')
    out = run([slot('intro', 'body', template=template, target='section')], failure,
              analysis={'sellingPoints': ['자수']})
    assert out['copyResults'][0]['texts'] == [{'role': 'body', 'text': expected, 'draft': True}]
    assert pc.copy_results_from_result(out) == [{'blockId': 'block', 'texts': [{'role': 'body', 'text': expected}]}]


def test_editor_uses_feature_v1_even_with_v2_setting_and_override():
    calls = []
    async def fake(req, timeout):
        calls.append(req); return {'response': {'featureCopy': [{'point': '자수', 'desc': '앞쪽에 자수가 있어요'}]}}
    result = asyncio.run(feature_copy.generate(make_settings(page_copy_prompt_version='v2'), ['자수'], PRODUCT, {},
        overrides={'caller': fake, 'prompt_version': 'v2'}))
    assert result == [{'point': '자수', 'desc': '앞쪽에 자수가 있어요'}]
    assert calls[0]['prompt_version'] == 'v1'


@pytest.mark.parametrize('fit', ['over', 'semi_over'])
def test_size_down_advice_requires_faq_fit_chart_and_soft_recommendation(fit):
    s = slot('faq', 'items.0.a', target='section')
    text = '평소보다 한 사이즈 작게 골라도 돼요. 실측표를 확인해 주세요.'
    facts = pc.facts_bundle(PRODUCT, {'fit': fit}, [s])
    assert 'size_advice' not in pc._slot_reasons(text, s, facts, {})
    for bad in [text.replace('작게', '크게'), text.replace('실측표', '사진'), text.replace('골라도 돼요', '꼭 고르세요')]:
        assert 'size_advice' in pc._slot_reasons(bad, s, facts, {})
    assert 'size_advice' in pc._slot_reasons(text, slot('feature', 'desc', target='feature'), facts, {})
    for normal in ['regular', 'slim']:
        assert 'size_advice' in pc._slot_reasons(text, s, pc.facts_bundle(PRODUCT, {'fit': normal}, [s]), {})


@pytest.mark.parametrize('analysis', [{'fabricStructure': '기모'}, {'sellingPoints': ['퀼팅 충전재']},
    {'sellingPoints': ['패딩 충전재']}, {'materials': [{'name': '울', 'ratio': 30}, {'name': '나일론', 'ratio': 70}]}])
def test_warmth_only_with_supported_evidence(analysis):
    s = slot('feature', 'desc', target='feature')
    facts = pc.facts_bundle(PRODUCT, analysis, [s])
    assert 'unverified_claim' not in pc._slot_reasons('쌀쌀한 날에도 따뜻하게 입어요', s, facts, {})
    assert 'unverified_claim' in pc._slot_reasons('하나도 안 춥고 따뜻해요', s, facts, {})
    assert 'unverified_claim' in pc._slot_reasons('따뜻하고 방수도 돼요', s, facts, {})
    assert 'unverified_claim' in pc._slot_reasons('따뜻하게 입어요', s, {}, {})


@pytest.mark.parametrize('text', ['색이 차분함', '살에 닿는 느낌이 부드러움', '입을 때 편함'])
def test_review_noun_endings_rejected(text):
    assert 'review_noun_ending' in pc._slot_reasons(text, slot('review', 'items.0.text', target='section'), {}, {})


def test_three_polite_review_endings_do_not_need_non_yo():
    slots = [dict(slot('review', f'items.{i}.text', target='section'), point=None) for i in range(3)]
    texts = ['색이 예뻐요', '촉감이 부드러워서 손이 가요', '단추가 작게 달려 있어서 마음에 쏙 들어요']
    out = pc.validate(response_for(slots, texts), slots, {}, {'slots': {}})
    assert set(out['accepted']) == {s['id'] for s in slots}


R5 = {row['line']: row for row in json.loads((Path(__file__).parent/'fixtures/page_copy_r5_b.json').read_text())}
R2 = json.loads((Path(__file__).parent/'fixtures/page_copy_r2_b.json').read_text())


@pytest.mark.parametrize('row', R2, ids=lambda row: f"{row['source']}:{row['line']}")
def test_r2_noun_ending_reviews_are_rejected(row):
    reasons = pc._slot_reasons(row['text'], slot(row['kind'], row['role'], target='section'), {}, {})
    assert 'review_noun_ending' in reasons


def test_r5_review_size_and_warmth_examples_remain_regression_evidence():
    # Original r5 rows: polite reviews stay accepted; no fabricated noun row is
    # attributed to the source. The noun mutation below is an explicit boundary case.
    for line in (9, 10):
        row = R5[line]
        reasons = pc._slot_reasons(row['text'], slot(row['kind'], row['role'], target='section'), {}, {})
        assert 'review_noun_ending' not in reasons
    changed = R5[9]['text'].replace('자주 손이 가요.', '편함.')
    assert 'review_noun_ending' in pc._slot_reasons(changed, slot('review', 'items.0.text', target='section'), {}, {})
    for line, fit, rejects in [(23, '오버핏', False), (74, '레귤러핏', True), (103, '레귤러핏', False)]:
        row = R5[line]
        reasons = pc._slot_reasons(row['text'], slot('faq', row['role'], target='section'), {'fit': fit}, {})
        assert ('size_advice' in reasons) is rejects
    row = R5[825]
    assert 'unverified_claim' in pc._slot_reasons(row['text'], slot('intro', 'body', target='section'), {'warmthEvidence': True}, {})


def test_merge_editor_result_preserves_stored_display_labels():
    stored = [{'point': '카라', 'desc': '예전 설명', 'label': '열린 목선'}]
    assert feature_copy.merge_stored(stored, [{'point': '카라', 'desc': '새 설명'}]) == [
        {'point': '카라', 'desc': '새 설명', 'label': '열린 목선'}]


@pytest.mark.parametrize('options', [{'mode': 'draft'}, {'mode': 'draft+polish'}, {'polish': {}}, {'capture_draft': True}, {'groups': {'intro': {}}}])
def test_removed_generation_branches_rejected_before_call(options):
    async def no_call(*args): pytest.fail('removed route called a provider')
    with pytest.raises(ValueError):
        run([slot()], no_call, **options)


def test_supported_show_part_warmth_does_not_fail_new_function_check():
    s = dict(slot(template='show'), point='기모 안감')
    facts = pc.facts_bundle(PRODUCT, {'detailRecommendations': {'candidates': [
        {'label': '기모 안감', 'reason': '안쪽에 기모가 들어간 원단'}]}}, [s])
    assert not pc._slot_reasons('따뜻한 기모 안감', s, facts, {})


@pytest.mark.parametrize('analysis,supported', [({'sellingPoints': ['울 혼방']}, True),
    ({'sellingPoints': ['울 혼방 아님']}, False), ({'materials': [{'name': '울', 'ratio': 100}]}, True)])
def test_warmth_accepts_wool_facts_and_rejects_negated_blend(analysis, supported):
    facts = pc.facts_bundle(PRODUCT, analysis, [])
    assert facts.get('warmthEvidence', False) is supported


@pytest.mark.parametrize('provider,included,expected_output', [('gemini', False, 8), ('gpt', True, 5), ('claude', False, 8)])
def test_call_usage_flows_to_existing_usage_ledger_with_job_and_stage(monkeypatch, provider, included, expected_output):
    from app import image_usage
    from app.agents.image_cost import estimate_cost
    records = []; stages = []; calls = []
    original_scope = image_usage.job_scope
    @contextmanager
    def scope(**kwargs):
        stages.append(kwargs.get('stage'))
        with original_scope(**kwargs): yield
    def record(**kwargs): records.append((image_usage.current_job_id(), kwargs))
    monkeypatch.setattr(image_usage, 'job_scope', scope)
    monkeypatch.setattr(image_usage, 'record', record)
    s = slot('intro', 'title', target='section')
    async def fake(req, timeout):
        calls.append(req)
        return {'response': response_for([s], ['최고의 선택' if len(calls) == 1 else '넓은 목선']),
                'usage': {'input_tokens': 10, 'output_tokens': 5, 'reasoning_tokens': 3, 'output_includes_reasoning': included}}
    with original_scope(job_id='job-copy-fixture'):
        run([s], fake, routes=f'{provider}:fixture-text-model', rewrite_slots=['intro.title'])
    assert stages == ['page_copy', 'page_copy_rewrite']
    assert len(records) == 2
    for index, (job, record) in enumerate(records):
        assert job == 'job-copy-fixture' and record['has_image'] is False
        assert record['latency_ms'] >= 0 and record['usage']['page_copy']['rewrite'] is bool(index)
        assert record['usage']['page_copy']['status'] == 'ok'
        cost = estimate_cost(record['model'], record['image_size'], record['usage'], has_image=False)
        assert cost.input_tokens == 10 and cost.output_text_tokens == expected_output
        assert cost.output_image_tokens == 0 and cost.usd is None


def test_cancelled_provider_call_is_recorded_and_cancellation_propagates(monkeypatch):
    from app import image_usage
    records = []
    monkeypatch.setattr(image_usage, 'record', lambda **kwargs: records.append(kwargs))
    async def fake(req, timeout): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): run([slot()], fake)
    assert len(records) == 1 and records[0]['usage']['page_copy']['status'] == 'cancelled'


def test_no_keys_in_first_two_routes_does_not_open_third_route(monkeypatch):
    calls = []
    async def fake(*args): calls.append(args); return {'response': {}}
    monkeypatch.setattr(pc, '_call', fake)
    settings = make_settings(page_copy_routes='gemini:one,gemini:two,gpt:three', gemini_api_key='', openai_api_key='fake')
    assert pc.has_provider_key(settings) is False
    result = asyncio.run(pc.generate_slots(settings, [slot()], PRODUCT, ANALYSIS, seed=1))
    assert not calls and result['partDescriptionCopy'] == []


def test_editor_preserves_all_legacy_provider_fallbacks():
    calls = []
    async def fake(req, timeout):
        calls.append(req['provider'])
        if req['provider'] != 'claude': raise RuntimeError('fixture failure')
        return {'response': {'featureCopy': [{'point': '자수', 'desc': '앞쪽에 자수가 있어요'}]}}
    result = asyncio.run(feature_copy.generate(make_settings(), ['자수'], PRODUCT, {},
        overrides={'caller': fake, 'order': ['gemini', 'gpt', 'claude'], 'models': {'claude': 'fixture-claude'}}))
    assert calls == ['gemini', 'gpt', 'claude']
    assert result == [{'point': '자수', 'desc': '앞쪽에 자수가 있어요'}]


def test_warmth_can_use_cleaned_seller_fact():
    facts = pc.facts_bundle(PRODUCT, {'mdComment': '안쪽에 기모가 있어요.'}, [])
    assert facts['warmthEvidence'] is True


@pytest.mark.parametrize('text', [42, None, [], {'text': '잘못된 꼴'}])
def test_non_string_copy_values_trigger_shape_fallback(text):
    s = slot('intro', 'title', target='section'); calls = []
    async def fake(req, timeout):
        calls.append(req['model'])
        return {'response': response_for([s], [text if len(calls) == 1 else '열린 목선'])}
    out = run([s], fake)
    assert calls == ['gemini-4-argon', 'gemini-4-hydrogen']
    assert out['copyResults'][0]['texts'][0]['text'] == '열린 목선'


@pytest.mark.parametrize('malformed', [
    {'sections': [{'key': 'S1', 'texts': [{'role': 'title'}]}]},
    {'partDescriptionCopy': [{'point': '카라'}]},
    {'featureCopy': [{'point': '카라'}]},
])
def test_present_rows_require_text_or_desc_but_whole_arrays_may_be_omitted(malformed):
    s = slot('intro', 'title', target='section'); calls = []
    async def fake(req, timeout):
        calls.append(req['model'])
        return {'response': malformed if len(calls) == 1 else response_for([s], ['열린 목선'])}
    out = run([s], fake)
    assert calls == ['gemini-4-argon', 'gemini-4-hydrogen']
    assert out['copyResults'][0]['texts'][0]['text'] == '열린 목선'
    assert pc._valid_response({'sections': []}) and pc._valid_response({})


@pytest.mark.parametrize('fact', ['무기모', '기모를 넣지 않았어요', '패딩 충전재가 들어 있지 않아요',
    '기모가 없어요', '기모 아님', '울 혼방이 아니에요', '퀼팅 충전재 미포함'])
def test_explicit_absence_does_not_authorize_warmth(fact):
    facts = pc.facts_bundle(PRODUCT, {'mdComment': fact}, [])
    assert facts['warmthEvidence'] is False


@pytest.mark.parametrize('text', [
    '두 사이즈 작게 골라도 돼요. 실측표에서 상세 치수를 확인해 주세요.',
    '평소보다 작은 사이즈로 골라도 돼요. 실측표를 확인해 주세요.',
    '큰 사이즈로 골라도 돼요. 실측표를 확인해 주세요.',
    '2사이즈 작게 골라도 돼요. 실측표를 확인해 주세요.',
])
@pytest.mark.parametrize('fit', ['regular', 'over'])
def test_size_recommendation_variants_do_not_escape_the_one_size_exception(text, fit):
    s = slot('faq', 'items.0.a', target='section')
    facts = pc.facts_bundle(PRODUCT, {'fit': fit}, [s])
    assert 'size_advice' in pc._slot_reasons(text, s, facts, {})
