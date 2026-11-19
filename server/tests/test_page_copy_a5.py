"""R2 section aliases and bounded retries for explicitly required empty slots."""
import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agents import page_copy as pc
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, ANALYSIS, response_for, slot

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')
R2 = json.loads((Path(__file__).parent/'fixtures/page_copy_r2_section_keys.json').read_text())


@pytest.mark.parametrize('page',R2,ids=lambda row:row['case'])
def test_real_argon_kind_keys_match_the_equivalent_numbered_response(page):
    response,slots=deepcopy(page['response']),page['slots']
    canonical=deepcopy(response)
    by_kind={s['kind']:s['key'] for s in slots if s['target']=='section'}
    expected={}
    for section in canonical['sections']:
        kind=section['key'];section['key']=by_kind[kind]
        for row in section['texts']:
            target=next(s for s in slots if s['target']=='section' and s['kind']==kind and s['role']==row['role'])
            expected[target['id']]=row['text']
    extracted,duplicates=pc._response_texts(response,slots)
    assert not duplicates
    assert all(extracted.get(identity)==text for identity,text in expected.items())
    actual=pc.validate(response,slots,{}, {'slots':{}})
    baseline=pc.validate(canonical,slots,{}, {'slots':{}})
    assert actual['accepted']==baseline['accepted']
    assert actual['rejected']==baseline['rejected']
    assert any(r['passed'] for r in actual['rows'])
    warnings=[w for w in actual['warnings'] if w['reason']=='section_key_by_kind']
    assert {w['slotId'] for w in warnings}==set(expected)
    assert all('missing' not in actual['rejected'].get(identity,[]) for identity in expected)
    assert response==page['response']


def test_kind_alias_counts_sections_not_roles():
    slots=[slot('intro','title',target='section'),slot('intro','body',target='section')]
    response={'sections':[{'key':'intro','texts':[{'role':'title','text':'넓은 목선'},{'role':'body','text':'본문'}]}]}
    texts,duplicate=pc._response_texts(response,slots)
    assert texts=={slots[0]['id']:'넓은 목선',slots[1]['id']:'본문'} and not duplicate


def test_ambiguous_kind_is_not_disambiguated_using_role():
    slots=[slot('intro','title',target='section'),dict(slot('intro','body',target='section'),id='S2:body',key='S2',sectionKey='emo.intro2')]
    response={'sections':[{'key':'intro','texts':[{'role':'title','text':'넓은 목선'}]}]}
    result=pc.validate(response,slots,{}, {'slots':{}})
    assert all('missing' in result['rejected'][s['id']] for s in slots)
    assert not any(w['reason']=='section_key_by_kind' for w in result['warnings'])
    response['sections'][0]['key']='S1'
    assert pc.validate(response,slots,{}, {'slots':{}})['accepted']=={slots[0]['id']:'넓은 목선'}


@pytest.mark.parametrize('key,role',[('intro','heading'),('intros','title'),('S99','title'),('Intro','title'),(None,'title')])
def test_wrong_role_or_unknown_kind_is_not_salvaged(key,role):
    s=slot('intro','title',target='section')
    result=pc.validate({'sections':[{'key':key,'texts':[{'role':role,'text':'넓은 목선'}]}]},[s],{}, {'slots':{}})
    assert result['rejected'][s['id']]==['missing']
    assert not any(w['reason']=='section_key_by_kind' for w in result['warnings'])


def test_alias_does_not_bypass_duplicate_detection_or_part_array_contract():
    s=slot('intro','title',target='section')
    response={'sections':[{'key':key,'texts':[{'role':'title','text':'넓은 목선'}]} for key in ('S1','intro')]}
    assert 'duplicate_slot' in pc.validate(response,[s],{}, {'slots':{}})['rejected'][s['id']]
    part=slot()
    response={'sections':[{'key':'points','texts':[{'role':part['role'],'text':'깃을 펼쳐 입어요'}]}]}
    assert pc._response_texts(response,[part])[0]=={}


