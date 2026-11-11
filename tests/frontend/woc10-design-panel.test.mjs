import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';
import details from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };
let vite, Picker, createCache;
before(async () => {
  vite = await createServer({ configFile:false,plugins:[react()],logLevel:'silent',optimizeDeps:{noDiscovery:true,include:[]},
    resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},server:{middlewareMode:true,watch:null,ws:false},appType:'custom' });
  ({ StoryboardSectionPicker:Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ createSectionDesignPreviewCache:createCache } = await vite.ssrLoadModule('/src/features/storyboard/storyboardDesignPreview.js').catch(()=>({})));
});
after(()=>vite?.close());
const product={name:'니트',clothingType:'top',colors:[{id:'base',isBase:true,name:'블루',swatchId:'blue',images:[{slot:'Front',src:'/front.jpg'},{slot:'Detail',src:'/detail.jpg'}]},{id:'ivory',name:'아이보리',swatchId:'ivory',images:[{slot:'Front',src:'/ivory.jpg'}]}]};
const analysis={targetGenders:['women'],pageStyle:defaultPageStyle(),detailRecommendations:details};
const catalogs={detailProduct:product,analysis,genExamples:[]};
const board=()=>seedStoryboard(product,analysis);
const runOf=(blocks,kind,usage)=>sectionRuns(blocks).find(r=>r.kind===kind&&(!usage||r.usage===usage));
const daily=(count=6)=>{
  const blocks=board(),run=runOf(blocks,'wear','styling'),source=run.blocks.find(b=>b.source!=='text');
  const photos=Array.from({length:count},(_,i)=>({...source,id:`actual-${i}`,slotKey:`wear_styling.g${i+1}`,source:'mine',cutType:null,shot:i%3?'medium':'full',ownImages:[`/actual-${i}.jpg`],thumb:null,previewThumb:null,spaceGroupId:undefined}));
  return [...blocks.slice(0,run.start),...photos,...blocks.slice(run.end)];
};
const render=(blocks,run=runOf(blocks,'wear','styling'),extra={})=>renderToStaticMarkup(React.createElement(Picker,{mode:'design',blocks,runKey:run.key,catalogs,...extra}));

test('WOC10 daily layout glyphs exclude decoration and photo thumbnails show every actual example',()=>{
  const blocks=daily(),html=render(blocks);
  assert.equal((html.match(/data-layout-glyph=/g)||[]).length,3);
  assert.match(html,/aria-label="배치"/); assert.match(html,/사진을 놓는 모양/); assert.match(html,/고른 배치에 입혀요/);
  assert.match(html,/완성 모습/); assert.match(html,/role="region"[^>]*aria-label="완성 모습"/);
  assert.match(html,/href="\/actual-5.jpg"/);
  assert.doesNotMatch(html,/previewWirePhoto|previewWireBackdrop|designCheck|<small>기본<\/small>|섹션 빼기/);
  assert.doesNotMatch(html,/\sstyle=/);
});

test('WOC10 frame count group is directly after the decoration previews and has the new label',()=>{
  const blocks=daily(4),key=runOf(blocks,'wear','styling').key;
  const framed=changeTemplateSectionDesign(blocks,key,'WEAR-STYLING-FRAME-COLUMN-02',{product});
  const html=render(framed);
  assert.equal((html.match(/data-layout-glyph=/g)||[]).length,2);
  assert.match(html,/aria-label="액자에 넣을 사진"/);
  assert.ok(html.indexOf('data-frame-count')>html.indexOf('꾸밈'));
  assert.ok(html.indexOf('data-frame-count')<html.indexOf('role="region"'));
  assert.doesNotMatch(render(blocks),/액자에 넣을 사진/);
});

for(const kind of ['points','first','fabric']) test(`WOC10 ${kind} is a flat design pair with finished preview`,()=>{
  const blocks=board(),run=runOf(blocks,kind),html=render(blocks,run);
  assert.match(html,/aria-label="디자인"/); assert.match(html,/글과 사진을 놓는 방식/);
  assert.equal((html.match(/data-design-thumbnail=/g)||[]).length,2);
  assert.doesNotMatch(html,/data-layout-glyph=|aria-label="꾸밈"/);
  assert.match(html,/완성 모습/);
});

test('WOC10 colors only show two layout glyphs; singleton design stages are hidden',()=>{
  const blocks=board(),html=render(blocks,runOf(blocks,'color_product'));
  assert.equal((html.match(/data-layout-glyph=/g)||[]).length,2);
  assert.match(html,/색별 이어 보기/); assert.match(html,/색 격자/);
  assert.doesNotMatch(html,/data-design-thumbnail=|aria-label="꾸밈"/);
  let withReview=blocks;
  if(!runOf(withReview,'review')) withReview=addTemplateSection(blocks,product,analysis,'review');
  const single=render(withReview,runOf(withReview,'review'));
  assert.doesNotMatch(single,/aria-label="디자인"|data-design-thumbnail=/);
  assert.match(single,/완성 모습/);
});

