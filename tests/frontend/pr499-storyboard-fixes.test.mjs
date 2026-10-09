import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import * as similar from '../../src/lib/storyboardSimilarCuts.js';
import * as spaces from '../../src/lib/storyboardSpaceSets.js';
import * as catalog from '../../src/lib/storyboardSpaceSetCatalog.js';
import * as sections from '../../src/lib/sections.js';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import { applyHookStyle, stripHookFrameFields } from '../../src/lib/storyboardHookFrame.js';
import { shuffleSectionExamples } from '../../src/lib/storyboardExampleShuffle.js';
import { restoreHorizonGroupBackground } from '../../src/lib/horizonBackground.js';
import { snapOutOfForeignBundle } from '../../src/features/storyboard/storyboardUnits.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { preserveDetailTargetBinding } from '../../src/lib/detailRecommendations.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { reconcileHorizonGroupBackground } from '../../src/lib/horizonBackgroundAvailability.js';

const code = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const slice = (start, end) => code.slice(code.indexOf(start), code.indexOf(end, code.indexOf(start)));
const evaluate = (body, context) => new Function('scope', `with (scope) { ${body} }`)(context);
const set = catalog.storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(s => s.setType === 'styling');
const groupId = catalog.spaceSetGroupId(set.id, 'g3');
const originals = (count = 3) => spaces.createSpaceSetMembers(set, {
  source: 'ai', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'stack',
  taxonomyVersion: 3, contentRole: 'coordination', colorId: 'navy',
}, { spaceGroupId: groupId, members: set.members.slice(0, count), makeId: (_, i) => `root${i}`, setSelectionOrigin: 'auto' })
  .map(b => ({ ...b, exampleSelectionOrigin: 'auto' }));
const followup = (root, id = 'child') => {
  const b = { ...root, id, similarSourceBlockId: root.id };
  delete b.spaceSetMemberOrder;
  return b;
};
const ids = bs => bs.map(b => b.id);

// Execute the actual UI mutation handlers. State, toasts and IDs are local doubles;
// membership, section adoption, normalization and reconciliation remain production code.
function board(initial, overrides = {}) {
  let blocks = initial, n = 0;
  const messages = [], undoEntries = [];
  const context = { ...similar, ...spaces, ...catalog, ...sections, ...taxonomy,
    stripHookFrameFields, restoreHorizonGroupBackground, snapOutOfForeignBundle, preserveDetailTargetBinding,
    assignGenerationExamples, reconcileHorizonGroupBackground,
    locked: false, uid: () => `added${++n}`, nextSeparatedSpaceGroupId: id => catalog.spaceSetGroupId(id, `split${++n}`),
    dismissUndo: () => {}, showUndo: (before, after) => undoEntries.push({ before, after }),
    setSelectedId: () => {}, setSplitOpen: () => {}, selectedId: null, finishEdit: () => {},
    autoDemoteTrail: { current: new WeakMap() }, toast: { push: (message, options) => messages.push({ message, ...options }) },
    clothingType: 'top', renderKeyForBlockId: () => 'styling', renderGroups: () => [],
    setPendingSectionMove: () => {},
    atomicSavingRef: { current: false }, setAtomicSaving: () => {}, pendingSectionMove: null,
    directSaveSnapshots: { current: new WeakSet() }, atomicRetryRef: { current: null },
    projectId: 'test', sbSaveNow: async () => {}, sbPending: new Map(), clearUndoForSnapshot: () => {},
    undoLabelForPatch: () => '설정', ...overrides,
    setBlocks: update => { blocks = similar.reconcileSimilarCutMutation(blocks, typeof update === 'function' ? update(blocks) : update); },
  };
  Object.defineProperty(context, 'blocks', { get: () => blocks });
  Object.assign(context, evaluate(`${slice('const withoutLayoutRow =', '// 첫 화면 슬롯')}
    ${slice('const detachHookSlotOnReshape =', 'const WORN_CUT_TYPES')}
    ${slice('const nextSeparatedSpaceGroupId =', 'const SECTION_ORDER')}
    ${slice('function dragGroupFor(', '/* underline tab')}
    return { withoutLayoutRow, normalizeStoryboardMutation, dragGroupFor, detachHookSlotOnReshape };`, context));
  const actions = evaluate(`${slice('  const addSimilar =', '  // 이동 후 adoptSection')}
    ${slice('  const atomicPatch =', '  const atomicBoardChange =')}
    ${slice('  const atomicBoardChange =', '  const finishEdit =')}
    ${slice('  const chooseSpaceSet =', '  const locked =')}
    ${slice('  const applySingleMove =', '  /* 위/아래 한 칸 이동')}
    return { addSimilar, duplicate, remove, applySingleMove, atomicPatch, chooseSpaceSet };`, context);
  return { ...actions, messages, undoEntries, blocks: () => blocks,
    update: bs => context.setBlocks(bs) };
}

