import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage, estimateTextHeight } from '../../src/lib/pageTemplates/assemble.js';
import { decorateGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';
import { fitTitlePlateBlocks } from '../../src/features/editor/editorTitlePlateFit.js';
import { editedTextElement } from '../../src/features/editor/editorTextEdit.js';
import { getBlockRenderHeight, getBlockExportElements } from '../../src/features/editor/editorGeometry.js';
import { resolveTextSpacing } from '../../src/features/editor/textSpacing.js';
import { CAPTION_GAP, CAPTION_STYLE, captionHeight } from '../../src/lib/editorTextSlots.js';

const fixture=JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json'));
const input=name=>structuredClone(fixture.cases.find(c=>c.name===name).input);
const legacyRecord=JSON.parse(readFileSync('tests/fixtures/page_copy_b4_legacy_wait.json'));
const legacyCode=legacyRecord.source.replace(/^import[\s\S]*?from ['"][^'"]+['"];\n/gm,'').replace(/^export /gm,'');
const bindings={CAPTION_GAP,CAPTION_STYLE,captionHeight,editedTextElement,getBlockRenderHeight,resolveTextSpacing,estimateTextHeight};
const legacy=new Function(...Object.keys(bindings),legacyCode+';return {decorateGenBlocks,mergeServerBlocks,patchTemplateElement};')(...Object.values(bindings));
const job={cuts:{},copy:{},status:'running'};
function points(template='emo', descriptions=[]) {
 const v=input(`${template}-2-mono`);
 v.storyboard=v.storyboard.filter(b=>b.pageSectionKind==='points');
 v.analysis.partDescriptionCopy=descriptions;v.analysis.featureCopy=[];
 v.copyResults=[{blockId:v.storyboard[0].id,texts:[]}];
 return v;
}
for(const template of ['emo','show']) for(const partial of [false,true]) test(`${template} copy failure keeps every photo/name and hides only empty detail descriptions; partial=${partial}`,()=>{
 const v=points(template,partial?[{point:'소매 단',desc:'소매 끝에 골지가 있어요'}]:[]);
 const blocks=assemblePage(v),els=blocks.flatMap(b=>b.elements);
 assert.deepEqual(els.filter(e=>e.type==='image').map(e=>e.sourceBlockId),[`${template}.points.detail1`,`${template}.points.detail2`]);
 assert.deepEqual(els.filter(e=>e.copyRole?.endsWith('.name')).map(e=>e.copyRole),['items.0.name','items.1.name']);
 const missing=els.find(e=>e.copyRole==='items.0.desc');assert.ok(missing.hidden);
 assert.ok(!blocks.flatMap(getBlockExportElements).some(e=>e.id===missing.id));
 if(partial)assert.equal(els.find(e=>e.copyRole==='items.1.desc').text,'소매 끝에 골지가 있어요');
});

test('main 1ba711f7 completion merge cannot move second detail description onto first photo after header edit',()=>{
 const v=points(),pending=legacy.decorateGenBlocks(assemblePage({...v,copyResults:[]}),job);
 const header=pending.flatMap(b=>b.elements).find(e=>e.copyRole==='title'||e.copyRole==='label');
 const local=pending.map(b=>b.elements.some(e=>e.id===header.id)?legacy.patchTemplateElement(b,header.id,{text:'셀러 제목'}):b);
 const completed=assemblePage(points('emo',[{point:'소매 단',desc:'소매에만 붙는 설명이에요'}]));
 const merged=legacy.mergeServerBlocks(local,completed);
 const photo1=merged.find(b=>b.elements.some(e=>e.sourceBlockId==='emo.points.detail1'&&e.type==='image'));
 const photo2=merged.find(b=>b.elements.some(e=>e.sourceBlockId==='emo.points.detail2'&&e.type==='image'));
 assert.equal(photo1.elements.find(e=>e.copyRole==='items.0.desc').text,'');
 assert.equal(photo2.elements.find(e=>e.copyRole==='items.1.desc').text,'소매에만 붙는 설명이에요');
 assert.ok(merged.flatMap(b=>b.elements).some(e=>e.text==='셀러 제목'));
});

function intro(body='',title='') {
 return assemblePage({storyboard:[{id:'intro',taxonomyVersion:4,source:'text',sectionId:'intro',pageSectionKey:'emo.intro',pageSectionKind:'intro',pageDesignId:'T01-D07',slotKey:null}],
 product:{name:'셔츠',colors:[]},analysis:{},copyResults:[{blockId:'intro',texts:[{role:'title',text:title},{role:'body',text:body}]}]}).filter(b=>b.pageSectionKind==='intro');
}
const introBodies=blocks=>blocks.flatMap(b=>b.elements).filter(e=>e.copyRole==='body');
test('edited waiting intro title survives with all incoming AI paragraphs and panels',()=>{
 const pending=decorateGenBlocks(intro(),job),block=pending[0],heading=block.elements.find(e=>e.copyRole==='title');
 const local=[patchTemplateElement(block,heading.id,{text:'셀러가 고친 제목'})];
 const final=intro('첫 문단이에요\n둘째 문단이에요\n셋째 문단이에요','AI 제목');
 const merged=fitTitlePlateBlocks(local,mergeServerBlocks(local,final));
 assert.ok(introBodies(merged).every(e=>e.x===201), 'outer editor fitting must not apply the avatar indent twice');
 assert.deepEqual(introBodies(merged).map(e=>e.text),['첫 문단이에요','둘째 문단이에요','셋째 문단이에요']);
 assert.equal(merged[0].elements.find(e=>e.copyRole==='title').text,'셀러가 고친 제목');
 assert.equal(new Set(merged[0].elements.map(e=>e.id)).size,merged[0].elements.length);
 for(const body of introBodies(merged))assert.ok(merged[0].elements.some(e=>e.type==='shape'&&e.groupId===body.groupId));
 assert.ok(merged[0].h>=Math.max(...merged[0].elements.filter(e=>!e.hidden).map(e=>e.y+e.h)));
});
for(const text of ['셀러가 직접 쓴 본문','']) test(`directly edited waiting intro body wins over late paragraphs: ${JSON.stringify(text)}`,()=>{
 const pending=decorateGenBlocks(intro('원래 AI 본문'),job),block=pending[0],body=introBodies(pending)[0];
 const local=[patchTemplateElement(block,body.id,{text})];
 const merged=mergeServerBlocks(local,intro('새 첫 문단\n새 둘째 문단','새 제목'));
 assert.deepEqual(introBodies(merged).map(e=>e.text),[text]);
 assert.ok(!introBodies(merged)[0].aiDraft);
});

for(const template of ['emo','show']) test(`${template} ready-empty MD uses seller text after promotion cleaning`,()=>{
 const v={storyboard:[{id:'intro',taxonomyVersion:4,source:'text',sectionId:'intro',pageSectionKey:`${template}.intro`,pageSectionKind:'intro',pageDesignId:template==='emo'?'T01-D07':'T06-D01',slotKey:null}],
 product:{name:'셔츠'},analysis:{mdComment:'구김이 안 가요.\n무료 배송이에요.\n가격은 3만원이에요.'},copyResults:[{blockId:'intro',texts:[]}]};
 assert.deepEqual(introBodies(assemblePage(v)).map(e=>e.text),['구김이 안 가요.']);
});

test('waiting intro preserves an added independent rectangle and template identities',()=>{
 const waiting=decorateGenBlocks(intro(),job),block=waiting[0];
 const rect={id:'seller-rect',type:'shape',shape:'rect',x:10,y:10,w:100,h:100,fill:'#ff0000'};
 const local=[{...block,elements:[rect,...block.elements]}];
 const final=intro('첫 문단이에요\n둘째 문단이에요','소개 제목');
 const merged=mergeServerBlocks(local,final)[0];
 assert.deepEqual(merged.elements.find(e=>e.id===rect.id),rect);
 assert.equal(merged.elements[0].id,rect.id);
 for(const old of block.elements.filter(e=>e.type==='shape')) {
  const next=merged.elements.find(e=>e.id===old.id);
  assert.equal(next.groupId,old.groupId);
 }
 assert.deepEqual(introBodies([merged]).map(e=>e.text),['첫 문단이에요','둘째 문단이에요']);
});

for(const written of ['', '숨기기로 한 제목']) test(`waiting intro preserves an explicitly hidden seller title: ${JSON.stringify(written)}`,()=>{
 const waiting=decorateGenBlocks(intro(),job),block=waiting[0],heading=block.elements.find(e=>e.copyRole==='title');
 const edited=patchTemplateElement(block,heading.id,{text:written});
 const hidden=patchTemplateElement(edited,heading.id,{hidden:true});
 const merged=mergeServerBlocks([hidden],intro('첫 문단이에요\n둘째 문단이에요','AI 제목'));
 const title=merged[0].elements.find(e=>e.copyRole==='title');
 assert.equal(title.text,written || 'AI 제목');assert.equal(title.hidden,true);
 assert.equal(introBodies(merged).length,2);
});
