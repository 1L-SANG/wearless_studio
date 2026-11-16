import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import * as choices from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { productSectionState, changeProductSectionPresentation } from '../../src/lib/pageTemplates/productPresentation.js';
import { PRODUCT_PRESENTATION_OPTIONS, productPresentationOf } from '../../src/lib/storyboardTaxonomy.js';
import { productPresentationExample } from '../../src/lib/productPresentationExamples.js';
import manifest from '../../src/data/productPresentationExamples.json' with { type: 'json' };
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { examplePlace } from '../../src/lib/pageTemplates/storyboardGallery.js';
import { storyboardSpaceSetsFor, inferStoryboardSpaceSet, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

let vite, ProductPicker, Gallery, Filters;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'silent' });
  ({ ProductPresentationPicker: ProductPicker } = await vite.ssrLoadModule('/src/features/storyboard/ProductPresentationPicker.jsx'));
  ({ StoryboardPhotoGallery: Gallery } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPhotoGallery.jsx'));
  ({ PhotoChoiceFilters: Filters } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardChoiceControls.jsx'));
});
after(() => vite?.close());
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const productPhoto = mode => ({ id: 'product', source: 'ai', pageSectionKey: 'emo.color_product', pageSectionKind: 'color_product',
  cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'base', productPresentation: mode });
const buttonFor = (html, label) => [...html.matchAll(/<button\b[\s\S]*?<\/button>/g)].map(match => match[0])
  .find(button => button.includes(`<strong>${label}</strong>`));
function assertSelected(html, value) {
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  for (const mode of PRODUCT_PRESENTATION_OPTIONS) {
    const button = buttonFor(html, mode.label);
    assert.ok(button);
    assert.match(button, new RegExp(`aria-pressed="${mode.value === value}"`));
    if (mode.value === value) {
      assert.match(button, /class="[^"]*chosen[^"]*"/);
      assert.match(button, /class="[^"]*check[^"]*"/);
    } else assert.doesNotMatch(button, /class="[^"]*(?:chosen|check)[^"]*"/);
  }
}

for (const value of ['ghost', 'flat', 'natural_light']) test(`R2-1 ${value} actually renders its pressed state, border class and check`, () => {
  assertSelected(render(ProductPicker, { block: productPhoto(value), clothingType: 'top' }), value);
});

test('R2-1 choosing another presentation moves the rendered selection with the saved run value', async () => {
  const h = hookHarness(); let blocks = [productPhoto('ghost')];
  const Component = await loadStoryboardComponent('ProductPresentationPicker.jsx', 'ProductPresentationPicker', {
    ...h.hooks, manifest, productSectionState, productPresentationOf, PRODUCT_PRESENTATION_OPTIONS, productPresentationExample, Icon: 'svg',
  });
  const props = () => ({ block: blocks[0], blocks, clothingType: 'top', onChange: async patch => {
    blocks = changeProductSectionPresentation(blocks, blocks[0].pageSectionKey, patch.productPresentation);
  } });
  try {
    let tree = h.render(Component, props()); assertSelected(renderToStaticMarkup(tree), 'ghost');
    await nodes(tree).find(node => node.props?.mode?.value === 'flat').props.onChoose();
    tree = h.render(Component, props()); assertSelected(renderToStaticMarkup(tree), 'flat');
    assert.equal(blocks[0].direction, 'back');
  } finally { h.close(); }
});

const photo = (id, placeType, shot, lightQuality, extra = {}) => ({ id, placeType, shot, lightQuality,
  cutType: 'styling', gender: 'women', applicableClothingTypes: ['top'], variants: ['all'], rank: 1,
  direction: 'front', thumb: `/${id}.webp`, ...extra });
const examples = [photo('r2-cafe-full', 'cafe', 'full', 'hard'), photo('r2-home-medium', 'home', 'medium', 'soft'),
  photo('r2-park-full', 'park', 'full', 'soft'), photo('r2-building-medium', 'building', 'medium', 'hard'),
  photo('r2-indoor-men', 'indoor', 'medium', 'soft', { gender: 'men' }),
  photo('r2-nature-bottom', 'nature', 'medium', 'soft', { applicableClothingTypes: ['bottom'] })];
const result = options => choices.photoGalleryChoices(examples, [], { gender: 'women', clothingType: 'top', ...options });
const values = tabs => tabs?.map(tab => tab.value);

