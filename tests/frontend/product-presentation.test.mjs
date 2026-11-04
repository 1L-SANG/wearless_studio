import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer, transformWithEsbuild } from 'vite';
import ReactRuntime from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { filterExamplesForModel } from '../../src/lib/identityScope.js';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import * as examples from '../../src/lib/generationExamples.js';
import * as selection from '../../src/lib/storyboardExampleSelection.js';
import * as details from '../../src/lib/detailRecommendations.js';
import * as photos from '../../src/lib/productSourcePhotos.js';
import * as closure from '../../src/lib/outerClosure.js';
import { ensureSections, normalizeBoard } from '../../src/lib/sections.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import { buildCanvasAiRequest } from '../../src/features/editor/canvasAiActions.js';

const modes = ['ghost', 'flat', 'natural_light'];
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [
  { slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' },
] }] };
const card = (extra = {}) => ({ id: 'product', taxonomyVersion: 3, source: 'ai', sectionRole: 'product', contentRole: 'productOverview',
  title: '제품 전체', cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'base', ...extra });
const catalogs = { productShotTypes: [{ value: 'ghost', label: '고스트샷' }, { value: 'detail', label: '디테일샷' }],
  shotTypes: [{ value: 'full', label: '풀샷' }], models: [{ id: 'mA', gender: 'women' }],
  genExamples: [{ id: 'product-example', cutType: 'product', shot: 'ghost', variants: ['all'], applicableClothingTypes: ['top'], gender: null }],
  detailProduct: product, creditCosts: { editorImage: 19 } };

test('presentation is preserved through board reload and duplication, while stale fields are removed', () => {
  for (const productPresentation of modes) {
    const [loaded] = ensureSections([card({ productPresentation })]);
    assert.equal(loaded.productPresentation, productPresentation);
    const duplicated = normalizeBoard([loaded, { ...loaded, id: 'copy' }]);
    assert.deepEqual(duplicated.map(block => block.productPresentation), [productPresentation, productPresentation]);
    assert.equal(duplicated[1].direction, 'back');
  }
  for (const block of [card({ productPresentation: 'flatlay' }), card({ productPresentation: null }),
    card({ shot: 'detail', contentRole: 'detail', productPresentation: 'flat' }),
    card({ cutType: 'styling', shot: 'full', sectionRole: 'styling', contentRole: 'coordination', productPresentation: 'flat' }),
    card({ source: 'mine', productPresentation: 'flat' })]) {
    assert.equal(Object.hasOwn(ensureSections([block])[0], 'productPresentation'), false);
    assert.equal(Object.hasOwn(normalizeBoard([block])[0], 'productPresentation'), false);
  }
  assert.equal(Object.hasOwn(ensureSections([card()])[0], 'productPresentation'), false);
});

test('distinct presentations generate distinct cuts and a selected presentation protects a default board from reseeding', () => {
  const blocks = [card(), ...modes.map(productPresentation => card({ productPresentation })), card({ productPresentation: 'flat' })];
  assert.equal(uniqueGenerationCutCount(blocks), 4);
  assert.equal(uniqueGenerationCutCount([card({ shot: 'detail' }), card({ shot: 'detail', productPresentation: 'flat' })]), 1);
  const seeded = defaultStoryboard(product.colors, 'extended', product);
  assert.equal(isDefaultStoryboardForMode(seeded, product.colors, 'extended', product), true);
  const changed = seeded.map(block => block.cutType === 'product' && block.shot === 'ghost' ? { ...block, productPresentation: 'flat' } : block);
  assert.equal(isDefaultStoryboardForMode(changed, product.colors, 'extended', product), false);
});

test('failed-slot retry and canvas color or face changes preserve presentation, detail changes remove it', () => {
  for (const productPresentation of modes) {
    const block = card({ productPresentation });
    const retry = buildFailedCutRetry([block], block.id);
    assert.equal(retry.request.productPresentation, productPresentation);
    const source = { src: '/product.jpg' }, assetRecipe = { kind: 'ghost', cutType: 'product', recipe: block };
    for (const [action, value] of [['color', 'extra'], ['direction', 'front']]) {
      assert.equal(buildCanvasAiRequest({ action, value, source, assetRecipe }).productPresentation, productPresentation);
    }
    const detail = buildCanvasAiRequest({ action: 'detail', value: 'back-target', source, assetRecipe, product,
      detailRecommendations: { version: 1, status: 'ready', candidates: [{ id: 'back-target', direction: 'back', label: '등판',
        informationGroup: 'back', photoUse: 'standalone', visibility: 'clear' }] } });
    assert.equal(Object.hasOwn(detail, 'productPresentation'), false);
  }
});