for (const index of [0, 1]) test(`set similar insertion after member ${index} keeps one run and includes the child in the count`, () => {
  const before = originals();
  const h = board(before);
  h.addSimilar(before[index].id);
  const bs = h.blocks();
  assert.equal(bs.length, 4);
  assert.equal(bs[index + 1].similarSourceBlockId, before[index].id);
  assert.equal(bs[index + 1].spaceGroupId, groupId, '후속 컷은 원본 세트에 포함된다');
  const runs = spaces.groupConsecutiveSpaceRuns(bs);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].items.length, 4);
  assert.equal(uniqueGenerationCutCount(bs), 4);
});

test('exhausted set allows similar additions and child duplication uses the original after existing followups', () => {
  const before = originals(set.members.length);
  const child = followup(before[0]);
  const h = board([before[0], child, ...before.slice(1)]);
  assert.equal(spaces.nextSpaceSetMemberReservation(set, before), null);
  h.duplicate(child.id);
  assert.deepEqual(ids(h.blocks()).slice(0, 4), ['root0', 'child', 'added1', 'root1']);
  assert.equal(h.blocks()[2].similarSourceBlockId, 'root0');
  assert.equal(h.blocks()[2].spaceSetMemberOrder, undefined);
  h.addSimilar('root0');
  assert.deepEqual(ids(h.blocks()).slice(0, 5), ['root0', 'child', 'added1', 'added2', 'root1']);
});

test('similar recipe membership comes only from the source and clears reserved and frame fields', () => {
  const root = originals()[0];
  const child = similar.createSimilarCutBlock(root, 'child');
  assert.equal(child.spaceGroupId, groupId);
  assert.equal(child.spaceVariation, root.spaceVariation);
  assert.equal(child.spaceSetMemberOrder, undefined);
  const out = similar.inheritSimilarCutRecipes([root, { ...child, spaceGroupId: 'wrong', spaceVariation: 'fixed', spaceSetMemberOrder: 5, hookSlotRole: 'right', colorwayId: 'old' }])[1];
  assert.equal(out.spaceGroupId, groupId);
  assert.equal(out.spaceSetMemberOrder, undefined);
  assert.equal(out.hookSlotRole, undefined);
  assert.equal(out.colorwayId, undefined);
  const detached = spaces.detachSpaceMembership(root);
  const outside = similar.inheritSimilarCutRecipes([detached, child])[1];
  assert.equal(outside.spaceGroupId, undefined);
  assert.equal(outside.spaceVariation, undefined);
});

test('set replacement retains children by surviving original IDs and removes children of removed originals', () => {
  const roots = originals();
  const before = [roots[0], followup(roots[0], 'keep'), roots[1], roots[2], followup(roots[2], 'drop')];
  const next = spaces.replaceSpaceSetRun(before, groupId, { ...set, members: set.members.slice(0, 2) }, { spaceGroupId: 'new-group' });
  assert.deepEqual(ids(next), ['root0', 'keep', 'root1']);
  assert.equal(next[1].similarSourceBlockId, 'root0');
  assert.equal(next[1].exampleId, next[0].exampleId);
  assert.equal(next[1].spaceGroupId, 'new-group');
  assert.equal(next[1].spaceSetMemberOrder, undefined);
});

