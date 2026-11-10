import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import ReactRuntime from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import * as examples from '../../src/lib/generationExamples.js';
import * as selection from '../../src/lib/storyboardExampleSelection.js';
import * as details from '../../src/lib/detailRecommendations.js';
import * as photos from '../../src/lib/productSourcePhotos.js';
import * as closure from '../../src/lib/outerClosure.js';
import { ensureSections, normalizeBoard } from '../../src/lib/sections.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { seedTemplateFixture as defaultStoryboard, isDefaultTemplateFixture as isDefaultStoryboardForMode, v4Fixture } from './helpers/templateStoryboard.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import * as garmentColors from '../../src/lib/garmentColors.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import { buildCanvasAiRequest } from '../../src/features/editor/canvasAiActions.js';

const modes = ['ghost', 'flat', 'natural_light'];
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [
  { slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' },
] }] };
const card = (extra = {}) => v4Fixture([{ id: 'product', taxonomyVersion: 3, source: 'ai', sectionRole: 'product', contentRole: 'productOverview',
  title: '제품 전체', cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'base', ...extra }])[0];
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
async function panelHarness(kind, clothingType = 'top', sourceProduct = product) {
  const editor = kind === 'editor';
  assert.equal(editor, true, 'the removed Inspector is covered through the v5 photo-panel harness');
  const source = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('const NEW_CUT_DEFAULT_SHOT'), source.indexOf('\n/*', source.indexOf('export function AIPanel('))).replace('export function', 'function');
  const { code } = await transformWithEsbuild(`${body}\nreturn ${editor ? 'AIPanel' : 'Inspector'};`, 'presentation.jsx', { loader: 'jsx' });
  const slots = []; let cursor = 0, request, block = card();
  const garmentProduct = { ...sourceProduct, clothingType };
  const garmentCatalogs = { ...catalogs, detailProduct: garmentProduct, frontClosure: { version: 1, kind: 'none', range: null },
    genExamples: clothingType === 'top' ? catalogs.genExamples : [] };
  const scope = { React, ...taxonomy, ...examples, ...selection, ...details, ...photos, ...closure, ...garmentColors,
    useState: initial => { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef: initial => { const index = cursor++; return slots[index] ||= { current: initial }; }, useEffect() {},
    isRealModelSelection: () => false, BRAND_USE_CATEGORIES: [], WORN_CUT_TYPES: new Set(['styling', 'horizon', 'mirror']),
    WORN_ROLE_BY_CUT_TYPE: { styling: 'coordination', horizon: 'fit', mirror: 'realWear' },
    shouldRenderGenerationExampleGuide: () => true,
    referenceFeedbackPatch: (_block, changes) => changes,
  };
  for (const name of ['UnderlineTabs', 'ColorDots', 'MoodGuide', 'ProductSourcePicker', 'DetailTargetPicker', 'PanelHead', 'Chips',
    'Button', 'Icon', 'OuterClosureField', 'MineImageTab', 'EmptyState', 'ShotSegment', 'GarmentColorField']) scope[name] = name;
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

test('WOC4 product panel forwards saved modes and fixed face to its section picker without source-face controls', async () => {
  for (const productPresentation of modes) {
    const h = await storyboardPhotoPanelHarness({ block: card({ productPresentation }), catalogs, clothingType: 'top' });
    try {
      const tree = h.render(), picker = nodes(tree, node => node.type === 'product-presentations')[0];
      assert.ok(picker); assert.equal(picker.props.block.productPresentation, productPresentation);
      assert.equal(picker.props.block.direction, 'back');
      assert.equal(nodes(tree, node => node.type === 'product-source' || node.type === 'mood-guide').length, 0);
      await picker.props.onChange({ productPresentation: 'flat' });
      assert.equal(h.block.productPresentation, 'flat'); assert.equal(h.block.direction, 'back');
    } finally { h.close(); }
  }
});

let vite, remote, mock, DB;
before(async () => {
  vite = await createServer({ configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
    define: { 'import.meta.env.VITE_API_BASE_URL': '"https://api.test"' },
    plugins: [react(), { name: 'presentation-session', enforce: 'pre',
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


test('v5 pending product recipe applies the ghost default without an example and preserves an available source face', async () => {
  for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    for (const hasBack of [true, false]) {
      const sourceProduct = hasBack ? product : { ...product, colors: [{ ...product.colors[0], images: product.colors[0].images.slice(0, 1) }] };
      const h = await storyboardPhotoPanelHarness({ block: card({ shot: 'detail', contentRole: 'detail' }), clothingType,
        catalogs: { ...catalogs, detailProduct: sourceProduct, genExamples: [] }, colorOpts: [{ id: 'base' }], detailColorOpts: [{ id: 'base' }] });
      try {
        nodes(h.render(), node => node.type === 'mood-guide')[0].props.onShotChange('ghost');
        const apply = nodes(h.render(), node => node.type === 'button' && node.props.children === '제품컷 적용')[0];
        assert.ok(apply, 'an explicit pending recipe can apply without an example');
        await apply.props.onClick();
        assert.equal(h.block.productPresentation, 'ghost');
        assert.equal(h.block.shot, 'ghost');
        assert.equal(h.block.exampleId, null);
        assert.equal(h.block.direction, hasBack ? 'back' : 'front');
      } finally { h.close(); }
    }
  }
});


test('explicit product modes keep whole/detail controls and hide examples; legacy mode restores the gallery', async () => {
  const { MoodGuide: Guide } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardExampleGallery.jsx');
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
