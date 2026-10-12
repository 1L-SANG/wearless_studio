import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

let vite, remote, mock, facade, DB;
before(async () => {
  vite = await createServer({
    configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    define: { 'import.meta.env.VITE_API_MODE': '"mock"', 'import.meta.env.VITE_API_BASE_URL': '"https://api.test"' },
    plugins: [{ name: 'canvas-ai-session', enforce: 'pre',
      resolveId(id) { if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0canvas-ai-session'; },
      load(id) { if (id === '\0canvas-ai-session') return 'export const supabase = { auth: { getSession: async () => ({ data: { session: { access_token: "test-token", user: { id: "test-user" } } } }) } };'; },
    }],
  });
  ({ httpAdapter: remote } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  facade = await vite.ssrLoadModule('/src/lib/api/index.js');
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());
beforeEach(() => {
  DB.account.credits = 200;
  DB.product = { clothingType: 'outer', colors: [
    { id: 'navy', isBase: true, images: [{ id: 'navy-front', slot: 'Front', src: '/navy.jpg' }] },
    { id: 'ivory', images: [{ id: 'ivory-front', slot: 'Front', src: '/ivory.jpg' }] },
    { id: 'red', images: [] },
  ] };
  DB.analysis = { frontClosure: { version: 1, kind: 'zipper', range: 'graded' } };
  DB.storyboard = [];
  DB.editorBlocks = [];
  DB.assets = {};
  DB.wardrobe = { navy: [
    { id: 'worn', assetId: 'worn', src: '/v1/assets/worn/file', cutType: 'horizon', ai: true,
      metadata: { generation: { version: 1, origin: 'editor_new', recipeFrom: 'self', recipe: {
        cutType: 'horizon', shot: 'full', colorId: 'navy', direction: 'back', outerClosureState: 'open', exampleId: 'scene', modelId: 'model',
      } } } },
  ], misc: [] };
});

async function withHttp(responses, run) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', headers: new Headers(options.headers),
      body: options.body == null ? undefined : JSON.parse(options.body) });
    const response = responses.shift();
    assert.ok(response, 'unexpected HTTP request');
    if (response instanceof Response) return response;
    return new Response(JSON.stringify(response), { status: response.jobId ? 202 : 200, headers: { 'Content-Type': 'application/json' } });
  };
  try { await run(calls); }
  finally { globalThis.fetch = original; }
}
const edit = (variationMode, value, idempotencyKey, source = '/v1/assets/worn/file') => ({
  mode: 'vary', variationMode, source: { src: source },
  ...(variationMode === 'color' ? { colorId: value } : { outerClosureState: value }), idempotencyKey,
});

test('canvas job failures keep the server message and UI-only options stay out of the body', async () => {
  for (const fill of [false, true]) {
    await withHttp([{ jobId: 'failed-job' }, { status: 'error', errorMessage: '품질 검사에 실패했어요.' }], async (calls) => {
      await assert.rejects(fill
        ? remote.fillEmptySlot('project', { slot: { blockId: 'b', elementId: 'el' }, idempotencyKey: 'failed-fill', preserveFailureMessage: true })
        : remote.generateImage('project', { ...edit('color', 'ivory', 'failed-edit'), preserveFailureMessage: true }),
      { message: '품질 검사에 실패했어요.' });
      assert.equal('preserveFailureMessage' in calls[0].body, false);
    });
  }
});

test('asset-info posts only asset IDs and exposes the service envelope', async () => {
  const items = { worn: { kind: 'worn', origin: 'editor_new', cutType: 'horizon', colorId: 'navy', realDerived: false,
    current: { colorId: 'navy', outerClosureState: 'open', direction: 'back', detailTargetId: null }, recipe: null } };
  await withHttp([{ items }], async (calls) => {
    assert.deepEqual(await remote.getEditorAssetInfo('project', ['worn', 'worn']), { items });
    assert.equal(calls[0].url, 'https://api.test/v1/projects/project/editor:asset-info');
    assert.equal(calls[0].method, 'POST');
    assert.deepEqual(calls[0].body, { assetIds: ['worn', 'worn'] });
  });
  assert.equal(typeof facade.getEditorAssetInfo, 'function');
});

