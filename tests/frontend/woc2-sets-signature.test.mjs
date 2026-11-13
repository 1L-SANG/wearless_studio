import test from 'node:test';
import assert from 'node:assert/strict';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES, spaceSetGroupId, spaceSetIdFromGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';

const ai = (extra = {}) => ({ id: 'one', source: 'ai', sectionId: 'life', cutType: 'styling',
  shot: 'full', direction: 'front', colorId: 'base', refScope: 'all', ...extra });
const examplesById = new Map(STORYBOARD_SPACE_SET_EXAMPLES.map(e => [e.id, e]));

for (const shot of ['full', 'medium']) for (const direction of ['front', 'side', 'back']) {
  test(`D204 selected published styling ${shot}/${direction} owns a singleton set`, () => {
    const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling'
      && s.members.some(m => m.shot === shot && m.direction === direction));
    assert.ok(set, `published aggregate fixture exists for ${shot}/${direction}`);
    const member = set.members.find(m => m.shot === shot && m.direction === direction);
    const before = ai();
    const next = { ...before, ...generationExampleSelectionPatch(before, examplesById.get(member.exampleId)).patch };
    assert.equal(spaceSetIdFromGroupId(next.spaceGroupId), set.id);
    assert.equal(next.spaceSetMemberOrder, member.order);
    assert.equal(next.spaceVariation, set.spaceVariation);
    assert.equal(next.setSelectionOrigin, 'user');
    assert.equal(next.refScope, 'pose');
    assert.equal(next.shot, shot);
    assert.equal(next.direction, direction);
    assert.equal(before.spaceGroupId, undefined);
  });
}

for (const type of ['horizon-sequence', 'horizon-rotation']) {
  test(`D204 ${type} singleton uses the complete studio reference`, () => {
    const set = STORYBOARD_SPACE_SETS.find(s => s.setType === type);
    const member = set.members.at(-1);
    const before = ai({ cutType: 'horizon' });
    const next = { ...before, ...generationExampleSelectionPatch(before, examplesById.get(member.exampleId)).patch };
    assert.equal(spaceSetIdFromGroupId(next.spaceGroupId), set.id);
    assert.equal(next.spaceSetMemberOrder, member.order);
    assert.equal(next.refScope, 'all');
    assert.equal(next.horizonBackgroundMode, 'reference');
  });
}

test('D204 selecting a different set rekeys while the same set preserves its instance', () => {
  const sets = STORYBOARD_SPACE_SETS.filter(s => s.setType === 'styling' && s.gender === 'women');
  const group = spaceSetGroupId(sets[0].id, 'before');
  const before = ai({ spaceGroupId: group, spaceVariation: sets[0].spaceVariation,
    exampleId: sets[0].members[0].exampleId, spaceSetMemberOrder: 1, refScope: 'pose' });
  const same = generationExampleSelectionPatch(before, examplesById.get(sets[0].members[1].exampleId)).patch;
  assert.equal({ ...before, ...same }.spaceGroupId, group);
  const other = generationExampleSelectionPatch(before, examplesById.get(sets[1].members[0].exampleId)).patch;
  assert.equal(spaceSetIdFromGroupId(other.spaceGroupId), sets[1].id);
  assert.notEqual(other.spaceGroupId, group);
  assert.equal(other.refScope, 'pose');
  const ordinary = generationExampleSelectionPatch(before, { id: 'ordinary', cutType: 'styling', shot: 'full', direction: 'front' }).patch;
  assert.equal(ordinary.spaceGroupId, null);
  assert.equal(ordinary.refScope, 'all');
});

const first = (over = {}) => ai({ id: 'show-first', taxonomyVersion: 4, pageSectionKey: 'show.first',
  pageSectionKind: 'first', slotKey: 'first.hero', contentRole: 'hero', cutType: 'horizon', ...over });

test('D206 v4 show first auto-assignment uses signature framing and leaves emo and other slots alone', () => {
  const blocks = [first(), first({ id: 'emo', pageSectionKey: 'emo.first' }),
    first({ id: 'product', cutType: 'product', shot: 'ghost', slotKey: 'first.ghost' }),
    ai({ id: 'wear', taxonomyVersion: 4, pageSectionKey: 'show.wear_fit', pageSectionKind: 'wear', cutType: 'horizon' })];
  const result = assignGenerationExamples(blocks, { catalog: [], product: { clothingType: 'top' }, gender: 'men' });
  assert.match(result.blocks[0].exampleId || '', /^sig_men_0[1-5]$/);
  assert.equal(result.blocks[0].shot, 'medium');
  assert.equal(result.blocks[0].direction, 'front');
  assert.equal(result.blocks[0].refScope, 'all');
  assert.equal(result.blocks[0].pageDesignId, blocks[0].pageDesignId);
  assert.deepEqual(result.blocks.slice(1), blocks.slice(1));
});

