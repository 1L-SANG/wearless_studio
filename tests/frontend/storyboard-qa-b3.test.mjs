import { readStoryboardSources } from './helpers/storyboardComponents.mjs';
import { v4Fixture } from './helpers/templateStoryboard.mjs';
import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { createServer } from 'vite';

import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { renderGroups } from '../../src/lib/storyboardRenderGroups.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { mineImageUrl, normalizeMineImages } from '../../src/lib/storyboardMineImages.js';
import { detachSpaceMembership, rekeySeparatedSpaceRuns } from '../../src/lib/storyboardSpaceSets.js';
import { spaceSetGroupId, storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';

const storyboardSource = readStoryboardSources();
const featureStyles = readFileSync(
  new URL('../../src/styles/features.css', import.meta.url),
  'utf8',
);
const apiIndexSource = readFileSync(
  new URL('../../src/lib/api/index.js', import.meta.url),
  'utf8',
);
const mockApiSource = readFileSync(
  new URL('../../src/mock/api.js', import.meta.url),
  'utf8',
);
const editorSource = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);

const jsonResponse = (payload, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
});

const restoreGlobal = (name, value) => {
  if (value === undefined) delete globalThis[name];
  else globalThis[name] = value;
};

let vite;
let httpAdapter;
let mockAdapter;
let supabase;

before(async () => {
  vite = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    configFile: false,
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null },
    appType: 'custom',
    logLevel: 'silent',
  });
  ({ httpAdapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ mockAdapter } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
});

after(async () => {
  await vite?.close();
});

test('1-01 HTTP image picker returns null on cancel without starting an upload', async () => {
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.document = {
    createElement(tag) {
      assert.equal(tag, 'input');
      return {
        click() { this.oncancel(); },
      };
    },
  };
  globalThis.fetch = async () => {
    fetchCount += 1;
    throw new Error('cancelled picker must not upload');
  };

  try {
    assert.equal(await httpAdapter.pickAnyImage('project-cancel'), null);
    assert.equal(fetchCount, 0);
  } finally {
    restoreGlobal('document', originalDocument);
    restoreGlobal('fetch', originalFetch);
  }
});

test('1-01 HTTP image picker uploads the selected file once and returns assetId with url', async () => {
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  const originalGetSession = supabase.auth.getSession;
  const file = { name: 'mine.png', type: 'image/png', size: 4 };
  const requests = [];

  globalThis.document = {
    createElement(tag) {
      assert.equal(tag, 'input');
      return {
        files: [file],
        click() { this.onchange(); },
      };
    },
  };
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test-token' } } });
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    const path = new URL(url, 'http://test.local').pathname;
    if (path === '/v1/assets/upload-url') {
      return jsonResponse({ assetId: 'asset-mine', uploadUrl: 'https://upload.test/mine' });
    }
    if (url === 'https://upload.test/mine') return jsonResponse(null);
    if (path === '/v1/assets/asset-mine/complete') {
      return jsonResponse({ url: 'https://cdn.test/mine.png' });
    }
    throw new Error(`unexpected request: ${url}`);
  };

  try {
    const uploaded = await httpAdapter.pickAnyImage('project-upload');
    assert.equal(uploaded.assetId, 'asset-mine');
    assert.match(uploaded.url, /\/v1\/assets\/asset-mine\/file\?e=1$/);
  } finally {
    restoreGlobal('document', originalDocument);
    restoreGlobal('fetch', originalFetch);
    supabase.auth.getSession = originalGetSession;
  }

  assert.equal(requests.filter(({ url }) => new URL(url, 'http://test.local').pathname === '/v1/assets/upload-url').length, 1);
  assert.equal(requests.filter(({ url }) => url === 'https://upload.test/mine').length, 1);
  assert.equal(requests.filter(({ url }) => url.endsWith('/complete')).length, 1);
  assert.equal(requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    filename: 'mine.png',
    mime: 'image/png',
    size: 4,
    projectId: 'project-upload',
    purpose: 'upload',
  });
});

