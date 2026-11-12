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
let vite, Picker;
before(async () => {
  vite = await createServer({ configFile:false,plugins:[react()],logLevel:'silent',optimizeDeps:{noDiscovery:true,include:[]},
    resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},server:{middlewareMode:true,watch:null,ws:false},appType:'custom' });
  ({ StoryboardSectionPicker:Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
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

test('WOC10 layout glyphs stay separate from the WOC12 fixed decoration illustrations',()=>{
  const blocks=daily(),html=render(blocks);
  assert.equal((html.match(/data-layout-glyph=/g)||[]).length,3);
  assert.match(html,/aria-label="배치"/); assert.match(html,/사진을 놓는 모양/); assert.match(html,/고른 배치에 입혀요/);
  assert.doesNotMatch(html,/완성 모습|actual-5.jpg/);
  assert.match(html,/data-design-illustration="wear-frame"/);
  assert.doesNotMatch(html,/previewWirePhoto|previewWireBackdrop|designCheck|<small>기본<\/small>|섹션 빼기/);
  assert.doesNotMatch(html,/\sstyle=/);
});

test('WOC10 colors only show two layout glyphs; singleton design stages are hidden',()=>{
  const blocks=board(),html=render(blocks,runOf(blocks,'color_product'));
  assert.equal((html.match(/data-layout-glyph=/g)||[]).length,2);
  assert.match(html,/색별 이어 보기/); assert.match(html,/색 격자/);
  assert.doesNotMatch(html,/data-design-thumbnail=|aria-label="디자인 선택"/);
  let withReview=blocks;
  if(!runOf(withReview,'review')) withReview=addTemplateSection(blocks,product,analysis,'review');
  const single=render(withReview,runOf(withReview,'review'));
  assert.doesNotMatch(single,/aria-label="디자인 선택"|data-design-thumbnail=/);
  assert.doesNotMatch(single,/완성 모습/);
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

test('WOC10 click and arrow selection still save designs, change the assembled page and preserve cuts',async()=>{
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
  const result=()=>assembleStoryboardPreview({storyboard:blocks,product,analysis,catalogs}).filter(b=>b.pageSectionKey===key);
  try{
    let tree=render(),old=result(tree);
    const opts=buttons(group(tree,'배치'));
    opts[0].props.onKeyDown({key:'ArrowRight',preventDefault(){},currentTarget:{parentElement:{querySelectorAll:()=>opts.map((_,i)=>({focus(){focused=i===1;}}))}}});
    tree=render();assert.equal(calls,1);assert.equal(focused,true);assert.notDeepEqual(result(tree),old);
    assert.equal(buttons(group(tree,'배치'))[1].props['aria-pressed'],true);
    buttons(group(tree,'디자인 선택')).find(b=>b.props.title==='액자').props.onClick();tree=render();
    assert.equal(calls,2);assert.ok(result(tree).some(b=>b.elements.some(e=>e.blur===48)));
    const before=structuredClone(blocks);busy=true;tree=render();
    assert.ok(buttons(tree).every(b=>b.props.disabled));
    buttons(group(tree,'디자인 선택'))[0].props.onClick();assert.deepEqual(blocks,before);
    assert.equal(uniqueGenerationCutCount(blocks,product),uniqueGenerationCutCount(initial,product));
    assert.deepEqual(blocks.map(b=>b.id),initial.map(b=>b.id));
  }finally{h.close();}
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
