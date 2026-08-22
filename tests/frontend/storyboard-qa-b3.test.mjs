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

const storyboardSource = readFileSync(
  new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url),
  'utf8',
);
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
    configFile: false,
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true },
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

test('1-01 Storyboard passes projectId, stores only URLs, and cancellation cannot create a null mine block', () => {
  assert.equal((storyboardSource.match(/api\.pickAnyImage\(projectId\)/g) || []).length, 3);
  assert.doesNotMatch(storyboardSource, /api\.pickAnyImage\(\)/);

  const addMineBlock = storyboardSource.slice(
    storyboardSource.indexOf('const addMineBlock ='),
    storyboardSource.indexOf('// drag-to-reorder blocks'),
  );
  assert.match(addMineBlock, /const src = mineImageUrl\(picked\);\s*if \(!src\) return;/);
  assert.ok(addMineBlock.indexOf('if (!src) return;') < addMineBlock.indexOf('const nb = mineBlock'));
  assert.deepEqual(normalizeMineImages([mineImageUrl(null)]), []);
  const mineBlock = storyboardSource.slice(
    storyboardSource.indexOf('const mineBlock ='),
    storyboardSource.indexOf('const addMineBlock ='),
  );
  assert.match(mineBlock, /ownImages: \[src\], thumb: src/);
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
    return { ok: true, status: 204 };
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

test('N4 general add is manual-empty, dropped examples are not, and the inspector still opens', () => {
  const addBlock = storyboardSource.slice(
    storyboardSource.indexOf('const addBlock ='),
    storyboardSource.indexOf('const mineBlock ='),
  );
  assert.match(addBlock, /\.\.\.\(!droppedExample \? \{ exampleChoice: 'manual' \} : \{\}\)/);
  assert.match(addBlock, /assignGenerationExamples\(out,[\s\S]*onlyBlockIds: \[nb\.id\]/);
  assert.match(addBlock, /setSelectedId\(nb\.id\); setSplitOpen\(true\)/);
  // 2026-08-15 오너: 빈 컷 안내는 짧게 — "분위기 예시를 골라주세요."
  assert.match(storyboardSource, /분위기 예시를 골라주세요\./);
  assert.match(storyboardSource, /이 조합의 예시를 준비하지 못했어요 — 컷 설정을 바꾸거나 직접 예시를 골라주세요/);
  assert.match(featureStyles, /\.sb-cutcard\.manual-empty, \.sb-frame-half\.manual-empty \{[^}]*border-style: dashed;[^}]*background: #f7fafc/);
});

test('N5 generation-example dataTransfer reaches an addzone and inserts after the exact card index', () => {
  const payload = new Map();
  const dataTransfer = {
    effectAllowed: 'none',
    setData(type, value) { payload.set(type, value); },
    getData(type) { return payload.get(type) || ''; },
  };
  dataTransfer.effectAllowed = 'copy';
  dataTransfer.setData('text/example-id', 'example-dragged');

  const blocks = [
    { id: 'a', sectionRole: 'styling' },
    { id: 'b', sectionRole: 'styling' },
    { id: 'c', sectionRole: 'styling' },
  ];
  const styling = renderGroups(blocks).find((group) => group.key === 'styling');
  const addzoneIndex = styling.items.find((item) => item.block.id === 'b').index;
  const inserted = [...blocks];
  inserted.splice(addzoneIndex, 0, { id: dataTransfer.getData('text/example-id') });
  assert.deepEqual(inserted.map((block) => block.id), ['a', 'b', 'example-dragged', 'c']);

  assert.match(storyboardSource, /event\.dataTransfer\.setData\('text\/example-id', example\.id\)/);
  assert.match(storyboardSource, /const exampleId = e\.dataTransfer\.getData\('text\/example-id'\) \|\| dragExampleId/);
  assert.match(storyboardSource, /addBlock\(idx, targetSid, targetRole, targetSpaceGroupId, targetGroupKey, exampleId\)/);
  // 2026-08-16: 세트 안 '사이 자리'는 언제나 드롭을 받고, ＋(직접 추가)만 예비 멤버가 남았을 때 열린다.
  assert.match(storyboardSource, /canAdd: !\(targetSpaceGroupId && !reservation\)/);
  assert.match(storyboardSource, /onAdd=\{canAdd \? \(\(event\) =>/);
  assert.match(storyboardSource, /const m = \[\.\.\.blocks\]; m\.splice\(idx, 0, nb\)/);
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
  return rekeySeparatedSpaceRuns(source, (setId) => spaceSetGroupId(setId, 'roundtrip-split'));
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
      return jsonResponse(null, 204);
    }
    if (pathname.endsWith('/storyboard')) return jsonResponse(stored);
    if (pathname.endsWith('/product')) return jsonResponse({ clothingType: 'top', colors: [] });
    if (pathname.endsWith('/analysis')) return jsonResponse({ targetGenders: ['women'] });
    if (pathname === '/v1/projects/http-b5') return jsonResponse({ composeMode: 'basic' });
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
  assert.match(uploadEditorImage, /setWardrobe\(\(current\) => \(\{ \.\.\.current, misc:/);
  assert.doesNotMatch(uploadEditorImage, /pickAnyImage/);
});
