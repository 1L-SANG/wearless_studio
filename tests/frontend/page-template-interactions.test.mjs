import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { moveTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { addTemplateSection, sectionRemovalImpact } from '../../src/lib/pageTemplates/sectionActions.js';
import { automaticSectionInsertionKey } from '../../src/lib/pageTemplates/sectionPlacement.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';

const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const evaluate = (code, scope) => new Function('scope', `with (scope) { ${code} }`)(scope);
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }, { id: 'extra' }] };
const analysis = { pageStyle: defaultPageStyle() };

test('the live reorder action returns keyboard focus to the moved handle and reveals it in the outline', () => {
  const blocks = seedStoryboard(product, analysis), key = blocks.find(block => block.pageSectionKind === 'review').pageSectionKey;
  const frames = [], calls = [];
  const row = { querySelector: () => ({ focus: () => calls.push('focus') }), scrollIntoView: options => calls.push(options.block) };
  const scope = { blocks, moveTemplateSection, atomicSavingRef: { current: false }, sectionRefs: { current: new Map([[key, row]]) },
    setBlocks() {}, showUndo() {}, requestAnimationFrame: callback => frames.push(callback) };
  const move = evaluate(`${slice('  const moveSection =', '  const changeDesign =')};return moveSection;`, scope);
  move(key, blocks.find(block => block.pageSectionKind === 'color_product').pageSectionKey);
  assert.equal(frames.length, 1); frames[0](); assert.deepEqual(calls, ['focus', 'nearest']);
});

// Execute production handlers with real catalog, seed and example assignment.
// Only React state and the timer/DOM boundaries are replaced; no browser runs.
function boardHarness() {
  let blocks = seedStoryboard(product, analysis), insertedKey = null, undo = null;
  const opened = [], schedules = [];
  const undoEntryRef = { current: null };
  const scope = {
    addTemplateSection, sectionRemovalImpact, automaticSectionInsertionKey, sectionRuns, assignGenerationExamples, pageTemplatesCatalog, templateSectionName, drawer: null,
    catalogs: { detailProduct: product, analysis, genExamples: [] },
    activeSectionKey: null, captureSectionPreview: () => null, locked: false, atomicSavingRef: { current: false }, projectId: 'woq2', identityKind: null,
    exampleGender: 'women', selectedId: null, setPicker: null,
    undoEntryRef, setUndoEntry: value => { undo = value; }, setUndoExiting() {},
    scheduleUndoDismiss: entry => schedules.push(entry),
    dismissUndo: () => { undoEntryRef.current = null; undo = null; },
    setBlocks: value => { blocks = value; }, finishEdit() {}, setSelectedId() {}, setSelectedViewId() {}, setSetPicker() {}, setDrawer() {}, setPendingRemoval() {},
    setInsertedSectionKey: value => { insertedKey = value; },
    setActiveSectionKey: key => opened.push(key), setOverview() {}, closeInspector() {},
    openRenderGroup: key => opened.push(key), toast: { push() {} },
  };
  Object.defineProperty(scope, 'blocks', { get: () => blocks });
  const actions = evaluate(`
    ${slice('  const showUndo =', '  const clearUndoForSnapshot =')}
    ${slice('  const undoLatest =', '  const enterUndo =')}
    ${slice('  const commitSectionRemoval =', '  async function retryAtomicSave()')}
    return { addSection, removeSection, undoLatest, showUndo };
  `, scope);
  return { ...actions, opened, schedules, scope,
    get blocks() { return blocks; }, get insertedKey() { return insertedKey; }, get undo() { return undo; },
  };
}

test('adding styling opens the new section, queues its reveal and announces exactly two cuts', () => {
  const h = boardHarness(), before = h.blocks;
  h.addSection('wear', 'styling');
  const added = h.blocks.filter(b => !before.some(previous => previous.id === b.id));
  assert.equal(added.length, 2);
  assert.equal(h.insertedKey, added[0].pageSectionKey);
  assert.deepEqual(h.opened, [added[0].pageSectionKey]);
  assert.equal(h.undo.message, '일상 착용컷 섹션을 넣었어요');
  assert.equal(h.undo.operationCount, 1);
  assert.equal(h.undo.standalone, true);
  assert.equal(new Set(added.map(b => b.spaceGroupId)).size, 1);
  assert.ok(added.every(b => b.colorId === 'base' && b.spaceGroupId));
  h.undoLatest();
  assert.deepEqual(h.blocks, before);
  assert.equal(h.insertedKey, null);
});