for (const [mode, value] of [['color', 'ivory'], ['closure', 'partial']]) {
  test(`${mode} editing sends the exact contract and keeps charge settlement`, async () => {
    const image = { id: 'result', assetId: 'result', src: '/v1/assets/result/file', cutType: 'horizon' };
    await withHttp([{ jobId: 'edit-job' }, { status: 'done', progress: 100, result: { data: image, credits: 181, creditsCharged: 19 } }], async (calls) => {
      const events = [];
      const result = await remote.generateImage('project', { ...edit(mode, value, 'edit-key'),
        onJobStarted: (id) => events.push({ id, calls: calls.length }), onProgress: (progress) => events.push(progress) });
      assert.deepEqual(calls[0].body, { mode: 'vary', variationMode: mode, source: { src: '/v1/assets/worn/file' },
        ...(mode === 'color' ? { colorId: 'ivory' } : { outerClosureState: 'partial' }) });
      assert.equal(calls[0].headers.get('Idempotency-Key'), 'edit-key');
      assert.deepEqual(events[0], { id: 'edit-job', calls: 1 });
      assert.equal(result.data.assetId, 'result');
      assert.equal(result.data.src, 'https://api.test/v1/assets/result/file?e=1');
      assert.equal(result.creditsCharged, 19);
    });
  });
}

test('garment edits without a key fail before HTTP and a key conflict remains a definite rejection', async () => {
  await withHttp([], async (calls) => {
    for (const mode of ['color', 'closure']) await assert.rejects(remote.generateImage('project', edit(mode, 'ivory')), {
      status: 400, code: 'idempotency_key_required', requestNotSent: true,
    });
    assert.equal(calls.length, 0);
  });
  await withHttp([new Response(JSON.stringify({ error: { code: 'idempotency_key_reused', message: '같은 요청 번호로 다른 내용이 왔어요. 다시 눌러 주세요.' } }), { status: 409 })], async () => {
    await assert.rejects(remote.generateImage('project', edit('color', 'ivory', 'reused')), (error) => {
      assert.equal(error.code, 'idempotency_key_reused');
      assert.equal(error.submissionUnknown, undefined);
      return error.status === 409;
    });
  });
});

test('asset URL parsing accepts signed absolute URLs and rejects lookalike paths', async () => {
  const { extractEditorAssetId } = await vite.ssrLoadModule('/src/lib/editorAssetInfo.js');
  for (const [src, id] of [
    ['/v1/assets/asset-1/file', 'asset-1'], ['https://api.test/v1/assets/asset-1/file?e=2#photo', 'asset-1'],
    ['//api.test/v1/assets/a%20b/file', 'a b'], ['/v1/assets/asset-1/file/', 'asset-1'],
    ['/v1/assets/a%2Fb/file', null], ['/v1/assets/a/file/other', null], ['/fake/v1/assets/a/file', null],
    ['data:image/png;base64,abc', null], ['blob:https://api.test/v1/assets/a/file', null], [null, null],
  ]) assert.equal(extractEditorAssetId(src), id, String(src));
});

test('asset classification uses authoritative kind and recipes without slot inference', async () => {
  const { classifyEditorAssetInfo } = await vite.ssrLoadModule('/src/lib/editorAssetInfo.js');
  for (const [info, expected] of [
    [{ kind: 'upload', cutType: 'product', recipe: { shot: 'ghost' } }, 'upload'],
    [{ upload: true }, 'upload'], [{ cutType: 'styling' }, 'worn'],
    [{ cutType: 'product', recipe: { shot: 'ghost' } }, 'ghost'],
    [{ cutType: 'horizon', detail_recipe: { target: 'pocket' } }, 'detail'],
    [{ cutType: 'product', recipe: { shot: 'detail' } }, 'detail'],
    [{ cutType: 'product' }, 'product_unknown'], [{ sourceBlockId: 'ghost', source: { cutType: 'product' } }, 'unknown'],
  ]) assert.equal(classifyEditorAssetInfo(info), expected);
});

