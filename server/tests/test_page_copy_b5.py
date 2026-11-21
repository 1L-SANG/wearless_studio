"""B5 exact review regressions. Synthetic prompts and local providers only."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents import page_copy as pc, page_copy_checks as checks
from conftest import make_settings
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, ANALYSIS, slot

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')


@pytest.mark.parametrize('price',['오늘만 2만 9천 원이에요','29,000원','2만9천원','₩29,000'])
def test_price_without_the_word_price_is_cleaned_and_rejected(price):
    assert checks.clean_seller_text('핏이 편해요. '+price+'.')=='핏이 편해요.'
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[price]},[s])
    assert 'promotion' in pc._slot_reasons(price,s,facts,{})


@pytest.mark.parametrize('source,text,kind,role',[
    ('친환경 소재예요','친환경 원단이에요','fabric','body'),
    ('군살을 보정해요','군살을 보정해 주는 핏이에요','feature','desc'),
    ('최고의 편안함','최고로 편해요','first','summary.line'),
])
@pytest.mark.parametrize('seller',[False,True])
def test_owner_claim_inflections_require_actual_seller_support(source,text,kind,role,seller):
    s=slot(kind,role,target='feature' if kind=='feature' else 'section')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[source] if seller else []},[s])
    prohibited={'fabric_forbidden','body_effect','hype','unverified_claim'}
    assert bool(prohibited.intersection(pc._slot_reasons(text,s,facts,{}))) is not seller


def test_local_sleeve_durability_does_not_authorize_zipper_durability():
    s=slot('points','items.0.desc',target='part',template='show');s['point']='지퍼'
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':['소매 끝이 튼튼해요'],
        'detailRecommendations':{'candidates':[{'label':'지퍼','reason':'지퍼가 달려 있어요'}]}},[s])
    assert {'unverified_claim','unsupported_part_fact'}.intersection(pc._slot_reasons('튼튼한 지퍼',s,facts,{}))


def test_absolute_modifier_stays_with_its_supported_claim():
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':['완벽하게 방수돼요'],
        'detailRecommendations':{'candidates':[{'label':'허리 밴딩','reason':'허리 밴딩이 있어요'}]}},[s])
    assert 'body_effect' in pc._slot_reasons('방수라서 완벽하게 날씬해 보여요',s,facts,{})


def test_faq_transition_season_exception_does_not_cover_midwinter():
    s=slot('faq','items.0.a',target='section')
    facts=pc.facts_bundle(PRODUCT,ANALYSIS,[s])
    assert 'season_claim' in pc._slot_reasons('간절기부터 한겨울까지 좋아요',s,facts,{'topic':'두께와 계절'})
    assert 'season_claim' not in pc._slot_reasons('간절기에 입기 좋아요',s,facts,{'topic':'두께와 계절'})


@pytest.mark.parametrize('label',['무료 배송','29,000원'])
def test_detail_label_promotion_is_dropped_without_losing_valid_description(label):
    s=slot()
    async def fake(req,timeout):return {'response':{'partDescriptionCopy':[{'point':s['point'],'desc':'깃이 넓게 펼쳐져요','label':label}]}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,ANALYSIS,seed=1,overrides={'caller':fake}))
    assert out['partDescriptionCopy']==[{'point':s['point'],'desc':'깃이 넓게 펼쳐져요'}]


@pytest.mark.parametrize('first',['무료 배송','29,000원'])
@pytest.mark.parametrize('safe',['뒤쪽 자수',None])
def test_md_fact_fallback_selects_a_safe_feature_or_part(first,safe):
    s=slot('intro','body',target='section')
    analysis={'mdComment':'무료 배송이에요.','sellingPoints':[first],
              'detailRecommendations':{'candidates':[{'label':safe,'reason':'확인한 디테일'}]} if safe else {}}
    async def fake(req,timeout):return {'response':{}}
    out=asyncio.run(pc.generate_slots(make_settings(),[s],PRODUCT,analysis,seed=1,overrides={'caller':fake}))
    expected=[{'role':'body','text':'뒤쪽 자수가 포인트인 상의예요.','draft':True}] if safe else []
    assert out['copyResults'][0]['texts']==expected


@pytest.mark.parametrize('origin,accepted',[('seller',True),('ai',False),('unknown',False),(None,False)])
def test_only_known_seller_name_is_claim_evidence(origin,accepted):
    product={'name':'구김 없는 셔츠','clothingType':'top',**({'nameSource':origin} if origin else {})}
    s=slot('feature','desc',target='feature');facts=pc.facts_bundle(product,{},[s])
    assert ('unverified_claim' not in pc._slot_reasons('구김이 없어요',s,facts,{})) is accepted


def test_worker_generated_name_is_marked_ai_before_page_copy(monkeypatch):
    from app import repo
    from app.workers import detail_page_job as worker
    from conftest import worker_job
    from test_detail_page_copy_b import setup,cuts_result
    captured={}
    async def generate(settings,board,product,analysis,**kwargs):captured.update(product);return {}
    app,*_=setup(monkeypatch,generate,AsyncMock(return_value=cuts_result()))
    monkeypatch.setattr(repo,'get_product',AsyncMock(return_value={'name':'','colors':[]}))
    monkeypatch.setattr(repo,'get_analysis',AsyncMock(return_value={'suggestedName':'구김 없는 셔츠'}))
    asyncio.run(worker.run_detail_page_job(app,worker_job()))
    assert captured['name']=='구김 없는 셔츠'
    assert captured.get('name_source',captured.get('nameSource'))=='ai'
    s=slot('feature','desc',target='feature')
    assert 'unverified_claim' in pc._slot_reasons('구김이 없어요',s,pc.facts_bundle(captured,{},[s]),{})


def test_unrecognized_extra_claim_does_not_borrow_supported_absolute_modifier():
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':['완벽하게 방수돼요']},[s])
    assert 'hype' in pc._slot_reasons('방수라서 완벽한 핏이에요',s,facts,{})


@pytest.mark.parametrize('origin',['ai','unknown'])
def test_automatic_name_cannot_supply_body_structure_evidence(origin):
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle({'name':'하이웨이스트 팬츠','nameSource':origin},{},[s])
    assert 'body_effect' in pc._slot_reasons('다리가 길어 보여요',s,facts,{})


def test_matching_named_subject_keeps_supported_durability():
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':['소매 끝이 튼튼해요']},[s])
    assert 'unverified_claim' not in pc._slot_reasons('소맷단이 튼튼해요',s,facts,{})


def test_supported_eco_claim_does_not_mask_added_body_claim():
    s=slot('fabric','body',target='section')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':['친환경 소재예요']},[s])
    assert 'body_effect' in pc._slot_reasons('친환경 원단이고 다이어트에 좋아요',s,facts,{})


@pytest.mark.parametrize('source,text,allowed',[
    ('구김이 안 가요','구김이 전혀 없어요',False),
    ('구김이 전혀 없어요','구김이 전혀 안 생겨요',True),
    ('방수가 돼요','방수가 완벽해요',False),
])
def test_postfix_modifier_keeps_its_strength(source,text,allowed):
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[source]},[s])
    rejected={'hype','unverified_claim'}.intersection(pc._slot_reasons(text,s,facts,{}))
    assert bool(rejected) is not allowed


def test_long_measurement_number_does_not_stall_price_cleanup():
    import subprocess
    import sys
    from pathlib import Path
    value='폭은 '+('1'*80)+'cm예요.'
    script='from app.agents.page_copy_checks import clean_seller_text\nprint(clean_seller_text('+repr(value)+'))'
    result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],
        capture_output=True,text=True,timeout=2,check=True)
    assert result.stdout.strip()==value


@pytest.mark.parametrize('source,text,allowed',[
    ('허리가 가늘어 보여요','허리가 부해 보여요',False),
    ('허리가 부해 보여요','허리가 가늘어 보여요',False),
    ('허리가 가늘어 보여요','허리가 가늘어 보여요',True),
    ('허리가 부해 보여요','허리가 부해 보여요',True),
    ('허리가 가늘어 보여요','허리가 가늘어 보이는 핏이에요',True),
    ('허리가 부해 보여요','허리가 부해 보이는 핏이에요',True),
])
def test_opposite_body_effects_do_not_share_seller_evidence(source,text,allowed):
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[source]},[s])
    assert ('body_effect' not in pc._slot_reasons(text,s,facts,{})) is allowed


@pytest.mark.parametrize('source,text',[
    ('키가 커 보여요','키가 커 보여요'),
    ('키가 커 보여요','키가 크게 보이는 핏이에요'),
    ('체형 보완이 돼요','체형 보완이 돼요'),
    ('체형 보완이 돼요','체형 보완을 도와줘요'),
    ('뱃살이 덜 나와 보여요','뱃살이 덜 나와 보여요'),
    ('뱃살이 덜 나와 보여요','뱃살이 덜 나와 보이는 핏이에요'),
])
@pytest.mark.parametrize('seller',[False,True])
def test_existing_checked_body_claims_accept_only_backed_exact_or_inflected_wording(source,text,seller):
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[source] if seller else []},[s])
    assert ('body_effect' not in pc._slot_reasons(text,s,facts,{})) is seller


@pytest.mark.parametrize('source,text',[
    ('키가 커 보여요','키가 커 보이고 다이어트에 좋아요'),
    ('체형 보완이 돼요','체형 보완이 되고 허리가 부해 보여요'),
    ('뱃살이 덜 나와 보여요','뱃살이 더 나와 보여요'),
    ('뱃살이 더 나와 보여요','뱃살이 덜 나와 보여요'),
    ('뱃살이 덜 나와 보여요','군살이 덜 나와 보여요'),
])
def test_new_body_effect_support_does_not_mask_extra_or_opposite_claims(source,text):
    s=slot('feature','desc',target='feature')
    facts=pc.facts_bundle(PRODUCT,{'sellingPoints':[source]},[s])
    assert 'body_effect' in pc._slot_reasons(text,s,facts,{})
