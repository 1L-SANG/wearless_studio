import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { assignGenerationExamples, canRerollGenerationExample } from '../../src/lib/generationExamples.js';
import { pickSignatureCut, repairSignatureCutSelections, signatureCutsFor } from '../../src/lib/signatureCutPool.js';
import { shuffleSectionExamples } from '../../src/lib/storyboardExampleShuffle.js';
import { seedStoryboard, seedProductKey, resolveStoryboardEntry, isUntouchedTemplateStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { withStoryboardSpaceSetExamples, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { rekeySeparatedSpaceRuns } from '../../src/lib/storyboardSpaceSets.js';
import { ensureSections } from '../../src/lib/sections.js';
import { hasDetailSource } from '../../src/lib/storyboardTaxonomy.js';
import { stripStaleSpaceSetBindings } from '../../src/lib/storyboardSpaceSetStaleness.js';
import { stripStaleExampleSelections } from '../../src/lib/storyboardExampleStaleness.js';
import { referenceFeedbackPatch } from '../../src/lib/storyboardExampleFeedback.js';
import { normalizeMatchIds } from '../../src/lib/api/matchingItems.js';
import { genderForClothingType } from '../../src/lib/productGender.js';
import { identityKindOf } from '../../src/lib/identityScope.js';
import { isRealModelSelection } from '../../src/features/analysis/modelSelection.js';
import { buildColorOpts, visibleColorOpts } from '../../src/lib/colorOpts.js';
import { uid } from '../../src/lib/ids.js';
import { splitAnalysisEditPatch } from '../../src/features/product-input/saveRouting.js';
import { loadStoryboardComponent, nodes } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

const product = (clothingType = 'bottom') => ({ clothingType, name: '판매자 상품',
  colors: [{ id: 'base', name: '블랙', isBase: true, images: [{ slot: 'Front', src: '/seller/front.webp' }] }] });
const analysis = (category = {}, gender = 'women') => ({ targetGenders: [gender],
  pageStyle: { ...defaultPageStyle(), templateId: 'show' }, ...category });
const signature = (over = {}) => ({ id: 'signature', source: 'ai', taxonomyVersion: 4,
  sectionId: 'opening', pageSectionKey: 'show.first', pageSectionKind: 'first', pageDesignId: 'P01-SHOW-01',
  slotKey: 'first.hero', sectionLayout: 'stack', cutType: 'horizon', contentRole: 'hero',
  colorId: 'base', shot: 'medium', direction: 'front', sideStyle: null, refScope: 'all', ...over });
const skirtCases = [
  ['mini_skirt', { subCategory: 'mini_skirt' }],
  ['midi_skirt', { subCategory: 'midi_skirt' }],
  ['long_skirt', { subCategory: 'long_skirt' }],
  ['legacy skirt', { subCategory: 'skirt' }],
  ['custom 치마', { subCategory: null, customCategory: '플리츠 치마' }],
  ['custom 스커트', { subCategory: null, customCategory: 'A라인 스커트' }],
];
const skirtIds = Array.from({ length: 8 }, (_, i) => `sig_women_bottom_skirt_${String(i + 2).padStart(2, '0')}`);
const pantsIds = Array.from({ length: 9 }, (_, i) => `sig_women_bottom_pants_${String(i + 1).padStart(2, '0')}`);

// The actual entry function runs all normalization and assignment passes. Only the
// persistence serializer is replaced because importing it also imports the API.
const prepareStoryboardEntry = await loadStoryboardComponent('Storyboard.jsx', 'prepareStoryboardEntry', {
  withStoryboardSpaceSetExamples, ensureSections, hasDetailSource, genderForClothingType,
  stripStaleSpaceSetBindings, stripStaleExampleSelections, referenceFeedbackPatch, normalizeMatchIds,
  rekeySeparatedSpaceRuns, spaceSetGroupId, uid, identityKindOf, isRealModelSelection,
  buildColorOpts, visibleColorOpts, assignGenerationExamples, sbStable: JSON.stringify,
});
const prepare = (p, a, blocks = seedStoryboard(p, a)) => (
  prepareStoryboardEntry([blocks, { genExamples: [] }, [], p, a], undefined, 'round3-signature')
);

for (const [name, category] of skirtCases) {
  test(`R3-1 actual storyboard entry assigns the Analysis ${name} skirt pool`, () => {
    const p = product(), a = analysis(category), snapshot = structuredClone(p);
    const result = prepare(p, a);
    assert.ok(skirtIds.includes(result.blocks[0].exampleId), result.blocks[0].exampleId);
    assert.equal(result.blocks[0].shot, 'medium');
    assert.equal(result.blocks[0].direction, 'front');
    assert.equal(result.blocks[0].refScope, 'all');
    assert.deepEqual(p, snapshot, 'Analysis classifications must never be added to Product');
  });

  test(`R3-1 actual photo panel pins one signature from the Analysis ${name} skirt pool`, async t => {
    const p = product(), a = analysis(category);
    const panel = await storyboardPhotoPanelHarness({ block: signature(), clothingType: 'bottom',
      subCategory: a.subCategory, customCategory: a.customCategory, exampleGender: 'women' });
    t.after(panel.close);
    const tree = panel.render({ catalogs: { detailProduct: p, analysis: a, genExamples: [],
      shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }] } });
    const gallery = nodes(tree).find(node => node.type === 'mood-guide');
    assert.ok(gallery);
    assert.deepEqual(gallery.props.catalogs.genExamples.map(item => item.id), skirtIds);
    assert.ok(skirtIds.includes(gallery.props.signature.id));
  });
}

