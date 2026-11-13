import test from 'node:test';
import assert from 'node:assert/strict';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { ensureSections } from '../../src/lib/sections.js';
import { normalizedRecipePatch } from '../../src/lib/storyboardTaxonomy.js';
const board=()=>seedStoryboard({colors:[{id:'base',isBase:true}]},{pageStyle:defaultPageStyle()});
test('v3 input is rejected instead of converted or sorted',()=>assert.throws(()=>ensureSections([{taxonomyVersion:3}]),/storyboard_outdated/));
test('section membership and page order survive recipe normalization',()=>{
 const b=board(), first=b[0]; const changed=b.map((v,i)=>i===0?{...v,cutType:'product',shot:'detail',exampleId:'stale',baseThumb:'/before',thumb:'/after'}:v);
 const next=ensureSections(changed);assert.equal(next[0].contentRole,'hero');assert.equal(next[0].cutType,'styling');assert.equal(next[0].exampleId,null);assert.equal(next[0].thumb,'/before');
 assert.deepEqual(next.map(v=>v.pageSectionKey),b.map(v=>v.pageSectionKey));assert.equal(next[0].sectionId,first.sectionId);
});
test('text cards have null generation roles and mine cards use custom',()=>{
 const b=board(), text=b.find(b=>b.source==='text');assert.equal(normalizedRecipePatch(text).cutType,null);
 const mine=normalizedRecipePatch({source:'mine',cutType:'horizon'},'fit');assert.equal(mine.contentRole,'custom');assert.equal(mine.cutType,null);
});
test('unbound manual cards infer their purpose from shooting recipes',()=>{
 for(const [cutType,shot,role] of [['styling','full','coordination'],['horizon','medium','fit'],['product','ghost','productOverview'],['product','detail','detail']]){
  assert.equal(normalizedRecipePatch({source:'ai',cutType,shot},null).contentRole,role);
 }
});