test('WOC10 preview cache reuses full assembled sections across design-only saves and invalidates real inputs',()=>{
  assert.equal(typeof createCache,'function');
  let calls=0; const cache=createCache(input=>{calls++;return assembleStoryboardPreview(input);});
  const blocks=daily(),key=runOf(blocks,'wear','styling').key,before=structuredClone(blocks);
  let preview=cache.prepare(blocks,key,catalogs);
  const column=preview('P02-D01'),two=preview('WEAR-STYLING-2COL-01');
  assert.equal(calls,2);assert.equal(column.flatMap(b=>b.elements).filter(e=>e.type==='image').length,6);
  assert.ok(column.flatMap(b=>b.elements).some(e=>e.src==='/actual-5.jpg'));
  const changed=changeTemplateSectionDesign(blocks,key,'WEAR-STYLING-2COL-01',{product});
  preview=cache.prepare(changed,key,{...catalogs,analysis:structuredClone(analysis)});
  assert.equal(preview('P02-D01'),column);assert.equal(preview('WEAR-STYLING-2COL-01'),two);assert.equal(calls,2);
  const replaced=changed.map(b=>b.id==='actual-5'?{...b,ownImages:['/replacement.jpg']}:b);
  assert.notEqual(cache.prepare(replaced,key,catalogs)('P02-D01'),column);assert.equal(calls,3);
  assert.notEqual(cache.prepare(replaced,key,{...catalogs,analysis:{...analysis,pageStyle:{...analysis.pageStyle,colorMode:'point'}}})('P02-D01'),column);assert.equal(calls,4);
  assert.deepEqual(blocks,before);
});

test('WOC10 tall drawer geometry fits the two controls and preview without outer scroll',()=>{
  const css=readFileSync('src/features/storyboard/StoryboardPanels.module.css','utf8');
  assert.match(css,/\.designDrawer[\s\S]*?\.drawerBody/);
  const panel=readFileSync('src/features/storyboard/StoryboardDesignPanel.module.css','utf8');
  assert.match(panel,/grid-template-columns:\s*minmax\(0,\s*18fr\) minmax\(0,\s*11fr\)/);
  assert.match(panel,/--design-thumb-height:\s*196px/);
  assert.match(panel,/height:\s*calc\(100dvh - 130px\)/);
  assert.match(panel,/overflow-y:\s*auto/);
  assert.doesNotMatch(panel,/word-break:|text-overflow:\s*ellipsis/);
  const available=900-24-2-48-4-24,previewHeight=900-130;
  assert.ok(previewHeight+28<=available);
  assert.ok(28+114+24+28+236+10+44<available);
  const inner=520-2-40,gap=16,left=(inner-gap)*18/29,right=(inner-gap)*11/29;
  assert.ok(Math.abs(left+right+gap-inner)<1e-8); assert.ok(left>280&&right>170);
});

test('WOC10 drawer heading contains only the section title and close action',async()=>{
  const {loadStoryboardComponent}=await import('./helpers/storyboardComponents.mjs');
  const Drawer=await loadStoryboardComponent('StoryboardDrawer.jsx','StoryboardDrawer',{
    useRef:React.useRef,useState:React.useState,useEffect:React.useEffect,createPortal:child=>child,Icon:()=>React.createElement('svg'),
  });
  const previous=Object.getOwnPropertyDescriptor(globalThis,'document');
  Object.defineProperty(globalThis,'document',{configurable:true,value:{body:{}}});
  try{
    const html=renderToStaticMarkup(React.createElement(Drawer,{mode:'design',title:'일상 착용컷 디자인',onClose(){}},React.createElement(Picker,{mode:'design',blocks:daily(),runKey:runOf(daily(),'wear','styling').key,catalogs})));
    assert.equal((html.match(/<h2/g)||[]).length,1);assert.match(html,/<h2>일상 착용컷 디자인<\/h2>/);
    assert.match(html,/aria-label="옆 판 닫기"/);assert.doesNotMatch(html,/섹션 빼기|designCheck|<small>기본/);
  }finally{if(previous)Object.defineProperty(globalThis,'document',previous);else delete globalThis.document;}
});

