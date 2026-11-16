import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { createServer, transformWithEsbuild } from 'vite';
import { renderToStaticMarkup } from 'react-dom/server';
import { hookHarness, nodes, textOf, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { isMinimalExample } from '../../src/lib/studioExamples.js';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import * as selections from '../../src/lib/cutDisplaySelection.js';
import * as generation from '../../src/lib/generationExamples.js';
import * as exampleSelection from '../../src/lib/storyboardExampleSelection.js';
import * as colors from '../../src/lib/garmentColors.js';
import * as details from '../../src/lib/detailRecommendations.js';
import { productSourceColorPatch } from '../../src/lib/productSourcePhotos.js';

let vite, mock, catalogs;
before(async () => {
  vite = await createServer({ configFile: false,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'silent' });
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  catalogs = withStoryboardSpaceSetExamples(await mock.getCatalogs());
});
after(() => vite?.close());

async function editorHarness({ gender = 'men', clothingType = 'outer' } = {}) {
  const h = hookHarness();
  const source = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('const NEW_CUT_DEFAULT_SHOT'), source.indexOf('\n/*', source.indexOf('export function AIPanel(')))
    .replace('export function', 'function');
  const { code } = await transformWithEsbuild(body, 'AIPanel.jsx', { loader: 'jsx' });
  const bindings = { React, ...h.hooks, ...taxonomy, ...selections, ...generation, ...exampleSelection, ...colors, ...details,
    productSourceColorPatch, isRealModelSelection: () => false, BRAND_USE_CATEGORIES: [] };
  for (const name of ['UnderlineTabs', 'StudioStyleChips', 'MoodGuide', 'ProductSourcePicker', 'DetailTargetPicker',
    'PanelHead', 'Chips', 'Button', 'Icon', 'OuterClosureField', 'GarmentColorField']) bindings[name] = name;
  const Panel = new Function(...Object.keys(bindings), `${code}; return AIPanel;`)(...Object.values(bindings));
  const requests = [];
  const product = { clothingType, colors: [{ id: 'base', isBase: true, images: [{ id: 'front', slot: 'Front', src: '/front.png' }] }] };
  const props = { catalogs: { ...catalogs, models: [{ id: 'model', gender }] }, product, clothingType,
    colorOpts: [{ id: 'base' }], detailColorOpts: [{ id: 'base' }], onGenerate: request => requests.push(request) };
  return { render: () => h.render(Panel, props), requests, close: h.close };
}

test('mock catalog exposes the same 16 minimal examples and real editor callbacks submit the existing recipe', async () => {
  assert.equal(catalogs.genExamples.filter(isMinimalExample).length, 16);
  const h = await editorHarness();
  try {
    let tree = h.render();
    nodes(tree).find(node => node.type === 'UnderlineTabs').props.onChange('horizon');
    tree = h.render();
    let sub = nodes(tree).find(node => node.type === 'StudioStyleChips');
    assert.equal(sub.props.value, 'horizon'); sub.props.onChange('minimal');
    tree = h.render();
    sub = nodes(tree).find(node => node.type === 'StudioStyleChips');
    assert.equal(sub.props.value, 'minimal');
    const gallery = nodes(tree).find(node => node.type === 'MoodGuide');
    assert.equal(gallery.props.cut, 'styling');
    assert.ok(isMinimalExample(catalogs.genExamples.find(example => example.id === gallery.props.exampleId)));
    nodes(tree).find(node => node.type === 'Button').props.onClick();
    assert.equal(h.requests[0].cutType, 'styling');
    assert.equal(h.requests[0].exampleId, gallery.props.exampleId);
    assert.equal(Object.hasOwn(h.requests[0], 'studioStyle'), false);
    nodes(tree).find(node => node.type === 'UnderlineTabs').props.onChange('styling');
    tree = h.render();
    assert.equal(nodes(tree).find(node => node.type === 'MoodGuide').props.exampleId, null);
    assert.equal(nodes(tree).find(node => node.type === 'UnderlineTabs').props.value, 'styling');
  } finally { h.close(); }
});

test('existing chip styling and disabled reason render without inventing a new minimal selection', async () => {
  const Chips = await loadStoryboardComponent('StoryboardControls.jsx', 'StudioStyleChips');
  const options = selections.studioStyleOptions([], { shots: ['full', 'medium'], gender: 'men', clothingType: 'dress' });
  const html = renderToStaticMarkup(React.createElement(Chips, { options, value: 'horizon', onChange() {} }));
  assert.match(html, /class="chips modal-actions"/);
  assert.match(html, /disabled="" title="이 조건은 아직 서비스에 공개되지 않았어요"/);
  assert.match(html, /미니멀컷/); assert.match(html, /호리존컷/);
});

test('R3 leaving ordinary minimal for styling saves the chosen photo and retries the same failed selection', async () => {
  const example = catalogs.genExamples.find(item => isMinimalExample(item) && item.gender === 'men');
  const block = { id: 'ordinary', taxonomyVersion: 4, pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'styling',
    pageDesignId: 'P02-D01', sectionId: 'wear', source: 'ai', slotKey: 'wear_styling.g1', contentRole: 'coordination',
    cutType: 'styling', shot: example.shot, direction: example.direction, exampleId: example.id };
  let fail = true; const calls = [];
  const h = await storyboardPhotoPanelHarness({ block, catalogs, clothingType: 'outer', exampleGender: 'men',
    onAtomicChange: async patch => { calls.push(patch); if (fail) throw Error('offline'); } });
  try {
    nodes(h.render()).find(node => node.type === 'underline-tabs').props.onChange('styling');
    assert.equal(calls.length, 0);
    const next = catalogs.genExamples.find(item => item.cutType === 'styling' && !isMinimalExample(item) && item.gender === 'men' && item.applicableClothingTypes.includes('outer'));
    await nodes(h.render()).find(node => node.type === 'mood-guide').props.onExampleChange(next.id);
    const tree = h.render();
    const retry = nodes(tree).find(node => node.type === 'button' && textOf(node) === '다시 시도');
    assert.ok(retry); assert.equal(retry.props.disabled, false);
    fail = false; await retry.props.onClick();
    assert.equal(calls.length, 2); assert.deepEqual(calls[0], calls[1]);
    const result = generation.assignGenerationExamples([{ ...block, ...calls[1] }],
      { catalog: catalogs.genExamples, product: { clothingType: 'outer' }, gender: 'men' }).blocks[0];
    assert.equal(result.cutType, 'styling'); assert.equal(result.exampleId, next.id);
    assert.equal(taxonomy.cutTypeDisplay(result.cutType, result.exampleId).value, 'styling');
  } finally { h.close(); }
});
