import test from 'node:test';
import assert from 'node:assert/strict';
import * as drag from '../../src/lib/storyboardCatalogDrag.js';
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES, spaceSetGroupId, spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
const members = ['a','b'].map((id, i) => ({exampleId:id,order:i+1,cutType:'horizon',shot:'full',direction:i?'back':'front'}));
const set = {id:'test-set',setType:'horizon-rotation',gender:'women',applicableClothingTypes:['top'],members};
const catalog = members.map(m => ({...m,id:m.exampleId,spaceSetId:set.id,gender:'women',applicableClothingTypes:['top'],variants:['all','pose']}));
const context = {catalog,gender:'women',clothingType:'top',sectionRole:'studio'};
test('a new independent catalog cut does not inherit a follow-up link', () => {
 const [created] = drag.createIndependentCatalogMembers(set, ['a'], {
  similarSourceBlockId: 'original', colorId: 'navy',
 }, {...context, makeId: () => 'new'});
 assert.equal(created.similarSourceBlockId, undefined);
 assert.equal(created.exampleId, 'a');
 assert.equal(created.colorId, 'navy');
});
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

test('complete-only horizon members can be independently added as a fresh singleton of the supplied set', () => {
 const allOnly = catalog.map(example => ({ ...example, variants: ['all'], thumb: `/${example.id}.webp` }));
 const selected = drag.resolveCatalogDrag({setId:set.id,exampleId:'a'},[set],{...context,catalog:allOnly});
 assert.equal(selected.member.exampleId,'a');
 const result = drag.createIndependentCatalogMembers(set,['a'],{spaceGroupId:'old',spaceVariation:'fixed',horizonBackgroundMode:'garment-tone',layoutRowId:'row',hookFrameId:'hook'}, {...context,catalog:allOnly,makeId:()=> 'new'});
 assert.equal(result[0].refScope,'all');assert.match(result[0].spaceGroupId,/^ssg1__test-set__/);
 assert.notEqual(result[0].spaceGroupId,'old');assert.equal(result[0].spaceSetMemberOrder,1);
 assert.equal(result[0].spaceVariation,'subtle');assert.equal(result[0].setSelectionOrigin,'user');
 assert.equal(result[0].horizonBackgroundMode,'reference');assert.equal(result[0].hookFrameId,undefined);assert.equal(result[0].layoutRowId,undefined);
});

test('D204 every real published leftover member retains its authoritative singleton contract', () => {
 const examples=new Map(STORYBOARD_SPACE_SET_EXAMPLES.map(example=>[example.id,example]));
 let checked=0;
 for(const released of STORYBOARD_SPACE_SETS) for(const member of released.members) {
  const previousGroup=spaceSetGroupId(released.id,'old-instance');
  const template={spaceGroupId:previousGroup,spaceVariation:'fixed',horizonBackgroundMode:'garment-tone',
   similarSourceBlockId:'old-source',ownImages:['seller:old'],refImages:['mood:old'],refAssetIds:['mood-id'],colorId:'seller-color'};
  const snapshot=JSON.stringify(template);
  const ctx={catalog:[examples.get(member.exampleId)],gender:released.gender,
   clothingType:released.applicableClothingTypes[0],sectionRole:released.setType==='styling'?'styling':'studio',makeId:()=>`leftover-${checked}`};
  const [created]=drag.createIndependentCatalogMembers(released,[member.exampleId],template,ctx);
  const [second]=drag.createIndependentCatalogMembers(released,[member.exampleId],template,ctx);
  assert.equal(spaceSetIdFromGroupId(created.spaceGroupId),released.id,member.exampleId);
  assert.notEqual(created.spaceGroupId,previousGroup);assert.notEqual(created.spaceGroupId,second.spaceGroupId);
  assert.equal(created.spaceSetMemberOrder,member.order);assert.equal(created.spaceVariation,released.spaceVariation);
  assert.equal(created.setSelectionOrigin,'user');assert.equal(created.exampleSelectionOrigin,'user');
  assert.equal(created.refScope,member.cutType==='styling'?'pose':'all');
  assert.equal(created.horizonBackgroundMode,member.cutType==='horizon'?'reference':null);
  assert.equal(created.exampleId,member.exampleId);assert.equal(created.direction,member.direction);assert.equal(created.shot,member.shot);
  assert.equal(created.colorId,'seller-color');assert.equal(created.similarSourceBlockId,undefined);assert.equal(created.ownImages,undefined);
  assert.deepEqual(created.refImages,[]);assert.deepEqual(created.refAssetIds,[]);
  assert.equal(JSON.stringify(template),snapshot);
  checked++;
 }
 assert.ok(checked>0);
});

test('D204 a supplied synthetic styling catalog retains pose scope without inventing missing example assets', () => {
 const supplied={...set,id:'provided-styling-set',setType:'styling',spaceVariation:'fixed',
  members:members.map(member=>({...member,cutType:'styling'}))};
 const suppliedCatalog=catalog.map(example=>({...example,cutType:'styling',spaceSetId:supplied.id}));
 const [created]=drag.createIndependentCatalogMembers(supplied,['b'],{},
  {...context,sectionRole:'styling',catalog:suppliedCatalog,makeId:()=> 'provided'});
 assert.match(created.spaceGroupId,/^ssg1__provided-styling-set__/);assert.equal(created.spaceVariation,'fixed');
 assert.equal(created.spaceSetMemberOrder,2);assert.equal(created.refScope,'pose');
 assert.equal(created.setSelectionOrigin,'user');assert.equal(created.exampleId,'b');
 assert.throws(()=>drag.createIndependentCatalogMembers(supplied,['b'],{},
  {...context,sectionRole:'styling',catalog:suppliedCatalog.filter(example=>example.id!=='b'),makeId:()=> 'missing'}),/catalog_member_unavailable/);
});
test('catalog member targets an explicit template section rather than a shooting-role band', () => {
 const board=[{id:'a',sectionId:'first',sectionRole:'hooking'},
 {id:'b',sectionId:'looks',sectionRole:'styling'},{id:'c',sectionId:'fit',sectionRole:'studio'}];
 assert.deepEqual(drag.catalogMemberTarget(board,{targetSid:'looks',targetRole:'studio',fallbackIndex:0}),
 {sectionId:'looks',sectionRole:'studio',index:2});
});
