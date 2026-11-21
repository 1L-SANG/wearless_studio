import pytest
from page_copy_test_support import page_copy_prompt_dir
from page_template_cases import case_input, assemble_input, elements
from test_page_template_copy_v2 import intro_input

@pytest.mark.parametrize('template',['emo','show'])
@pytest.mark.parametrize('partial',[False,True])
def test_completed_failed_detail_copy_keeps_source_cards_and_indices(template,partial):
    v=case_input(f'{template}-2-mono')
    v['storyboard']=[b for b in v['storyboard'] if b['pageSectionKind']=='points']
    v['analysis']['featureCopy']=[]
    v['analysis']['partDescriptionCopy']=[{'point':'소매 단','desc':'소매 설명이에요'}] if partial else []
    v['copyResults']=[{'blockId':v['storyboard'][0]['id'],'texts':[]}]
    els=elements(assemble_input(v))
    assert [e['sourceBlockId'] for e in els if e['type']=='image']==[f'{template}.points.detail1',f'{template}.points.detail2']
    assert next(e for e in els if e.get('copyRole')=='items.0.desc').get('hidden')
    assert next(e for e in els if e.get('copyRole')=='items.1.name')['text']=='소매 단'
    if partial:assert next(e for e in els if e.get('copyRole')=='items.1.desc')['text']=='소매 설명이에요'

@pytest.mark.parametrize('template',['emo','show'])
def test_ready_empty_md_falls_back_to_cleaned_seller_text(template):
    v=intro_input(template=template,seller='구김이 안 가요.\n무료 배송이에요.\n가격은 3만원이에요.',body='')
    v['copyResults'][0]['texts']=[]
    assert [e['text'] for e in elements(assemble_input(v)) if e.get('copyRole')=='body']==['구김이 안 가요.']


@pytest.mark.parametrize('template',['emo','show'])
@pytest.mark.parametrize('partial',[False,True])
def test_real_generate_with_fake_failed_copy_retains_successful_detail_photos(template,partial,page_copy_prompt_dir):
    import asyncio
    from app.agents import page_copy as pc
    from conftest import make_settings
    v=case_input(f'{template}-2-mono')
    v['storyboard']=[b for b in v['storyboard'] if b['pageSectionKind']=='points']
    v['analysis']['partDescriptionCopy']=[]
    v['analysis']['featureCopy']=[]
    for candidate in v['analysis']['detailRecommendations']['candidates']:
        candidate['reason']=candidate['label']+'의 모양이 보여요'
    copy='소매 끝이 단정해요' if template=='emo' else '단정한 소매 끝'
    calls=[]
    async def fake(req,timeout):
        calls.append(req['stage'])
        if not partial:raise RuntimeError('fake provider unavailable')
        return {'response':{'sections':[],'partDescriptionCopy':[{'point':'소매 단','desc':copy}],'featureCopy':[]}}
    out=asyncio.run(pc.generate(make_settings(),v['storyboard'],v['product'],v['analysis'],seed=1,
                               overrides={'caller':fake},timeout_seconds=.5))
    v['analysis'].update(partDescriptionCopy=out['partDescriptionCopy'],featureCopy=out['featureCopy'])
    v['copyResults']=pc.copy_results_from_result(out,v['storyboard'])
    els=elements(assemble_input(v))
    assert [e['sourceBlockId'] for e in els if e['type']=='image']==[f'{template}.points.detail1',f'{template}.points.detail2']
    assert len(calls)<=2
    assert next(e for e in els if e.get('copyRole')=='items.0.desc')['hidden']
    second=next(e for e in els if e.get('copyRole')=='items.1.desc')
    assert second['text']==(copy if partial else '')