test('mock asset-info resolves wardrobe and assets with known current values and omits missing IDs', async () => {
  DB.wardrobe.ivory = [{ id: 'upload', src: '/upload.jpg', source: 'upload' }];
  DB.wardrobe.misc = [
    { id: 'ghost', src: '/ghost.jpg', cutType: 'product', recipe: { cutType: 'product', shot: 'ghost', direction: 'front' } },
    { id: 'detail', src: '/detail.jpg', cutType: 'product', detailReshoot: true, recipe: { shot: 'detail', detailTargetId: 'pocket' } },
    { id: 'old', src: '/old.jpg', cutType: 'product' },
  ];
  DB.assets = { unknown: { id: 'unknown', projectId: 'project', src: '/unknown.jpg' },
    foreign: { id: 'foreign', projectId: 'other-project', source: 'upload' } };
  const { items } = await mock.getEditorAssetInfo('project', ['worn', 'upload', 'ghost', 'detail', 'old', 'unknown', 'foreign', 'missing', 'worn']);
  assert.deepEqual(Object.fromEntries(Object.entries(items).map(([id, value]) => [id, value.kind])), {
    worn: 'worn', upload: 'upload', ghost: 'ghost', detail: 'detail', old: 'product_unknown', unknown: 'unknown',
  });
  assert.deepEqual(items.worn.current, { colorId: 'navy', outerClosureState: 'open', direction: 'back', detailTargetId: null });
  assert.deepEqual(items.upload.current, { colorId: 'ivory', outerClosureState: null, direction: null, detailTargetId: null });
  assert.equal(items.upload.recipe, null);
  assert.equal(items.ghost.current.direction, 'front');
  assert.equal(items.detail.current.detailTargetId, 'pocket');
  assert.equal(items.worn.recipe.exampleId, 'scene');
  assert.equal(Object.keys(items.worn.recipe).length, 22);
  assert.equal(items.worn.recipe.sideStyle, null);
  assert.equal(items.worn.realDerived, false);
  assert.equal((await mock.getWardrobe('project')).navy[0].assetId, 'worn');
});

test('mock asset-info checks list format and maximum request size', async () => {
  for (const ids of [null, 'worn', [null], [''], Array.from({ length: 61 }, (_, i) => `asset-${i}`)]) {
    await assert.rejects(mock.getEditorAssetInfo('project', ids), { status: 400, code: 'asset_ids_invalid' });
  }
  assert.deepEqual(await mock.getEditorAssetInfo('project', []), { items: {} });
});

test('mock color edit inherits scene recipe, moves to selected color group, and records result identity', async () => {
  const result = await mock.generateImage('project', edit('color', 'ivory', 'mock-color'));
  assert.equal(result.credits, 195);
  assert.equal(result.creditsCharged, 5);
  assert.equal(result.data.assetId, result.data.id);
  assert.equal(result.data.wardrobeGroup, 'ivory');
  const info = (await mock.getEditorAssetInfo('project', [result.data.assetId])).items[result.data.assetId];
  assert.equal(info.kind, 'worn');
  assert.equal(info.origin, 'editor_color');
  assert.deepEqual(info.current, { colorId: 'ivory', outerClosureState: 'open', direction: 'back', detailTargetId: null });
  assert.equal(info.recipe.exampleId, 'scene');
  assert.equal(result.data.metadata.generation.sourceAssetId, 'worn');
  assert.equal(result.data.metadata.generation.recipeFrom, 'anchor');
  assert.equal(result.data.metadata.generation.autoSelected, false);
});

test('mock closure applies binary normalization and preserves its source color group', async () => {
  DB.analysis.frontClosure = { version: 1, kind: 'button', range: 'binary' };
  const result = await mock.generateImage('project', edit('closure', 'partial', 'mock-binary'));
  const info = (await mock.getEditorAssetInfo('project', [result.data.assetId])).items[result.data.assetId];
  assert.equal(result.credits, 195);
  assert.equal(result.creditsCharged, 5);
  assert.equal(info.origin, 'editor_closure');
  assert.equal(info.current.outerClosureState, 'closed');
  assert.equal(result.data.wardrobeGroup, 'navy');
  assert.equal(info.recipe.colorId, 'navy');
  assert.equal(info.recipe.exampleId, 'scene');
});

test('mock edit rejection leaves credits and wardrobe unchanged', async () => {
  DB.wardrobe.misc = [{ id: 'product', src: '/product.jpg', cutType: 'product', recipe: { shot: 'ghost' } }];
  for (const [request, code, status] of [
    [edit('color', 'unknown', 'unknown-color'), 'color_unknown', 400],
    [edit('color', 'red', 'missing-color-photo'), 'no_selected_color_images', 409],
    [edit('color', 'navy', 'same-color'), 'edit_same_value', 400],
    [edit('closure', 'open', 'same-closure'), 'edit_same_value', 400],
    [edit('color', 'ivory', 'product-edit', '/product.jpg'), 'edit_source_unsupported', 400],
    [edit('color', 'ivory'), 'idempotency_key_required', 400],
  ]) await assert.rejects(mock.generateImage('project', request), { code, status });
  DB.analysis.frontClosure = { version: 1, kind: 'none', range: null };
  await assert.rejects(mock.generateImage('project', edit('closure', 'closed', 'hidden-closure')), { code: 'closure_unsupported' });
  DB.product.clothingType = 'top';
  await assert.rejects(mock.generateImage('project', edit('closure', 'closed', 'not-outer')), { code: 'closure_unsupported' });
  assert.equal(DB.account.credits, 200);
  assert.equal(Object.values(DB.wardrobe).flat().length, 2);
});

