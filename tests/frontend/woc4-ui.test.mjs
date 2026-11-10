import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle, selectFontStyle, selectColorModeStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns, visibleSectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';
import { withStoryboardSpaceSetExamples, storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { signatureCardForBlock } from '../../src/lib/signatureCutPool.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { photoGalleryChoices } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { productSectionState } from '../../src/lib/pageTemplates/productPresentation.js';
import { productPresentationOf, PRODUCT_PRESENTATION_OPTIONS } from '../../src/lib/storyboardTaxonomy.js';
import { productPresentationExample } from '../../src/lib/productPresentationExamples.js';
import productImages from '../../src/data/productPresentationExamples.json' with { type: 'json' };
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

let vite, Gallery, Filters, Stage, Preview, Picker, Themes, ProductPicker, createQueue, saveStyle, waitStyle, serviceApi;
before(async()=>{
  vite=await createServer({configFile:false,plugins:[react()],resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},
    server:{middlewareMode:true,watch:null,ws:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]},logLevel:'silent'});
  ({StoryboardPhotoGallery:Gallery}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardPhotoGallery.jsx'));
  ({PhotoChoiceFilters:Filters}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardChoiceControls.jsx'));
  ({StoryboardStage:Stage}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({StoryboardPreview:Preview}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({StoryboardSectionPicker:Picker}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({StoryboardThemePicker:Themes}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardThemePicker.jsx'));
  ({ProductPresentationPicker:ProductPicker}=await vite.ssrLoadModule('/src/features/storyboard/ProductPresentationPicker.jsx'));
  ({createStoryboardStyleSaveQueue:createQueue,saveStoryboardPageStyle:saveStyle}=await vite.ssrLoadModule('/src/features/storyboard/storyboardPageStyle.js'));
  ({waitForAnalysisEditSave:waitStyle}=await vite.ssrLoadModule('/src/features/product-input/saveRouting.js'));
  ({api:serviceApi}=await vite.ssrLoadModule('/src/lib/api/index.js'));
});
after(()=>vite?.close());
const render=(C,props)=>renderToStaticMarkup(React.createElement(C,props));
const product={clothingType:'top',colors:[{id:'base',isBase:true,name:'아이보리',images:[{slot:'Front',src:'/front.jpg'},{slot:'Back',src:'/back.jpg'}]},{id:'blue',name:'블루'}]};
const analysis={targetGenders:['women'],pageStyle:defaultPageStyle()};
const catalogs={...withStoryboardSpaceSetExamples({genExamples:raw}),detailProduct:product,analysis,
  shotTypes:[{value:'full',label:'전신'},{value:'medium',label:'상반신'}],productShotTypes:[{value:'ghost',label:'전체컷'},{value:'detail',label:'디테일'}]};
const board=()=>seedStoryboard(product,analysis);
const basic=(block=board()[0])=>({block,catalogs,cut:block.cutType,shot:block.shot,clothingType:'top',gender:'women',exampleId:block.exampleId,projectId:'woc4',onExampleChange(){}});

test('WOC4 actual photo filters show every place with cafe selected and a light dropdown',()=>{
  const html=render(Filters,{place:'cafe',light:'all'});
  assert.match(html,/빛: 전체/);assert.match(html,/>공원<\/button>/);assert.match(html,/aria-pressed="true"[^>]*>카페/);
  assert.doesNotMatch(html,/>전체<\/button>|더보기/);
  const css=readFileSync('src/features/storyboard/StoryboardChoices.module.css','utf8');
  assert.match(css,/\.places \{[^}]*flex-wrap: wrap/);assert.match(css,/\.places button \{[^}]*white-space: nowrap/);
  assert.match(css,/\.lightActive \{[^}]*1px var\(--sb-line-control\)/);
});

test('WOC4 actual first gallery has one pinned signature and medium framing with photo-only cards',()=>{
  const block=board()[0],signature=signatureCardForBlock(block,{product,analysis,gender:'women',projectId:'woc4'});
  const html=render(Gallery,{...basic(block),signature});
  assert.equal((html.match(/aria-label="시그니처 사진"/g)||[]).length,1);
  assert.equal([...html.matchAll(/data-example-id="([^"]+)"/g)][0][1],signature.id);
  assert.match(html,/aria-pressed="true"[^>]*>상반신/);
  assert.doesNotMatch(html,/이미 사용|지난번|n번째 컷에|장<|PASS|Attrangs/);
  const bottom=render(Gallery,{...basic(block),clothingType:'bottom'});
  assert.match(bottom,/aria-pressed="true"[^>]*>하반신/);
});

test('WOC4 a single-photo set expands in place with only the chosen framing and selecting invokes one example',async()=>{
  const h=hookHarness(),picked=[];
  const Comp=await loadStoryboardComponent('StoryboardPhotoGallery.jsx','StoryboardPhotoGallery',{
    ...h.hooks,storyboardSpaceSetsFor,filterSpaceSetsForModel,generationExampleImageSources,
    Icon:'icon',EditorMoodGuide:'editor-guide',MineImageTab:'mine-images',api:{},useAppStore:{getState:()=>({})},
  });
  const member=photoGalleryChoices(catalogs.genExamples,storyboardSpaceSetsFor({gender:'women',clothingType:'top'}),{gender:'women',clothingType:'top',shot:'medium',place:'home'}).entries.find(e=>e.members)?.members[0];
  const block={...board()[0],exampleId:member.id},props={...basic(block),onExampleChange:id=>picked.push(id)};
  try {
    let tree=h.render(Comp,props);
    const frame=nodes(tree).find(n=>n.type==='photo-framing'); assert.equal(frame.props.value,'medium');
    const group=nodes(tree).find(n=>n.type==='button' && n.props['aria-expanded']===false && n.props['data-photo-choice']);
    assert.ok(group);group.props.onClick();tree=h.render(Comp,props);
    const open=nodes(tree).find(n=>n.props?.['aria-label']==='이 장소의 사진');assert.ok(open);
    const members=nodes(open).filter(n=>n.type==='button');assert.ok(members.length);
    for(const button of members)assert.equal(catalogs.genExamples.find(e=>e.id===button.props['data-example-id']).shot,'medium');
    await members[0].props.onClick();await Promise.resolve();assert.equal(picked.length,1);
    tree=h.render(Comp,props);assert.equal(nodes(tree).some(n=>n.props?.['aria-label']==='이 장소의 사진'),false);
  } finally {h.close();}
});

test('WOC4 emotional first can select signature then a place set member with valid respective recipes',async()=>{
  const block=board()[0];const h=await storyboardPhotoPanelHarness({block,catalogs,clothingType:'top',exampleGender:'women',projectId:'woc4',colorOpts:[{id:'base'}]});
  try {
    const tree=h.render();assert.equal(nodes(tree).some(n=>n.type==='underline-tabs'),false,'single-photo slots use the place/framing gallery, not the retired cut-family transaction');
    let guide=nodes(tree).find(n=>n.type==='mood-guide');const sig=guide.props.signature;
    assert.ok(sig);guide.props.onExampleChange(sig.id,'all');
    assert.equal(h.block.cutType,'horizon');assert.equal(h.block.shot,'medium');assert.equal(h.block.refScope,'all');assert.equal(h.block.exampleId,sig.id);
    const member=photoGalleryChoices(catalogs.genExamples,storyboardSpaceSetsFor({gender:'women',clothingType:'top'}),{gender:'women',clothingType:'top',shot:'medium',place:'home'}).entries.find(e=>e.members)?.members[0];
    assert.ok(member);guide=nodes(h.render()).find(n=>n.type==='mood-guide');guide.props.onExampleChange(member.id,'pose');
    assert.equal(h.block.cutType,'styling');assert.equal(h.block.exampleId,member.id);assert.equal(h.block.refScope,'pose');assert.ok(h.block.spaceGroupId);
  } finally {h.close();}
});

test('WOC4 product panel contains only three mode choices and preserves the selected back face',()=>{
  const blocks=board(),block=blocks.find(b=>b.cutType==='product'&&b.shot==='ghost'&&b.direction==='back');
  const html=render(ProductPicker,{blocks,block,clothingType:'top'});
  for(const label of ['고스트컷','평면컷','자연광컷'])assert.match(html,new RegExp(`<strong>${label}</strong>`));
  assert.equal((html.match(/aria-pressed=/g)||[]).length,3);assert.match(html,/top_ghost_back.webp/);
  assert.doesNotMatch(html,/내 앞면 사진|내 뒷면 사진|생성예시 갤러리|대상 색상/);
  assert.equal(block.direction,'back');
});

test('WOC4 product mode failed save keeps retry for the same whole-run request',async()=>{
  const h=hookHarness(),calls=[];let fail=true;
  const Comp=await loadStoryboardComponent('ProductPresentationPicker.jsx','ProductPresentationPicker',{
    ...h.hooks,productSectionState,productPresentationOf,PRODUCT_PRESENTATION_OPTIONS,productPresentationExample,manifest:productImages,Icon:'icon',
  });
  const blocks=board(),block=blocks.find(b=>b.pageSectionKind==='color_product');
  const props={blocks,block,clothingType:'top',onChange:async(changes,options)=>{calls.push({changes,options});if(fail)throw Error('offline');}};
  try {
    let tree=h.render(Comp,props);const natural=nodes(tree).find(n=>n.props?.mode?.value==='natural_light');await natural.props.onChoose();
    tree=h.render(Comp,props);assert.ok(textOf(tree).includes('촬영 방식을 저장하지 못했어요.'));fail=false;
    await nodes(tree).find(n=>n.type==='button'&&textOf(n)==='다시 시도').props.onClick();
    assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);assert.equal(calls[0].options.retryAtomic,true);
    assert.equal(block.productPresentation,'ghost');
  } finally {h.close();}
});

test('WOC4 section panel removal is in its header except first; names have no descriptions',()=>{
  const blocks=board(),run=sectionRuns(blocks).find(r=>r.kind==='wear');
  const html=render(Picker,{mode:'design',runKey:run.key,blocks,catalogs});assert.match(html,/>섹션 빼기<\/button>/);
  assert.doesNotMatch(html,/컷 줄어요|카페, 골목, 집처럼/);
  const first=render(Picker,{mode:'design',runKey:blocks[0].pageSectionKey,blocks,catalogs});assert.doesNotMatch(first,/>섹션 빼기<\/button>/);
  assert.ok(html.indexOf('>기본</small>')<html.indexOf('<strong>'));
});

test('WOC4 stage has the new subtitle, white pencil design button and color dots without face/place captions',()=>{
  const blocks=board(),run=sectionRuns(blocks).find(r=>r.kind==='color_product');
  const html=render(Stage,{blocks,run,page:assembleStoryboardPreview({storyboard:blocks,catalogs}),catalogs,
    colorOpts:[{id:'base',hex:'#eee'},{id:'blue',hex:'#abc'}],photoOrdinals:new Map(blocks.map((b,i)=>[b.id,i+1])),canDesign:true});
  assert.match(html,/예시 이미지를 클릭해서 변경할 수 있어요/);assert.match(html,/섹션 디자인 변경/);
  assert.doesNotMatch(html,/>앞면<|>뒷면<|>아이보리<|>기타</);assert.match(html,/<circle[^>]*fill="#eee"/);
  const css=readFileSync('src/features/storyboard/StoryboardLayout.module.css','utf8');
  assert.match(css,/\.designButton \{[^}]*background: var\(--bg-1\)[^}]*color: var\(--fg-1\)/);
  assert.doesNotMatch(css,/notch|노치/);
});

test('WOC4 outline text bars are thicker while design previews keep their geometry',()=>{
  const blocks=[{id:'b',h:100,elements:[{id:'t',type:'text',x:0,y:0,w:100,h:30,style:{size:16,lineHeight:24}}]}];
  const normal=render(Preview,{blocks}),bold=render(Preview,{blocks,emphasizeText:true});
  assert.match(normal,/height="5\.6"/);assert.match(bold,/height="10"/);
  assert.equal(blocks[0].elements[0].h,30);
});

test('WOC4 theme specimens are ordered, two-line, titled only and use the approved tokens',()=>{
  const html=render(Themes,{value:'serif'});
  assert.ok(html.indexOf('title="매거진"')<html.indexOf('title="볼드"')&&html.indexOf('title="볼드"')<html.indexOf('title="다정"'));
  assert.equal((html.match(/>wearless</g)||[]).length,3);assert.equal((html.match(/>웨어리스</g)||[]).length,3);
  assert.doesNotMatch(html,/>매거진<|>볼드<|>다정</);
  const tokens=readFileSync('src/styles/tokens.css','utf8');assert.match(tokens,/--sb-theme-inactive: \.35/);assert.match(tokens,/--sb-theme-width: 96px/);
});

test('WOC4 font saves serialize fast clicks, wait for the latest request and preserve the other style fields',async()=>{
  const done=[],calls=[];const queue=createQueue(style=>new Promise((resolve,reject)=>{calls.push(style);done.push({resolve,reject});}));
  const style={...defaultPageStyle(),colorMode:'point'};
  const first=selectFontStyle(style,'serif'),second=selectFontStyle(first,'boldGothic');
  const p1=queue.save(first),p2=queue.save(second);await Promise.resolve();await Promise.resolve();assert.equal(calls.length,1);
  done[0].resolve();await p1;await Promise.resolve();await Promise.resolve();assert.equal(calls.length,2);done[1].resolve();await p2;await queue.wait();
  assert.equal(calls[1].fontStyle,'boldGothic');assert.equal(calls[1].colorMode,'point');assert.equal(calls[1].templateId,style.templateId);
  assert.equal(selectColorModeStyle(second,'mono').fontStyle,'boldGothic');
});

test('WOC4 route re-entry waits for the complete latest font save chain, including a queued second choice',async()=>{
  const original=serviceApi.saveAnalysis,done=[],calls=[];
  serviceApi.saveAnalysis=(_id,patch)=>new Promise(resolve=>{calls.push(patch.pageStyle.fontStyle);done.push(resolve);});
  const projectId='woc4-style-reentry',queue=createQueue(style=>saveStyle(projectId,style),{projectId});
  try {
    const first=queue.save(selectFontStyle(defaultPageStyle(),'serif'));
    const second=queue.save(selectFontStyle(defaultPageStyle(),'boldGothic'));
    await Promise.resolve();await Promise.resolve();assert.deepEqual(calls,['serif']);
    let entered=false;const reentry=waitStyle(projectId).then(()=>{entered=true;});
    done[0]({pageStyle:selectFontStyle(defaultPageStyle(),'serif')});await first;
    for(let i=0;i<8;i++)await Promise.resolve();
    assert.deepEqual(calls,['serif','boldGothic']);assert.equal(entered,false,'the second font choice must reach storage before re-entry reads analysis');
    done[1]({pageStyle:selectFontStyle(defaultPageStyle(),'boldGothic')});await second;await reentry;assert.equal(entered,true);
  } finally {for(const resolve of done)resolve({});await queue.wait().catch(()=>{});serviceApi.saveAnalysis=original;}
});

test('WOC4 keyboard paging moves focus off a disappearing photo into the persistent gallery region',async()=>{
  const h=hookHarness(),focusedCard={},focusCalls=[];
  const Comp=await loadStoryboardComponent('StoryboardPhotoGallery.jsx','StoryboardPhotoGallery',{
    ...h.hooks,storyboardSpaceSetsFor,filterSpaceSetsForModel,generationExampleImageSources,
    Icon:'icon',EditorMoodGuide:'editor-guide',MineImageTab:'mine-images',api:{},useAppStore:{getState:()=>({})},
  });
  const chosen=photoGalleryChoices(catalogs.genExamples,storyboardSpaceSetsFor({gender:'men',clothingType:'bottom'}),{gender:'men',clothingType:'bottom',place:'building'}).entries[0].example;
  const block={...board()[0],pageSectionKind:'wear',cutType:'styling',exampleId:chosen.id};
  const props={...basic(block),gender:'men',clothingType:'bottom'};
  const attach=tree=>{
    const region=nodes(tree).find(n=>n.props?.['aria-label']==='생성예시 갤러리');
    if(region.ref)region.ref.current={ownerDocument:{activeElement:focusedCard},contains:el=>el===focusedCard,focus:options=>focusCalls.push(options)};
  };
  try {
    let tree=h.render(Comp,props,attach);
    nodes(tree).find(n=>n.type==='photo-framing').props.onChange('all');
    tree=h.render(Comp,props,attach);assert.ok(nodes(tree).find(n=>n.type==='photo-pages').props.count>1);
    const region=nodes(tree).find(n=>n.props?.['aria-label']==='생성예시 갤러리');
    region.props.onKeyDown({key:'ArrowRight',preventDefault(){}});
    assert.deepEqual(focusCalls,[{preventScroll:true}]);
    tree=h.render(Comp,props,attach);assert.equal(nodes(tree).find(n=>n.type==='photo-pages').props.page,1);
    assert.match(readFileSync('src/features/storyboard/StoryboardChoices.module.css','utf8'),/\.galleryRegion:focus-visible/);
  } finally {h.close();}
});