function pickerContext() {
  return { setPicker: { mode: 'replace', spaceGroupId: groupId }, setSetPicker: () => {}, setSetPickerError: () => {},
    catalogs: { genExamples: catalog.STORYBOARD_SPACE_SET_EXAMPLES, detailProduct: { clothingType: 'top' } },
    exampleGender: 'women', identityKind: null };
}

test('inspector set replacement assigns unique original IDs when the new set has more members', async () => {
  const roots = originals(2);
  const before = [roots[0], followup(roots[0]), roots[1]];
  const h = board(before, pickerContext());
  const replacement = catalog.storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })
    .find(candidate => candidate.setType === 'styling' && candidate.id !== set.id);
  await h.chooseSpaceSet({ ...replacement, members: replacement.members.slice(0, 3) });
  const next = h.blocks();
  assert.equal(next.length, 4);
  assert.equal(new Set(ids(next)).size, 4);
  assert.deepEqual(ids(next).slice(0, 3), ['root0', 'child', 'root1']);
  assert.deepEqual(h.undoEntries[0].before, before);
  h.update(h.undoEntries[0].before);
  assert.deepEqual(h.blocks(), before);
});

test('inspector set replacement announces removed dependent cuts and whole-board undo restores them', async () => {
  const roots = originals();
  const before = [roots[0], followup(roots[0], 'keep'), roots[1], roots[2], followup(roots[2], 'drop')];
  const h = board(before, pickerContext());
  const replacement = catalog.storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })
    .find(candidate => candidate.setType === 'styling' && candidate.id !== set.id);
  await h.chooseSpaceSet({ ...replacement, members: replacement.members.slice(0, 2) });
  assert.deepEqual(ids(h.blocks()), ['root0', 'keep', 'root1']);
  assert.ok(h.messages.some(entry => entry.message === '원본과 연결된 비슷한 컷 1장도 함께 정리했어요'));
  h.update(h.undoEntries[0].before);
  assert.deepEqual(h.blocks(), before);
});

test('set shuffle keeps only the original member count and preserves followups', () => {
  const roots = originals(2);
  const before = [roots[0], followup(roots[0]), roots[1]];
  const next = shuffleSectionExamples(before, { sectionId: 'styling', catalog: catalog.STORYBOARD_SPACE_SET_EXAMPLES,
    product: { clothingType: 'top' }, gender: 'women', onlySpaceGroupId: groupId, uid: () => 'shuffled' });
  assert.notEqual(next, before);
  assert.deepEqual(ids(next), ['root0', 'child', 'root1']);
  assert.equal(next.filter(b => !b.similarSourceBlockId).length, 2);
  assert.equal(next[1].spaceGroupId, next[0].spaceGroupId);
});

for (const id of ['root1', 'child']) test(`deleting ${id} and immediate undo preserves the complete set snapshot`, () => {
  const roots = originals();
  const before = [roots[0], roots[1], followup(roots[1]), roots[2]];
  const h = board(before);
  h.remove(id);
  assert.deepEqual(ids(h.blocks()), id === 'root1' ? ['root0', 'root2'] : ['root0', 'root1', 'root2']);
  assert.ok(h.blocks().every(b => b.spaceGroupId === groupId));
  h.messages.at(-1).undo();
  assert.deepEqual(h.blocks(), before);
});

test('fallback deletion undo restores original and children inside the remaining continuous set', () => {
  const roots = originals();
  const before = [roots[0], roots[1], followup(roots[1]), roots[2]];
  const h = board(before);
  h.remove('root1');
  // Another edit inserts an unrelated cut before the set, invalidating whole-board undo.
  const unrelated = { ...spaces.detachSpaceMembership(roots[0]), id: 'other', sectionId: 'hook', sectionRole: 'hooking', contentRole: 'hero' };
  h.update([unrelated, ...h.blocks()]);
  h.messages[0].undo();
  assert.deepEqual(ids(h.blocks()), ['other', 'root0', 'root1', 'child', 'root2']);
  assert.deepEqual(h.blocks().slice(1).map(b => b.spaceGroupId), Array(4).fill(groupId));
  assert.equal(spaces.groupConsecutiveSpaceRuns(h.blocks()).filter(r => r.kind === 'space').length, 1);
});

