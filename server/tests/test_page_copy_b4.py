"""Owner B4 seller evidence and fallback contracts; every provider is synthetic."""
import asyncio
import json
from pathlib import Path
import re

import pytest

from app.agents import page_copy as pc, page_copy_checks as checks
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, ANALYSIS, slot, response_for

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')
CLAIM_REASONS = {'unverified_claim', 'fabric_forbidden', 'unsupported_part_fact', 'unsupported_care', 'care_claim', 'hype', 'body_effect', 'review_claim'}


def seller_facts(source, claim, slots):
    product = dict(PRODUCT)
    analysis = {**ANALYSIS, 'sellingPoints': [], 'aiSuggestedPoints': [],
                'detailRecommendations': {'candidates': [{'label': '오픈 카라', 'reason': '가까이 보이는 깃'}]}}
    if source == 'name':product.update(name=claim+' 셔츠',nameSource='seller')
    else:analysis[source] = [claim] if source in ('sellingPoints', 'aiSuggestedPoints') else claim
    return pc.facts_bundle(product, analysis, slots)


@pytest.mark.parametrize('source', ['mdComment', 'sellingPoints', 'name'])
@pytest.mark.parametrize('kind,role,target,template,text', [
    ('intro','body','section','emo','구김이 잘 안 생겨요'),
    ('feature','desc','feature','emo','구김이 잘 안 생겨요'),
    ('points','items.0.desc','part','show','구김이 잘 안 생기는 깃'),
    ('fabric','body','section','emo','구김이 잘 안 생겨요'),
    ('faq','items.0.a','section','emo','구김이 잘 안 생겨요'),
    ('review','items.0.text','section','emo','구김이 잘 안 생겨요'),
])
def test_seller_wrinkle_claim_rephrasing_is_supported_in_every_copy_path(source,kind,role,target,template,text):
    s = slot(kind,role,target=target,template=template)
    reasons = pc._slot_reasons(text,s,seller_facts(source,'구김이 안 가요',[s]),{})
    assert not CLAIM_REASONS.intersection(reasons)


@pytest.mark.parametrize('source', ['mdComment','sellingPoints','name'])
@pytest.mark.parametrize('claim,text', [('튼튼한 소재','튼튼해요'), ('보풀이 안 생겨요','보풀이 잘 안 생겨요'),
    ('비침이 없어요','비침이 없어요'), ('늘어나지 않아요','늘어나지 않아요'),
    ('하나도 안 춥어요','하나도 안 춥어요'), ('완벽한 방수','완벽하게 방수돼요'),
    ('군살을 보정해요','군살을 보정해요'), ('날씬해 보여요','날씬해 보여요')])
def test_same_seller_performance_and_literal_body_claims_are_allowed(source,claim,text):
    s = slot('fabric','body',target='section')
    reasons = pc._slot_reasons(text,s,seller_facts(source,claim,[s]),{})
    assert not CLAIM_REASONS.intersection(reasons)


@pytest.mark.parametrize('claim,text', [('구김이 있어요','구김이 없어요'), ('방수가 안 돼요','방수가 돼요'),
    ('따뜻해요','하나도 안 춥어요'), ('구김이 잘 안 생겨요','구김이 전혀 없어요'),
    ('튼튼한 소재','보풀이 전혀 안 생겨요'), ('날씬해 보여요','확실히 날씬해 보여요'),
    ('날씬해 보이지 않아요','날씬해 보여요'), ('방수돼요','방수가 안 돼요')])
def test_seller_evidence_does_not_authorize_opposite_or_stronger_claim(claim,text):
    s = slot('feature','desc',target='feature')
    reasons = pc._slot_reasons(text,s,seller_facts('mdComment',claim,[s]),{})
    assert CLAIM_REASONS.intersection(reasons)


def test_one_supported_claim_cannot_hide_a_new_claim_or_material_or_quantity():
    s = slot('feature','desc',target='feature')
    facts = seller_facts('sellingPoints','튼튼한 소재',[s])
    for text,reason in [('튼튼하고 방수도 돼요','unverified_claim'), ('튼튼한 캐시미어예요','unknown_material'),
                        ('튼튼해서 99년 입어요','unknown_number')]:
        assert reason in pc._slot_reasons(text,s,facts,{})


