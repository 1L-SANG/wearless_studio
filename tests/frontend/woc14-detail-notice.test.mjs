import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {assembleStoryboardPreview,detailPreviewCrop,detailStageGeometry} from '../../src/features/storyboard/storyboardPreview.js';
import {assemblePage} from '../../src/lib/pageTemplates/assemble.js';
import {sectionRuns,visibleSectionRuns} from '../../src/lib/pageTemplates/sectionRuns.js';
let vite,Stage,List,Preview,buildDraft;
const locationDescriptor=Object.getOwnPropertyDescriptor(globalThis,'location');
before(async()=>{
 vite=await createServer({configFile:false,plugins:[react()],logLevel:'silent',optimizeDeps:{noDiscovery:true,include:[]},resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},server:{middlewareMode:true,watch:null,ws:false}});
 ({StoryboardStage:Stage}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));({StoryboardPageList:List}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));({StoryboardPreview:Preview}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));({buildDraft}=await vite.ssrLoadModule('/src/mock/db.js'));
});
after(async()=>{await vite?.close();if(locationDescriptor)Object.defineProperty(globalThis,'location',locationDescriptor);else delete globalThis.location;});
const make=template=>{globalThis.location={search:`?mockTemplate=${template}&mockDetail=full`};const draft=buildDraft();return{...draft,catalogs:{detailProduct:draft.product,analysis:draft.analysis,genExamples:[]}};};
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
for(const template of ['emo','show'])test(`WOC14 ${template} stage, outline and overview show the same planned-detail veil without persisting it`,()=>{
 const data=make(template),saved=structuredClone(data.storyboard),page=assembleStoryboardPreview(data),planned=page.flatMap(b=>b.elements).filter(e=>e.previewDetailLabel);
 assert.equal(planned.length,4);const props={blocks:data.storyboard,page,catalogs:data.catalogs,photoOrdinals:new Map(data.storyboard.map((b,i)=>[b.id,i+1]))};
 for(const kind of ['points','fabric']){
  const run=sectionRuns(data.storyboard).find(r=>r.kind===kind),html=render(Stage,{...props,run,number:3});
  assert.match(html,/AI가 이 부분을 새로 만들어요/);assert.match(html,/예시 이미지를 클릭해서 변경할 수 있어요/);
  for(const el of planned.filter(e=>run.blocks.some(b=>b.id===e.sourceBlockId)))assert.ok(html.includes(`${el.previewDetailLabel} 확대컷`));
  const selected=render(Stage,{...props,run,number:3,selectedId:run.blocks.find(b=>b.source==='ai').id});assert.equal((html.match(/detailVeil/g)||[]).length,(selected.match(/detailVeil/g)||[]).length);
 }
 const outline=render(List,{runs:visibleSectionRuns(data.storyboard),page,sectionRefs:{current:new Map()}}),overview=render(Stage,{...props,overview:true});
 assert.equal((outline.match(/data-detail-veil/g)||[]).length,4);assert.doesNotMatch(outline,/AI가 이 부분을 새로 만들어요/);
 assert.equal((overview.match(/data-detail-veil/g)||[]).length,4);assert.equal((overview.match(/AI가 이 부분을 새로 만들어요/g)||[]).length,4);
 const photos=[...overview.matchAll(/<svg x="[^"]+" y="[^"]+" width="([^"]+)" height="([^"]+)"[^>]*>([\s\S]*?)<\/svg>/g)].filter(match=>match[3].includes('data-detail-notice'));
 assert.equal(photos.length,4);
 for(const photo of photos){
  const width=Number(photo[1]),height=Number(photo[2]);
  for(const line of photo[3].matchAll(/<text[^>]* x="([^"]+)" y="([^"]+)"/g)){
   const x=Number(line[1]),y=Number(line[2]);
   assert.ok(x>=0&&x<width&&y-32>=0&&y<height,'overview text baseline and ascent inside photograph');
  }
 }
 assert.deepEqual(data.storyboard,saved);assert.ok(assemblePage(data).every(b=>b.elements.every(e=>!e.previewDetailLabel&&!e.previewDetailCrop)));
});
test('WOC14 generated result takes priority in both preview assembly and stage with no planned mask',()=>{
 const data=make('emo'),run=sectionRuns(data.storyboard).find(r=>r.kind==='fabric'),card=run.blocks.find(b=>b.source==='ai');
 const cutResults=[{blockId:card.id,imageUrl:'/generated.webp',width:1024,height:1280}],page=assembleStoryboardPreview({...data,cutResults});
 const el=page.flatMap(b=>b.elements).find(e=>e.sourceBlockId===card.id&&e.type==='image');assert.equal(el.src,'/generated.webp');assert.equal(el.previewDetailLabel,undefined);
 const expected=assemblePage({...data,cutResults}).flatMap(b=>b.elements).find(e=>e.sourceBlockId===card.id&&e.type==='image');assert.deepEqual(el,expected);
 const html=render(Stage,{blocks:data.storyboard,run,page,catalogs:data.catalogs,photoOrdinals:new Map()});assert.match(html,/src="\/generated.webp"/);assert.doesNotMatch(html,/AI가 이 부분을 새로 만들어요|detailVeil/);
});
test('WOC14 missing candidate retains a generic notice over the existing whole-source fallback',()=>{
 const data=make('emo'),card=data.storyboard.find(b=>b.detailTargetId);card.detailTargetId='missing';
 const el=assembleStoryboardPreview(data).flatMap(b=>b.elements).find(e=>e.sourceBlockId===card.id&&e.type==='image');assert.equal(el.previewDetailLabel,'디테일');assert.equal(el.previewDetailCrop,undefined);
});
test('WOC14 768px originals may upscale but keep the original-width 30% zoom cap',()=>{
 const crop=detailPreviewCrop(880,1100,{width:768,height:1024,region:{x:.35,y:.2,w:.1,h:.1}});
 assert.equal(crop.iw,Math.round(880/.3));assert.ok(crop.iw>768);assert.ok(crop.ox>0&&crop.oy>0);
 const large=detailPreviewCrop(880,1100,{width:3072,height:4096,region:{x:.35,y:.2,w:.1,h:.1}});assert.deepEqual(large,crop);
 const stage=detailStageGeometry({w:880,h:1100,crop,previewDetailLabel:'목선',previewDetailSource:{width:768,height:1024,region:{x:.35,y:.2,w:.1,h:.1}}});assert.equal(stage.h/stage.w,4/3);assert.equal(stage.crop.iw,Math.round(880/.3));
});
test('WOC14 mask tokens, stable hover and reference label sizes are scoped to the storyboard',()=>{
 const css=readFileSync('src/features/storyboard/StoryboardLayout.module.css','utf8'),tokens=readFileSync('src/styles/tokens.css','utf8');
 for(const value of ['--sb-detail-veil-start: .56','--sb-detail-veil-end: .84','--sb-detail-title-size: 15px','--sb-detail-note-size: 12px'])assert.ok(tokens.includes(value));
 assert.match(css,/detailVeil[^}]*52%/);assert.match(css,/detailNotice[^}]*var\(--sp-12\)[^}]*calc\(var\(--sp-8\) \+ var\(--sp-2\)\)/);
 assert.doesNotMatch(css,/\.detail\w*[^{}]*:(?:hover|focus)[^{]*\{|\.detail\w*[^}]*blur\(/);
 const html=render(Preview,{blocks:[{id:'p',h:400,bg:'white',elements:[{id:'i',type:'image',x:0,y:0,w:300,h:400,src:'/source.webp',previewDetailLabel:'목선'}]}],detailLabels:true});
 assert.match(html,/목선 확대컷/);assert.match(html,/data-detail-veil/);assert.match(html,/52%/);
});

test('R2 extreme source aspect retains the whole region with a same-source backdrop instead of blank gutters',()=>{
 const region={x:.15,y:.15,w:.7,h:.7},crop=detailPreviewCrop(428,428,{width:100,height:150,region});
 assert.ok(crop.iw<428&&crop.ox<0);assert.ok(region.h*crop.ih<=429);
 const el={id:'i',type:'image',x:0,y:0,w:428,h:428,src:'/same-source.webp',crop,previewDetailLabel:'원단'};
 const html=render(Preview,{blocks:[{id:'p',h:428,elements:[el]}],detailLabels:true});
 const images=[...html.matchAll(/<image\b[^>]+>/g)].map(match=>match[0]);assert.equal(images.length,2);
 assert.ok(images.every(image=>image.includes('href="/same-source.webp"')));
 assert.match(images[0],/width="428" height="428"/);assert.ok(images[1].includes(`x="${-crop.ox}"`));
});