test('fallback undo preserves a child moved before its original inside the set', () => {
  const roots = originals();
  const before = [followup(roots[0]), ...roots];
  const h = board(before);
  h.remove('root0');
  h.update(h.blocks().map(block => ({ ...block, colorId: 'ivory' })));
  h.messages[0].undo();
  assert.deepEqual(ids(h.blocks()), ['child', 'root0', 'root1', 'root2']);
  assert.ok(h.blocks().every(block => block.spaceGroupId === groupId));
});

test('fallback undo of an entirely deleted set never splits a newly inserted foreign set', () => {
  const root = originals(1)[0];
  const flat = id => ({ ...spaces.detachSpaceMembership(root), id });
  const before = [flat('flat0'), flat('flat1'), root, followup(root)];
  const h = board(before);
  h.remove(root.id);
  const foreign = originals().map(block => ({ ...block, id: `foreign-${block.id}`,
    spaceGroupId: catalog.spaceSetGroupId(set.id, 'foreign') }));
  h.update([...foreign, ...h.blocks()]);
  h.messages[0].undo();
  assert.deepEqual(ids(h.blocks()), ['foreign-root0', 'foreign-root1', 'foreign-root2', 'root0', 'child', 'flat0', 'flat1']);
  assert.deepEqual(spaces.groupConsecutiveSpaceRuns(h.blocks()).filter(run => run.kind === 'space').map(run => run.items.length), [3, 2]);
});

test('duplicating an outside followup inserts a new child behind the original photo row', () => {
  const roots = originals().map(spaces.detachSpaceMembership);
  const [first, second, third] = roots;
  const before = [{ ...first, layoutRowId: 'row', sectionLayout: 'twoColumn' },
    { ...second, layoutRowId: 'row', sectionLayout: 'twoColumn' }, third, followup(first)];
  const h = board(before);
  h.duplicate('child');
  assert.deepEqual(ids(h.blocks()), ['root0', 'root1', 'added1', 'root2', 'child']);
  assert.equal(h.blocks()[2].similarSourceBlockId, 'root0');
});

test('moving a set original outside carries its children and leaves remaining members in their set', () => {
  const roots = originals();
  const before = [roots[0], followup(roots[0]), roots[1], roots[2]];
  const next = spaces.moveBlockWithSpaceMembership(before, 'root0', before.length, { nextGroupId: () => 'unexpected' });
  assert.deepEqual(ids(next), ['root1', 'root2', 'root0', 'child']);
  assert.equal(next[2].spaceGroupId, undefined);
  assert.equal(next[3].spaceGroupId, undefined);
  assert.equal(next[0].spaceGroupId, groupId);
  const h = board(before);
  h.applySingleMove({ id: 'root0', idx: before.length, targetSid: 'new-styling', targetRole: 'styling' });
  assert.deepEqual(ids(h.blocks()), ['root1', 'root2', 'root0', 'child']);
  assert.equal(h.blocks()[2].sectionId, h.blocks()[3].sectionId);
});

test('inspector section move carries set children when the new section requires a different example', async () => {
  const roots = originals();
  const before = [roots[0], followup(roots[0]), roots[1], roots[2]];
  const h = board(before, { pendingSectionMove: { blockId: 'root0', index: 4, targetSid: 'studio', targetRole: 'studio' } });
  await h.atomicPatch('root0', { cutType: 'horizon', shot: 'full', exampleId: 'new-example' });
  assert.deepEqual(ids(h.blocks()), ['root1', 'root2', 'root0', 'child']);
  const [root, child] = h.blocks().slice(2);
  assert.equal(root.sectionId, child.sectionId);
  assert.equal(root.sectionRole, 'studio');
  assert.equal(child.cutType, 'horizon');
  assert.equal(child.exampleId, 'new-example');
  assert.ok(!root.spaceGroupId);
  assert.ok(!child.spaceGroupId);
});

