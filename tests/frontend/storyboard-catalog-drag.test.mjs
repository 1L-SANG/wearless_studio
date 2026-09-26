import test from 'node:test';
import assert from 'node:assert/strict';
import * as drag from '../../src/lib/storyboardCatalogDrag.js';
const members = ['a','b'].map((id, i) => ({exampleId:id,order:i+1,cutType:'horizon',shot:'full',direction:i?'back':'front'}));
const set = {id:'test-set',setType:'horizon-rotation',gender:'women',applicableClothingTypes:['top'],members};
const catalog = members.map(m => ({...m,id:m.exampleId,spaceSetId:set.id,gender:'women',applicableClothingTypes:['top'],variants:['all','pose']}));
const context = {catalog,gender:'women',clothingType:'top',sectionRole:'studio'};
test('valid whole and individual selections resolve only from supplied eligible catalog', () => {
 assert.equal(typeof drag.resolveCatalogDrag,'function');
 assert.equal(drag.resolveCatalogDrag({setId:set.id,exampleId:null},[set],context).set,set);
 assert.equal(drag.resolveCatalogDrag({setId:set.id,exampleId:'b'},[set],context).member.exampleId,'b');
});
test('invalid context, stale members, locked targets and nested group drops are rejected', () => {
 assert.equal(typeof drag.resolveCatalogDrag,'function');
 for (const changes of [{gender:'men'},{clothingType:'bottom'},{sectionRole:'styling'},{catalog:catalog.slice(0,1)},{locked:true},{canAdd:false},{targetSpaceGroupId:'existing'}]) {
  assert.equal(drag.resolveCatalogDrag({setId:set.id,exampleId:null},[set],{...context,...changes}),null);
 }
 for(const payload of [null,{},'{bad}',{setId:'missing'},{setId:set.id,exampleId:'missing'}]) assert.equal(drag.resolveCatalogDrag(payload,[set],context),null);
});
test('insertion keeps adjacent group and row records intact, refuses splitting their interior', () => {
 assert.equal(typeof drag.insertCatalogBlocks,'function');
 const blocks=[{id:'a',spaceGroupId:'g'},{id:'b',spaceGroupId:'g'},{id:'c',layoutRowId:'r'},{id:'d',layoutRowId:'r'}];
 const added=[{id:'new'}];
 const result=drag.insertCatalogBlocks(blocks,2,added);
 assert.deepEqual(result.map(x=>x.id),['a','b','new','c','d']);
 assert.equal(result[0],blocks[0]); assert.equal(result[3],blocks[2]);
 assert.throws(()=>drag.insertCatalogBlocks(blocks,1,added),/bundle/);
 assert.throws(()=>drag.insertCatalogBlocks(blocks,3,added),/bundle/);
 assert.equal(blocks.length,4);
});

test('complete-only horizon members can be independently added with all scope and no group binding', () => {
 const allOnly = catalog.map(example => ({ ...example, variants: ['all'], thumb: `/${example.id}.webp` }));
 const selected = drag.resolveCatalogDrag({setId:set.id,exampleId:'a'},[set],{...context,catalog:allOnly});
 assert.equal(selected.member.exampleId,'a');
 const result = drag.createIndependentCatalogMembers(set,['a'],{spaceGroupId:'old',spaceVariation:'fixed',horizonBackgroundMode:'garment-tone',layoutRowId:'row',hookFrameId:'hook'}, {...context,catalog:allOnly,makeId:()=> 'new'});
 assert.equal(result[0].refScope,'all');assert.equal(result[0].spaceGroupId,undefined);assert.equal(result[0].horizonBackgroundMode,undefined);assert.equal(result[0].hookFrameId,undefined);assert.equal(result[0].layoutRowId,undefined);
});
test('single catalog cut lands at the end of its section, or at the picker slot when the section is empty', () => {
 const board = [
  {id:'h1',sectionId:'hook',sectionRole:'hooking'},{id:'h2',sectionId:'hook',sectionRole:'hooking'},
  {id:'s1',sectionId:'sty',sectionRole:'styling'},{id:'p1',sectionId:'prod',sectionRole:'product'},
 ];
 assert.equal(drag.catalogMemberInsertIndex(board,'sty','styling',99),3);
 // 빈 스튜디오 섹션: 갤러리를 연 자리(스타일링 뒤)에 넣는다. 예전에는 0 이라 후킹 앞에 들어갔다.
 assert.equal(drag.catalogMemberInsertIndex(board,'empty:studio','studio',3),3);
 assert.equal(drag.catalogMemberInsertIndex(board,'empty:studio','studio',undefined),board.length);
});
