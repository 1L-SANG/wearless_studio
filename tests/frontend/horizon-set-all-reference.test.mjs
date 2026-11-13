import { nodes } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { v4Fixture } from './helpers/templateStoryboard.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { createSpaceSetMembers, nextSpaceSetMemberReservation, moveBlockWithSpaceMembership } from '../../src/lib/storyboardSpaceSets.js';
import { ensureSections } from '../../src/lib/sections.js';
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES, spaceSetGroupId, spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
const setFor = cutType => ({ id: `${cutType}-set`, members: [1, 2].map(order => ({ exampleId: `${cutType}-${order}`, cutType, order, direction: 'front', shot: 'full', thumb: `/${order}.png` })) });
for (const [cutType, expected] of [['horizon','all'],['styling','pose']]) {
  test(`${cutType} grouped creation, reservation, selection and move keep ${expected} scope`, () => {
    const set = setFor(cutType), spaceGroupId = `ssg1__${set.id}__instance`;
    const members = createSpaceSetMembers(set, { sectionRole: cutType === 'horizon' ? 'studio' : 'styling' }, { spaceGroupId });
    assert.ok(members.every(member => member.refScope === expected));
    assert.equal(nextSpaceSetMemberReservation(set, members.slice(0,1)).blockPatch.refScope, expected);
    const chosen = generationExampleSelectionPatch({ ...members[0], refScope:'pose' }, { id:'external-complete',cutType,direction:'front',variants:['all'] }, { refScope:'pose' });
    assert.equal(chosen.patch.refScope, 'all');
    const moved = moveBlockWithSpaceMembership(members, members[0].id, members.length, { targetSpaceGroupId:spaceGroupId, nextGroupId:()=>'unused' });
    assert.ok(moved.every(member=>member.refScope===expected));
    // 콘티 진입(prepareStoryboardEntry → ensureSections)을 거쳐도 서버와 같은 범위를 유지한다.
    // 2026-09-26: 호리존이 'pose' 로 뒤집혀 모든 컷에 '조건이 바뀌어 예시를 다시 골라주세요'가 떴다.
    assert.ok(ensureSections(v4Fixture(members)).every(member => member.refScope === expected));
    // 옛 저장본(호리존인데 'pose' 로 저장)도 서버가 쓰는 'all' 로 맞춘다. 스타일링 'all' 은 'pose' 로.
    const legacy = members.map(member => ({ ...member, refScope: expected === 'all' ? 'pose' : 'all' }));
    assert.ok(ensureSections(v4Fixture(legacy)).every(member => member.refScope === expected));
  });
}
test('pending recipe and reserved addition accept published complete-only horizon members without pose assets', async () => {
 const styling=STORYBOARD_SPACE_SETS.find(set=>set.setType==='styling'&&set.gender==='women'&&set.applicableClothingTypes.includes('top'));
 const horizon=STORYBOARD_SPACE_SETS.find(set=>set.setType==='horizon-sequence'&&set.gender==='women'&&set.applicableClothingTypes.includes('top'));
 const selected=STORYBOARD_SPACE_SET_EXAMPLES.find(example=>example.spaceSetId===horizon.id&&example.shot==='medium');
 assert.deepEqual(selected.variants,['all']);
 const current=createSpaceSetMembers(styling,{ taxonomyVersion:4,pageSectionKey:'emo.wear_99',pageSectionKind:'wear',
  pageSectionUsage:'styling',pageDesignId:'P02-D01',sectionId:'live',sectionRole:'styling',contentRole:'coordination',colorId:'base' },
  {spaceGroupId:spaceSetGroupId(styling.id,'pending')})[0];
 const h=await storyboardPhotoPanelHarness({block:current,catalogs:{genExamples:STORYBOARD_SPACE_SET_EXAMPLES,
  detailProduct:{clothingType:'top'},shotTypes:[{value:'full'},{value:'medium'}],productShotTypes:[{value:'ghost'}]},
  clothingType:'top',exampleGender:'women',colorOpts:[{id:'base'}],detailColorOpts:[{id:'base'}],
  requestedRecipe:{blockId:current.id,cutType:'horizon',shot:'medium',sectionRole:'studio'}});
 try {
  h.render();
  const guide=nodes(h.render()).find(node=>node.type==='mood-guide');
  assert.equal(guide.props.cut,'horizon');assert.equal(guide.props.refScope,'all');
  await guide.props.onExampleChange(selected.id);
  assert.equal(h.block.exampleId,selected.id);assert.equal(h.block.refScope,'all');
  assert.equal(h.block.shot,selected.shot);assert.equal(h.block.direction,selected.direction);
  assert.equal(spaceSetIdFromGroupId(h.block.spaceGroupId),horizon.id);
  assert.notEqual(h.block.spaceGroupId,current.spaceGroupId);
  const members=createSpaceSetMembers(horizon,{}, {spaceGroupId:spaceSetGroupId(horizon.id,'reserved')});
  const reservation=nextSpaceSetMemberReservation(horizon,members.slice(0,1));
  assert.equal(reservation.blockPatch.refScope,'all');
  assert.equal(reservation.blockPatch.horizonBackgroundMode,'reference');
 } finally {h.close();}
});