test('set child may move within its set but cannot move outside or into another set', () => {
  const roots = originals();
  const before = [roots[0], followup(roots[0]), roots[1], roots[2]];
  const moved = spaces.moveBlockWithSpaceMembership(before, 'child', before.length, { targetSpaceGroupId: groupId });
  assert.deepEqual(ids(moved), ['root0', 'root1', 'root2', 'child']);
  assert.equal(moved.at(-1).spaceGroupId, groupId);
  for (const targetSpaceGroupId of [null, 'other-set']) {
    assert.equal(spaces.moveBlockWithSpaceMembership(before, 'child', before.length, { targetSpaceGroupId }), before);
    const h = board(before);
    h.applySingleMove({ id: 'child', idx: before.length, targetSpaceGroupId });
    assert.deepEqual(h.blocks(), before);
    assert.equal(h.messages.length, 1);
  }
  const h = board(before);
  h.applySingleMove({ id: 'child', idx: before.length, targetSid: 'styling', targetSpaceGroupId: groupId });
  assert.deepEqual(ids(h.blocks()), ['root0', 'root1', 'root2', 'child']);
});

test('whole set movement includes followups and deleting the last original also removes its children', () => {
  const root = originals(1)[0];
  const other = { ...spaces.detachSpaceMembership(root), id: 'other' };
  const before = [root, followup(root), other];
  assert.deepEqual(ids(spaces.moveSpaceSetRun(before, groupId, 3)), ['other', 'root0', 'child']);
  const h = board(before);
  h.remove(root.id);
  assert.deepEqual(ids(h.blocks()), ['other']);
});

for (const childFirst of [false, true]) test(`hook restoration excludes unframed followups, child first ${childFirst}`, () => {
  const root = { ...spaces.detachSpaceMembership(originals(1)[0]), id: 'root', sectionId: 'hook', sectionRole: 'hooking', contentRole: 'hero', cutType: 'horizon', shot: 'medium' };
  const child = followup(root);
  const before = childFirst ? [child, root] : [root, child];
  const next = applyHookStyle(before, 'pair', { createBlock: slot => ({ ...root, ...slot, id: 'new-slot' }) });
  assert.deepEqual(ids(next), ['root', 'new-slot', 'child']);
  assert.equal(next[2].hookFrameId, undefined);
  assert.equal(next[2].layoutRowId, undefined);
});

test('hook restoration creates slots when its only AI card is a followup of an original in another section', () => {
  const root = { ...spaces.detachSpaceMembership(originals(1)[0]), id: 'root', sectionRole: 'studio', sectionId: 'studio', cutType: 'horizon', contentRole: 'fit' };
  const child = { ...followup(root), sectionRole: 'hooking', sectionId: 'hook', contentRole: 'hero' };
  let count = 0;
  const next = applyHookStyle([child, root], 'pair', { createBlock: slot => ({ ...root, ...slot,
    id: `slot${++count}`, sectionRole: 'hooking', sectionId: 'hook' }) });
  assert.deepEqual(ids(next), ['slot1', 'slot2', 'child', 'root']);
  assert.equal(next[2].hookFrameId, undefined);
  assert.equal(next[2].similarSourceBlockId, 'root');
});

test('inspector scope tells how many similar cuts follow the selected original', async () => {
  const jsx = code.match(/\{selected && !setPicker && <span>.*?<\/span>\}/)[0];
  const { code: rendered } = await transformWithEsbuild(`return <>${jsx}</>;`, 'scope.jsx', { loader: 'jsx' });
  const React = { createElement: (type, props, ...children) => ({ type, props, children }), Fragment: 'fragment' };
  for (const inSet of [false, true]) {
    const selected = inSet ? originals(1)[0] : spaces.detachSpaceMembership(originals(1)[0]);
    for (const count of [0, 2]) {
      const blocks = [selected, ...Array.from({ length: count }, (_, i) => followup(selected, `child${i}`))];
      const context = { React, selected, setPicker: null, blocks, selectedSimilarCount: count };
      const result = evaluate(rendered, context);
      assert.equal(result.children[0].children[0], count ? '이 사진과 비슷한 컷 2장에 함께 적용' : '아래 설정은 이 사진에만 적용');
    }
  }
});