test('wear removals open a preflight dialog while other sections remove immediately', () => {
  const h = boardHarness(), before = h.blocks, pending = [];
  h.scope.setPendingRemoval = value => pending.push(value);
  const wearKey = before.find(block => block.pageSectionKind === 'wear').pageSectionKey;
  h.removeSection(wearKey);
  assert.equal(h.blocks, before); assert.equal(pending[0].key, wearKey); assert.ok(pending[0].impact.confirmationRequired);
  const key = before.find(block => block.pageSectionKind === 'review').pageSectionKey;
  h.removeSection(key); assert.equal(h.blocks.some(block => block.pageSectionKey === key), false);
  h.undoLatest(); assert.deepEqual(h.blocks, before);
});

test('add, add and remove are undone one at a time, preserving an earlier setting change', () => {
  const h = boardHarness(), original = h.blocks;
  const edited = original.map((b, i) => i ? b : { ...b, angle: 'high' });
  h.scope.setBlocks(edited); h.showUndo(original, edited, { label: '각도' });
  h.addSection('wear', 'styling'); const first = h.blocks;
  h.addSection('sns'); const second = h.blocks;
  assert.equal(h.undo.message, 'SNS 화면 섹션을 넣었어요');
  assert.equal(h.undo.operationCount, 1);
  const reviewKey = h.blocks.find(b => b.pageSectionKind === 'review').pageSectionKey;
  h.removeSection(reviewKey);
  assert.equal(h.undo.message, '후기 섹션을 뺐어요');
  assert.equal(h.undo.operationCount, 1);
  assert.equal(h.blocks.some(b => b.pageSectionKey === reviewKey), false);
  for (const expected of [second, first, edited, original]) {
    h.undoLatest(); assert.deepEqual(h.blocks, expected);
  }
  assert.equal(h.undo, null);
});

test('settings after an isolated section change stay in their own group', () => {
  const h = boardHarness(), before = h.blocks;
  h.addSection('wear', 'fit'); const added = h.blocks;
  assert.equal(h.undo.message, '스튜디오 착용컷 섹션을 넣었어요');
  const one = [...added], two = [...one];
  h.scope.setBlocks(one); h.showUndo(added, one, { label: '예시' });
  h.scope.setBlocks(two); h.showUndo(one, two, { label: '색상' });
  assert.equal(h.undo.operationCount, 2);
  assert.equal(h.undo.message, '2건 변경');
  h.undoLatest(); assert.deepEqual(h.blocks, added);
  assert.equal(h.undo.operationCount, 1);
  h.undoLatest(); assert.deepEqual(h.blocks, before);
});

test('adding an existing singleton and removing an unknown key create no undo or reveal', () => {
  const h = boardHarness(), before = h.blocks;
  h.addSection('first'); h.removeSection('unknown');
  assert.equal(h.blocks, before);
  assert.equal(h.undo, null);
  assert.equal(h.insertedKey, null);
  assert.deepEqual(h.opened, []);
});

for (const reduced of [false, true]) test(`inserted section scroll/highlight waits for flight then lasts one second and cleans up (reduced motion: ${reduced})`, () => {
  const scrolls = [], frames = new Map(), timers = new Map(); let cleanup, insertedKey = 'emo.wear_1';
  const effect = slice('  useEffect(() => {\n    if (!insertedSectionKey)', '  useLayoutEffect(() => { composeModeSeedRef');
  evaluate(effect, {
    insertedSectionKey: insertedKey,
    sectionRefs: { current: new Map([[insertedKey, { scrollIntoView: options => scrolls.push(options) }]]) },
    prefersReducedMotion: () => reduced,
    setInsertedSectionKey: value => { insertedKey = value; },
    setActiveSectionKey: key => opened.push(key), setOverview() {}, closeInspector() {},
    useEffect: fn => { cleanup = fn(); },
    requestAnimationFrame: fn => { frames.set(1, fn); return 1; }, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: (fn, delay) => { assert.equal(delay, reduced ? 1000 : 1520); timers.set(2, fn); return 2; }, clearTimeout: id => timers.delete(id),
  });
  assert.deepEqual(scrolls, []);
  frames.get(1)();
  assert.deepEqual(scrolls, [{ block: 'nearest', behavior: 'instant' }]);
  timers.get(2)(); assert.equal(insertedKey, null);
  cleanup(); assert.equal(frames.size, 0); assert.equal(timers.size, 0);
});