test('1-01 mock picker keeps its existing placeholder contract while HTTP owns the real picker', async () => {
  const placeholder = await mockAdapter.pickAnyImage('mock-project');
  assert.equal(typeof placeholder, 'string');
  assert.ok(placeholder.length > 0);
  assert.match(mockApiSource, /async pickAnyImage\(\) \{ await wait\(120\); return Placeholder\.any/);
  assert.doesNotMatch(apiIndexSource, /CLIENT_ONLY = \[[^\]]*pickAnyImage/);
});

test('seller-photo replacement stores the selected URL on the existing photo', async () => {
  const { storyboardPhotoPanelHarness } = await import('./helpers/storyboardPhotoPanelHarness.mjs');
  const { nodes } = await import('./helpers/storyboardComponents.mjs');
  const h = await storyboardPhotoPanelHarness({ block: { id: 'photo', source: 'ai', cutType: 'styling', shot: 'full' }, projectId: 'project' });
  const guide = nodes(h.render()).find(node => node.type === 'mood-guide');
  guide.props.onUseMine({ url: '/seller-photo.webp' });
  assert.deepEqual(h.block.ownImages, ['/seller-photo.webp']); assert.equal(h.block.thumb, '/seller-photo.webp'); h.close();
});
test('N4 initial blocks auto-assign while a user-added manual block stays empty after reload', () => {
  const catalog = [{
    id: 'example-1',
    rank: 1,
    thumb: '/example-1.webp',
    cutType: 'styling',
    shot: 'full',
    gender: 'women',
    mood: 'daily',
    applicableClothingTypes: ['top'],
    variants: ['all'],
  }];
  const initial = {
    id: 'seed', source: 'ai', cutType: 'styling', shot: 'full', direction: 'front',
  };
  const manual = {
    ...initial,
    id: 'added',
    exampleChoice: 'manual',
  };
  const firstEntry = assignGenerationExamples([initial, manual], {
    catalog,
    product: { clothingType: 'top' },
    gender: 'women',
  });
  assert.equal(firstEntry.blocks[0].exampleId, 'example-1');
  assert.equal(firstEntry.blocks[0].exampleSelectionOrigin, 'auto');
  assert.equal(firstEntry.blocks[1].exampleId, undefined);
  assert.equal(firstEntry.blocks[1].exampleChoice, 'manual');

  const reloaded = JSON.parse(JSON.stringify(firstEntry.blocks));
  const secondEntry = assignGenerationExamples(reloaded, {
    catalog,
    product: { clothingType: 'top' },
    gender: 'women',
  });
  assert.equal(secondEntry.blocks[1].exampleId, undefined);
  assert.equal(secondEntry.blocks[1].exampleChoice, 'manual');
});

test('N4 choosing an example ends manual waiting and HTTP save preserves the field shape', async () => {
  const selected = generationExampleSelectionPatch(
    { exampleChoice: 'manual', cutType: 'styling', shot: 'full' },
    { id: 'example-picked' },
  );
  assert.equal(selected.patch.exampleId, 'example-picked');
  assert.equal(selected.patch.exampleChoice, null);

  const originalFetch = globalThis.fetch;
  const originalGetSession = supabase.auth.getSession;
  let request;
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test-token' } } });
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, status: 200, json: async () => ({ blocks: [], revision: options?.method === 'PUT' ? 1 : 0 }) };
  };
  const blocks = [{ id: 'added', source: 'ai', exampleChoice: 'manual' }];
  try {
    await httpAdapter.saveStoryboard('manual-project', blocks);
  } finally {
    restoreGlobal('fetch', originalFetch);
    supabase.auth.getSession = originalGetSession;
  }
  assert.equal(new URL(request.url, 'http://test.local').pathname, '/v1/projects/manual-project/storyboard');
  assert.deepEqual(JSON.parse(request.options.body), blocks);
});

test('v5 cut addition fills an eligible example automatically', async () => {
  const { seedStoryboard } = await import('../../src/lib/pageTemplates/seed.js');
  const { addStoryboardPhoto } = await import('../../src/lib/pageTemplates/photoActions.js');
  const { withStoryboardSpaceSetExamples } = await import('../../src/lib/storyboardSpaceSetCatalog.js');
  const catalog = withStoryboardSpaceSetExamples({ genExamples: JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8')) }).genExamples;
  const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] }, before = seedStoryboard(product, { targetGenders: ['women'] });
  const host = before.find(block => block.pageSectionUsage === 'styling');
  const after = addStoryboardPhoto(before, host.pageSectionKey, { catalog, product, gender: 'women' });
  const added = after.find(block => !before.some(old => old.id === block.id)); assert.ok(added.exampleId); assert.notEqual(added.exampleChoice, 'manual');
});
test('v5 example drops use the selected section gap and lower run key', async () => {
  const { loadStoryboardComponent, hookHarness, nodes } = await import('./helpers/storyboardComponents.mjs');
  const { templateSectionName } = await import('../../src/lib/pageTemplates/slots.js');
  const { reuseSlotsForBlock } = await import('../../src/lib/pageTemplates/sectionDependencies.js');
  const h = hookHarness(), calls = [];
  const List = await loadStoryboardComponent('StoryboardPageList.jsx', 'StoryboardPageList', { ...h.hooks, Icon: 'svg', StoryboardPreview: 'preview', templateSectionName, reuseSlotsForBlock, CATALOG_DRAG_MIME: 'catalog' });
  const runs = ['first', 'wear'].map((kind, index) => ({ key: `emo.${kind}`, kind, blocks: [{ source: 'ai', pageSectionKind: kind }] }));
  const tree = h.render(List, { runs, page: [], onAdd() {}, onMove() {}, onDropContent: (...args) => calls.push(args), sectionRefs: { current: new Map() } });
  const transfer = { getData: () => '', types: ['text/example-id'] };
  nodes(tree).find(node => node.props?.['data-section-drop'] === 'emo.wear').props.onDrop({ dataTransfer: transfer, preventDefault() {} });
  assert.equal(calls[0][1], 'emo.wear'); h.close();
});
test('N5 drop-on shifts both adjacent cards, keeps terminal hit areas, and disables motion when requested', () => {
  assert.match(featureStyles, /\.sb-addzone\.end \{[^}]*display: grid;[^}]*width: 24px/);
  assert.match(featureStyles, /:has\(\.sb-addzone:hover, \.sb-addzone:focus-within, \.sb-addzone\.drop-on\) \.sb-cutcard,[\s\S]*?translate3d\(-5px, 0, 0\)/);
  assert.match(featureStyles, /:has\(\.sb-addzone:hover, \.sb-addzone:focus-within, \.sb-addzone\.drop-on\) \+ \.sb-grid-unit \.sb-cutcard,[\s\S]*?translate3d\(5px, 0, 0\)/);
  assert.match(featureStyles, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?:has\(\.sb-addzone:hover, \.sb-addzone:focus-within, \.sb-addzone\.drop-on\)[\s\S]*?transform: none;/);
});

