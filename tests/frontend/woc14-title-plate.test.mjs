import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {assemblePage,collectOverlayRegions} from '../../src/lib/pageTemplates/assemble.js';
import {pageTemplatesCatalog as catalog,walkLayout,validateCatalog} from '../../src/lib/pageTemplates/catalog.js';
import {isTitlePlate,hasTitlePlate} from '../../src/lib/pageTemplates/titlePlate.js';
import {fitTitlePlateBlocks} from '../../src/features/editor/editorTitlePlateFit.js';
import {selectionIdsForElement} from '../../src/features/editor/editorSelection.js';
import {hookHarness} from './helpers/storyboardComponents.mjs';
const fixture=JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json','utf8'));
const input=()=>structuredClone(fixture.cases.find(c=>c.name==='emo-2-mono').input);
const key='emo.first:first.hero:center';
function page(position='center',plated=true){const data=input();data.cutResults[0].overlayTextPositions={[key]:position};data.cutResults[0].overlayTextPlates={[key]:plated};return assemblePage(data);}
const first=blocks=>blocks.find(b=>b.pageSectionKey==='emo.first');
test('WOC14 active opening variants remove tagline and hidden original variants retain it',()=>{
 for(const id of ['P01-D09','P01-D09-G']){const nodes=[];walkLayout(catalog.designs[id].layout,n=>nodes.push(n));assert.ok(!nodes.some(n=>n.copyRole==='tagline'));assert.equal(nodes.filter(n=>n.type==='rule').length,2);}
 for(const id of ['P01-D01','P01-D11']){const nodes=[];walkLayout(catalog.designs[id].layout,n=>nodes.push(n));assert.ok(nodes.some(n=>n.copyRole==='tagline'));}
});
for(const position of ['top','center','bottom'])for(const plated of [false,true])test(`WOC14 ${position} plate=${plated} uses ordinary shapes and unchanged ink rules`,()=>{
 const blocks=page(position,plated),els=first(blocks).elements,shapes=els.filter(e=>e.type==='shape'),texts=els.filter(e=>e.type==='text');
 assert.equal(shapes.length,plated?1:2);
 if(plated){const plate=shapes[0];assert.ok(isTitlePlate(plate));assert.equal(plate.opacity,.6);assert.equal(plate.fill,catalog.neutralColors.titlePlate);assert.equal(plate.x+plate.w/2,500);
 for(const el of texts){assert.ok(hasTitlePlate(el,els));assert.equal(el.style.color,catalog.overlayColors.light);assert.ok(el.contrastLines.every(line=>!line.shade));}
 assert.deepEqual(selectionIdsForElement(els,plate),[plate.id]);
 if(position==='bottom')assert.equal(plate.y+plate.h,1150);else assert.equal(plate.y+plate.h/2,625);
 }
});
test('WOC14 text growth shifts following copy and refits plate, while undo/direct resize/deletion stay ordinary',()=>{
 const before=page(),old=first(before),title=old.elements.find(e=>e.copyRole==='name'),plate=old.elements.find(isTitlePlate),color=old.elements.find(e=>e.copyRole==='colors');
 const edited=before.map(b=>b!==old?b:{...b,elements:b.elements.map(e=>e.id===title.id?{...e,text:'길게 바꾼 상품 제목',keepWords:false,h:e.h+100}:e)});
 const fitted=fitTitlePlateBlocks(before,edited),after=first(fitted);
 assert.equal(after.elements.find(isTitlePlate).h,plate.h+100);assert.equal(after.elements.find(e=>e.id===color.id).y,color.y+100);
 assert.equal(old.elements.find(isTitlePlate).h,plate.h);assert.deepEqual(before,page());
 const history=[before];assert.deepEqual(history.pop(),before,'ordinary undo snapshot restores text and plate together');
 const resized=before.map(b=>b!==old?b:{...b,elements:b.elements.map(e=>e.id===plate.id?{...e,h:10}:e)});assert.equal(fitTitlePlateBlocks(before,resized),resized);
 const removed=before.map(b=>b!==old?b:{...b,elements:b.elements.filter(e=>e.id!==plate.id)});assert.equal(fitTitlePlateBlocks(before,removed),removed);assert.equal(hasTitlePlate(title,first(removed).elements),false);
});
test('WOC14 editor state refits functional text patches and undo leaves exact snapshots intact',()=>{
 const source=readFileSync('src/features/editor/Editor.jsx','utf8');assert.match(source,/typeof u === 'function' \? fitTitlePlateBlocks\(prev, source\) : source/);
 assert.match(source,/titlePlate=\{hasTitlePlate\(el, block.elements\)\}/);assert.match(source,/plated: titlePlate/);
 const start=source.indexOf('  const setBlocks = useCallback('),end=source.indexOf('  const [wardrobe,',start);
 let blocks=page();const before=structuredClone(blocks),latestBlocks={current:blocks};
 const scope={useCallback:fn=>fn,setBlocksState:fn=>{blocks=fn(blocks);},latestBlocks,fitTitlePlateBlocks,
   normalizeEditorSelectionGroups:v=>v,expandBlockHeights:v=>v,upgradeLegacyKiwiTemplateBlocks:v=>v,uid:()=>''};
 const update=new Function('scope',`with(scope){${source.slice(start,end)};return setBlocks;}`)(scope);
 update(current=>current.map(b=>b.pageSectionKey!=='emo.first'?b:{...b,elements:b.elements.map(e=>e.copyRole==='name'?{...e,text:'길게 고친 제목',h:e.h+90}:e)}));
 assert.equal(first(blocks).elements.find(isTitlePlate).h,first(before).elements.find(isTitlePlate).h+90);
 const undoStart=source.indexOf('  const undo ='),undoEnd=source.indexOf('  const redo =',undoStart);
 new Function('scope',`with(scope){${source.slice(undoStart,undoEnd)};undo();}`)({croppingRef:{current:null},hist:{current:{past:[before],future:[]}},prevBlocks:{current:blocks},fromHistory:{current:false},clearSel(){},setBlocks:update,toast:{push(){}}});
 assert.deepEqual(blocks,before);

 const h=hookHarness();let calls=0;const hookSrc=readFileSync('src/features/editor/useEditedTemplateContrast.js','utf8').replace(/^import .*;\n/gm,'').replace('export function','function');
 const hook=new Function('useLayoutEffect','useState','measureEditedTemplateContrast','trackEditedContrast',hookSrc+';return useEditedTemplateContrast;')(h.hooks.useEffect,h.hooks.useState,()=>{calls++;return Promise.resolve([])},()=>{});
 const title=first(page()).elements.find(e=>e.copyRole==='name');h.render(()=>hook({...title,keepWords:false,text:'길어진 제목'}, {current:{}},{editing:false,preview:false,plated:true}),{});assert.equal(calls,0);h.close();
});
test('WOC14 JS sampling plans expose the shared clothing bands and padded plate positions',()=>{
 for(const [kind,shots]of Object.entries(catalog.firstTitlePlate.garmentBands))for(const shot of ['full','medium']){
 const value=input();value.product.clothingType=kind;value.storyboard.find(b=>b.pageSectionKind==='first').shot=shot;
 const p=collectOverlayRegions(value).find(p=>p.key===key);assert.deepEqual(p.garmentBand,shots[shot]);assert.equal(p.plateCandidates.length,2);
 }
 const invalid=structuredClone(catalog);invalid.firstTitlePlate.opacity=2;assert.throws(()=>validateCatalog(invalid),/first title plate/);
});


