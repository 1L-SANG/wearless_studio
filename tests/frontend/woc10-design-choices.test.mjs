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
  const photos = Array.from({ length: count }, (_, index) => ({ ...photo, id: `woc10-wear-${index}`,
    pageDesignId: designId, slotKey: index === count - 1 ? null : `wear_styling.g${index + 1}` }));
  return [...original.slice(0, run.start), ...photos, ...original.slice(run.end)];
}
const axes = blocks => sectionDesignAxes(daily(blocks), { storyboard: blocks, product });
const choices = blocks => sectionDesignChoices(daily(blocks), { storyboard: blocks, product });
const frameIds = blocks => choices(blocks).filter(design => design.framePhotos).map(design => design.id);
const withoutDesign = blocks => blocks.map(({ pageDesignId, ...block }) => block);

test('WOC10 four photos offer column and two-column layouts with two automatic framed photos', () => {
  const blocks = board(4, 'WEAR-STYLING-FRAME-COLUMN-02');
  assert.deepEqual(frameIds(blocks), [
    'WEAR-STYLING-FRAME-COLUMN-02', 'WEAR-STYLING-FRAME-2COL-02',
  ]);
  assert.deepEqual(axes(blocks).layouts.map(option => [option.id, option.designId]), [
    ['column', 'WEAR-STYLING-FRAME-COLUMN-02'], ['twoColumn', 'WEAR-STYLING-FRAME-2COL-02'],
  ]);
  assert.deepEqual(axes(board(4, 'WEAR-STYLING-FRAME-COLUMN-03')).layouts.map(option => option.id), ['column', 'twoColumn']);
});

test('WOC15 a saved one-frame hero projects to the available automatic two-frame column', () => {
  const blocks = board(4, 'WEAR-STYLING-FRAME-HERO-01'), before = structuredClone(blocks);
  const value = axes(blocks);
  assert.equal(Object.hasOwn(value, 'framePhotoChoices'), false);
  assert.equal(value.currentLayoutId, 'column');
  assert.equal(value.decorations.find(option => option.id === 'frame').selected, true);
  assert.equal(value.decorations.find(option => option.id === 'frame').designId, 'WEAR-STYLING-FRAME-COLUMN-02');
  assert.deepEqual(blocks, before);
});

for (const [total, frameCount, expectedLayouts] of [
  [1, 1, ['column']], [2, 1, ['column']], [2, 2, ['column']], [3, 3, ['column']],
  [3, 1, ['column']], [4, 1, ['column', 'twoColumn']],
  [5, 3, ['column', 'twoColumn', 'heroPair']], [6, 3, ['column', 'twoColumn', 'heroPair']],
]) test(`WOC10 ${total} actual photos with ${frameCount} saved frames expose layouts for the automatic remainder`, () => {
  const blocks = board(total, `WEAR-STYLING-FRAME-COLUMN-0${frameCount}`), value = axes(blocks);
  assert.deepEqual(value.layouts.map(option => option.id), expectedLayouts);
  assert.equal(value.currentDecorationId, 'frame');
  assert.equal(value.currentLayoutId, 'column');
  assert.equal(currentSectionDesignId(daily(blocks), blocks), `WEAR-STYLING-FRAME-COLUMN-0${frameCount}`);
  assert.ok(value.layouts.every(option => catalog.designs[option.designId].framePhotos === (total === 1 ? 1 : 2)));
  assert.ok(choices(blocks).every(design => sectionDesignGenerationCount(daily(blocks), design.id, blocks, product) === total));
});

for (const [total, initial, target] of [
  [1, 'P02-D01', 'WEAR-STYLING-FRAME-COLUMN-01'],
  [2, 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [3, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [4, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-COLUMN-02'],
  [4, 'WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-2COL-02'],
  [5, 'WEAR-STYLING-HERO-01', 'WEAR-STYLING-FRAME-HERO-02'],
]) test(`WOC10 entering frame from ${initial} with ${total} photos chooses the automatic count first`, () => {
  const blocks = board(total, initial), value = axes(blocks);
  assert.equal(value.decorations.find(option => option.id === 'frame')?.designId, target);
  assert.deepEqual(value.layouts.map(option => option.id), ['column', 'twoColumn', 'heroPair'].slice(0, Math.min(3, total)));
  assert.equal(value.layouts.find(option => option.id === 'column').isDefault, true);
  assert.equal(value.decorations.find(option => option.id === 'basic').isDefault, true);
});

test('WOC15 an ineligible saved frame projects valid axes without changing its preview or saved blocks', () => {
  const blocks = board(4, 'WEAR-STYLING-FRAME-HERO-03'), saved = structuredClone(blocks);
  const before = assemblePage({ storyboard: blocks, product, analysis });
  const value = axes(blocks);
  assert.equal(currentSectionDesignId(daily(blocks), blocks), 'WEAR-STYLING-FRAME-HERO-03');
  assert.equal(value.currentLayoutId, 'column');
  assert.equal(value.currentDecorationId, 'frame');
  assert.deepEqual(value.layouts.map(option => [option.id, option.selected]), [['column', true], ['twoColumn', false]]);
  assert.equal(value.decorations.find(option => option.id === 'frame').selected, true);
  assert.equal(value.decorations.find(option => option.id === 'frame').designId, 'WEAR-STYLING-FRAME-COLUMN-02');
  assert.deepEqual(blocks, saved);
  assert.deepEqual(assemblePage({ storyboard: blocks, product, analysis }), before);
});

function designActionHarness(initial) {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
  let state = initial;
  const undoEntryRef = { current: null }, scope = { blocks: initial, changeTemplateSectionDesign,
    drawer: { mode: 'design', runKey: daily(initial).key }, catalogs: { detailProduct: product },
    detailPageSubmitRef: { current: false }, undoEntryRef, setBlocks: value => { state = value; }, setUndoExiting() {}, setUndoEntry() {}, scheduleUndoDismiss() {},
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

test('WOC10 real design action rejects invalid IDs, changes the full run once, and undoes the fallback exactly', () => {
  const original = board(4, 'WEAR-STYLING-FRAME-HERO-03'), saved = structuredClone(original);
  const key = daily(original).key, cuts = uniqueGenerationCutCount(original, product), h = designActionHarness(original);
  for (const id of ['WEAR-STYLING-FRAME-HERO-02', 'WEAR-STYLING-FRAME-COLUMN-01', 'WEAR-STYLING-FRAME-COLUMN-03']) {
    h.choose(id);
    assert.equal(h.blocks, original); assert.equal(h.undo, null);
  }
  const option = axes(h.blocks).decorations.find(choice => choice.id === 'frame');
  h.choose(option.designId);
  assert.ok(h.blocks.filter(block => block.pageSectionKey === key).every(block => block.pageDesignId === 'WEAR-STYLING-FRAME-COLUMN-02'));
  assert.equal(validateStoryboardV4(h.blocks), h.blocks);
  assert.equal(h.undo.operationCount, 1); assert.equal(h.undo.standalone, true);
  assert.equal(uniqueGenerationCutCount(h.blocks, product), cuts);
  assert.deepEqual(withoutDesign(h.blocks), withoutDesign(original));
  assert.deepEqual(original, saved);
  h.undoLatest();
  assert.deepEqual(h.blocks, saved); assert.equal(h.undo, null);
});

test('WOC10 fit, color, flat sections and first attachments keep their existing choices', () => {
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
