import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { QueryClient } from '@tanstack/react-query';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { selectFrameShopName } from '../../src/lib/frameShopName.js';
import { alignSkeletonToServer, decorateGenBlocks, mergeServerBlocks, patchTemplateElement } from '../../src/lib/editorWaitSkeleton.js';

let vite, buildEditorBlocksFromStoryboard, shopQueries;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ buildEditorBlocksFromStoryboard } = await vite.ssrLoadModule('/src/mock/db.js'));
  shopQueries = await vite.ssrLoadModule('/src/features/storyboard/storyboardShopName.js');
});
after(() => vite?.close());

const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const product = { name: '코튼 셔츠', colors: [] };
const storyboard = [{ id: 'frame-photo', taxonomyVersion: 4, source: 'ai', cutType: 'styling',
  sectionId: 'wear', pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', pageSectionUsage: 'styling',
  pageDesignId: 'WEAR-STYLING-FRAME-COLUMN-01', slotKey: null }];
const job = { projectId: 'wait-project', status: 'running', cuts: {}, copy: {}, live: [] };
const identity = value => value;
const footer = blocks => blocks.flatMap(block => block.elements).filter(element => element.copyRole === 'who').map(element => element.text);

async function loadWaiting(t, connections, recovery = false, waitForBlocks = true) {
  const client = new QueryClient(); t.after(() => client.clear());
  if (connections) client.setQueryData(['mall-connections', 'owner-a'], { items: connections });
  const cleanups = [];
  const view = {}, state = { accountUserId: 'owner-a', detailPageJob: job, copywriting: false,
    startDetailPageGeneration() {}, loadAccount: async () => ({}) };
  const bindings = { projectId: 'wait-project', loadAttempt: 0, useEffect: effect => { cleanups.push(effect()); },
    useCallback: fn => fn, useQueryClient: () => client, useAppStore: { getState: () => state },
    pageTemplatesCatalog, selectFrameShopName, ...shopQueries,
    editorWorkCopies: { read: () => null }, loadEditorWaitDraftEntry: () => null,
    pendingGenerationDraft: { current: false }, genActiveRef: { current: false }, editorConflictPending: { current: false },
    wardrobeContext: { current: {} }, isMockMode: true,
    api: { getEditorBlocks: async () => [], getWardrobe: async () => ({}), getCatalogs: async () => ({}),
      getProduct: async () => product, getAnalysis: async () => ({}), getStoryboard: async () => storyboard,
      getMatchClothing: async () => [] },
    retryRead: get => get(), withStoryboardSpaceSetExamples: identity, normalizeEditorBlockRole: identity,
    buildColorOpts: () => [], visibleColorOpts: identity, hexForCol: () => '',
    buildEditorBlocksFromStoryboard, alignSkeletonToServer, decorateGenBlocks,
    upgradeLegacyKiwiTemplateBlocks: identity, stripPhotoBlockTextElements: identity, uid: () => 'id',
    mergeEditorImagesIntoWardrobe: ({ wardrobe }) => wardrobe, classifyEditorLoadError: identity,
    waitBoardError: 'retry', waitBoardAttempt: 0, blocks: null, product, analysis: {}, catalogs: {},
  };
  // Execute the production callback and both production entry effects, as the
  // existing library-in-progress harness does, with only remote reads replaced.
  const loaderStart = editor.indexOf('  const waitShopQueryClient = useQueryClient();');
  if (loaderStart !== -1) {
    const loader = editor.slice(loaderStart, editor.indexOf("  const [waitBoardError", loaderStart));
    bindings.loadWaitingShopName = new Function(...Object.keys(bindings), `${loader}; return loadWaitingShopName;`)(...Object.values(bindings));
  }
  const start = recovery ? editor.indexOf('  useEffect(() => {\n    if (!waitBoardError') : editor.indexOf('  useEffect(() => {\n    setLoadError(null);');
  const end = recovery ? editor.indexOf('  // 화면 대기(3분)', start) : editor.indexOf('  // 상세페이지 생성 사진은', start);
  let resolve, reject;
  const loaded = new Promise((yes, no) => { resolve = yes; reject = no; });
  for (const name of ['LoadError', 'WaitBoardError', 'GenActive', 'Blocks', 'Wardrobe', 'Catalogs', 'FmModels', 'SelBlock',
    'ProductName', 'ClothingType', 'MatchClothing', 'Product', 'Analysis', 'DetailColorOpts', 'ColorOpts']) {
    bindings[`set${name}`] = value => {
      view[name] = value;
      if (name === 'Blocks') resolve(value);
      if (name === 'LoadError' && value) reject(value.error);
    };
  }
  new Function(...Object.keys(bindings), editor.slice(start, end))(...Object.values(bindings));
  if (!waitForBlocks) return { client, view, unmount: () => cleanups.forEach(cleanup => cleanup?.()) };
  const blocks = await loaded;
  return { blocks, client, loadShopName: bindings.loadWaitingShopName };
}

