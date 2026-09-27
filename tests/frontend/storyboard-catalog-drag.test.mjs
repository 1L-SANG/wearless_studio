import test from 'node:test';
import assert from 'node:assert/strict';
import * as drag from '../../src/lib/storyboardCatalogDrag.js';
import { adoptSection } from '../../src/lib/sections.js';
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
test('single catalog cuts keep one section: first click in an empty section, then more clicks', () => {
 // 실제 삽입 경로(insertCatalogBlocks + adoptSection)로 두 번 연속 누른다. 갤러리는 열린 채라
 // targetSid 는 계속 'empty:studio' 다. 예전에는 첫 컷이 후킹 앞에, 두 번째 컷부터 섹션이 둘로 쪼개졌다.
 let board = [
  {id:'h',sectionId:'hook',sectionRole:'hooking'},{id:'s',sectionId:'sty',sectionRole:'styling'},{id:'p',sectionId:'prod',sectionRole:'product'},
 ];
 const picker = {targetSid:'empty:studio',targetRole:'studio',fallbackIndex:2};
 for (const id of ['n1','n2']) {
  const target = drag.catalogMemberTarget(board, picker);
  board = adoptSection(drag.insertCatalogBlocks(board, target.index, [{id}]), [id], target.sectionId, target.sectionRole);
 }
 assert.deepEqual(board.map(b => b.id), ['h','s','n1','n2','p']);
 assert.equal(new Set(board.filter(b => b.sectionRole === 'studio').map(b => b.sectionId)).size, 1);
 // 비어 있지 않은 섹션은 끝에, 교체 모드(targetSid 없음)는 세트가 있는 섹션 끝에 붙인다.
 assert.equal(drag.catalogMemberTarget(board, {targetSid:'sty',targetRole:'styling',fallbackIndex:9}).index, 2);
 const grouped = board.map(b => b.id === 'n1' ? {...b, spaceGroupId:'g'} : b);
 assert.deepEqual(drag.catalogMemberTarget(grouped, {spaceGroupId:'g'}), {sectionId:grouped[2].sectionId, sectionRole:'studio', index:4});
});