test('D206 already-saved show first repairs wrong-garment signatures and preserves ordinary choices without touching seller settings', () => {
  const before = first({ exampleId: 'sig_men_01', exampleSelectionOrigin: 'user', colorId: 'seller-color',
    faceExposure: 'hide', matchIds: ['selected'], refImages: ['kept'], angle: 'same' });
  const result = assignGenerationExamples([before], { catalog: [], product: { clothingType: 'bottom', subCategory: '스커트' }, gender: 'women' });
  assert.match(result.blocks[0].exampleId, /^sig_women_bottom_skirt_0[2-9]$/);
  assert.equal(result.blocks[0].shot, 'medium');
  assert.equal(result.blocks[0].colorId, 'seller-color');
  assert.deepEqual(result.blocks[0].matchIds, ['selected']);
  assert.deepEqual(result.blocks[0].refImages, ['kept']);
  assert.equal(before.exampleId, 'sig_men_01');
  const ordinary = first({exampleId:'ordinary',exampleSelectionOrigin:'user'});
  assert.equal(assignGenerationExamples([ordinary], {catalog:[],product:{clothingType:'top'},gender:'women'}).blocks[0], ordinary);
});

const { singleMemberSpaceSet } = await import('../../src/lib/storyboardSetSelection.js');
const { moveBlockWithSpaceMembership, createSpaceSetMembers, nextUnusedSpaceSetMember,
  nextSpaceSetMemberReservation, replaceSpaceSetRun, rekeySeparatedSpaceRuns } = await import('../../src/lib/storyboardSpaceSets.js');
const { createSimilarCutBlock, inheritSimilarCutRecipes } = await import('../../src/lib/storyboardSimilarCuts.js');
const { uniqueGenerationCutCount } = await import('../../src/lib/generationCutCount.js');
const { signatureCutsFor, signatureGenderFor, signatureExamplesForBlock } = await import('../../src/lib/signatureCutPool.js');

test('D204 reuse fallback clones a fresh singleton and ordinary fallback stays ungrouped', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling');
  const before = ai({ exampleId: set.members[0].exampleId, spaceGroupId: spaceSetGroupId(set.id, 'reuse'),
    refScope: 'pose', spaceSetMemberOrder: 1, spaceVariation: set.spaceVariation,
    setSelectionOrigin: 'user', matchIds: ['kept'], custom: { nested: 'kept' } });
  const snapshot = JSON.stringify(before);
  const copy = singleMemberSpaceSet(before);
  const second = singleMemberSpaceSet(before);
  assert.notEqual(copy, before);
  assert.notEqual(copy.spaceGroupId, before.spaceGroupId);
  assert.notEqual(copy.spaceGroupId, second.spaceGroupId);
  assert.equal(spaceSetIdFromGroupId(copy.spaceGroupId), set.id);
  assert.equal(copy.setSelectionOrigin, 'auto');
  assert.deepEqual(copy.matchIds, ['kept']);
  assert.deepEqual(copy.custom, { nested: 'kept' });
  assert.equal(JSON.stringify(before), snapshot);
  const ordinary = singleMemberSpaceSet({ ...before, exampleId: 'ordinary' });
  assert.equal(ordinary.spaceGroupId, undefined);
  assert.equal(ordinary.spaceSetMemberOrder, undefined);
  assert.equal(ordinary.refScope, 'all');
});

test('D204 moving a published member and similar follower outside its group keeps the location contract', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling');
  const group = spaceSetGroupId(set.id, 'move');
  const originals = createSpaceSetMembers(set, ai(), { spaceGroupId: group });
  const child = createSimilarCutBlock(originals[0], 'child');
  const before = [originals[0], child, originals[1], ai({ id: 'gap' })];
  const snapshot = JSON.stringify(before);
  const next = moveBlockWithSpaceMembership(before, originals[0].id, before.length);
  const moved = next.find(b => b.id === originals[0].id);
  assert.equal(spaceSetIdFromGroupId(moved.spaceGroupId), set.id);
  assert.notEqual(moved.spaceGroupId, group);
  assert.equal(moved.refScope, 'pose');
  assert.equal(next.at(-1).similarSourceBlockId, moved.id);
  assert.equal(next.at(-1).spaceGroupId, moved.spaceGroupId);
  assert.equal(uniqueGenerationCutCount(next), uniqueGenerationCutCount(before));
  assert.equal(JSON.stringify(before), snapshot, 'undo retains the exact original state');
});

test('D204 split/replacement/shuffle cardinality includes sources and similar followers once', () => {
  const sets = STORYBOARD_SPACE_SETS.filter(s => s.setType === 'styling' && s.gender === 'women'
    && s.applicableClothingTypes.includes('top'));
  const group = spaceSetGroupId(sets[0].id, 'split');
  const members = createSpaceSetMembers(sets[0], ai(), { spaceGroupId: group });
  const before = [members[0], ai({ id: 'gap' }), ...members.slice(1)];
  const rekeyed = rekeySeparatedSpaceRuns(before, id => spaceSetGroupId(id, 'tail'));
  assert.equal(rekeyed[0].spaceGroupId, group);
  assert.notEqual(rekeyed[2].spaceGroupId, group);
  const source = members.slice(0, 2);
  const family = [source[0], createSimilarCutBlock(source[0], 'child'), source[1]];
  const snapshot = JSON.stringify(family);
  const next = replaceSpaceSetRun(family, group, sets[1], { spaceGroupId: spaceSetGroupId(sets[1].id, 'replacement'),
    members: sets[1].members.slice(0, 2) });
  assert.equal(next.length, 3);
  assert.equal(uniqueGenerationCutCount(next), 3);
  assert.equal(next[1].similarSourceBlockId, next[0].id);
  assert.equal(next[1].spaceGroupId, next[0].spaceGroupId);
  assert.equal(next[1].exampleId, next[0].exampleId);
  assert.equal(JSON.stringify(family), snapshot);
  assert.deepEqual(inheritSimilarCutRecipes(next), next);
});

