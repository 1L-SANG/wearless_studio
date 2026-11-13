import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import detailRecommendations from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignAxes } from '../../src/lib/pageTemplates/designAxes.js';
import { currentSectionDesignId, sectionDesignChoices, sectionDesignGenerationCount } from '../../src/lib/pageTemplates/designChoices.js';
import { changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { addStageAddition, stageAdditions } from '../../src/lib/pageTemplates/stageAdditions.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';

const product = { name: '셔츠', clothingType: 'top', colors: [
  { id: 'base', isBase: true, name: '블루', images: [{ slot: 'Front', src: '/front.jpg' }] },
  { id: 'cream', name: '아이보리', images: [{ slot: 'Front', src: '/cream.jpg' }] },
] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle(), detailRecommendations };
const daily = blocks => sectionRuns(blocks).find(run => run.kind === 'wear' && run.usage === 'styling');
function board(count, designId = 'P02-D01') {
  const original = seedStoryboard(product, analysis), run = daily(original);
  const photo = run.blocks.find(block => block.source !== 'text');
  const photos = Array.from({ length: count }, (_, index) => ({ ...photo, id: `woc12-wear-${index}`,
    pageDesignId: designId, slotKey: index === count - 1 ? null : `wear_styling.g${index + 1}` }));
  return [...original.slice(0, run.start), ...photos, ...original.slice(run.end)];
}
const axes = blocks => sectionDesignAxes(daily(blocks), { storyboard: blocks, product });
const choices = blocks => sectionDesignChoices(daily(blocks), { storyboard: blocks, product });
const withoutDesign = blocks => blocks.map(({ pageDesignId, ...block }) => block);

for (const [total, expected] of [
  [1, ['WEAR-STYLING-FRAME-COLUMN-01']],
  [2, ['WEAR-STYLING-FRAME-COLUMN-02']],
  [3, ['WEAR-STYLING-FRAME-COLUMN-02']],
  [4, ['WEAR-STYLING-FRAME-COLUMN-02', 'WEAR-STYLING-FRAME-2COL-02']],
  [5, ['WEAR-STYLING-FRAME-COLUMN-02', 'WEAR-STYLING-FRAME-2COL-02', 'WEAR-STYLING-FRAME-HERO-02']],
  [6, ['WEAR-STYLING-FRAME-COLUMN-02', 'WEAR-STYLING-FRAME-2COL-02', 'WEAR-STYLING-FRAME-HERO-02']],
]) test(`WOC12 ${total} photos offer only the automatic frame count and eligible remainder layouts`, () => {
  const blocks = board(total), before = structuredClone(blocks), offered = choices(blocks);
  assert.deepEqual(offered.filter(design => design.framePhotos).map(design => design.id), expected);
  assert.equal(axes(blocks).decorations.find(option => option.id === 'frame').designId, expected[0]);
  assert.equal(Object.hasOwn(axes(blocks), 'framePhotoChoices'), false);
  assert.ok(offered.every(design => sectionDesignGenerationCount(daily(blocks), design.id, blocks, product) === total));
  const framed = changeTemplateSectionDesign(blocks, daily(blocks).key, expected[0], { product });
  assert.deepEqual(axes(framed).layouts.map(option => option.designId), expected);
  assert.deepEqual(blocks, before);
});

test('WOC12 an empty styling run has no frame candidate or decoration', () => {
  const run = { ...daily(board(1)), blocks: [] };
  assert.ok(sectionDesignChoices(run, { storyboard: [], product }).every(design => !design.framePhotos));
  assert.ok(sectionDesignAxes(run, { storyboard: [], product }).decorations.every(option => option.id !== 'frame'));
});

for (const [total, initial, target] of [
  [1, 'P02-D01', 'WEAR-STYLING-FRAME-COLUMN-01'],
  [2, 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [3, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [4, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [4, 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-2COL-02'],
  [5, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-HERO-02'],
  [4, 'WEAR-STYLING-FRAME-HERO-03', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [4, 'WEAR-STYLING-FRAME-2COL-03', 'WEAR-STYLING-FRAME-2COL-02'],
  [5, 'WEAR-STYLING-FRAME-HERO-03', 'WEAR-STYLING-FRAME-HERO-02'],
]) test(`WOC12 frame action from ${initial} with ${total} photos preserves only an eligible layout`, () => {
  const blocks = board(total, initial), value = axes(blocks);
  assert.equal(value.decorations.find(option => option.id === 'frame').designId, target);
  assert.equal(value.decorations.find(option => option.id === 'basic').isDefault, true);
  assert.equal(value.layouts[0].id, 'column');
  assert.equal(value.layouts[0].isDefault, true);
});

for (const [initial, layout, total, expectedLayouts] of [
  ['WEAR-STYLING-FRAME-COLUMN-03', 'column', 3, ['column']],
  ['WEAR-STYLING-FRAME-2COL-03', 'twoColumn', 4, ['column', 'twoColumn']],
  ['WEAR-STYLING-FRAME-HERO-03', 'heroPair', 5, ['column', 'twoColumn', 'heroPair']],
]) test(`WOC12 saved ${initial} reads unchanged and selecting its current layout explicitly moves to two frames`, () => {
  const blocks = board(total, initial), saved = structuredClone(blocks);
  const before = assemblePage({ storyboard: blocks, product, analysis }), value = axes(blocks);
  assert.equal(currentSectionDesignId(daily(blocks), blocks), initial);
  assert.equal(value.currentLayoutId, layout);
  assert.equal(value.currentDecorationId, 'frame');
  assert.equal(value.decorations.find(option => option.id === 'frame').selected, true);
  assert.deepEqual(value.layouts.map(option => option.id), expectedLayouts);
  const selected = value.layouts.find(option => option.id === layout);
  assert.equal(selected.selected, true);
  assert.equal(selected.designId, initial.replace(/03$/, '02'));
  assert.deepEqual(blocks, saved);
  assert.deepEqual(assemblePage({ storyboard: blocks, product, analysis }), before);
  const next = changeTemplateSectionDesign(blocks, daily(blocks).key, selected.designId, { product });
  assert.equal(daily(next).designId, initial.replace(/03$/, '02'));
  assert.deepEqual(withoutDesign(next), withoutDesign(saved));
});

test('WOC12 exactly the three saved three-photo frames are hidden while their render definitions remain available', () => {
  const frames = Object.values(catalog.designs).filter(design => design.framePhotos);
  assert.equal(frames.length, 9);
  assert.deepEqual(frames.filter(design => design.pickerHidden).map(design => design.id), [
    'WEAR-STYLING-FRAME-COLUMN-03', 'WEAR-STYLING-FRAME-2COL-03', 'WEAR-STYLING-FRAME-HERO-03',
  ]);
  for (const design of frames) assert.equal(design.layout.find(node => node.type === 'photoFrame').count, design.framePhotos);
});

test('WOC12 saved three-photo frame fixtures still assemble to their original expected output', () => {
  const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url), 'utf8'));
  const cases = fixture.cases.filter(item => /^woc6b-(emo|show)-(COLUMN|2COL|HERO)-3$/.test(item.name));
  assert.equal(cases.length, 6);
  for (const item of cases) {
    const before = structuredClone(item.input);
    const run = daily(item.input.storyboard);
    const value = sectionDesignAxes(run, { storyboard: item.input.storyboard, product: item.input.product });
    assert.equal(value.currentDecorationId, 'frame');
    assert.deepEqual(assemblePage(item.input), item.expected, item.name);
    assert.deepEqual(item.input, before);
  }
});

function designActionHarness(initial) {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
  let state = initial;
  const undoEntryRef = { current: null }, scope = { blocks: initial, changeTemplateSectionDesign,
    drawer: { mode: 'design', runKey: daily(initial).key }, catalogs: { detailProduct: product },
    undoEntryRef, setBlocks: value => { state = value; }, setUndoExiting() {}, setUndoEntry() {}, scheduleUndoDismiss() {},
    dismissUndo: () => { undoEntryRef.current = null; }, setPicker: null, setInsertedSectionKey() {}, toast: { push() {} },
  };
  const handlers = new Function('scope', `with (scope) {
    ${slice('  const showUndo =', '  const clearUndoForSnapshot =')}
    ${slice('  const undoLatest =', '  const enterUndo =')}
    ${slice('  const changeDesign =', '  const selectSection =')}
    return { changeDesign, undoLatest };
  }`)(scope);
  return { get blocks() { return state; }, get undo() { return undoEntryRef.current; },
    choose(id) { scope.blocks = state; handlers.changeDesign(id); },
    undoLatest() { scope.blocks = state; handlers.undoLatest(); },
  };
}

test('WOC12 actual design action rejects nonautomatic counts, changes only the full run design and undoes exactly', () => {
  const original = board(4, 'WEAR-STYLING-FRAME-HERO-03'), saved = structuredClone(original);
  const key = daily(original).key, cuts = uniqueGenerationCutCount(original, product), h = designActionHarness(original);
  for (const invalid of ['WEAR-STYLING-FRAME-COLUMN-01', 'WEAR-STYLING-FRAME-COLUMN-03', 'WEAR-STYLING-FRAME-HERO-02']) {
    h.choose(invalid);
    assert.equal(h.blocks, original); assert.equal(h.undo, null);
  }
  h.choose(axes(h.blocks).decorations.find(option => option.id === 'frame').designId);
  assert.ok(h.blocks.filter(block => block.pageSectionKey === key).every(block => block.pageDesignId === 'WEAR-STYLING-FRAME-COLUMN-02'));
  assert.equal(validateStoryboardV4(h.blocks), h.blocks);
  assert.equal(h.undo.operationCount, 1); assert.equal(h.undo.standalone, true);
  assert.equal(uniqueGenerationCutCount(h.blocks, product), cuts);
  assert.deepEqual(withoutDesign(h.blocks), withoutDesign(original));
  assert.deepEqual(original, saved);
  h.undoLatest();
  assert.deepEqual(h.blocks, saved); assert.equal(h.undo, null);
});

test('WOC12 fit, color, flat sections and first attachments keep their choices and cut counts', () => {
  const blocks = seedStoryboard(product, analysis);
  const expected = {
    first: ['P01-D09', 'P01-SHOW-01'], color_product: ['CP-COLUMN-01', 'CP-EMO-01'],
    points: ['PT-PHOTO-CARD-01', 'PT-TEXT-PHOTO-01'], review: ['I09-D06'],
    intro: ['T01-D07', 'T06-D01'], for_you: ['T08-D01'], faq: ['T08-D07'], fabric: ['T03-D01', 'T03-D06'],
  };
  for (const run of sectionRuns(blocks).filter(run => run.kind !== 'wear' || run.usage === 'fit')) {
    const offered = sectionDesignChoices(run, { storyboard: blocks, product });
    assert.deepEqual(offered.map(design => design.id), run.usage === 'fit'
      ? ['WEAR-FIT-COLUMN-01', 'WEAR-FIT-2COL-01', 'WEAR-FIT-HERO-01'] : expected[run.kind], run.kind);
    assert.ok(sectionDesignAxes(run, { storyboard: blocks, product }).decorations.every(option => option.id !== 'frame'));
  }
  const first = sectionRuns(blocks).find(run => run.kind === 'first');
  const ghost = stageAdditions(blocks, first.key, { product, analysis }).find(item => item.id === 'firstGhost');
  const paired = addStageAddition(blocks, first.key, ghost, { product, analysis });
  const next = changeTemplateSectionDesign(paired, first.key, 'P01-SHOW-01', { product });
  assert.equal(sectionRuns(next).find(run => run.kind === 'first').designId, 'P01-SHOW-01-G');
  assert.equal(uniqueGenerationCutCount(next, product), uniqueGenerationCutCount(blocks, product));
});