@pytest.mark.parametrize('source', ['mdComment','sellingPoints','name'])
@pytest.mark.parametrize('text', ['배송이 빨라요','할인 중이에요','가격이 좋아요'])
def test_promotions_never_receive_factual_name_or_seller_exemption(source,text):
    s = slot('feature','desc',target='feature')
    assert 'promotion' in pc._slot_reasons(text,s,seller_facts(source,text,[s]),{})


def test_ai_suggested_points_fallback_does_not_become_seller_claim_evidence():
    s = slot('feature','desc',target='feature')
    facts = seller_facts('aiSuggestedPoints','구김이 안 가요',[s])
    assert 'unverified_claim' in pc._slot_reasons('구김이 안 가요',s,facts,{})
    assert 'unverified_claim' in pc._slot_reasons('구김이 잘 안 생겨요',s,facts,{})


def test_shared_cleaning_patterns_retain_existing_sentence_behavior():
    data = json.loads((Path(pc.DATA_DIR)/'page_copy_seller_cleanup.json').read_text())
    for text,expected in [
        ('구김이 안 가요. 무료 배송이에요.\n튼튼해요.', '구김이 안 가요.\n튼튼해요.'),
        ('폭은 1.5cm예요.\n이 사진을 써 주세요. 보정돼요.', '폭은 1.5cm예요.\n보정돼요.'),
        ('할인 안내 https://example.com/a.\n튼튼해요.', '튼튼해요.'),
    ]:
        lines=[]
        for line in text.splitlines():
            kept=[s.strip() for s in re.split(r'(?<=[.!?。！？])\s+|…+',line) if s.strip()
                  and not re.search(data['promotion'],s,re.I) and not re.search(data['design_direction'],s)]
            if kept:lines.append(' '.join(kept))
        assert '\n'.join(lines) == checks.clean_seller_text(text) == expected


@pytest.mark.parametrize('failure', ['outage','missing_twice','rewrite_outage','partial'])
def test_final_missing_polished_body_always_returns_cleaned_seller_original(failure):
    body = slot('intro','body',target='section'); title=slot('intro','title',target='section')
    calls=[]; original='군살을 보정해요. 무료 배송이에요.\n구김이 안 가요.'
    async def fake(req,timeout):
        calls.append(req)
        if failure=='outage' or failure=='rewrite_outage' and req['stage']=='rewrite':raise RuntimeError('synthetic outage')
        return {'response': response_for([title],['편하게 입는 셔츠']) if failure=='partial' and req['stage']=='single' else {}}
    out=asyncio.run(pc.generate_slots(make_settings(),[body,title],PRODUCT,{'mdComment':original},seed=1,
        overrides={'caller':fake,'required_slots':['intro.body'],'rewrite_slots':[]}))
    texts=out['copyResults'][0]['texts']; retained=next(t for t in texts if t['role']=='body')
    assert retained=={'role':'body','text':'군살을 보정해요.\n구김이 안 가요.','draft':False}
    row=next(r for r in out['log']['slots'] if r['slotId']==body['id'])
    assert row['source']=='seller_cleaned' and not row['draft']
    assert len(calls)==2
    if failure!='outage':assert [r['stage'] for r in calls]==['single','rewrite']
    if failure=='partial':assert any(t['role']=='title' and t['text']=='편하게 입는 셔츠' for t in texts)


def test_missing_twice_preserves_exact_nonpromotional_seller_original_source():
    s=slot('intro','body',target='section')
    async def fake(req,timeout):return {'response':{}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'mdComment':'구김이 안 가요.'},seed=1,
        overrides={'caller':fake,'required_slots':['intro.body']}))
    assert out['copyResults'][0]['texts'][0]['text']=='구김이 안 가요.'
    assert out['log']['slots'][0]['source']=='seller_original'
    assert 'missing_required' in out['log']['slots'][0]['reasons']


def test_raw_md_claim_survives_promotion_sentence_cleaning_without_copying_promotion():
    s=slot('feature','desc',target='feature')
    facts=seller_facts('mdComment','구김이 안 가고 배송도 빨라요',[s])
    assert facts['sellerComment']==''
    assert not CLAIM_REASONS.intersection(pc._slot_reasons('구김이 잘 안 생겨요',s,facts,{}))
    assert 'promotion' in pc._slot_reasons('배송도 빨라요',s,facts,{})


