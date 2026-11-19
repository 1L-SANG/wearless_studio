"""A3 regression and staged generation contracts, using local fake completions."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from page_copy_test_support import page_copy_prompt_dir, write_page_copy_prompts, FIXTURE_ARGON_THINKING

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")

from app.agents import page_copy as pc
from conftest import make_settings
from test_page_copy import slot, response_for, PRODUCT, ANALYSIS


@pytest.mark.parametrize('text', ['허리 아래로 깊게 잡은 주름 두 줄', '깊게 접힌 두 줄의 주름'])
def test_numbered_show_part_uses_original_evidence(text):
    s = dict(slot(template='show'), point='1. 앞 핀턱')
    analysis = {'detailRecommendations': {'candidates': [
        {'label': '앞 핀턱', 'reason': '허리 아래로 깊게 잡은 주름 두 줄'}]}}
    facts = pc.facts_bundle({'clothingType': 'bottom'}, analysis, [s])
    checked = pc.validate(response_for([s], [text]), [s], facts, {'slots': {}})
    assert checked['accepted'] == {s['id']: text}


def test_r1_all_numbered_show_parts_recover_evidence():
    samples = json.loads((Path(__file__).parent / 'fixtures/page_copy_r1_parts.json').read_text())
    for item in samples['samples']:
        case = samples['cases'][item['case']]
        i = int(item['role'].split('.')[1])
        point = case['analysis']['detailRecommendations']['candidates'][i]['label']
        s = dict(slot(template='show'), id=item['slotId'], point=f'{i+1}. {point}')
        facts = pc.facts_bundle(case['product'], case['analysis'], [s])
        result = pc.validate(response_for([s], [item['text']]), [s], facts, {'slots': {}})
        assert 'missing_part_evidence' not in result['rejected'].get(s['id'], []), item


@pytest.mark.parametrize('source,expected', [('seller','발목'),('measure','발목'),('ai','발목'),('unsure',None),(None,None)])
def test_bottom_length_sources_and_cut_words(source, expected):
    analysis = {'fit': 'over', 'fitProfile': {'category': 'pants', 'gender': 'women',
        'axes': {'length': 'ankle'}, 'axisSources': {'length': source}},
        'mdComment': '일자로 떨어지는 스트레이트 통이에요'}
    facts = pc.facts_bundle({'name': '와이드 슬랙스', 'clothingType': 'bottom'}, analysis, [])
    assert not facts.get('fit') and not facts['faqEvidence'].get('fit')
    assert facts.get('axes', {}).get('length') == expected
    assert set(facts['bottomCut']) >= {'와이드', '스트레이트'}


@pytest.mark.parametrize('text', ['오버핏으로 입어요','품이 넉넉해요','품이 여유 있어요'])
def test_bottom_rejects_top_fit_language_even_if_seller_used_it(text):
    s = slot('intro','body',target='section')
    facts = pc.facts_bundle({'clothingType': 'bottom'}, {'mdComment': text}, [s])
    assert 'bottom_top_fit' in pc.validate(response_for([s],[text]),[s],facts,{'slots':{}})['rejected'][s['id']]


def test_facts_do_not_feed_allowed_feel_words():
    facts = pc.facts_bundle(PRODUCT, ANALYSIS, [])
    assert 'allowed' not in facts.get('fabricFeel', {})
    assert 'forbidden' in facts['fabricFeel']


def test_plans_only_assign_distinct_topics_and_lengths():
    slots = [slot('review',f'items.{i}.text',target='section') for i in range(3)]
    slots += [dict(slot('for_you',f'items.{i}',target='section'),id=f'R{i}') for i in range(4)]
    slots += [slot(), dict(slot(target='feature'),id='F0')]
    plan = pc.plans(2,slots,pc.facts_bundle(PRODUCT,ANALYSIS,slots))
    reviews = [plan['slots'][s['id']] for s in slots if s['kind']=='review']
    assert len({r['topic'] for r in reviews}) == 3
    assert all(set(r)=={'topic','length'} for r in reviews)
    assert all(r['topic']!='다시 사고 싶은 마음' for r in reviews)
    assert all(not {'angle','axis','ending','part','pairing','occasion'} & set(p) for p in plan['slots'].values())


def run(slots, fake, analysis=None, **overrides):
    return asyncio.run(pc.generate_slots(make_settings(),slots,PRODUCT,ANALYSIS if analysis is None else analysis,
        seed=5,timeout_seconds=2,overrides={'caller':fake,**overrides}))


def test_missing_and_empty_slots_never_rewrite_but_invalid_body_does():
    slots = [slot('intro','body',target='section'),dict(slot('faq','items.0.a',target='section'),id='Q'),
             dict(slot('fabric','body',target='section'),id='B',key='S2')]
    calls=[]
    async def fake(req,timeout):
        calls.append(req)
        return {'response':response_for(req['slots'],['할인 중이에요','',''])}
    out=run(slots,fake)
    assert len(calls)==2
    assert [s['id'] for s in calls[1]['slots']]==[slots[0]['id']]
    assert calls[1]['stage']=='rewrite'
    assert 'accepted' not in calls[1]['prompt'].split('CORRECTION CONTEXT (data):')[-1]
    assert out['log']['slots'][1]['source']=='ai'


def test_empty_response_does_not_retry_and_rewrite_configuration_is_optional():
    calls=[]
    async def fake(req, timeout):calls.append(req);return {'response':{}}
    run([slot()],fake)
    assert len(calls)==1
    calls.clear()
    async def bad(req,timeout):calls.append(req);return {'response':response_for(req['slots'],['최고예요'])}
    run([slot()],bad)
    assert len(calls)==1
    calls.clear()
    run([slot()],bad,rewrite_slots=['part.desc'])
    assert len(calls)==2


@pytest.mark.parametrize('seller,source,kept', [
    ('카라가 넓어요.','seller_original','카라가 넓어요.'),
    ('이번 주만 1+1 특가!! 카라가 넓어요. 인스타 팔로우하면 쿠폰 드려요','seller_cleaned','카라가 넓어요.'),
])
def test_seller_fallback_is_cleaned_and_attributed(seller,source,kept):
    async def fake(req,timeout):return {'response':{}}
    out=run([slot('intro','body',target='section')],fake,analysis={'mdComment':seller})
    assert out['copyResults'][0]['texts'][0]['text']==kept
    assert out['log']['slots'][0]['source']==source


def test_seller_with_no_remaining_sentences_uses_empty_seller_draft_path():
    calls=[]
    async def fake(req,timeout):
        calls.append(req)
        assert '1+1' not in req['prompt']
        assert req['slots'][0]['draft'] is True
        return {'response':response_for(req['slots'],['카라가 넓게 열려 있어요\n청바지에 입기 좋아요'])}
    out=run([slot('intro','body',target='section')],fake,analysis={'mdComment':'이번 주만 1+1 특가!! 인스타 팔로우하면 쿠폰 드려요'})
    assert out['log']['slots'][0]['source']=='ai'
    assert out['copyResults'][0]['texts'][0]['draft'] is True








@pytest.mark.parametrize('text', ['세 개의 금속 단추','길이 조절이 가능한 스트랩','지퍼로 여닫는 포켓'])
def test_show_part_rejects_new_facts_without_requiring_evidence_words(text):
    s=dict(slot(template='show'),point='1. 앞 핀턱')
    facts=pc.facts_bundle({}, {'detailRecommendations':{'candidates':[
        {'label':'앞 핀턱','reason':'허리 아래로 깊게 잡은 주름 두 줄'}]}}, [s])
    result=pc.validate(response_for([s],[text]),[s],facts,{'slots':{}})
    assert 'unsupported_part_fact' in result['rejected'][s['id']]


def v2_prompts(tmp_path,monkeypatch):
    directory=write_page_copy_prompts(tmp_path/'prompts')
    monkeypatch.setattr(pc,'PROMPT_DIR',directory)
    envelope='임시. 카피 총괄이 다시 쓴다\n{{rules}}\n---USER---\n'+json.dumps({
        'facts':'FACTS','plans':'PLANS','examples':'EXAMPLES','slots':'SLOTS','exemplars':'EXEMPLARS'},ensure_ascii=False)
    for key in ('facts','plans','examples','slots','exemplars'):
        envelope=envelope.replace('"'+key.upper()+'"','{{'+key+'}}')
    (directory/'single_gpt_v2.md').write_text(envelope)
    examples=[{'slot':'intro.title','template':'all','garment':garment,
               'text':f'{garment} 예시 {i} 전용 문장','origin':'owner4'}
              for garment in ('top','bottom','dress','outer') for i in range(6)]
    (directory/'exemplars_v2.json').write_text(json.dumps({'exemplars':examples},ensure_ascii=False))
    return directory


def test_model_template_precedes_provider_and_exemplars_are_seeded(tmp_path,monkeypatch):
    directory=v2_prompts(tmp_path,monkeypatch)
    specific=directory/'single_gemini-4-argon_v2.md'
    specific.write_text((directory/'single_gpt_v2.md').read_text().replace('임시.','MODEL 임시.',1))
    s=slot('intro','title',target='section');facts=pc.facts_bundle(PRODUCT,ANALYSIS,[s]);plan=pc.plans(1,[s],facts)
    a=pc.build_request([s],facts,plan,'gemini','gemini-4-argon',prompt_version='v2')
    b=pc.build_request([s],facts,plan,'gemini','gemini-4-argon',prompt_version='v2')
    assert a['system_prompt'].startswith('MODEL') and a['exemplars']==b['exemplars']
    assert 2<=len(a['exemplars'])<=3
    assert all(e['garment']!='top' for e in a['exemplars'])
    assert a['settings']['thinking_config']==FIXTURE_ARGON_THINKING




def test_eight_character_exemplar_copy_rejected_in_live_validation():
    s=slot('intro','title',target='section')
    text='매일 손이 가는 편안한 옷'
    result=pc.validate(response_for([s],[text]),[s],{}, {'slots':{}},
                       exemplars=[{'text':'매일손이가는편안한옷으로 다른 분위기'}])
    assert 'exemplar_copy' in result['rejected'][s['id']]


def test_fabric_title_restatement_and_faq_overlap_are_integrated():
    slots=[dict(slot('fabric','title',target='section'),sectionKey='fabric'),
           dict(slot('fabric','body',target='section'),sectionKey='fabric')]
    result=pc.validate(response_for(slots,['부드러운 골지','골지가 부드러워요']),slots,{}, {'slots':{}})
    assert 'fabric_title_repeated' in result['rejected'][slots[1]['id']]
    slots=[slot('faq',f'items.{i}.{kind}',target='section') for i in range(2) for kind in ('q','a')]
    facts=pc.facts_bundle(PRODUCT,ANALYSIS,slots)
    facts['faqEvidence']['care'] = ['찬물로 세탁하고 세탁망에 넣어 주세요.']
    result=pc.validate(response_for(slots,['어떻게 세탁하나요?','찬물로 세탁해 주세요','손빨래해도 되나요?','세탁망에 넣어 주세요']),slots,facts,{'slots':{}})
    assert 'faq_overlap' in result['rejected'][slots[2]['id']]


def test_page_repetitions_only_log_and_never_reject():
    slots=[dict(slot(target='feature',template='show'),id=f'F{i}',point=f'특징 {i}',sectionKey='features') for i in range(4)]
    texts=['단정한 카라','단정한 주름','단정한 소매','단정한 마감']
    facts=pc.facts_bundle(PRODUCT,ANALYSIS,slots)
    result=pc.validate(response_for(slots,texts),slots,facts,{'slots':{}})
    assert len(result['accepted'])==4
    assert any(w['reason']=='page_content_repetition' for w in result['warnings'])


def test_review_endings_may_repeat_when_one_is_non_yo():
    slots=[slot('review',f'items.{i}.text',target='section') for i in range(3)]
    texts=['색이 예뻐요','핏이 넉넉해서 마음에 들어요','단추가 작게 들어가서 취향에 꼭 맞는 디자인']
    result=pc.validate(response_for(slots,texts),slots,{}, {'slots':{s['id']:{'ending':'noun'} for s in slots}})
    assert len(result['accepted'])==3




def test_removed_designer_instruction_cannot_supply_bottom_cut():
    facts=pc.facts_bundle({'name':'팬츠','clothingType':'bottom'},
                         {'mdComment':'상세페이지에는 와이드핏으로 강조해 주세요.'},[])
    assert facts['sellerComment']=='' and not facts.get('bottomCut')


@pytest.mark.parametrize('policy',['주문 취소는 오후 3시까지 가능합니다.','결제 후 취소가 불가합니다.','입금 기한은 오늘까지입니다.'])
def test_seller_policy_sentences_never_return_through_fallback(policy):
    async def fake(req,timeout):return {'response':{}}
    out=run([slot('intro','body',target='section')],fake,analysis={'mdComment':policy})
    assert out['copyResults']==[{'blockId': 'block', 'texts': []}]
    assert out['log']['slots'][0]['source']=='ai'


def test_feature_audit_excludes_editor_discarded_duplicate():
    from app.agents import feature_copy
    audit={}
    async def fake(req,timeout):return {'response':{'featureCopy':[{'point':'카라','desc':'카라의 마감 디테일을 확인할 수 있어요'}]}}
    out=asyncio.run(feature_copy.generate(make_settings(),['카라'],{},
         {'partDescriptionCopy':[{'point':'카라','desc':'카라의 마감 디테일을 확인할 수 있어요.'}]},
         overrides={'caller':fake},audit=audit))
    assert out==[]
    assert not any(r.get('passed') and r.get('source')=='ai' for r in audit['log']['slots'])




def test_sparse_temporary_exemplars_are_explicitly_reported(tmp_path,monkeypatch):
    directory=v2_prompts(tmp_path,monkeypatch)
    (directory/'exemplars_v2.json').write_text(json.dumps({'exemplars':[]}))
    s=slot('intro','title',target='section')
    req=pc.build_request([s],pc.facts_bundle(PRODUCT,ANALYSIS,[s]),pc.plans(0,[s],{}),'gpt','gpt-6-luna',prompt_version='v2')
    assert req['warnings']==[{'reason':'insufficient_exemplars','slotKind':'intro.title','count':0,'stage':'single'}]


@pytest.mark.parametrize('evidence,text',[
    ('단추 두 개로 여미는 소맷단','단추 2개로 여미는 마감'),
    ('앞판 가운데 세로로 잡은 가는 주름 여섯 줄','앞판 가운데 6줄로 잡은 가는 주름'),
])
def test_r1_native_counts_can_be_written_as_digits(evidence,text):
    s=dict(slot(template='show'),point='1. 마감')
    facts=pc.facts_bundle({}, {'detailRecommendations':{'candidates':[{'label':'마감','reason':evidence}]}},[s])
    result=pc.validate(response_for([s],[text]),[s],facts,{'slots':{}})
    assert 'unknown_number' not in result['rejected'].get(s['id'],[])
    assert 'unsupported_part_fact' not in result['rejected'].get(s['id'],[])
