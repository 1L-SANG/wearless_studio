import test from 'node:test';
import assert from 'node:assert/strict';
import {assemblePage} from '../../src/lib/pageTemplates/assemble.js';
import {decorateGenBlocks,mergeServerBlocks,patchTemplateElement} from '../../src/lib/editorWaitSkeleton.js';
import {fitTitlePlateBlocks} from '../../src/features/editor/editorTitlePlateFit.js';
import {getBlockExportElements} from '../../src/features/editor/editorGeometry.js';

const job={cuts:{},copy:{},status:'running'};
function value(template='emo',seller='',body='',title='') {
 return {storyboard:[{id:'intro',taxonomyVersion:4,source:'text',sectionId:'intro',pageSectionKey:`${template}.intro`,pageSectionKind:'intro',pageDesignId:template==='emo'?'T01-D07':'T06-D01',slotKey:null}],
 product:{name:'셔츠'},analysis:{mdComment:seller},copyResults:[{blockId:'intro',texts:[{role:'title',text:title},{role:'body',text:body}]}]};
}
const intro=v=>assemblePage(v).filter(b=>b.pageSectionKind==='intro');
const bodies=b=>b.elements.filter(e=>e.copyRole==='body');
const panels=b=>bodies(b).sort((a,b)=>a.paragraphIndex-b.paragraphIndex).map(body=>b.elements.find(e=>e.type==='shape'&&e.groupId===body.groupId));
for(const template of ['emo','show']) for(const state of ['absent','empty','disabled']) test(`${template} ${state} copy displays only safe seller sentences`,()=>{
 const v=value(template,'핏이 편해요. 무료 배송이에요.\n오늘만 2만 9천 원이에요\n29,000원');
 if(state==='absent')v.copyResults=[];
 if(state==='empty')v.copyResults[0].texts=[];
 if(state==='disabled')v.copywriting=false;
 assert.deepEqual(bodies(intro(v)[0]).map(e=>e.text),['핏이 편해요.']);
});
for(const separator of ['\u2028','\u2029','\r\n']) test(`seller pasted separator ${JSON.stringify(separator)} retains non-promotion line`,()=>{
 const v=value('emo',`핏이 편해요${separator}무료 배송이에요.`);v.copyResults=[];
 assert.deepEqual(bodies(intro(v)[0]).map(e=>e.text),['핏이 편해요']);
});
for(const h of [189,252]) test(`three-line edited intro title moves every server paragraph by final height delta ${h}`,()=>{
 const pending=decorateGenBlocks(intro(value()),job),head=pending[0].elements.find(e=>e.copyRole==='title');
 const edited=fitTitlePlateBlocks(pending,[patchTemplateElement(pending[0],head.id,{text:'첫 번째 제목\n두 번째 제목\n세 번째 제목',h})]);
 const final=intro(value('emo','','첫 문단이에요\n둘째 문단이에요\n셋째 문단이에요','AI 제목'));
 const merged=fitTitlePlateBlocks(edited,mergeServerBlocks(edited,final))[0];
 const ps=panels(merged);
 assert.equal(ps.length,3);
 for(let i=1;i<ps.length;i++)assert.equal(ps[i].y-ps[i-1].y-ps[i-1].h,12);
 for(const body of bodies(merged)) {
  const panel=merged.elements.find(e=>e.type==='shape'&&e.groupId===body.groupId);
  assert.ok(body.y+body.h<=panel.y+panel.h);
 }
 assert.equal(merged.elements.find(e=>e.copyRole==='title').text,'첫 번째 제목\n두 번째 제목\n세 번째 제목');
});

test('hidden single waiting body stays hidden across all three arriving AI paragraphs',()=>{
 const pending=decorateGenBlocks(intro(value()),job),body=bodies(pending[0])[0];
 const hidden=[patchTemplateElement(pending[0],body.id,{hidden:true})];
 const merged=fitTitlePlateBlocks(hidden,mergeServerBlocks(hidden,intro(value('emo','','첫 문단이에요\n둘째 문단이에요\n셋째 문단이에요','AI 제목'))))[0];
 assert.equal(bodies(merged).length,3);
 assert.ok(bodies(merged).every(e=>e.hidden));
 assert.ok(!getBlockExportElements(merged).some(e=>e.copyRole==='body'));
});

test('stored promotional detail labels fall back to the original part name',async()=>{
 const {readFileSync}=await import('node:fs');
 const f=JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json'));
 const v=structuredClone(f.cases.find(c=>c.name==='emo-2-mono').input);
 v.storyboard=v.storyboard.filter(b=>b.pageSectionKind==='points');
 v.analysis.partDescriptionCopy=[{point:'카라',desc:'카라 설명이에요',label:'무료 배송'}];
 const es=assemblePage(v).flatMap(b=>b.elements);
 assert.equal(es.find(e=>e.copyRole==='items.0.name').text,'카라');
});