@pytest.mark.parametrize('kind,role', [('review','items.0.text'),('faq','items.0.a'),('intro','body')])
def test_literal_seller_care_claim_reaches_each_independent_slot_check(kind,role):
    s=slot(kind,role,target='section')
    text='세탁해도 줄지 않아요'
    assert not CLAIM_REASONS.intersection(pc._slot_reasons(text,s,seller_facts('sellingPoints',text,[s]),{}))


@pytest.mark.parametrize('source,text', [('세탁할 수 없어요','세탁할 수 있어요'), ('안 날씬해 보여요','날씬해 보여요')])
def test_seller_negation_does_not_license_literal_substring_or_md_word_exemption(source,text):
    s=slot('intro','body',target='section')
    assert CLAIM_REASONS.intersection(pc._slot_reasons(text,s,seller_facts('mdComment',source,[s]),{}))


@pytest.mark.parametrize('text',['환불해 드려요','결제하면 돼요','입금 후 보내드려요'])
def test_cleanup_promotion_vocabulary_cannot_leak_through_copy_validation(text):
    s=slot('feature','desc',target='feature')
    assert 'promotion' in pc._slot_reasons(text,s,seller_facts('sellingPoints',text,[s]),{})


def test_exact_seller_part_claim_does_not_fail_detail_concept_check():
    s=slot('points','items.0.desc',target='part',template='show')
    text='튼튼한 단추'
    assert not CLAIM_REASONS.intersection(pc._slot_reasons(text,s,seller_facts('sellingPoints',text,[s]),{}))


def test_raw_seller_provenance_is_validator_only_and_never_returns_promotions_to_prompt():
    s=slot('intro','body',target='section')
    facts=seller_facts('mdComment','구김이 안 가고 배송도 빨라요',[s])
    request=pc.build_request([s],facts,{'seed':'fixture','slots':{}},'gemini','fixture',prompt_version='v2')
    assert '배송' not in request['prompt']
    assert 'sellerClaims' not in json.loads(request['prompt'])['facts']


@pytest.mark.parametrize('source,text', [('날씬해 보여요','입으면 날씬해 보여요'), ('군살 보정','군살 보정이 포인트예요')])
def test_literal_seller_body_claim_can_be_embedded_without_becoming_a_new_claim(source,text):
    s=slot('feature','desc',target='feature')
    assert 'body_effect' not in pc._slot_reasons(text,s,seller_facts('sellingPoints',source,[s]),{})


def test_seller_feature_claim_survives_fact_fallback_when_md_is_empty():
    s=slot('intro','body',target='section')
    async def fake(req,timeout):return {'response':{}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,{'sellingPoints':['군살 보정']},seed=1,
        overrides={'caller':fake,'required_slots':[]}))
    assert out['copyResults'][0]['texts'][0]['text']=='군살 보정이 포인트인 상의예요.'


@pytest.mark.parametrize('source,text', [('방수가 안돼요','방수가 돼요'), ('구김이 없진 않아요','구김이 없어요'),
    ('튼튼하지 않은 건 아니에요','튼튼하지 않아요'), ('보정이 안돼요','보정')])
def test_compact_and_double_negation_do_not_authorize_opposite_claims(source,text):
    s=slot('feature','desc',target='feature')
    assert CLAIM_REASONS.intersection(pc._slot_reasons(text,s,seller_facts('mdComment',source,[s]),{}))


@pytest.mark.parametrize('source,text,supported', [
    ('한여름에 입기 좋아요','한겨울에 입기 좋아요',False),
    ('간절기에 입기 좋아요','사계절 입기 좋아요',False),
    ('한겨울에 입기 좋아요','한여름에 입기 좋아요',False),
    ('한여름에 입기 좋아요','한여름에도 입기 좋아요',True),
    ('간절기에 입기 좋아요','간절기에도 입기 좋아요',True),
])
@pytest.mark.parametrize('source_field', ['sellingPoints', 'mdComment', 'name'])
def test_seller_season_support_requires_the_same_specific_season(source,text,supported,source_field):
    s=slot('intro','body',target='section') if source_field=='mdComment' else slot('feature','desc',target='feature')
    reasons=pc._slot_reasons(text,s,seller_facts(source_field,source,[s]),{})
    assert ('season_claim' not in reasons) is supported