test('R5 place availability uses garment, gender and framing without light filtering', () => {
  assert.deepEqual(values(result({ shot: 'all' }).tabs), ['cafe', 'home', 'building', 'park']);
  assert.deepEqual(values(result({ shot: 'full' }).tabs), ['cafe', 'park']);
  assert.deepEqual(values(result({ shot: 'medium' }).tabs), ['home', 'building']);
  assert.deepEqual(values(result({ shot: 'all', light: 'soft' }).tabs), ['cafe', 'home', 'building', 'park']);
  assert.deepEqual(values(result({ shot: 'all', light: 'hard' }).tabs), ['cafe', 'home', 'building', 'park']);
});

test('R2-2 resolves the current photo place when visible, otherwise the first visible place, and no place when empty', () => {
  const home = result({ shot: 'medium', place: 'home' }); assert.equal(home.place, 'home'); assert.equal(home.entries[0].example.id, 'r2-home-medium');
  const fallback = result({ shot: 'medium', place: 'cafe' }); assert.equal(fallback.place, 'home'); assert.ok(fallback.entries.length);
  assert.equal(result({ shot: 'full', place: 'home' }).place, 'cafe');
  const empty = result({ clothingType: 'outer' }); assert.deepEqual(empty.tabs, []); assert.equal(empty.place, null); assert.deepEqual(empty.entries, []);
});

test('R2-2 the actual tab row renders only supplied available places', () => {
  const tabs = [choices.PHOTO_PLACE_TABS.find(t => t.value === 'home'), choices.PHOTO_PLACE_TABS.find(t => t.value === 'park')];
  const html = render(Filters, { tabs, place: 'home', light: 'all' });
  assert.match(html, /aria-pressed="true"[^>]*>집/); assert.match(html, />공원<\/button>/);
  assert.doesNotMatch(html, />카페<\/button>|>실내<\/button>|>전체<\/button>|더보기/);
});

const blockFor = example => ({ id: 'opening', source: 'ai', pageSectionKind: 'first', cutType: 'styling',
  shot: example?.shot || 'full', exampleId: example?.id });
const signature = { id: 'sig_women_03', cutType: 'horizon', shot: 'medium', direction: 'front', thumb: '/signature.webp', signature: true };
const propsFor = example => ({ block: blockFor(example), catalogs: { genExamples: examples }, cut: 'styling',
  exampleId: example?.id, clothingType: 'top', gender: 'women', projectId: 'r2', signature, onExampleChange() {} });
const filters = tree => nodes(tree).find(node => node.type === 'photo-filters').props;
const framing = tree => nodes(tree).find(node => node.type === 'photo-framing').props;
async function galleryHarness(props) {
  const h = hookHarness();
  const Component = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, storyboardSpaceSetsFor: () => [], filterSpaceSetsForModel, generationExampleImageSources,
    Icon: 'svg', EditorMoodGuide: 'editor-guide', MineImageTab: 'mine-images', api: {}, useAppStore: { getState: () => ({}) },
  });
  return { render: next => h.render(Component, { ...props, ...next }), close: h.close };
}

test('R5 framing availability keeps the current place and starts on its available framing', async () => {
  const h = await galleryHarness(propsFor(examples[0]));
  try {
    let tree = h.render(); filters(tree).onPlace('park'); tree = h.render(); assert.equal(filters(tree).place, 'park');
    assert.equal(framing(tree).options.find(option => option.value === 'medium').disabled, true);
    filters(tree).onPlace('home'); tree = h.render(); assert.equal(filters(tree).place, 'home');
    assert.equal(framing(tree).value, 'medium'); assert.equal(framing(tree).options.find(option => option.value === 'full').disabled, true);
    assert.deepEqual(values(filters(tree).tabs), ['cafe', 'home', 'building', 'park']);
  } finally { h.close(); }
});

test('R5 styling exposes no light callback and never adds a signature', async () => {
  const h = await galleryHarness(propsFor(examples[0]));
  try {
    const tree = h.render(); assert.equal(filters(tree).onLight, undefined);
    assert.ok(nodes(tree).filter(node => node.props?.['data-photo-choice']).every(node => node.props['data-example-id'] !== signature.id));
  } finally { h.close(); }
});

test('R3 all-empty styling conditions render no place tabs and the existing empty message without a signature', () => {
  const html = render(Gallery, { ...propsFor(null), catalogs: { genExamples: [] } });
  assert.doesNotMatch(html, />카페<\/button>|>집<\/button>|>공원<\/button>/);
  assert.match(html, /이 조건의 장소 사진을 준비 중이에요/);
  assert.equal((html.match(/aria-label="시그니처 사진"/g) || []).length, 0);
});