def generate(slots,caller,**overrides):
    return asyncio.run(pc.generate_slots(make_settings(),slots,PRODUCT,ANALYSIS,seed=1,timeout_seconds=2,
        overrides={'caller':caller,'order':['gpt'],'models':{'gpt':'test-model'},'prompt_version':'v2',**overrides}))


def feature():
    return dict(slot('feature','desc',target='feature'),id='F0',point='자수')


@pytest.mark.parametrize('empty',[None,'','  \n\t  '])
def test_required_empty_and_rejected_text_share_one_correction(empty):
    body=slot('intro','body',target='section');f=feature()
    optional=dict(slot('fabric','body',target='section'),id='S2:body',key='S2',sectionKey='emo.fabric')
    slots=[body,f,optional];calls=[]
    async def fake(req,timeout):
        calls.append(req)
        if req['stage']=='single':
            response=response_for([body],['할인 중이에요'])
            if empty is not None:response['featureCopy']=[{'point':f['point'],'desc':empty}]
            return {'response':response}
        correction=json.loads(req['prompt'].split('CORRECTION CONTEXT (data):\n')[1])['rejections']
        assert set(correction)=={body['id'],f['id']}
        assert correction[f['id']]['reasons']==['missing_required']
        assert not correction[f['id']]['text'].strip()
        assert 'promotion' in correction[body['id']]['reasons']
        return {'response':response_for(req['slots'],[
            '카라가 넓게 열려 있어요\n청바지에 맞춰 입기 좋아요' if s['kind']=='intro' else '앞쪽에 작게 자수를 더했어요' for s in req['slots']])}
    out=generate(slots,fake,required_slots=['feature.desc'])
    assert [r['stage'] for r in calls]==['single','rewrite']
    assert {s['id'] for s in calls[1]['slots']}=={body['id'],f['id']}
    assert out['featureCopy']==[{'point':'자수','desc':'앞쪽에 작게 자수를 더했어요'}]
    assert any(r['slotId']==optional['id'] and r['reasons']==['missing'] for r in out['log']['slots'])


@pytest.mark.parametrize('kind',['feature','feature.desc'])
def test_required_omission_retries_even_when_normal_rewrite_list_is_empty(kind):
    calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    out=generate([feature()],empty,required_slots=[kind],rewrite_slots=[])
    assert [r['stage'] for r in calls]==['single','rewrite']
    assert out['featureCopy']==[] and out['log']['slots'][0]['reasons']==['missing_required']


def test_optional_omission_and_nonempty_invalid_required_slot_stay_unretried():
    calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    generate([feature()],empty)
    assert len(calls)==1
    calls.clear()
    async def invalid(req,timeout):calls.append(req);return {'response':response_for(req['slots'],['**금지 문구**'])}
    generate([feature()],invalid,required_slots=['feature.desc'],rewrite_slots=[])
    assert len(calls)==1


def test_required_slots_from_settings_can_be_disabled_by_override(page_copy_prompt_dir):
    config=page_copy_prompt_dir/'settings_v2.json'
    settings=json.loads(config.read_text());settings['required_slots']=['feature.desc'];config.write_text(json.dumps(settings))
    calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    generate([feature()],empty)
    assert len(calls)==2
    calls.clear();generate([feature()],empty,required_slots=[])
    assert len(calls)==1


@pytest.mark.parametrize('value',[None,False,'feature.desc',{},[None],['']])
def test_invalid_required_slot_configuration_fails_before_call(value):
    async def fake(*a):pytest.fail('sent invalid configuration')
    with pytest.raises(ValueError,match='required_slots'):
        generate([feature()],fake,required_slots=value)


def test_required_missing_rewrite_is_one_page_call():
    slots=[dict(slot('intro','title',target='section'),group='intro'),dict(slot('fabric','title',target='section'),id='S2:title',key='S2',group='fabric',sectionKey='emo.fabric')]
    calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    generate(slots,empty,required_slots=['intro.title','fabric.title'],rewrite_slots=[])
    assert Counter((r['stage'],r['provider']) for r in calls)=={('single','gpt'):1,('rewrite','gpt'):1}


