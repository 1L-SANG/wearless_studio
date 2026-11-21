import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {assemblePage} from '../../src/lib/pageTemplates/assemble.js';
import {cleanSellerText} from '../../src/lib/pageTemplates/sellerCopy.js';
import {decorateGenBlocks, mergeServerBlocks} from '../../src/lib/editorWaitSkeleton.js';
import {createFeatureRun, mergeFeaturePoints, stripFeatureClientFields} from '../../src/lib/featureRun.js';
import {mergeAnalysisPatch} from '../../src/lib/analysisPatch.js';

const samples = [
 ['핏이 편해요. 2만 9천 원짜리예요.', '핏이 편해요.'],
 ['핏이 편해요.무료 배송이에요.', '핏이 편해요.'],
 ['스티치 간격이 1.5cm예요.무료 배송이에요.', '스티치 간격이 1.5cm예요.'],
 ['핏이 편해요!무료 배송이에요.소매가 길어요.', '핏이 편해요! 소매가 길어요.'],
 ['핏이 편해요. https://shop.example.com에서 주문해요.', '핏이 편해요.'],
];
test('B6 normal chip saves and fast core/refinement retain aiSuggestedPoints in the saved payload', async () => {
 const initial={sellingPoints:[],aiSuggestedPoints:['구김이 안 가요']};
 const normal=mergeAnalysisPatch(initial,{sellingPoints:[...initial.aiSuggestedPoints]});
 assert.deepEqual(normal.aiSuggestedPoints,initial.aiSuggestedPoints);
 let saved={};
 const run=createFeatureRun({readAnalysis:()=>saved,saveAnalysis:async patch=>{saved={...saved,...patch};return true;},
  requestFeatures:()=>assert.fail('No provider request expected'),requestMerge:()=>assert.fail('No merge request expected')});
 run.prepare({runId:'b6',startedAt:Date.now()});
 await run.receiveCore(initial);
 assert.deepEqual(saved.sellingPoints,initial.aiSuggestedPoints);
 assert.deepEqual(stripFeatureClientFields(saved).aiSuggestedPoints,initial.aiSuggestedPoints);
 const refined=mergeFeaturePoints({...saved,early:initial.aiSuggestedPoints,late:['구김이 잘 안 가요'],
  decisions:[{action:'refine',early:0,late:0}]});
 assert.deepEqual(refined.sellingPoints,['구김이 잘 안 가요']);
 assert.deepEqual(stripFeatureClientFields(refined).aiSuggestedPoints,['구김이 잘 안 가요']);
});
for (const [source, expected] of samples) test(`B6 MD cleanup: ${source}`, () => {
 assert.equal(cleanSellerText(source), expected);
});
test('B6 adjacent sentences and decimals agree in both cleanup implementations', () => {
 const result=spawnSync('server/.venv/bin/python',['-c',
  'import sys,json;from app.agents.page_copy_checks import clean_seller_text;print(json.dumps([clean_seller_text(s) for s in json.load(sys.stdin)],ensure_ascii=False))'],
  {input:JSON.stringify(samples.map(([source])=>source)),encoding:'utf8',env:{...process.env,PYTHONPATH:'server',PYTHONDONTWRITEBYTECODE:'1'}});
 assert.equal(result.status,0,result.stderr);
 assert.deepEqual(JSON.parse(result.stdout),samples.map(([,expected])=>expected));
});

function intro(body='') {
 return assemblePage({storyboard:[{id:'intro',taxonomyVersion:4,source:'text',sectionId:'intro',
  pageSectionKey:'emo.intro',pageSectionKind:'intro',pageDesignId:'T01-D07',slotKey:null}],
  product:{name:'셔츠'},analysis:{},copyResults:[{blockId:'intro',texts:[{role:'title',text:'소개 제목'},{role:'body',text:body}]}]})
  .filter(block=>block.pageSectionKind==='intro');
}
for (const rearrangement of ['panel-above-body','reverse-existing']) test(`B6 intro preserves seller layer order: ${rearrangement}`,()=>{
 const waiting=decorateGenBlocks(intro(),{cuts:{},copy:{},status:'running'});
 const local=structuredClone(waiting[0]);
 const body=local.elements.find(el=>el.copyRole==='body');
 const panel=local.elements.find(el=>el.type==='shape'&&el.groupId===body.groupId);
 if(rearrangement==='reverse-existing')local.elements.reverse();
 else {
  local.elements=local.elements.filter(el=>el.id!==panel.id);
  local.elements.splice(local.elements.findIndex(el=>el.id===body.id)+1,0,panel);
 }
 const server=intro('첫 문단이에요\n둘째 문단이에요\n셋째 문단이에요');
 const merged=mergeServerBlocks([local],server)[0];
 const oldIds=new Set(local.elements.map(el=>el.id));
 assert.deepEqual(merged.elements.filter(el=>oldIds.has(el.id)).map(el=>el.id),local.elements.map(el=>el.id));
 const newGroups=new Set(server[0].elements.filter(el=>el.copyRole==='body'&&el.paragraphIndex>0).map(el=>el.groupId));
 const identity=el=>[el.groupId,el.type,el.copyRole||null];
 assert.deepEqual(merged.elements.filter(el=>newGroups.has(el.groupId)).map(identity),
  server[0].elements.filter(el=>newGroups.has(el.groupId)).map(identity));
 assert.equal(merged.elements.filter(el=>el.copyRole==='body').length,3);
});