for (const [name, example, expected] of [
  ['full', examples[0], 'full'], ['medium', examples[1], 'medium'],
  ['signature', { ...signature, shot: 'full' }, 'medium'], ['missing', null, 'medium'],
  ['unknown framing', { ...examples[0], id: 'r2-unknown-framing', shot: 'unknown' }, 'medium'],
]) test(`R2-3 ${name} opens the appropriate framing`, () => {
  assert.equal(choices.initialPhotoFraming(blockFor(example), example), expected);
  const props = propsFor(example), html = render(Gallery, { ...props, catalogs: { genExamples: example ? [...examples, example] : examples } });
  const initialPlace = choices.initialPhotoPlace(example, result({ shot: 'all' }).tabs);
  const available = examples.filter(item => item.gender === 'women' && item.applicableClothingTypes.includes('top') && item.placeType === initialPlace);
  const actual = available.some(item => item.shot === expected) ? expected : available[0]?.shot;
  const selected = [...html.matchAll(/<button[^>]*aria-pressed="true"[\s\S]*?<\/button>/g)].map(match => match[0]);
  assert.ok(selected.some(button => button.includes(`data-label="${actual === 'full' ? '전신' : '상반신'}"`)));
});

test('R2-3 the current full photo is present and checked on the first gallery render', () => {
  const html = render(Gallery, propsFor(examples[0]));
  assert.match(html, /aria-pressed="true"[^>]*data-example-id="r2-cafe-full"/);
});

test('R5 set replacement uses visible place tabs without a light filter', async () => {
  const h = hookHarness();
  const sets = [examples[1], examples[2], examples[3]].map(example => ({ id: `set-${example.id}`, setType: 'styling',
    members: [{ ...example, exampleId: example.id }] }));
  const Component = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, storyboardSpaceSetsFor: () => sets, filterSpaceSetsForModel, inferStoryboardSpaceSet: () => null, examplePlace, Icon: 'svg',
  });
  const props = { mode: 'add', cutType: 'styling', gender: 'women', clothingType: 'top', onChoose() {} };
  try {
    let tree = h.render(Component, props); assert.deepEqual(values(filters(tree).tabs), ['home', 'building', 'park']);
    assert.equal(filters(tree).place, 'home'); assert.equal(filters(tree).onLight, undefined); filters(tree).onPlace('building');
    tree = h.render(Component, props); assert.equal(filters(tree).place, 'building');
    assert.deepEqual(values(filters(tree).tabs), ['home', 'building', 'park']);
    assert.ok(!textOf(tree).includes('준비 중'));
  } finally { h.close(); }
});

test('R2-2 switching a mounted set picker opens the new target place instead of retaining the previous fallback', async () => {
  const h = hookHarness();
  const Component = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', {
    ...h.hooks, storyboardSpaceSetsFor, filterSpaceSetsForModel, inferStoryboardSpaceSet, examplePlace, Icon: 'svg',
  });
  const sets = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' });
  const studio = sets.find(set => set.setType === 'horizon-rotation');
  const park = sets.find(set => set.setType === 'styling' && examplePlace({ id: set.members[0].exampleId }).value === 'park');
  const home = sets.find(set => set.setType === 'styling' && examplePlace({ id: set.members[0].exampleId }).value === 'home');
  assert.ok(studio && park && home);
  const props = (set, instance = 'r2') => ({ mode: 'replace', gender: 'women', clothingType: 'top', onChoose() {},
    cutType: set.members[0].cutType, usage: set.setType === 'styling' ? 'styling' : 'fit',
    currentMembers: set.members.map(member => ({ ...member, spaceGroupId: spaceSetGroupId(set.id, instance) })),
  });
  try {
    h.render(Component, props(studio));
    let tree = h.render(Component, props(park));
    assert.equal(filters(tree).place, 'park', 'the new target place is correct on its first render');
    tree = h.render(Component, props(park)); assert.equal(filters(tree).place, 'park');
    assert.ok(nodes(tree).some(node => node.props?.['data-space-set'] === park.id && node.props['aria-pressed']));
    filters(tree).onPlace('home'); tree = h.render(Component, props(park)); assert.equal(filters(tree).place, 'home');
    tree = h.render(Component, props(park, 'another-instance')); assert.equal(filters(tree).place, 'park');
    filters(tree).onPlace('home'); tree = h.render(Component, props(park, 'another-instance')); assert.equal(filters(tree).place, 'home');
    tree = h.render(Component, props(home)); assert.equal(filters(tree).place, 'home');
    tree = h.render(Component, props(home)); assert.equal(filters(tree).place, 'home');
    tree = h.render(Component, props(park, 'another-instance')); assert.equal(filters(tree).place, 'park', 'returning to a previous target starts from its current place too');
  } finally { h.close(); }
});
