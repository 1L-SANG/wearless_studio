"""A4 prompt compatibility retained for single generation; no live providers."""
import asyncio
from copy import deepcopy
import json

import pytest

from page_copy_test_support import page_copy_prompt_dir

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")

from app.agents import page_copy as pc
from conftest import make_settings
from test_page_copy import PRODUCT, ANALYSIS, response_for, slot


@pytest.fixture
def prompt_files(page_copy_prompt_dir):
    return page_copy_prompt_dir

def template(stage, optional=()):
    keys=['facts','slots','exemplars',*optional]
    return '{{rules}}\n---USER---\n'+'\n'.join('{{'+key+'}}' for key in keys)


@pytest.mark.parametrize('stage', ['single'])
@pytest.mark.parametrize('optional',[(),('plans',),('examples',),('plans','examples')])
def test_v2_optional_placeholders(prompt_files,stage,optional):
    (prompt_files/f'{stage}_gpt_v2.md').write_text(template(stage,optional))
    s=slot('intro','title',target='section')
    request=pc.build_request([s],pc.facts_bundle(PRODUCT,ANALYSIS,[s]),pc.plans(0,[s],{}),
        'gpt','fixture-model',prompt_version='v2',mode='single',
        stage='single')
    assert '{{' not in request['prompt']


@pytest.mark.parametrize('stage',['single'])
def test_absent_examples_does_not_read_v1_pair_file(prompt_files,monkeypatch,stage):
    (prompt_files/f'{stage}_gpt_v2.md').write_text(template(stage))
    (prompt_files/'examples_v1.json').unlink()
    s=slot('intro','title',target='section')
    pc.build_request([s],{}, {'seed':'x','slots':{}},'gpt','fixture',prompt_version='v2',
        mode='single',stage='single')


@pytest.mark.parametrize('stage,missing',[(s,k) for s in ('single',)
    for k in ('rules','facts','slots','exemplars')])
def test_v2_required_placeholders_still_fail(prompt_files,stage,missing):
    (prompt_files/f'{stage}_gpt_v2.md').write_text(template(stage).replace('{{'+missing+'}}',''))
    with pytest.raises(ValueError,match='placeholder'):
        pc.build_request([],{}, {},'gpt','fixture',prompt_version='v2',mode='single',stage='single')


@pytest.mark.parametrize('name',['unknown','drafts','unknown-name'])
def test_v2_other_placeholders_rejected(prompt_files,name):
    (prompt_files/'single_gpt_v2.md').write_text(template('single')+'\n{{'+name+'}}')
    with pytest.raises(ValueError,match='placeholder'):
        pc.build_request([],{}, {},'gpt','fixture',prompt_version='v2')


@pytest.mark.parametrize('missing',['examples','plans'])
def test_v1_still_requires_both_legacy_placeholders(prompt_files,missing):
    (prompt_files/'gpt_v1.md').write_text('{{rules}}\n---USER---\n'+''.join('{{'+k+'}}' for k in ('facts','slots','plans','examples') if k!=missing))
    with pytest.raises(ValueError,match='placeholder'):
        pc.build_request([],{}, {},'gpt','fixture')


SLOT_NAMES=[('intro.title','intro','title','section'),('intro.body','intro','body','section'),
    ('part.desc','points','items.0.desc','part'),('feature.desc','feature','desc','feature'),
    ('fabric.title','fabric','title','section'),('fabric.body','fabric','body','section'),
    ('review.text','review','items.0.text','section'),('faq.q','faq','items.0.q','section'),
    ('faq.a','faq','items.0.a','section'),('for_you','for_you','items.0','section')]


@pytest.mark.parametrize('name,kind,role,target',SLOT_NAMES)
def test_lead_exemplars_selected_by_public_slot_name(prompt_files,name,kind,role,target):
    examples=[{'slot':name,'template':'all','garment':'bottom','text':f'{name} 본보기 {i}','origin':'lead'} for i in range(3)]
    examples.append({'slot':name,'template':'all','garment':'top','text':'같은 옷 종류 제외','origin':'lead'})
    (prompt_files/'exemplars_v2.json').write_text(json.dumps({'exemplars':examples},ensure_ascii=False))
    s=dict(slot(kind,role,target=target),id='fixture')
    request=pc.build_request([s],{'garmentType':'top'},{'seed':'fixed','slots':{}},'gpt','fixture',prompt_version='v2')
    assert len(request['exemplars'])==3
    assert all(e['origin']=='lead' and e['slot']==name and e['garment']=='bottom' for e in request['exemplars'])