test('D204 singleton member reservations keep published member order and stop when exhausted', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling');
  const chosen = set.members[1];
  const singleton = singleMemberSpaceSet(ai({ exampleId: chosen.exampleId, shot: chosen.shot, direction: chosen.direction }));
  const reservation = nextSpaceSetMemberReservation(set, [singleton]);
  assert.equal(reservation.member.exampleId, set.members[0].exampleId);
  assert.equal(reservation.blockPatch.spaceGroupId, singleton.spaceGroupId);
  const all = createSpaceSetMembers(set, ai(), { spaceGroupId: singleton.spaceGroupId });
  assert.equal(nextUnusedSpaceSetMember(set, all), null);
});

for (const [clothingType, gender, subCategory, prefix, count] of [
  ['top', 'men', '', 'sig_men_', 5], ['outer', 'women', '', 'sig_women_', 4],
  ['bottom', 'women', '치마', 'sig_women_bottom_skirt_', 8],
  ['bottom', 'women', 'shorts', 'sig_women_bottom_pants_', 9],
  ['bottom', 'men', 'skirt', 'sig_men_bottom_pants_', 7],
  ['dress', 'women', '', 'sig_women_dress_', 6], ['dress', 'men', '', 'sig_men_', 5],
]) test(`D206 signature pool ${gender}/${clothingType}/${subCategory}`, () => {
  const pool = signatureCutsFor({ clothingType, gender, subCategory });
  assert.equal(pool.length, count);
  assert.ok(pool.every(c => c.id.startsWith(prefix)));
  const shown = signatureExamplesForBlock(first(), { product: { clothingType, subCategory }, gender });
  assert.deepEqual(shown.map(e => e.id), pool.map(e => e.id));
  assert.ok(shown.every(e => e.cutType === 'horizon' && e.shot === 'medium' && e.direction === 'front'));
  const patch = generationExampleSelectionPatch(first(), shown[0]).patch;
  assert.equal(patch.shot, 'medium');
  assert.equal(patch.direction, 'front');
  assert.equal(patch.refScope, 'all');
});

test('D206 signature gender and ordinary gallery boundaries follow the operating rules', () => {
  assert.equal(signatureGenderFor({ clothingType: 'dress', targetGenders: ['men'] }), 'women');
  assert.equal(signatureGenderFor({ targetGenders: ['남성', 'women'] }), 'men');
  assert.equal(signatureGenderFor({ targetGenders: [] }), 'women');
  assert.equal(signatureExamplesForBlock(first({ pageSectionKey: 'emo.first' })).length, 4);
  assert.equal(signatureExamplesForBlock(first({ slotKey: 'first.ghost' })), null);
});

test('D206 automatic signature choices are stable per project and use the operating pool across projects', () => {
  const pick = projectId => assignGenerationExamples([first()], { catalog: [], product: { clothingType: 'top' }, gender: 'women', projectId }).blocks[0].exampleId;
  assert.equal(pick('stable-project'), pick('stable-project'));
  const picks = new Set(Array.from({ length: 20 }, (_, index) => pick(`project-${index}`)));
  assert.ok(picks.size > 1, 'different projects must not all receive the first pool item');
});

test('D204 an already-saved ungrouped published selection heals before generation, including similar followers', () => {
  const set = STORYBOARD_SPACE_SETS.find(s => s.setType === 'styling');
  const member = set.members[0];
  const before = ai({ exampleId: member.exampleId, exampleSelectionOrigin: 'user', shot: member.shot,
    direction: member.direction, refScope: 'all' });
  const child = createSimilarCutBlock(before, 'old-child');
  const snapshot = JSON.stringify([before, child]);
  const next = assignGenerationExamples([before, child], { catalog: [], product: { clothingType: set.applicableClothingTypes[0] }, gender: set.gender }).blocks;
  assert.equal(spaceSetIdFromGroupId(next[0].spaceGroupId), set.id);
  assert.equal(next[0].refScope, 'pose');
  assert.equal(next[1].spaceGroupId, next[0].spaceGroupId);
  assert.equal(next[1].similarSourceBlockId, before.id);
  assert.equal(JSON.stringify([before, child]), snapshot);
  assert.deepEqual(assignGenerationExamples(next, { catalog: [], product: { clothingType: set.applicableClothingTypes[0] }, gender: set.gender }).blocks, next);
});
