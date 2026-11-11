import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { pageTemplatesCatalog, getTemplate, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignChoices } from '../../src/lib/pageTemplates/designChoices.js';
import { normalizedRecipePatch } from '../../src/lib/storyboardTaxonomy.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { storyboardPreviewCuts } from '../../src/features/storyboard/storyboardPreview.js';
import rawExamples from '../../src/data/genExamples.json' with { type: 'json' };
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { storyboardSpaceSetsFor, spaceSetGroupId, spaceSetIdFromGroupId, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';
import * as generation from '../../src/lib/generationExamples.js';
import { filterExamplesForModel, filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { examplePlace, filterGalleryExamples, visiblePlaceTabs } from '../../src/lib/pageTemplates/storyboardGallery.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import { loadStoryboardComponent, hookHarness, nodes, textOf } from './helpers/storyboardComponents.mjs';

const product = { name: '셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
const catalogs = withStoryboardSpaceSetExamples({ genExamples: [] });
const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(set => set.setType === 'styling');
let vite, Picker, Stage;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
});
after(async () => { await vite?.close(); });

test('WOC1b-2 a location-set photo opens with its own set first and the full gallery collapsed', async () => {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', { ...h.hooks, ...generation,
    filterExamplesForModel, filterSpaceSetsForModel, storyboardSpaceSetsFor, spaceSetDisplayName, examplePlace,
    filterGalleryExamples, visiblePlaceTabs, isSourceBasedDetailRecipe, ExampleThumb: 'example-thumb', MineImageTab: 'mine-images', ShotSegment: 'shots', Icon: 'svg' });
  const chosen = [], props = { catalogs, cut: 'styling', shot: 'full', clothingType: 'top', gender: 'women',
    exampleId: set.members[0].exampleId, spaceContext: { set }, onExampleChange: id => chosen.push(id) };
  let tree = h.render(Gallery, props);
  assert.match(textOf(tree), /같은 장소의 다른 사진/);
  assert.ok(!textOf(tree).includes('단일컷'));
  const expand = nodes(tree).find(node => node.type === 'button' && textOf(node).includes('이 컷만 다른 사진으로 바꾸기'));
  assert.equal(expand.props['aria-expanded'], false); expand.props.onClick();
  tree = h.render(Gallery, props); assert.match(textOf(tree), /단일컷세트컷/); assert.match(textOf(tree), /이 사진만 바뀌어요/);
  const collapse = nodes(tree).find(node => node.type === 'button' && textOf(node) === '접기');
  collapse.props.onClick(); tree = h.render(Gallery, props); assert.ok(!textOf(tree).includes('단일컷'));
  tree = h.render(Gallery, { ...props, spaceContext: null }); assert.match(textOf(tree), /단일컷세트컷/);
  h.close();
});

test('D204 selecting the same set preserves its instance; another set creates a singleton for this photo', () => {
  const block = { id: 'member', cutType: 'styling', shot: 'full', exampleId: set.members[0].exampleId,
    spaceGroupId: spaceSetGroupId(set.id, 'woc1b'), spaceVariation: set.spaceVariation, spaceSetMemberOrder: 1, setSelectionOrigin: 'user' };
  const same = catalogs.genExamples.find(example => example.id === set.members[1].exampleId);
  const patch = generationExampleSelectionPatch(block, same).patch;
  assert.equal(patch.spaceGroupId ?? block.spaceGroupId, block.spaceGroupId);
  assert.notEqual(patch.spaceGroupId, null);
  assert.equal(patch.spaceSetMemberOrder, set.members[1].order);
  const other = catalogs.genExamples.find(example => example.cutType === 'styling' && !set.members.some(member => member.exampleId === example.id));
  const otherPatch = generationExampleSelectionPatch(block, other).patch;
  assert.equal(spaceSetIdFromGroupId(otherPatch.spaceGroupId), other.spaceSetId);
  assert.notEqual(otherPatch.spaceGroupId, block.spaceGroupId);
  assert.equal(otherPatch.refScope, 'pose');
});

test('WOC1b-2 studio sets show their own members first and keep other studio examples expanded as in the reference', async () => {
  const h = hookHarness(), studio = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(set => set.setType === 'horizon-rotation');
  const Gallery = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', { ...h.hooks, ...generation,
    filterExamplesForModel, filterSpaceSetsForModel, storyboardSpaceSetsFor, spaceSetDisplayName, examplePlace,
    filterGalleryExamples, visiblePlaceTabs, isSourceBasedDetailRecipe, ExampleThumb: 'example-thumb', MineImageTab: 'mine-images', ShotSegment: 'shots', Icon: 'svg' });
  const tree = h.render(Gallery, { catalogs, cut: 'horizon', shot: 'full', clothingType: 'top', gender: 'women', exampleId: studio.members[0].exampleId,
    spaceContext: { set: studio }, onUseMine() {} });
  assert.match(textOf(tree), /같은 세트의 다른 사진/); assert.match(textOf(tree), /다른 스튜디오 사진/);
  assert.match(textOf(tree), /단일컷세트컷/); assert.match(textOf(tree), /내 사진으로 바꾸기/);
  assert.doesNotMatch(textOf(tree), /이 컷만 다른 사진으로 바꾸기/); h.close();
});