for (const recovery of [false, true]) {
  test(`editor ${recovery ? 'recovery' : 'entry'} carries one connected shop through title and blur edits and completion`, async t => {
    const { blocks } = await loadWaiting(t, [
      { mall: 'zigzag', shopName: '마켓몰', status: 'connected' },
      { mall: 'cafe24', shopName: '  자사몰  ', status: 'connected' },
    ], recovery);
    assert.deepEqual(footer(blocks), ['자사몰']);
    const title = blocks[0].elements.find(element => element.copyRole === 'name');
    let edited = patchTemplateElement(blocks[0], title.id, { text: '직접 고친 상품명' });
    edited = { ...edited, elements: edited.elements.map(element => element.blur ? { ...element, blur: 12 } : element) };
    const final = assemblePage({ storyboard, product, analysis: {}, copywriting: false, shopName: '자사몰',
      cutResults: [{ blockId: 'frame-photo', imageUrl: '/stable-photo.png', width: 900, height: 1200 }] });
    const merged = mergeServerBlocks([edited, ...blocks.slice(1)], final);
    assert.equal(merged[0].elements.find(element => element.id === title.id).text, '직접 고친 상품명');
    assert.equal(merged[0].elements.find(element => element.blur).blur, 12);
    assert.deepEqual(footer(merged), ['자사몰']);
    assert.ok(merged[0].elements.filter(element => element.type === 'image').every(element => element.src === '/stable-photo.png'));
  });
}

test('editor waiting frame omits disconnected mall names', async t => {
  const { blocks } = await loadWaiting(t, [{ mall: 'cafe24', shopName: '옛 상점', status: 'revoked' }]);
  assert.deepEqual(footer(blocks), []);
});

test('failed mall refresh resolves without a stale shop or blocking skeleton assembly', async t => {
  const { client, loadShopName } = await loadWaiting(t, []);
  const { api } = await vite.ssrLoadModule('/src/lib/api/index.js');
  const original = api.listMalls; t.after(() => { api.listMalls = original; });
  client.setQueryData(['mall-connections', 'owner-a'], { items: [{ mall: 'cafe24', status: 'connected', shopName: '옛 연결' }] });
  await client.invalidateQueries({ queryKey: ['mall-connections', 'owner-a'] });
  api.listMalls = async () => { throw new Error('offline'); };
  assert.equal(await loadShopName(storyboard), null);
});

for (const recovery of [false, true]) {
  test(`leaving editor ${recovery ? 'recovery' : 'entry'} while the shop loads discards late assembly`, async t => {
    const { api } = await vite.ssrLoadModule('/src/lib/api/index.js');
    const original = api.listMalls; t.after(() => { api.listMalls = original; });
    let finish, started;
    const ready = new Promise(resolve => { started = resolve; });
    api.listMalls = () => new Promise(resolve => { finish = resolve; started(); });
    const { view, unmount } = await loadWaiting(t, null, recovery, false);
    await ready;
    unmount();
    finish({ items: [{ mall: 'cafe24', status: 'connected', shopName: '자사몰' }] });
    await new Promise(setImmediate);
    assert.equal(view.Blocks, undefined);
  });
}
