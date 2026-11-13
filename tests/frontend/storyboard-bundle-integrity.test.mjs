import test from 'node:test';
import assert from 'node:assert/strict';
import { snapOutOfForeignBundle } from '../../src/features/storyboard/storyboardUnits.js';
import { groupConsecutiveSpaceRuns, moveBlockWithSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
const root={id:'root',source:'ai',cutType:'horizon',exampleId:'ex',sectionId:'sec',pageSectionKey:'show.wear_fit',spaceGroupId:'space'};
test('an outside card cannot split a coherent location set',()=>{
 const b=[{id:'before'},root,{...root,id:'second'},{id:'after'}];assert.equal(snapOutOfForeignBundle(b,b[0],2),3);
});
test('a source and its similar followups remain a coherent location run',()=>{
 const child=createSimilarCutBlock(root,'child');const b=[root,child,{...root,id:'second'}];
 assert.equal(groupConsecutiveSpaceRuns(b).length,1);
 const moved=moveBlockWithSpaceMembership(b,child.id,0,{targetSpaceGroupId:'space'});
 assert.equal(moved[0].id,'child');assert.equal(groupConsecutiveSpaceRuns(moved).length,1);
});