test('R3-1 a seller-selected valid skirt signature survives actual entry and autofill', () => {
  const p = product(), a = analysis({ subCategory: 'midi_skirt' });
  const seller = signature({ exampleId: skirtIds[1], exampleSelectionOrigin: 'user', faceExposure: 'hide',
    matchIds: ['seller-match'], refImages: ['/seller/reference.png'], angle: 'same' });
  const result = prepare(p, a, [seller]).blocks[0];
  assert.equal(result.exampleId, skirtIds[1]);
  assert.equal(result.exampleSelectionOrigin, 'user');
  assert.equal(result.faceExposure, 'hide');
  assert.deepEqual(result.matchIds, ['seller-match']);
  assert.deepEqual(result.refImages, ['/seller/reference.png']);
  const saved = [result];
  assert.equal(assignGenerationExamples(saved, { catalog: [], product: p, analysis: a, gender: 'women' }).blocks, saved);
});

test('R3-1 explicit Analysis is authoritative over stale legacy Product categories', () => {
  const p = { ...product(), subCategory: 'long_skirt', customCategory: '스커트' };
  const a = analysis({ subCategory: null, customCategory: null });
  assert.deepEqual(signatureCutsFor({ ...p, analysis: a, gender: 'women' }).map(item => item.id), pantsIds);
  const chosen = assignGenerationExamples([signature()], { catalog: [], product: p, analysis: a, gender: 'women' }).blocks[0];
  assert.ok(pantsIds.includes(chosen.exampleId), chosen.exampleId);
});

test('R3-1 direct legacy Product categories still select a skirt when Analysis is omitted', () => {
  const chosen = assignGenerationExamples([signature()], { catalog: [],
    product: { clothingType: 'bottom', subCategory: '스커트' }, gender: 'women' }).blocks[0];
  assert.ok(skirtIds.includes(chosen.exampleId));
});

test('R3-1 direct pick and repair receive the Analysis category without replacing valid seller choices', () => {
  const context = { clothingType: 'bottom', gender: 'women', analysis: analysis({ customCategory: '치마' }), projectId: 'round3-signature' };
  assert.ok(skirtIds.includes(pickSignatureCut(context).id));
  const saved = [signature({ exampleId: skirtIds[1], exampleSelectionOrigin: 'user' })];
  const kept = repairSignatureCutSelections(saved, context);
  assert.equal(kept.blocks, saved);
  assert.equal(kept.changed, false);
  const repaired = repairSignatureCutSelections([signature({ exampleId: pantsIds[0] })], context);
  assert.ok(skirtIds.includes(repaired.blocks[0].exampleId));
  assert.deepEqual(repaired.repairedIds, ['signature']);
});

for (const onlyBlockId of [null, 'signature']) test(`R3-1 ${onlyBlockId ? 'single-card' : 'section'} reroll stays in the Analysis skirt pool`, () => {
  const before = [signature({ exampleId: skirtIds[0], exampleSelectionOrigin: 'auto' })];
  const options = { sectionId: 'opening', catalog: [], product: product(),
    analysis: analysis({ subCategory: 'midi_skirt' }), gender: 'women', onlyBlockId };
  assert.equal(canRerollGenerationExample(before[0], options), true);
  const next = shuffleSectionExamples(before, options);
  assert.ok(skirtIds.includes(next[0].exampleId));
  assert.notEqual(next[0].exampleId, skirtIds[0]);
  assert.equal(shuffleSectionExamples(before, options)[0].exampleId, next[0].exampleId);
  assert.equal(before[0].exampleId, skirtIds[0]);
});

for (const [clothingType, gender, category, expected] of [
  ['bottom', 'women', {}, pantsIds],
  ['bottom', 'women', { subCategory: 'shorts' }, pantsIds],
  ['bottom', 'men', { subCategory: 'midi_skirt' }, [1,3,4,5,6,7,8].map(i => `sig_men_bottom_pants_${String(i).padStart(2,'0')}`)],
  ['top', 'men', { subCategory: 'shirt' }, ['sig_men_01', 'sig_men_02', 'sig_men_03', 'sig_men_04', 'sig_men_05']],
  ['top', 'women', { subCategory: 'knit' }, ['sig_women_01', 'sig_women_02', 'sig_women_03', 'sig_women_04']],
  ['outer', 'women', { customCategory: '코트' }, ['sig_women_01', 'sig_women_02', 'sig_women_03', 'sig_women_04']],
  ['dress', 'women', { subCategory: 'long_dress' }, Array.from({length:6},(_,i)=>`sig_women_dress_${String(i+1).padStart(2,'0')}`)],
]) test(`R3-1 actual entry preserves the ${gender}/${clothingType}/${category.subCategory || 'unknown'} pool`, () => {
  const result = prepare(product(clothingType), analysis(category, gender));
  assert.ok(expected.includes(result.blocks[0].exampleId), result.blocks[0].exampleId);
});

