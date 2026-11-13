import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as similar from '../../src/lib/storyboardSimilarCuts.js';
import * as spaces from '../../src/lib/storyboardSpaceSets.js';
import * as catalog from '../../src/lib/storyboardSpaceSetCatalog.js';
import * as photos from '../../src/lib/pageTemplates/photoActions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { reconcileSectionDependencies } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { wornCropRegions } from '../../src/lib/pageTemplates/seed.js';
import { normalizeBoard } from '../../src/lib/sections.js';
import { preserveDetailTargetBinding } from '../../src/lib/detailRecommendations.js';
import { reconcileHorizonGroupBackground } from '../../src/lib/horizonBackgroundAvailability.js';
import { shuffleSectionExamples } from '../../src/lib/storyboardExampleShuffle.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';

const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const set = catalog.storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'styling');
const groupId = catalog.spaceSetGroupId(set.id, 'regression');
const originals = (count = 3) => spaces.createSpaceSetMembers(set, { source: 'ai', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'stack',
  taxonomyVersion: 4, pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'styling', pageDesignId: 'P02-D01', contentRole: 'coordination', colorId: 'base',
}, { spaceGroupId: groupId, setSelectionOrigin: 'auto', members: set.members.slice(0, count), makeId: (_member, i) => `root${i}` }).map(block => ({ ...block, exampleSelectionOrigin: 'auto' }));
const followup = (root, id = 'child') => similar.createSimilarCutBlock(root, id);
const ids = blocks => blocks.map(block => block.id);
function board(initial, overrides = {}) {
  let blocks = initial, renderSnapshot = null, n = 0;
  const undo = [], messages = [];
  const context = { ...similar, ...spaces, ...catalog, ...photos, sectionRuns, normalizeBoard, reconcileSectionDependencies, wornCropRegions,
    preserveDetailTargetBinding, reconcileHorizonGroupBackground,
    catalogs: { genExamples: catalog.STORYBOARD_SPACE_SET_EXAMPLES, detailProduct: { clothingType: 'top', colors: [{ id: 'base', isBase: true }] } },
    uid: () => `added${++n}`, locked: false, atomicSavingRef: { current: false }, setAtomicSaving() {}, setSelectedId() {},
    directSaveSnapshots: { current: new WeakSet() }, atomicRetryRef: { current: null }, setDrawer() {},
    setSetPicker() {}, setSetPickerError() {}, selectedId: null, closeInspector() {},
    showUndo: (before, after) => undo.push({ before, after }), clearUndoForSnapshot() {},
    toast: { push: message => messages.push(message) }, sbSaveNow: async () => {}, sbPending: new Map(), api: {}, projectId: 'test', clothingType: 'top',
    undoLabelForPatch: () => '설정', ...overrides,
    setBlocks: update => { blocks = similar.reconcileSimilarCutMutation(blocks, typeof update === 'function' ? update(blocks) : update); },
  };
  Object.defineProperty(context, 'blocks', { get: () => renderSnapshot || blocks });
  const body = `${slice('const nextSeparatedSpaceGroupId =', 'function usePhotoUploadProgress')}
    ${slice('  const atomicPatch =', '  const closeInspector =')}
    ${slice('  const addSimilar =', '  const chooseSpaceSet =')}
    ${slice('  const chooseSpaceSet =', '  const locked =')}
    return { addSimilar, remove, atomicPatch, chooseSpaceSet };`;
  const actions = new Function('scope', `with(scope) { ${body} }`)(context);
  const invoke = action => (...args) => {
    renderSnapshot = blocks;
    try { const result = action(...args); if (result?.then) return result.finally(() => { renderSnapshot = null; }); renderSnapshot = null; return result; }
    catch (error) { renderSnapshot = null; throw error; }
  };
  return { ...Object.fromEntries(Object.entries(actions).map(([key, action]) => [key, invoke(action)])), undo, messages, blocks: () => blocks, restore: snapshot => { blocks = snapshot; } };
}
for (const index of [0, 1]) test(`similar cut after set member ${index} keeps one continuous run and increases the generation count`, () => {
  const before = originals(), h = board(before); h.addSimilar(before[index].id);
  assert.equal(h.blocks()[index + 1].similarSourceBlockId, before[index].id);
  assert.equal(h.blocks()[index + 1].spaceGroupId, groupId);
  assert.equal(spaces.groupConsecutiveSpaceRuns(h.blocks()).length, 1);
  assert.equal(uniqueGenerationCutCount(h.blocks()), 4);
});
test('similar additions follow the original and all its earlier children even when the set has no reserved member', () => {
  const roots = originals(set.members.length), h = board([roots[0], followup(roots[0]), ...roots.slice(1)]);
  assert.equal(spaces.nextSpaceSetMemberReservation(set, roots), null);
  h.addSimilar('child'); h.addSimilar('root0');
  assert.deepEqual(ids(h.blocks()).slice(0, 5), ['root0', 'child', 'added1', 'added2', 'root1']);
  assert.ok(h.blocks().slice(1, 4).every(block => block.similarSourceBlockId === 'root0' && !block.spaceSetMemberOrder));
});
test('similar recipe membership comes only from the source and detaches with the original', () => {
  const root = originals()[0], child = followup(root);
  const fixed = similar.inheritSimilarCutRecipes([root, { ...child, spaceGroupId: 'wrong', spaceSetMemberOrder: 5 }])[1];
  assert.equal(fixed.spaceGroupId, root.spaceGroupId); assert.equal(fixed.spaceSetMemberOrder, undefined);
  assert.equal(similar.inheritSimilarCutRecipes([spaces.detachSpaceMembership(root), child])[1].spaceGroupId, undefined);
});
test('set replacement preserves children of retained original IDs and removes children whose originals disappeared', () => {
  const roots = originals(), before = [roots[0], followup(roots[0], 'keep'), roots[1], roots[2], followup(roots[2], 'drop')];
  const next = spaces.replaceSpaceSetRun(before, groupId, { ...set, members: set.members.slice(0, 2) }, { spaceGroupId: 'new' });
  assert.deepEqual(ids(next), ['root0', 'keep', 'root1']);
  assert.equal(next[1].spaceGroupId, 'new'); assert.equal(next[1].exampleId, next[0].exampleId);
});
test('live set replacement assigns unique new IDs and undo restores all original members and followups', async () => {
  const roots = originals(2), before = [roots[0], followup(roots[0]), roots[1]];
  const h = board(before, { setPicker: { mode: 'replace', spaceGroupId: groupId, targetSid: 'styling' } });
  const replacement = catalog.storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'styling' && item.id !== set.id);
  await h.chooseSpaceSet({ ...replacement, members: replacement.members.slice(0, 3) });
  assert.equal(h.blocks().length, 4); assert.equal(new Set(ids(h.blocks())).size, 4);
  assert.deepEqual(ids(h.blocks()).slice(0, 3), ['root0', 'child', 'root1']);
  h.restore(h.undo[0].before); assert.deepEqual(h.blocks(), before);
});
test('set shuffle keeps the original-member count and preserves similar cuts', () => {
  const roots = originals(2), before = [roots[0], followup(roots[0]), roots[1]];
  const next = shuffleSectionExamples(before, { sectionId: 'styling', catalog: catalog.STORYBOARD_SPACE_SET_EXAMPLES,
    product: { clothingType: 'top' }, gender: 'women', onlySpaceGroupId: groupId, uid: () => 'shuffled' });
  assert.notEqual(next, before); assert.deepEqual(ids(next), ['root0', 'child', 'root1']);
  assert.equal(next[1].spaceGroupId, next[0].spaceGroupId);
});
for (const id of ['root1', 'child']) test(`deleting ${id} and undo preserve the complete set snapshot`, () => {
  const roots = originals(), before = [roots[0], roots[1], followup(roots[1]), roots[2]], h = board(before);
  h.remove(id); assert.deepEqual(ids(h.blocks()), id === 'root1' ? ['root0', 'root2'] : ['root0', 'root1', 'root2']);
  h.restore(h.undo[0].before); assert.deepEqual(h.blocks(), before);
});
test('photo recipe changes update similar children without moving the section run', async () => {
  const roots = originals(), before = [roots[0], followup(roots[0]), ...roots.slice(1)], h = board(before);
  await h.atomicPatch('root0', { shot: 'medium', exampleId: roots[1].exampleId });
  assert.deepEqual(ids(h.blocks()), ids(before));
  assert.equal(h.blocks()[1].shot, h.blocks()[0].shot); assert.equal(h.blocks()[1].exampleId, h.blocks()[0].exampleId);
  h.restore(h.undo[0].before); assert.deepEqual(h.blocks(), before);
});
test('set child movement remains restricted to its own set in the shared membership helper', () => {
  const roots = originals(), before = [roots[0], followup(roots[0]), ...roots.slice(1)];
  const moved = spaces.moveBlockWithSpaceMembership(before, 'child', before.length, { targetSpaceGroupId: groupId });
  assert.deepEqual(ids(moved), ['root0', 'root1', 'root2', 'child']);
  for (const targetSpaceGroupId of [null, 'other']) assert.equal(spaces.moveBlockWithSpaceMembership(before, 'child', before.length, { targetSpaceGroupId }), before);
});