test('WOC10 actual click and arrow selection update the finished composition, keep cuts and respect busy',async()=>{
  const {hookHarness,loadStoryboardComponent,nodes,textOf}=await import('./helpers/storyboardComponents.mjs');
  const {pageTemplatesCatalog}=await import('../../src/lib/pageTemplates/catalog.js');
  const {sectionDesignAxes}=await import('../../src/lib/pageTemplates/designAxes.js');
  const {sectionDesignChoices,defaultSectionDesignId,currentSectionDesignId}=await import('../../src/lib/pageTemplates/designChoices.js');
  const {sectionDesignPreviewSample}=await import('../../src/lib/pageTemplates/designPreview.js');
  const {templateSectionName}=await import('../../src/lib/pageTemplates/slots.js');
  const {uniqueGenerationCutCount}=await import('../../src/lib/generationCutCount.js');
  const h=hookHarness();
  const Component=await loadStoryboardComponent('StoryboardSectionPicker.jsx','StoryboardSectionPicker',{
    ...h.hooks,pageTemplatesCatalog,sectionRuns,addTemplateSection,changeTemplateSectionDesign,sectionDesignAxes,sectionDesignChoices,
    defaultSectionDesignId,currentSectionDesignId,sectionDesignPreviewSample,templateSectionName,StoryboardPreview:'section-preview',NEW_SECTION_DRAG_MIME:'application/x-section',
  });
  let blocks=daily(6),busy=false,calls=0,focused=false;const initial=structuredClone(blocks),key=runOf(blocks,'wear','styling').key;
  const render=()=>h.render(Component,{mode:'design',blocks,runKey:key,catalogs,busy,onChoose:(_kind,_usage,id)=>{calls++;blocks=changeTemplateSectionDesign(blocks,key,id,{product});}});
  const group=(tree,name)=>nodes(tree).find(n=>n.props?.role==='group'&&n.props['aria-label']===name);
  const buttons=tree=>nodes(tree).filter(n=>n.type==='button');
  const result=tree=>nodes(tree).find(n=>n.props?.label?.endsWith('완성 모습')).props.blocks;
  try{
    let tree=render(),old=result(tree);
    const opts=buttons(group(tree,'배치'));
    opts[0].props.onKeyDown({key:'ArrowRight',preventDefault(){},currentTarget:{parentElement:{querySelectorAll:()=>opts.map((_,i)=>({focus(){focused=i===1;}}))}}});
    tree=render();assert.equal(calls,1);assert.equal(focused,true);assert.notDeepEqual(result(tree),old);
    assert.equal(buttons(group(tree,'배치'))[1].props['aria-pressed'],true);
    buttons(group(tree,'꾸밈')).find(b=>textOf(b)==='액자').props.onClick();tree=render();
    assert.equal(calls,2);assert.ok(result(tree).some(b=>b.elements.some(e=>e.blur===48)));
    const before=structuredClone(blocks);busy=true;tree=render();
    assert.ok(buttons(tree).every(b=>b.props.disabled));
    buttons(group(tree,'꾸밈'))[0].props.onClick();assert.deepEqual(blocks,before);
    assert.equal(uniqueGenerationCutCount(blocks,product),uniqueGenerationCutCount(initial,product));
    assert.deepEqual(blocks.map(b=>b.id),initial.map(b=>b.id));
  }finally{h.close();}
});

test('WOC10 real preview cache distinguishes a free reused opening attachment from its base',async()=>{
  const {addStageAddition,stageAdditions}=await import('../../src/lib/pageTemplates/stageAdditions.js');
  const blocks=board(),run=runOf(blocks,'first'),cache=createCache(),plain=cache.prepare(blocks,run.key,catalogs)('P01-D09');
  const addition=stageAdditions(blocks,run.key,{product,analysis}).find(item=>item.kind==='firstGhost');assert.ok(addition);
  const paired=addStageAddition(blocks,run.key,addition,{product,analysis});
  const page=cache.prepare(paired,run.key,catalogs)('P01-D09');
  assert.notEqual(page,plain);
  assert.equal(page.flatMap(b=>b.elements).filter(e=>e.type==='image').length,plain.flatMap(b=>b.elements).filter(e=>e.type==='image').length+1);
});

test('WOC10 hovering a selected option cannot downgrade its two-pixel selection border',()=>{
  const css=readFileSync('src/features/storyboard/StoryboardDesignPanel.module.css','utf8');
  for(const name of ['layout','thumbnail']){
    const hover=css.match(new RegExp(`\\.${name}:hover([^{}]*)\\{[^}]*box-shadow:[^}]*\\}`));
    assert.ok(hover,`${name} hover style`);
    assert.ok(hover[1].includes(':not([aria-pressed="true"])'),`${name} hover only applies to unselected options`);
    assert.match(css,new RegExp(`\\.${name}\\[aria-pressed="true"\\](?:::after)? \\{[^}]*inset 0 0 0 2px var\\(--fg-1\\)`));
  }
});
