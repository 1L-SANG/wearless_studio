import test, { before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createServer } from 'vite';

let vite, adapter, mock, db;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root}/src` } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  const { supabase } = await vite.ssrLoadModule('/src/lib/supabase.js');
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'local-session' } } });
  // Exercise the production two-second delay with a controlled clock.
  const scale = process.env.MOCK_API_LATENCY_SCALE;
  process.env.MOCK_API_LATENCY_SCALE = '1';
  try {
    ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  } finally {
    if (scale === undefined) delete process.env.MOCK_API_LATENCY_SCALE;
    else process.env.MOCK_API_LATENCY_SCALE = scale;
  }
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
});
beforeEach(() => {
  db.mallConnections = {};
  db.mallRegistrations = []; db.mallRegistrationBatches = {}; db.mallDefaults = {}; db.mallSalesDefaults = null;
  db.detailPublications = [{ publicationId: 'publication-local', projectId: 'project-local',
    status: 'ready', editorRevision: 4, files: [{ index: 0 }, { index: 1 }] }];
});
after(() => vite?.close());

const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
test('peekEditorRevision does not touch revision sync', async (t) => {
  const projectId = 'project-peek', original = [{ id: 'text', text: 'A' }], calls = [];
  let server = { revision: 4, blocks: original };
  t.mock.method(console, 'error', () => {});
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push([String(url), options.method]);
    if (options.method === 'PUT') return json({ error: { code: 'editor_conflict', message: '다른 창에서 저장했어요' } }, 409);
    return json(server);
  });
  await adapter.getEditorBlocks(projectId);
  server = { revision: 5, blocks: [{ id: 'text', text: 'B' }] };
  assert.equal(await adapter.peekEditorRevision(projectId), 5);
  assert.equal(adapter.editorRevisionOf(projectId), 4);
  await adapter.saveEditorBlocks(projectId, original);
  assert.equal(calls.length, 2);
  await assert.rejects(adapter.saveEditorBlocks(projectId, [{ id: 'text', text: 'A edited' }]),
    (error) => error.status === 409 && error.code === 'editor_conflict');
  assert.deepEqual(calls, [
    [`/v1/projects/${projectId}/editor-blocks?withRevision=1`, 'GET'],
    [`/v1/projects/${projectId}/editor-blocks?withRevision=1`, 'GET'],
    [`/v1/projects/${projectId}/editor-blocks?baseRevision=4`, 'PUT'],
  ]);
  assert.equal(adapter.editorRevisionOf(projectId), 4);
  assert.equal(await adapter.peekEditorRevision('project-peek-unloaded'), 5);
  assert.equal(adapter.editorRevisionOf('project-peek-unloaded'), null);
  for (const response of [[], { blocks: [], revision: '5' }]) {
    server = response;
    assert.equal(await adapter.peekEditorRevision(projectId), null);
    assert.equal(adapter.editorRevisionOf(projectId), 4);
  }
});

test('mock peekEditorRevision keeps the revision unknown and editor blocks unchanged', async () => {
  const before = structuredClone(db.editorBlocks);
  assert.equal(await mock.peekEditorRevision('project-local'), null);
  assert.equal(mock.editorRevisionOf('project-local'), null);
  assert.deepEqual(db.editorBlocks, before);
});

test('listMalls adds cafe24 unavailable when the server omits it', async (t) => {
  const items = [{ mall: 'zigzag', status: 'connected', shopName: '스토어' },
    { mall: 'ably', status: 'unavailable' }, { mall: 'smartstore', status: 'unavailable' }];
  t.mock.method(globalThis, 'fetch', async () => json({ items, disabled: false }));
  assert.deepEqual(await adapter.listMalls(), { items: [...items, { mall: 'cafe24', status: 'unavailable' }], disabled: false });
});

test('install sends normalized mall id with credentials include and reads start url', async (t) => {
  const calls = [];
  const startUrl = 'https://api.example.test/v1/malls/cafe24/install/local-attempt/start';
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url: String(url), options });
    return json({ startUrl });
  });
  for (const mallId of ['  MyShop  ', 'HTTPS://MyShop.cafe24.com/admin?x=1',
    'http://MyShop.cafe24api.com/#settings', 'MyShop.cafe24.com']) {
    assert.deepEqual(await adapter.startCafe24Install({ mallId, returnTo: '/editor/project-local' }), { startUrl });
    const { url, options } = calls.at(-1);
    assert.equal(url, '/v1/malls/cafe24/install');
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers.Authorization, 'Bearer local-session');
    assert.deepEqual(JSON.parse(options.body), { mallId: 'myshop', returnTo: '/editor/project-local' });
  }
  await adapter.startCafe24Install({ mallId: 'myshop' });
  assert.deepEqual(JSON.parse(calls.at(-1).options.body), { mallId: 'myshop' });
});

const requestDraft = (patch = {}) => ({ projectId: 'project-local', publicationId: 'publication-local', editorRevision: 4,
  selectedMalls: ['cafe24'], productName: '니트', basePrice: 39000, sizes: ['FREE'], mainAssetId: 'asset-local', ...patch });
const clock = (t) => t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: new Date('2026-10-26T00:00:00Z') });
async function accept(patch = {}) {
  const draft = requestDraft(patch), check = await mock.preflightMallRegistration(draft);
  for (const item of check.items) if (item.status === 'ready') (draft.confirmedSourcesByMall ||= {})[item.mall] = item.sourceVersions;
  return mock.createMallRegistrationBatch({ batchId: randomUUID(), draft });
}
async function finish(t, patch = {}) {
  const receipt = await accept(patch); t.mock.timers.tick(2000); await Promise.resolve();
  return mock.getMallRegistration(receipt.items[0].jobId);
}

test('mock install exposes public connection generation and preserves sales across disconnect', async () => {
  await mock.saveSalesDefaults({ baseRevision: 0, disclosure: { manufacturer: '내 상점' } });
  for (const [mallId, code] of [['bad-shop', 'invalid_mall_id'], ['closed-shop', 'cafe24_not_allowed'], ['x', 'invalid_mall_id']]) {
    await assert.rejects(mock.startCafe24Install({ mallId }), (error) => error.code === code);
  }
  await mock.startCafe24Install({ mallId: 'MyShop.cafe24.com' });
  const connection = (await mock.listMalls()).items.find((item) => item.mall === 'cafe24');
  assert.equal(connection.mallId, 'myshop'); assert.equal(connection.registration.status, 'available');
  assert.equal('shopId' in connection, false); assert.ok(connection.connectionVersion);
  await mock.disconnectCafe24();
  assert.equal((await mock.listMalls()).items.find((item) => item.mall === 'cafe24').status, 'none');
  assert.equal((await mock.getSalesDefaults()).disclosure.manufacturer, '내 상점');
});

test('mock common registration preserves pending outcome and terminal view fields', async (t) => {
  clock(t); await mock.startCafe24Install({ mallId: 'myshop' });
  const receipt = await accept(), jobId = receipt.items[0].jobId;
  const pending = await mock.getMallRegistration(jobId);
  assert.equal(pending.outcome, null); assert.equal(pending.setupStatus, 'pending');
  assert.equal((await mock.getJob(jobId)).result, null);
  assert.equal((await mock.listMallRegistrations('project-local')).recoveries.cafe24[0].jobId, jobId);
  t.mock.timers.tick(2000); await Promise.resolve();
  const view = await mock.getMallRegistration(jobId);
  assert.deepEqual(Object.keys(view).sort(), ['jobId', 'mall', 'status', 'progress', 'stage', 'outcome', 'setupStatus', 'productId', 'productUrl', 'adminUrl', 'stages', 'retryAction', 'code', 'warnings', 'retention', 'publicationId', 'createdAt', 'finishedAt'].sort());
  assert.equal(view.outcome, 'created'); assert.equal(view.setupStatus, 'complete'); assert.equal(typeof view.productId, 'string');
  await assert.rejects(mock.verifyMallRegistration(jobId), (error) => error.code === 'not_verifiable');
  await assert.rejects(mock.resumeMallRegistration(jobId, { requestKey: randomUUID() }), (error) => error.code === 'registration_not_resumable');
  const failed = await finish(t, { basePrice: 999 });
  assert.equal(failed.outcome, 'failed'); assert.equal(failed.retryAction, 'new_request'); assert.equal(failed.code, 'invalid_request');
});

test('mock unknown verification never creates a job and resume reuses its product', async (t) => {
  clock(t); await mock.startCafe24Install({ mallId: 'myshop' });
  const unknown = await finish(t, { productName: 'unknown 니트' });
  assert.equal(unknown.retryAction, 'verify');
  assert.equal((await mock.verifyMallRegistration(unknown.jobId)).outcome, 'unknown');
  const found = await mock.verifyMallRegistration(unknown.jobId);
  assert.equal(found.outcome, 'created'); assert.equal(found.retryAction, 'resume');
  const key = randomUUID(), pending = await mock.resumeMallRegistration(found.jobId, { requestKey: key });
  assert.equal(pending.outcome, null); assert.equal(pending.productId, found.productId);
  assert.deepEqual(await mock.resumeMallRegistration(found.jobId, { requestKey: key }), pending);
  t.mock.timers.tick(2000); await Promise.resolve();
  const done = await mock.getMallRegistration(found.jobId);
  assert.equal(done.productId, found.productId); assert.equal(done.setupStatus, 'complete');
  assert.equal(db.mallRegistrations.length, 1);
});

test('mock setup failure resumes the same job and preserves successful product identity', async (t) => {
  clock(t); await mock.startCafe24Install({ mallId: 'myshop' });
  const partial = await finish(t, { productName: 'setup-fail 니트' });
  assert.equal(partial.outcome, 'created'); assert.equal(partial.setupStatus, 'needs_attention');
  assert.equal((await mock.listMallRegistrations('project-local')).recoveries.cafe24[0].jobId, partial.jobId);
  await mock.resumeMallRegistration(partial.jobId, { requestKey: randomUUID() });
  t.mock.timers.tick(2000); await Promise.resolve();
  const done = await mock.getMallRegistration(partial.jobId);
  assert.equal(done.productId, partial.productId); assert.equal(done.setupStatus, 'complete');
  assert.equal(db.mallRegistrations.length, 1);
});

test('mock admission requires confirmed revisions and replays receipts before connection checks', async (t) => {
  clock(t); await mock.startCafe24Install({ mallId: 'myshop' });
  const draft = requestDraft(), batchId = randomUUID();
  await assert.rejects(mock.createMallRegistrationBatch({ batchId, draft }), (error) => error.code === 'no_ready_malls' && error.items[0].issues[0].code === 'preflight_required');
  const check = await mock.preflightMallRegistration(draft);
  draft.confirmedSourcesByMall = { cafe24: check.items[0].sourceVersions };
  await mock.saveSalesDefaults({ baseRevision: 0, shippingDays: 3 });
  await assert.rejects(mock.createMallRegistrationBatch({ batchId, draft }), (error) => error.items[0].issues[0].code === 'defaults_changed');
  draft.confirmedSourcesByMall.cafe24 = (await mock.preflightMallRegistration(draft)).items[0].sourceVersions;
  const receipt = await mock.createMallRegistrationBatch({ batchId, draft });
  await mock.disconnectCafe24();
  assert.deepEqual(await mock.createMallRegistrationBatch({ batchId, draft }), receipt);
  await assert.rejects(mock.createMallRegistrationBatch({ batchId, draft: { ...draft, basePrice: 40000 } }), (error) => error.code === 'idempotency_key_reused');
});

test('mock history finds older recoveries for the current store beyond recent five', async (t) => {
  clock(t); await mock.startCafe24Install({ mallId: 'myshop' });
  const old = await finish(t, { productName: 'unknown 니트' });
  db.detailPublications.push({ ...db.detailPublications[0], projectId: 'other-project' });
  for (let i = 0; i < 6; i++) await finish(t, { projectId: 'other-project' });
  // Completed same-project history can follow an unresolved record imported from an earlier session.
  for (const row of db.mallRegistrations.slice(1)) row.draft.projectId = 'project-local';
  const list = await mock.listMallRegistrations('project-local');
  assert.equal(list.items.length, 5); assert.equal(list.items.some((i) => i.jobId === old.jobId), false);
  assert.equal(list.recoveries.cafe24[0].jobId, old.jobId); assert.deepEqual(list.recoveries.zigzag, []);
  list.recoveries.cafe24[0].warnings.push('mutation');
  assert.deepEqual((await mock.getMallRegistration(old.jobId)).warnings, []);
  await mock.startCafe24Install({ mallId: 'othershop' });
  assert.deepEqual((await mock.listMallRegistrations('project-local')).recoveries.cafe24, []);
  await assert.rejects(mock.verifyMallRegistration(old.jobId), (error) => error.code === 'connection_changed');
});
