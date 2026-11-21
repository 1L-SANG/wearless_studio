import pytest
from page_template_cases import assemble_input,elements
from test_page_template_copy_v2 import intro_input

@pytest.mark.parametrize('template',['emo','show'])
@pytest.mark.parametrize('state',['absent','empty','disabled'])
def test_no_copy_result_still_cleans_md_price_and_delivery(template,state):
    v=intro_input(template=template,seller='핏이 편해요. 무료 배송이에요.\n오늘만 2만 9천 원이에요\n29,000원',body='')
    if state=='absent':v['copyResults']=[]
    if state=='empty':v['copyResults'][0]['texts']=[]
    if state=='disabled':v['copywriting']=False
    assert [e['text'] for e in elements(assemble_input(v)) if e.get('copyRole')=='body']==['핏이 편해요.']

@pytest.mark.parametrize('separator',['\u2028','\u2029','\r\n'])
def test_pasted_unicode_line_separator_is_cleaned_consistently(separator):
    v=intro_input(seller=f'핏이 편해요{separator}무료 배송이에요.',body='')
    v['copyResults']=[]
    assert [e['text'] for e in elements(assemble_input(v)) if e.get('copyRole')=='body']==['핏이 편해요']


def test_stored_promotional_detail_label_falls_back_to_original_name():
    from page_template_cases import case_input
    v=case_input('emo-2-mono')
    v['storyboard']=[b for b in v['storyboard'] if b['pageSectionKind']=='points']
    v['analysis']['partDescriptionCopy']=[{'point':'카라','desc':'카라 설명이에요','label':'무료 배송'}]
    assert next(e for e in elements(assemble_input(v)) if e.get('copyRole')=='items.0.name')['text']=='카라'


@pytest.mark.parametrize('mode',['error','timeout'])
def test_whole_copy_task_failure_then_real_assembly_never_exposes_promotion(monkeypatch,mode):
    import asyncio,time
    from unittest.mock import AsyncMock
    from app.agents import page_copy
    from app.workers import detail_page_job as worker
    from conftest import make_settings
    v=intro_input(seller='핏이 편해요. 무료 배송이에요.',body='')
    async def unavailable(*args,**kwargs):
        if mode=='timeout':await asyncio.sleep(10)
        raise RuntimeError('fake entire task failure')
    monkeypatch.setattr(page_copy,'generate',unavailable)
    monkeypatch.setattr(worker,'_copy_ready',AsyncMock())
    result=asyncio.run(worker._generate_page_copy(make_settings(),v['storyboard'],v['product'],v['analysis'],None,'fake-job',time.monotonic()+.02))
    assert result=={}
    v['copyResults']=page_copy.copy_results_from_result(result,v['storyboard'])
    assert [e['text'] for e in elements(assemble_input(v)) if e.get('copyRole')=='body']==['핏이 편해요.']