test('mock upload supports similar and garment edits without inventing current recipe values', async () => {
  const upload = await mock.uploadPhoto('project', { filename: 'seller.jpg', mime: 'image/jpeg', blob: new Blob(['photo'], { type: 'image/jpeg' }) });
  try {
    const info = (await mock.getEditorAssetInfo('project', [upload.assetId])).items[upload.assetId];
    assert.equal(info.kind, 'upload');
    assert.deepEqual(info.current, { colorId: null, outerClosureState: null, direction: null, detailTargetId: null });
    const similar = await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: upload.url }, idempotencyKey: 'upload-similar' });
    assert.equal(similar.creditsCharged, 8);
    assert.equal(similar.credits, 192);
    assert.equal(similar.data.cutType, 'styling');
    assert.equal(similar.data.similarCut.sourceAssetId, upload.assetId);
    const next = await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: similar.data.src }, idempotencyKey: 'upload-similar-again' });
    assert.equal(next.data.similarCut.rootAssetId, upload.assetId);
    assert.equal(next.data.similarCut.sourceAssetId, similar.data.assetId);
    assert.notEqual(next.data.similarCut.plan.actionKey, similar.data.similarCut.plan.actionKey);
    const color = await mock.generateImage('project', edit('color', 'ivory', 'upload-color', upload.url));
    const colorInfo = (await mock.getEditorAssetInfo('project', [color.data.assetId])).items[color.data.assetId];
    assert.equal(colorInfo.origin, 'editor_color');
    assert.equal(colorInfo.recipe.colorId, 'ivory');
    assert.equal(colorInfo.recipe.direction, null);
    assert.equal(colorInfo.recipe.outerClosureState, null);
  } finally { URL.revokeObjectURL(upload.url); }
});

test('mock generated ghost and detail images keep recipes for future menu choices', async () => {
  const ghost = await mock.generateImage('project', { mode: 'new', cutType: 'product', shot: 'ghost', direction: 'back', colorId: 'ivory' });
  const detail = await mock.generateImage('project', { mode: 'new', cutType: 'product', shot: 'detail', detailTargetId: 'pocket', colorId: 'navy' });
  const { items } = await mock.getEditorAssetInfo('project', [ghost.data.assetId, detail.data.assetId]);
  assert.equal(items[ghost.data.assetId].kind, 'ghost');
  assert.equal(items[ghost.data.assetId].current.direction, 'back');
  assert.equal(items[detail.data.assetId].kind, 'detail');
  assert.equal(items[detail.data.assetId].current.detailTargetId, 'pocket');
});

test('mock standalone canvas sources resolve by URL and support similar without guessing from slot ancestry', async () => {
  DB.editorBlocks = [{ id: 'placed', elements: [
    { id: 'canvas-worn', type: 'image', src: '/demo-worn.jpg', cutType: 'styling', sourceBlockId: 'old-detail' },
    { id: 'canvas-upload', type: 'image', src: '/seller-local.jpg', sourceBlockId: 'old-ghost' },
  ] }];
  DB.storyboard = [{ id: 'old-detail', cutType: 'product', shot: 'detail' }, { id: 'old-ghost', cutType: 'product', shot: 'ghost' }];
  const { items } = await mock.getEditorAssetInfo('project', ['/demo-worn.jpg', '/seller-local.jpg']);
  assert.equal(items['/demo-worn.jpg'].kind, 'worn');
  assert.equal(items['/seller-local.jpg'].kind, 'upload');
  const similar = await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: '/demo-worn.jpg' }, idempotencyKey: 'canvas-similar' });
  assert.equal(similar.data.cutType, 'styling');
  assert.equal(similar.data.similarCut.sourceAssetId, '/demo-worn.jpg');
});