const persistedSplitBoard = () => {
  const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })
    .find((candidate) => candidate.setType === 'styling');
  const originalGroupId = spaceSetGroupId(set.id, 'roundtrip-original');
  const source = [
    { id: 'set-a', source: 'ai', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'twoColumn', layoutRowId: 'layout-front', layoutRowVersion: 1, cutType: 'styling', spaceGroupId: originalGroupId, spaceVariation: 'subtle', spaceSetMemberOrder: 1, refScope: 'pose' },
    { id: 'set-b', source: 'ai', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'twoColumn', layoutRowId: 'layout-front', layoutRowVersion: 1, cutType: 'styling', spaceGroupId: originalGroupId, spaceVariation: 'subtle', spaceSetMemberOrder: 2, refScope: 'pose' },
    detachSpaceMembership({ id: 'mine-middle', source: 'mine', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'stack', spaceGroupId: originalGroupId, spaceVariation: 'subtle', spaceSetMemberOrder: 3, refScope: 'pose', ownImages: ['mine.webp'] }),
    { id: 'set-c', source: 'ai', sectionId: 'styling', sectionRole: 'styling', sectionLayout: 'stack', cutType: 'styling', spaceGroupId: originalGroupId, spaceVariation: 'subtle', spaceSetMemberOrder: 3, refScope: 'pose' },
  ];
  return rekeySeparatedSpaceRuns(v4Fixture(source).map(({ layoutRowId, layoutRowVersion, ...b }) => b), (setId) => spaceSetGroupId(setId, 'roundtrip-split'));
};

test('B5 mock save and reload preserve split-run order, mine placement, and row layout fields', async () => {
  const original = await mockAdapter.getStoryboard('mock-b5');
  const board = persistedSplitBoard();
  try {
    await mockAdapter.saveStoryboard('mock-b5', board);
    const loaded = await mockAdapter.getStoryboard('mock-b5');
    assert.deepEqual(loaded, board);
  } finally {
    await mockAdapter.saveStoryboard('mock-b5', original);
  }
});

test('B5 HTTP PUT and reload preserve the same server-valid split board', async () => {
  const originalFetch = globalThis.fetch;
  const originalGetSession = supabase.auth.getSession;
  const board = persistedSplitBoard();
  let stored = null;
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test-token' } } });
  globalThis.fetch = async (url, options = {}) => {
    const pathname = new URL(url, 'http://test.local').pathname;
    if (pathname.endsWith('/storyboard') && options.method === 'PUT') {
      stored = JSON.parse(options.body);
      return jsonResponse({ blocks: stored, revision: 1 });
    }
    if (pathname.endsWith('/storyboard')) return jsonResponse({ blocks: stored || [], revision: stored ? 1 : 0 });
    if (pathname.endsWith('/product')) return jsonResponse({ clothingType: 'top', colors: [] });
    if (pathname.endsWith('/analysis')) return jsonResponse({ targetGenders: ['women'] });
    if (pathname === '/v1/projects/http-b5') return jsonResponse({ id: 'http-b5' });
    throw new Error(`unexpected request: ${pathname}`);
  };
  try {
    await httpAdapter.saveStoryboard('http-b5', board);
    assert.deepEqual(await httpAdapter.getStoryboard('http-b5'), board);
  } finally {
    restoreGlobal('fetch', originalFetch);
    supabase.auth.getSession = originalGetSession;
  }
});

test('Editor clothing upload regression keeps uploadPhoto and uploaded.url without pickAnyImage', () => {
  const uploadEditorImage = editorSource.slice(
    editorSource.indexOf('const uploadEditorImage ='),
    editorSource.indexOf('const dropImageFiles ='),
  );
  assert.match(uploadEditorImage, /const prepared = await toUploadableImage\(file\)/);
  assert.match(uploadEditorImage, /const uploaded = await api\.uploadPhoto\(projectId, \{/);
  assert.match(uploadEditorImage, /id: uploaded\.assetId \|\| uid\('w'\),\s*src: uploaded\.url/);
  assert.match(uploadEditorImage, /setWardrobe\(\(current\) => \(\{ \.\.\.current, \[colorId\]:/);
  assert.doesNotMatch(uploadEditorImage, /pickAnyImage/);
});