const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children: children.flat() } }) };
const nodes = (tree, predicate) => Array.isArray(tree) ? tree.flatMap(child => nodes(child, predicate))
  : tree && typeof tree === 'object' ? [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)] : [];
const evaluate = (code, scope) => new Function('scope', `with (scope) { ${code} }`)(scope);
const boardSource = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
async function panelHarness(kind, clothingType = 'top', sourceProduct = product) {
  const editor = kind === 'editor';
  const source = editor ? readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8') : boardSource;
  const body = editor
    ? source.slice(source.indexOf('const NEW_CUT_DEFAULT_SHOT'), source.indexOf('\n/*', source.indexOf('export function AIPanel('))).replace('export function', 'function')
    : source.slice(source.indexOf('function Inspector('), source.indexOf('\n/* 업로드 진행률'));
  const { code } = await transformWithEsbuild(`${body}\nreturn ${editor ? 'AIPanel' : 'Inspector'};`, 'presentation.jsx', { loader: 'jsx' });
  const slots = []; let cursor = 0, request, block = card();
  const garmentProduct = { ...sourceProduct, clothingType };
  const garmentCatalogs = { ...catalogs, detailProduct: garmentProduct, frontClosure: { version: 1, kind: 'none', range: null },
    genExamples: clothingType === 'top' ? catalogs.genExamples : [] };
  const scope = { React, ...taxonomy, ...examples, ...selection, ...details, ...photos, ...closure,
    useState: initial => { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef: initial => { const index = cursor++; return slots[index] ||= { current: initial }; }, useEffect() {},
    isRealModelSelection: () => false, BRAND_USE_CATEGORIES: [], WORN_CUT_TYPES: new Set(['styling', 'horizon', 'mirror']),
    WORN_ROLE_BY_CUT_TYPE: { styling: 'coordination', horizon: 'fit', mirror: 'realWear' },
    shouldRenderGenerationExampleGuide: () => true,
    referenceFeedbackPatch: (_block, changes) => changes,
  };
  for (const name of ['UnderlineTabs', 'ColorDots', 'MoodGuide', 'ProductSourcePicker', 'DetailTargetPicker', 'PanelHead', 'Chips',
    'Button', 'Icon', 'OuterClosureField', 'MineImageTab', 'EmptyState', 'ShotSegment']) scope[name] = name;
  const Component = evaluate(code, scope);
  const render = () => { cursor = 0; return Component({ block, catalogs: garmentCatalogs, product: garmentProduct, clothingType, colorOpts: [{ id: 'base' }], detailColorOpts: [{ id: 'base' }],
    onChange: changes => { block = { ...block, ...(typeof changes === 'function' ? changes(block) : changes) }; },
    onAtomicChange: async changes => { block = { ...block, ...changes }; },
    onGenerate: value => { request = value; } }); };
  const component = name => nodes(render(), node => node.type === name)[0];
  return { render, component, request: () => request, block: () => block, setBlock: value => { block = value; },
    generate: () => component('Button').props.onClick() };
}

test('editor product choices send each selected presentation, preserve the back source, and clear on shot or family change', async () => {
  const h = await panelHarness('editor');
  h.component('UnderlineTabs').props.onChange('product');
  for (const productPresentation of modes) {
    const picker = nodes(h.render(), node => node.type === 'Chips' && node.props.options.some(option => option.value === 'natural_light'))[0];
    assert.ok(picker, 'product presentation choices must be visible');
    assert.deepEqual(picker.props.options.filter(option => option.value).map(option => option.label), ['고스트컷', '평면컷', '자연광컷']);
    picker.props.onChange(productPresentation);
    h.component('ProductSourcePicker').props.onChange({ direction: 'back' });
    h.component('MoodGuide').props.onExampleChange('product-example');
    h.generate();
    assert.equal(h.request().productPresentation, productPresentation);
    assert.equal(h.request().direction, 'back');
    assert.equal(h.request().shot, 'ghost');
  }
  h.component('MoodGuide').props.onShotChange('detail'); h.generate();
  assert.equal(Object.hasOwn(h.request(), 'productPresentation'), false);
  assert.equal(nodes(h.render(), node => node.type === 'Chips' && node.props.options.some(option => option.value === 'flat')).length, 0);
  h.component('MoodGuide').props.onShotChange('ghost'); h.generate();
  assert.equal(Object.hasOwn(h.request(), 'productPresentation'), false);
  h.component('UnderlineTabs').props.onChange('styling'); h.generate();
  assert.equal(Object.hasOwn(h.request(), 'productPresentation'), false);
});

test('storyboard inspector exposes labeled presentation choices for whole product only and records the choice', async () => {
  const h = await panelHarness('storyboard');
  for (const productPresentation of modes) {
    const picker = nodes(h.render(), node => node.props?.['aria-label'] === '촬영 방식')[0];
    assert.ok(picker, 'whole-product inspector must show presentation choices');
    const buttons = nodes(picker, node => node.type === 'button');
    assert.deepEqual(buttons.slice(1).map(node => node.props.children.join('')), ['고스트컷', '평면컷', '자연광컷']);
    buttons[modes.indexOf(productPresentation) + 1].props.onClick();
    assert.equal(h.block().productPresentation, productPresentation);
    assert.equal(h.block().direction, 'back');
  }
  h.setBlock(card({ shot: 'detail', contentRole: 'detail' }));
  assert.equal(nodes(h.render(), node => node.props?.['aria-label'] === '촬영 방식').length, 0);
  h.setBlock(card({ cutType: 'styling', shot: 'full', contentRole: 'coordination', sectionRole: 'styling' }));
  assert.equal(nodes(h.render(), node => node.props?.['aria-label'] === '촬영 방식').length, 0);
});

test('product card summary names explicit presentation and preserves legacy labels', () => {
  const body = boardSource.slice(boardSource.indexOf('function cardLabels('), boardSource.indexOf('\n/* 캡션'));
  const labels = evaluate(`${body}; return cardLabels;`, {});
  assert.equal(labels(card(), catalogs).shot, '고스트샷 · 뒷면 사진');
  for (const [index, label] of ['고스트컷', '평면컷', '자연광컷'].entries()) {
    assert.equal(labels(card({ productPresentation: modes[index] }), catalogs).shot, `${label} · 뒷면 사진`);
  }
});

let vite, remote, mock, DB;
before(async () => {
  vite = await createServer({ configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
    define: { 'import.meta.env.VITE_API_BASE_URL': '"https://api.test"' },
    plugins: [{ name: 'presentation-session', enforce: 'pre',
      resolveId(id) { if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0presentation-session'; },
      load(id) { if (id === '\0presentation-session') return 'export const supabase = { auth: { getSession: async () => ({ data: { session: { access_token: "test-token", user: { id: "test-user" } } } }) } };'; },
    }],
  });
  ({ httpAdapter: remote } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());


test('explicit whole-product recipes are available without a published example for all four garments', () => {
  for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    for (const productPresentation of modes) {
      assert.equal(examples.hasAvailableGenerationRecipe([], { cutType: 'product', shot: 'ghost', clothingType, productPresentation }), true);
    }
    assert.equal(examples.hasAvailableGenerationRecipe([], { cutType: 'product', shot: 'ghost', clothingType }), false);
  }
  assert.equal(examples.hasAvailableGenerationRecipe([], { cutType: 'product', shot: 'ghost', clothingType: 'unknown', productPresentation: 'ghost' }), false);
});

test('editor opens whole product for every garment and submits all presentations without adding a back photo', async () => {
  for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    const h = await panelHarness('editor', clothingType);
    h.component('UnderlineTabs').props.onChange('product');
    assert.equal(h.component('MoodGuide').props.shot, 'ghost');
    for (const productPresentation of modes) {
      const picker = nodes(h.render(), node => node.type === 'Chips' && node.props.options.some(option => option.value === 'flat'))[0];
      picker.props.onChange(productPresentation); h.generate();
      assert.equal(h.request().productPresentation, productPresentation);
      assert.equal(h.request().direction, 'front');
      assert.equal(h.request().exampleId, null);
    }
  }
});


test('storyboard switches detail to an explicit whole product without an example for all garments and keeps only an available source face', async () => {
  for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    for (const hasBack of [true, false]) {
      const sourceProduct = hasBack ? product : { ...product, colors: [{ ...product.colors[0], images: product.colors[0].images.slice(0, 1) }] };
      const h = await panelHarness('storyboard', clothingType, sourceProduct);
      h.setBlock(card({ shot: 'detail', contentRole: 'detail' }));
      h.component('MoodGuide').props.onShotChange('ghost');
      const picker = nodes(h.render(), node => node.props?.['aria-label'] === '촬영 방식')[0];
      const buttons = nodes(picker, node => node.type === 'button');
      buttons[2].props.onClick();
      await h.component('Button').props.onClick();
      assert.equal(h.block().productPresentation, 'natural_light');
      assert.equal(h.block().shot, 'ghost');
      assert.equal(h.block().exampleId, null);
      assert.equal(h.block().direction, hasBack ? 'back' : 'front');
    }
  }
});


test('explicit product modes keep whole/detail controls and hide examples; legacy mode restores the gallery', async () => {
  const segment = boardSource.slice(boardSource.indexOf('function ShotSegment('), boardSource.indexOf('const MINE_SHOT_OPTION'));
  const guide = boardSource.slice(boardSource.indexOf('export function MoodGuide('), boardSource.indexOf('function Inspector(')).replace('export function', 'function');
  const { code } = await transformWithEsbuild(`${segment}\n${guide}\nreturn MoodGuide;`, 'presentation-guide.jsx', { loader: 'jsx' });
  const Guide = evaluate(code, { ...taxonomy, ...examples, ...details, React: ReactRuntime,
    useState: ReactRuntime.useState, useRef: ReactRuntime.useRef, useEffect: ReactRuntime.useEffect,
    filterExamplesForModel, MINE_SHOT_OPTION: { value: 'mine', label: '내 이미지' },
    Icon: () => null, ExampleThumb: () => ReactRuntime.createElement('img', { src: '/example.jpg', alt: '' }),
  });
  const render = productPresentation => renderToStaticMarkup(ReactRuntime.createElement(Guide, {
    catalogs, cut: 'product', shot: 'ghost', clothingType: 'top', direction: 'front', productPresentation,
    exampleId: 'product-example', onShotChange() {}, onExampleChange() {}, onRefsChange() {},
  }));
  for (const productPresentation of modes) {
    const markup = render(productPresentation);
    assert.doesNotMatch(markup, /sb-exgallery|sb-exempty|예시 사진|촬영 연출 예시/);
    assert.match(markup, /제품컷 형식/);
    assert.match(markup, /전체컷/);
    assert.match(markup, /디테일샷/);
  }
  assert.match(render(null), /sb-exgallery/);
});

test('HTTP new-image body carries valid presentation and drops stale presentation', async () => {
  const original = globalThis.fetch;
  const bodies = [];
  globalThis.fetch = async (_url, options) => { bodies.push(JSON.parse(options.body)); return new Response(JSON.stringify({ data: { id: 'image' } }), { status: 200, headers: { 'Content-Type': 'application/json' } }); };
  try {
    for (const productPresentation of modes) {
      await remote.generateImage('project', { mode: 'new', ...card({ productPresentation }) });
      assert.equal(bodies.at(-1).productPresentation, productPresentation);
    }
    for (const changes of [{ productPresentation: 'flatlay' }, { shot: 'detail', productPresentation: 'flat' }, { cutType: 'styling', productPresentation: 'flat' }, {}]) {
      await remote.generateImage('project', { mode: 'new', ...card(changes) });
      assert.equal(Object.hasOwn(bodies.at(-1), 'productPresentation'), false);
    }
  } finally { globalThis.fetch = original; }
});

test('mock asset recipe snapshots preserve explicit presentation and face for later edits', async () => {
  DB.account.credits = 200; DB.storyboard = []; DB.editorBlocks = []; DB.assets = {}; DB.wardrobe = { base: [], misc: [] }; DB.product = product;
  for (const productPresentation of modes) {
    const result = await mock.generateImage('project', { mode: 'new', ...card({ productPresentation }), idempotencyKey: `new-${productPresentation}` });
    const { items } = await mock.getEditorAssetInfo('project', [result.data.assetId]);
    assert.equal(items[result.data.assetId].recipe.productPresentation, productPresentation);
    assert.equal(items[result.data.assetId].recipe.direction, 'back');
  }
});