test('R3-1 a direct men/dress signature context retains the existing upper-body fallback', () => {
  assert.deepEqual(signatureCutsFor({ clothingType: 'dress', gender: 'men', analysis: analysis() }).map(item => item.id),
    ['sig_men_01', 'sig_men_02', 'sig_men_03', 'sig_men_04', 'sig_men_05']);
});

for (const category of [{ subCategory: 'midi_skirt' }, { customCategory: '플리츠 치마' }]) {
  const name = Object.keys(category)[0];
  test(`R3-1 changing Analysis ${name} rebuilds an untouched board and keeps its next entry stable`, () => {
    const p = product(), beforeAnalysis = analysis(), nextAnalysis = analysis(category);
    const saved = seedStoryboard(p, beforeAnalysis);
    assert.equal(isUntouchedTemplateStoryboard(saved), true);
    assert.notEqual(seedProductKey(p, beforeAnalysis), seedProductKey(p, nextAnalysis));
    const rebuilt = resolveStoryboardEntry(saved, p, nextAnalysis).blocks;
    assert.notEqual(rebuilt, saved);
    assert.ok(skirtIds.includes(prepare(p, nextAnalysis, rebuilt).blocks[0].exampleId));
    assert.equal(resolveStoryboardEntry(rebuilt, p, nextAnalysis).blocks, rebuilt);
  });

  test(`R3-1 changing Analysis ${name} retains an edited board list and seller edits`, () => {
    const p = product(), a = analysis(), saved = seedStoryboard(p, a);
    const edited = saved.map((block, index) => index === 0 ? { ...block, faceExposure: 'hide',
      exampleId: skirtIds[1], exampleSelectionOrigin: 'user', refImages: ['/seller/reference.webp'] } : block);
    assert.equal(isUntouchedTemplateStoryboard(edited), false);
    const snapshot = structuredClone(edited);
    assert.equal(resolveStoryboardEntry(edited, p, analysis(category)).blocks, edited);
    assert.deepEqual(edited, snapshot);
  });
}

test('R3-1 an untouched pre-category seed key rebuilds once even when its category is unknown', () => {
  const p = product(), a = analysis(), saved = seedStoryboard(p, a);
  const oldKey = JSON.parse(saved[0].seedProductKey);
  delete oldKey.subCategory;
  delete oldKey.customCategory;
  saved[0].seedProductKey = JSON.stringify(oldKey);
  const rebuilt = resolveStoryboardEntry(saved, p, a).blocks;
  assert.notEqual(rebuilt, saved);
  assert.equal(resolveStoryboardEntry(rebuilt, p, a).blocks, rebuilt);
});

test('R3-1 an edited pre-category seed key preserves the exact saved list', () => {
  const p = product(), a = analysis(), saved = seedStoryboard(p, a);
  const oldKey = JSON.parse(saved[0].seedProductKey);
  delete oldKey.subCategory;
  delete oldKey.customCategory;
  saved[0].seedProductKey = JSON.stringify(oldKey);
  saved[0].faceExposure = 'hide';
  const snapshot = structuredClone(saved);
  assert.equal(resolveStoryboardEntry(saved, p, analysis({ subCategory: 'midi_skirt' })).blocks, saved);
  assert.deepEqual(saved, snapshot);
});

test('R3-1 category edits remain Analysis-owned', () => {
  assert.deepEqual(splitAnalysisEditPatch({ subCategory: 'midi_skirt', customCategory: '치마' }), {
    productPatch: {}, analysisPatch: { subCategory: 'midi_skirt', customCategory: '치마' },
  });
});

test('R3-1 Analysis category edits invalidate the actual warmed entry before the save finishes', async t => {
  const vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  t.after(() => vite.close());
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  const prefetch = await vite.ssrLoadModule('/src/features/storyboard/storyboardEntryPrefetch.js');
  const { persistAnalysisEdit } = await vite.ssrLoadModule('/src/features/product-input/saveRouting.js');
  for (const patch of [{ subCategory: 'midi_skirt' }, { customCategory: '스커트' }]) {
    prefetch.invalidateStoryboardEntryPrefetch(DB.project.id);
    await prefetch.prefetchStoryboardEntry(DB.project.id);
    assert.ok(prefetch.peekStoryboardEntry(DB.project.id));
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const save = persistAnalysisEdit({
      saveProduct() { assert.fail('Analysis category fields must not be written to Product'); },
      saveAnalysis: () => pending,
    }, DB.project.id, patch);
    assert.equal(prefetch.peekStoryboardEntry(DB.project.id), null, JSON.stringify(patch));
    finish(patch);
    await save;
  }
});