test('WOC14 typing previews plate height immediately, commits measured height, and canceled editing restores geometry',()=>{
 const block=first(page()),element=block.elements.find(e=>e.copyRole==='name'),plate=block.elements.find(isTitlePlate);
 const nodes=block.elements.map(e=>({dataset:{elid:e.id},style:{}}));
 const node=nodes.find(n=>n.dataset.elid===element.id);node.offsetHeight=element.h+80;node.parentElement={querySelectorAll:()=>nodes};
 const source=readFileSync('src/features/editor/editorTitlePlatePreview.js','utf8').replace(/^import .*;\n/gm,'').replace('export function','function');
 const h=hookHarness();const hook=new Function('useLayoutEffect','useRef','fitTitlePlateBlocks','hasTitlePlate',source+';return useTitlePlatePreview;')(h.hooks.useEffect,h.hooks.useRef,fitTitlePlateBlocks,hasTitlePlate);
 const render=editing=>h.render(()=>hook(element,block.elements,{current:node},editing),{});
 try{
  const preview=render(true),size=preview(node,'입력 중 더 길어진 상품 제목');assert.deepEqual(size,{h:element.h+80});
  const background=nodes.find(n=>n.dataset.elid===plate.id);assert.equal(background.style.height,`${plate.h+80}px`);
  const colors=block.elements.find(e=>e.copyRole==='colors');assert.equal(nodes.find(n=>n.dataset.elid===colors.id).style.top,`${colors.y+80}px`);
  assert.equal(node.style.height,'auto');render(false);assert.equal(background.style.height,`${plate.h}px`);
  assert.equal(plate.h,first(page()).elements.find(isTitlePlate).h);
 }finally{h.close();}
});