for (const [count, previewPhotos] of [[1, 0], [2, 8], [10, 12]]) test(`WOC6 daily-wear choices use four sample photos only for eligible layouts with ${count} actual cuts`, () => {
  const original = seedStoryboard(product, analysis('emo')), run = sectionRuns(original).find(run => run.usage === 'styling');
  const photos = Array.from({ length: count }, (_, index) => ({ ...run.blocks[index % run.blocks.length], id: `actual:${index}`,
    source: 'mine', ownImages: [`photo:${index}`], thumb: `photo:${index}`, slotKey: `wear_styling.g${index + 1}` }));
  const blocks = [...original.slice(0, run.start), ...photos, ...original.slice(run.end)], snapshot = structuredClone(blocks);
  const html = renderToStaticMarkup(React.createElement(Picker, { mode: 'design', runKey: run.key, blocks,
    catalogs: { ...catalogs, detailProduct: product, analysis: analysis('emo') } }));
  assert.equal((html.match(/previewWirePhoto/g) || []).length, previewPhotos);
  const sample = sectionDesignPreviewSample(blocks, run.key).filter(block => block.pageSectionKey === run.key && block.source !== 'text');
  assert.equal(new Set(sample.map(block => block.spaceGroupId)).size, 1, 'display-only samples make hero and two-column layouts distinguishable');
  assert.ok(!html.includes('href="photo:4"')); assert.deepEqual(blocks, snapshot);
});

test('WOC1b-4 wear designs declare a matching usage, independently of photo count', () => {
  const blocks = seedStoryboard(product, analysis('emo'));
  for (const run of sectionRuns(blocks).filter(run => run.kind === 'wear')) {
    const choices = sectionDesignChoices(run, { storyboard: blocks });
    assert.ok(choices.length);
    assert.ok(choices.every(design => design.usage === run.usage));
    if (run.usage === 'styling') assert.ok(!choices.some(design => design.id === 'P04-D01'));
  }
  const invalid = structuredClone(pageTemplatesCatalog); delete invalid.designs['P04-D01'].usage;
  assert.throws(() => validateCatalog(invalid), /design usage/);
});

test('WOC1b-5 text-only sections use the polished text guidance', () => {
  const blocks = seedStoryboard(product, analysis('emo'));
  for (const run of sectionRuns(blocks).filter(run => ['intro', 'for_you'].includes(run.kind))) {
    const html = renderToStaticMarkup(React.createElement(Stage, { run, number: 0, blocks, page: [], catalogs, photoOrdinals: new Map() }));
    assert.match(html, /글은 생성할 때 AI가 써요/); assert.doesNotMatch(html, /사진을 누르면/);
  }
});

test('D179 retired guide falls back to the selected default without removing shared designs', () => {
  assert.equal(getTemplate('guide'), undefined);
  const blocks = seedStoryboard(product, analysis('guide'));
  assert.ok(blocks.every(block => block.pageSectionKey.startsWith('emo.')));
  assert.ok(pageTemplatesCatalog.designs['CP-GUIDE-01']);
});

test('WOC1b-3 fixed fit samples keep three positions and preview copies never alter saved blocks or cut count', () => {
  const blocks = seedStoryboard(product, analysis('emo')), run = sectionRuns(blocks).find(run => run.usage === 'fit');
  const before = blocks.filter(block => block.id !== run.blocks.at(-1).id), snapshot = structuredClone(before);
  const sample = sectionDesignPreviewSample(before, run.key).filter(block => block.pageSectionKey === run.key);
  assert.equal(sample.length, 3); assert.deepEqual(sample.map(block => block.direction), ['front', 'side', 'back']);
  assert.equal(new Set(sample.map(block => block.id)).size, 3); assert.equal(sample[2].exampleId, sample[0].exampleId);
  assert.deepEqual(before, snapshot); validateStoryboardV4(before);
});

for (const templateId of ['emo', 'show']) for (const colors of [1, 2, 4]) test(`WOC1b-6 ${templateId}/${colors} colors fills every initial assembled photo`, () => {
  const p = { ...product, colors: Array.from({ length: colors }, (_, index) => ({ id: `color${index}`, isBase: !index,
    images: [{ slot: 'Front', src: 'seller:front' }, { slot: 'Back', src: 'seller:back' }] })) };
  const a = { ...analysis(templateId), detailRecommendations: { version: 1, status: 'ready', candidates: ['fabric', 'neck', 'hem'].map((id, index) => ({
    id, kind: !index ? 'fabric' : 'structure', label: id, rank: index + 1, informationGroup: id, photoUse: 'standalone', visibility: 'clear', direction: 'front',
  })) } };
  const fullCatalogs = { ...withStoryboardSpaceSetExamples({ genExamples: rawExamples }), detailProduct: p, detailRecommendations: a.detailRecommendations };
  const blocks = generation.assignGenerationExamples(seedStoryboard(p, a), { catalog: fullCatalogs.genExamples, product: p, gender: 'women' }).blocks;
  validateStoryboardV4(blocks);
  const images = assemblePage({ storyboard: blocks, product: p, analysis: a, cutResults: storyboardPreviewCuts(blocks, fullCatalogs) })
    .flatMap(block => block.elements).filter(element => element.type === 'image');
  assert.ok(images.length); assert.ok(images.every(image => image.src && image.sourceBlockId));
  if (templateId === 'guide') assert.ok(blocks.filter(block => block.pageSectionUsage === 'styling').every(block => block.cutType === 'styling'));
});