test('mock uploaded similar families can repeat from candidates and avoid recent actions', async () => {
  DB.wardrobe.ivory = [{ id: 'seller-source', src: '/seller-source.jpg', source: 'upload' }];
  const first = (await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: '/seller-source.jpg' }, idempotencyKey: 'seller-family-1' })).data;
  const second = (await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: first.src }, idempotencyKey: 'seller-family-2' })).data;
  assert.equal(second.similarCut.rootAssetId, 'seller-source');
  assert.equal(second.similarCut.sourceAssetId, first.assetId);
  assert.notEqual(second.similarCut.plan.actionKey, first.similarCut.plan.actionKey);
  assert.equal(second.wardrobeGroup, 'ivory');
});

test('mock real-derived edits retain asset provenance in the generated result', async () => {
  DB.wardrobe.navy[0].metadata.facemarket_real_derived = true;
  assert.equal((await mock.getEditorAssetInfo('project', ['worn'])).items.worn.realDerived, true);
  const result = await mock.generateImage('project', edit('color', 'ivory', 'real-derived-edit'));
  assert.equal((await mock.getEditorAssetInfo('project', [result.data.assetId])).items[result.data.assetId].realDerived, true);
});

test('mock uploaded photos remain styling sources even when an old canvas cut type is present', async () => {
  DB.wardrobe.ivory = [{ id: 'upload-stale', src: '/upload-stale.jpg', source: 'upload', cutType: 'product' }];
  const edited = await mock.generateImage('project', edit('closure', 'partial', 'upload-stale-closure', '/upload-stale.jpg'));
  assert.equal(edited.data.cutType, 'styling');
  const similar = await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: '/upload-stale.jpg' }, idempotencyKey: 'upload-stale-similar' });
  assert.equal(similar.data.cutType, 'styling');
});

test('mock photos with identical placeholder artwork retain separate source identities', async () => {
  const random = Math.random;
  const values = [0.2, 2 / 101, 0.4, 72 / 101];
  let first, second;
  try {
    Math.random = () => values.shift() ?? 0.5;
    first = (await mock.generateImage('project', { mode: 'new', cutType: 'styling', colorId: 'navy' })).data;
    second = (await mock.generateImage('project', { mode: 'new', cutType: 'styling', colorId: 'ivory' })).data;
  } finally { Math.random = random; }
  const similar = await mock.generateImage('project', { mode: 'vary', variationMode: 'similar', source: { src: second.src }, idempotencyKey: 'placeholder-identity' });
  assert.notEqual(first.assetId, second.assetId);
  assert.equal(similar.data.similarCut.sourceAssetId, second.assetId);
  assert.equal(similar.data.wardrobeGroup, 'ivory');
});

test('mock same-key joins reuse one job and charge while payload changes are rejected', async () => {
  const jobs = [];
  const request = edit('color', 'ivory', 'deduplicate-edit');
  const first = mock.generateImage('project', { ...request, onJobStarted: (id) => jobs.push(id) });
  const second = mock.generateImage('project', { source: request.source, colorId: 'ivory', variationMode: 'color', mode: 'vary',
    idempotencyKey: request.idempotencyKey, _internal: 'ignored', onJobStarted: (id) => jobs.push(id) });
  await assert.rejects(mock.generateImage('project', edit('closure', 'partial', request.idempotencyKey)), {
    status: 409, code: 'idempotency_key_reused', message: '같은 요청 번호로 다른 내용이 왔어요. 다시 눌러 주세요.',
  });
  const [one, two] = await Promise.all([first, second]);
  assert.equal(one.data.id, two.data.id);
  assert.equal(jobs.length, 2);
  assert.equal(jobs[0], jobs[1]);
  assert.equal(DB.account.credits, 195);
  assert.equal(DB.wardrobe.ivory.length, 1);
  assert.equal((await mock.generateImage('project', request)).data.id, one.data.id);
  assert.equal(DB.account.credits, 195);
});

test('mock empty-slot joins preserve the same-slot operation across different keys', async () => {
  const slot = { blockId: 'block', elementId: 'empty' };
  const first = mock.fillEmptySlot('project', { slot, idempotencyKey: 'fill-one' });
  const second = mock.fillEmptySlot('project', { slot, idempotencyKey: 'fill-two' });
  const [one, two] = await Promise.all([first, second]);
  assert.equal(one.data.id, two.data.id);
  assert.equal(DB.account.credits, 181);
  assert.equal(DB.wardrobe.navy.length, 2);
  assert.equal((await mock.getEditorAssetInfo('project', [one.data.assetId])).items[one.data.assetId].origin, 'context_fill');
});