def test_alias_is_accepted_before_required_slot_selection():
    s=slot('intro','title',target='section');calls=[]
    async def fake(req,timeout):
        calls.append(req)
        return {'response':{'sections':[{'key':'intro','texts':[{'role':'title','text':'넓은 목선'}]}]}}
    out=generate([s],fake,required_slots=['intro.title'])
    assert len(calls)==1 and out['copyResults'][0]['texts'][0]['text']=='넓은 목선'
    assert any(w['reason']=='section_key_by_kind' for w in out['log']['calls'][0]['validation']['warnings'])


def test_required_rule_does_not_retry_transport_failures():
    s=slot('intro','title',target='section');calls=[]
    async def failure(req,timeout):calls.append(req);raise RuntimeError('fake outage')
    generate([s],failure,required_slots=['intro.title'])
    assert len(calls)==1


def test_required_empty_after_retry_stays_empty_even_with_seller_fallback():
    s=slot('intro','body',target='section');calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'mdComment':'카라가 넓어요.'},seed=1,timeout_seconds=2,
        overrides={'caller':empty,'order':['gpt'],'models':{'gpt':'test-model'},'required_slots':['intro.body'],'prompt_version':'v2'}))
    assert len(calls)==2 and out['copyResults']==[{'blockId': 'block', 'texts': []}]




def test_transport_failure_without_a_draft_preserves_seller_fallback():
    s=slot('intro','body',target='section');calls=[]
    async def failure(req,timeout):calls.append(req);raise RuntimeError('fake provider outage')
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'mdComment':'카라가 넓어요.'},seed=1,timeout_seconds=2,
        overrides={'caller':failure,'order':['gpt'],'models':{'gpt':'test-model'},'required_slots':['intro.body'],'prompt_version':'v2'}))
    assert len(calls)==1
    assert out['copyResults'][0]['texts'][0]['text']=='카라가 넓어요.'
    assert out['log']['slots'][0]['source']=='seller_original'


def test_empty_required_slot_stays_empty_if_its_correction_provider_fails():
    s=slot('intro','body',target='section');calls=[]
    async def fake(req,timeout):
        calls.append(req)
        if req['stage']=='rewrite':raise RuntimeError('fake correction outage')
        return {'response':{}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'mdComment':'카라가 넓어요.'},seed=1,timeout_seconds=2,
        overrides={'caller':fake,'order':['gpt'],'models':{'gpt':'test-model'},'required_slots':['intro.body'],'prompt_version':'v2'}))
    assert len(calls)==2 and out['copyResults']==[{'blockId': 'block', 'texts': []}]


def test_feature_route_does_not_fill_required_omission_with_dictionary_copy():
    from app.agents import feature_copy
    calls=[]
    async def empty(req,timeout):calls.append(req);return {'response':{}}
    options={'caller':empty,'order':['gpt'],'models':{'gpt':'test-model'},'prompt_version':'v2'}
    audit={}
    result=asyncio.run(feature_copy.generate(make_settings(),['앞 지퍼 여밈'],{}, {},audit=audit,
        overrides={**options,'required_slots':['feature.desc']}))
    assert len(calls)==2 and result==[]
    assert audit['log']['slots'][0]['reasons']==['missing_required']
    calls.clear()
    result=asyncio.run(feature_copy.generate(make_settings(),['앞 지퍼 여밈'],{}, {},overrides=options))
    assert len(calls)==1 and result


def test_required_slot_becomes_blank_when_correction_omits_rejected_text():
    s=slot('intro','body',target='section');calls=[]
    async def fake(req,timeout):
        calls.append(req)
        return {'response':response_for([s],['할인 중이에요']) if req['stage']=='single' else {}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'mdComment':'카라가 넓어요.'},seed=1,timeout_seconds=2,
        overrides={'caller':fake,'order':['gpt'],'models':{'gpt':'test-model'},'required_slots':['intro.body'],'prompt_version':'v2'}))
    assert len(calls)==2 and out['copyResults']==[{'blockId': 'block', 'texts': []}]
    assert out['log']['slots'][0]['text']==''
    assert out['log']['slots'][0]['reasons']==['missing_required']
