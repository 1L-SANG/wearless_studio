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
  db.mallRegistrations = [];
  db.detailPublications = [{ publicationId: 'publication-local', projectId: 'project-local',
    status: 'ready', files: [{ index: 0 }, { index: 1 }] }];
});
after(() => vite?.close());

const payload = (patch = {}) => ({ projectId: 'project-local', publicationId: 'publication-local',
  requestKey: randomUUID(), productName: '니트', price: 39000, ...patch });
const registrationFields = ['jobId', 'status', 'progress', 'outcome', 'productNo', 'productUrl',
  'mallId', 'publicationId', 'imageCount', 'thumbnail', 'code', 'errorMessage', 'externalCode', 'verifiable', 'warnings', 'createdAt', 'finishedAt'];
const connectionFields = ['mall', 'status', 'shopName', 'mallId', 'permissions', 'connectedAt', 'lastRegisteredAt', 'tokenExpiresAt'];
const fieldsMatch = (value, fields) => assert.deepEqual(Object.keys(value).sort(), [...fields].sort());
const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
const clock = (t) => t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: new Date('2026-10-26T00:00:00Z') });
async function finish(t, request) {
  const accepted = await mock.createCafe24Registration(request);
  t.mock.timers.tick(2000);
  await Promise.resolve();
  return mock.getCafe24Registration(accepted.jobId);
}

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

test('registration routes and request key match contract', async (t) => {
  const calls = [];
  const request = payload({ supplyPrice: 20000, display: 'F', selling: 'T', taxType: 'B',
    categoryNo: 27, thumbnailAssetId: 'asset-local' });
  const accepted = { jobId: randomUUID(), status: 'queued', externalCode: 'wl0123456789abcdef' };
  const detail = { jobId: accepted.jobId, status: 'done', outcome: 'unknown', verifiable: true,
    mallId: 'myshop', publicationId: 'publication-local', progress: 100, productNo: null, productUrl: null,
    imageCount: 2, thumbnail: null, externalCode: accepted.externalCode,
    code: null, errorMessage: null, warnings: [], createdAt: '2026-10-26T00:00:00Z', finishedAt: '2026-10-26T00:00:01Z' };
  const categories = [{ categoryNo: 27, depth: 2, name: '니트', fullName: ['상의', '니트'] }];
  const replies = [accepted, detail, detail, { found: false, checkedAt: '2026-10-26T00:00:00Z', message: '아직 확인되지 않았어요' },
    { ...detail, outcome: 'created', verifiable: false }, { items: [detail], recovery: detail }, categories];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url: String(url), options });
    return options.method === 'DELETE' ? new Response(null, { status: 204 }) : json(replies.shift());
  });
  assert.deepEqual(await adapter.createCafe24Registration(request), accepted);
  assert.deepEqual(await adapter.createCafe24Registration(request), detail);
  assert.deepEqual(await adapter.getCafe24Registration(accepted.jobId), detail);
  assert.equal((await adapter.verifyCafe24Registration(accepted.jobId)).found, false);
  assert.equal((await adapter.verifyCafe24Registration(accepted.jobId)).outcome, 'created');
  assert.deepEqual(await adapter.listMallRegistrations(request.projectId), { items: [detail], recovery: detail });
  assert.deepEqual(await adapter.listCafe24Categories(), categories);
  await adapter.disconnectCafe24();
  const route = `/v1/malls/cafe24/registrations/${accepted.jobId}`;
  assert.deepEqual(calls.map(({ url, options }) => [url, options.method]), [
    ['/v1/malls/cafe24/registrations', 'POST'], ['/v1/malls/cafe24/registrations', 'POST'], [route, 'GET'],
    [`${route}/verify`, 'POST'], [`${route}/verify`, 'POST'],
    ['/v1/projects/project-local/mall-registrations', 'GET'], ['/v1/malls/cafe24/categories', 'GET'], ['/v1/malls/cafe24', 'DELETE'],
  ]);
  assert.deepEqual(JSON.parse(calls[0].options.body), request);
  assert.equal(JSON.parse(calls[1].options.body).requestKey, request.requestKey);
  assert.ok(calls.every(({ options }) => options.credentials === undefined));
  t.mock.method(console, 'error', () => {});
  t.mock.method(globalThis, 'fetch', async () => json({ error: {
    code: 'registration_in_progress', message: '이 프로젝트의 상품을 등록하고 있어요', jobId: accepted.jobId,
  } }, 409));
  await assert.rejects(adapter.createCafe24Registration(payload()), (error) =>
    error.status === 409 && error.code === 'registration_in_progress' && error.jobId === accepted.jobId);
});

test('mock install connects with mall id and never stores tokens', async (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://studio.example.test' } });
  t.after(() => previous ? Object.defineProperty(globalThis, 'location', previous) : delete globalThis.location);
  const absent = (await mock.listMalls()).items.find((item) => item.mall === 'cafe24');
  fieldsMatch(absent, connectionFields);
  assert.equal(absent.status, 'none');
  assert.equal(absent.mallId, null);
  for (const [mallId, code, status] of [['bad-shop', 'invalid_mall_id', 400], ['closed-shop', 'cafe24_not_allowed', 403],
    ['x', 'invalid_mall_id', 400], ['https://shop.example.test', 'invalid_mall_id', 400]]) {
    await assert.rejects(mock.startCafe24Install({ mallId }), (error) => error.code === code && error.status === status);
  }
  const result = await mock.startCafe24Install({ mallId: 'HTTPS://MyShop.cafe24.com/admin',
    returnTo: '/editor/project-local', access_token: 'fixture-access', refresh_token: 'fixture-refresh' });
  assert.deepEqual(result, { startUrl: 'https://studio.example.test/settings/malls?mall=cafe24&result=connected' });
  const connected = (await mock.listMalls()).items.find((item) => item.mall === 'cafe24');
  fieldsMatch(connected, connectionFields);
  assert.equal(connected.status, 'connected');
  assert.equal(connected.mallId, 'myshop');
  assert.equal(typeof connected.shopName, 'string');
  assert.ok(Date.parse(connected.tokenExpiresAt) > Date.now());
  for (const value of [result, connected, db]) {
    assert.doesNotMatch(JSON.stringify(value), /fixture-access|fixture-refresh|access_token|refresh_token|accessKey|secretKey/);
  }
  await mock.disconnectCafe24();
  assert.equal((await mock.listMalls()).items.find((item) => item.mall === 'cafe24').status, 'none');
  assert.equal(db.mallConnections.cafe24, undefined);
});

test('mock registration reaches created, unknown, failed with code, and verify', async (t) => {
  clock(t);
  await mock.startCafe24Install({ mallId: 'myshop' });
  const accepted = await mock.createCafe24Registration(payload({ thumbnailAssetId: 'asset-local' }));
  assert.equal(accepted.status, 'queued');
  assert.match(accepted.externalCode, /^wl[0-9a-f]{16}$/);
  assert.equal(accepted.externalCode, `wl${accepted.jobId.replaceAll('-', '').slice(0, 16)}`);
  assert.equal((await mock.getCafe24Registration(accepted.jobId)).status, 'pending');
  assert.equal((await mock.getJob(accepted.jobId)).result, null);
  t.mock.timers.tick(1999);
  await Promise.resolve();
  assert.equal((await mock.getCafe24Registration(accepted.jobId)).status, 'pending');
  t.mock.timers.tick(1);
  await Promise.resolve();
  const created = await mock.getCafe24Registration(accepted.jobId);
  fieldsMatch(created, registrationFields);
  assert.equal(created.status, 'done');
  assert.equal(created.outcome, 'created');
  assert.equal(created.verifiable, false);
  assert.equal(created.progress, 100);
  assert.equal(created.imageCount, 2);
  assert.equal(created.thumbnail, 'ok');
  assert.equal((await mock.getJob(accepted.jobId)).result.outcome, 'created');
  assert.match(created.productUrl, /^https:\/\/myshop.cafe24.com\/product\/detail.html\?product_no=\d+$/);
  assert.equal((await mock.listMalls()).items.find((item) => item.mall === 'cafe24').lastRegisteredAt, created.finishedAt);
  await assert.rejects(mock.verifyCafe24Registration(created.jobId), (error) => error.code === 'not_verifiable' && error.status === 409);
  const unknown = await finish(t, payload({ productName: 'unknown 니트' }));
  assert.equal(unknown.status, 'done');
  assert.equal(unknown.outcome, 'unknown');
  assert.equal(unknown.verifiable, true);
  assert.equal(unknown.productNo, null);
  const notFound = await mock.verifyCafe24Registration(unknown.jobId);
  fieldsMatch(notFound, ['found', 'checkedAt', 'message']);
  assert.equal(notFound.found, false);
  assert.equal((await mock.getCafe24Registration(unknown.jobId)).outcome, 'unknown');
  const found = await mock.verifyCafe24Registration(unknown.jobId);
  fieldsMatch(found, registrationFields);
  assert.equal(found.outcome, 'created');
  assert.equal(found.status, 'done');
  assert.equal(found.verifiable, false);
  assert.equal(found.thumbnail, 'skipped');
  assert.deepEqual(found.warnings, ['대표 이미지는 카페24 관리자에서 넣어 주세요']);
  assert.equal(db.mallRegistrations.length, 2);
  const failed = await finish(t, payload({ price: 999, productName: 'unknown 거절' }));
  assert.equal(failed.status, 'error');
  assert.equal(failed.outcome, 'failed');
  assert.equal(failed.code, 'invalid_request');
  assert.equal(failed.errorMessage, '카페24가 입력을 받지 않았어요. 판매가와 상품명을 확인해 주세요');
  assert.equal(failed.verifiable, false);
  assert.equal(failed.productNo, null);
  assert.equal((await mock.getJob(failed.jobId)).result.code, 'invalid_request');
  await assert.rejects(mock.getCafe24Registration('missing'), (error) => error.status === 404 && error.code === 'registration_not_found');
});

test('same request key returns same job id in mock', async (t) => {
  clock(t);
  await mock.startCafe24Install({ mallId: 'myshop' });
  const request = payload();
  const first = await mock.createCafe24Registration(request);
  const retry = await mock.createCafe24Registration({ ...request, supplyPrice: request.price, display: 'T', selling: 'T', taxType: 'A' });
  assert.equal(retry.jobId, first.jobId);
  fieldsMatch(retry, registrationFields);
  await assert.rejects(mock.createCafe24Registration(payload()), (error) =>
    error.status === 409 && error.code === 'registration_in_progress' && error.jobId === first.jobId);
  for (const patch of [{ price: 40000 }, { projectId: 'other-project' }]) {
    await assert.rejects(mock.createCafe24Registration({ ...request, ...patch }), (error) =>
      error.status === 409 && error.code === 'idempotency_key_reused');
  }
  t.mock.timers.tick(2000);
  await Promise.resolve();
  assert.equal((await mock.createCafe24Registration(request)).jobId, first.jobId);
  assert.equal((await mock.createCafe24Registration(request)).outcome, 'created');
  assert.equal(db.mallRegistrations.length, 1);
  assert.notEqual((await mock.createCafe24Registration(payload())).jobId, first.jobId);
});

test('listing fields match server contract', async (t) => {
  clock(t);
  await mock.startCafe24Install({ mallId: 'myshop' });
  const categories = await mock.listCafe24Categories();
  assert.ok(categories.length > 0);
  for (const item of categories) {
    fieldsMatch(item, ['categoryNo', 'depth', 'name', 'fullName']);
    assert.ok(Array.isArray(item.fullName));
  }
  const recent = [];
  for (let index = 0; index < 6; index += 1) recent.push(await finish(t, payload()));
  await finish(t, payload({ projectId: 'other-project' }));
  const listing = await mock.listMallRegistrations('project-local');
  fieldsMatch(listing, ['items', 'recovery']);
  assert.equal(listing.recovery, null);
  assert.deepEqual(listing.items.map((item) => item.jobId), recent.slice(-5).reverse().map((item) => item.jobId));
  for (const item of listing.items) {
    fieldsMatch(item, registrationFields);
    assert.deepEqual(item, await mock.getCafe24Registration(item.jobId));
  }
  listing.items[0].warnings.push('mutated');
  assert.deepEqual((await mock.getCafe24Registration(listing.items[0].jobId)).warnings, []);
  assert.deepEqual(await mock.listMallRegistrations('empty-project'), { items: [], recovery: null });
  const connections = await mock.listMalls();
  for (const status of ['none', 'connected', 'reconnect_required', 'unavailable']) {
    const cafe24 = { ...connections.items.find((item) => item.mall === 'cafe24'), status };
    t.mock.method(globalThis, 'fetch', async () => json({ items: [cafe24] }));
    assert.deepEqual(await adapter.listMalls(), { items: [cafe24] });
  }
});

test('recovery finds an older unresolved registration for the current mall beyond recent five', async (t) => {
  clock(t);
  await mock.startCafe24Install({ mallId: 'myshop' });
  const unresolved = await finish(t, payload({ productName: 'unknown 니트' }));
  for (let index = 0; index < 6; index++) await finish(t, payload());
  await mock.startCafe24Install({ mallId: 'othershop' });
  const otherMall = await finish(t, payload({ productName: 'unknown 다른 몰' }));
  await mock.startCafe24Install({ mallId: 'myshop' });
  const listing = await mock.listMallRegistrations('project-local');
  assert.equal(listing.items.length, 5);
  assert.equal(listing.items[0].jobId, otherMall.jobId);
  assert.equal(listing.items.some((item) => item.jobId === unresolved.jobId), false);
  assert.equal(listing.recovery?.jobId, unresolved.jobId);
  assert.equal(listing.recovery.mallId, 'myshop');
  assert.equal(listing.recovery.publicationId, 'publication-local');
  listing.recovery.warnings.push('mutated');
  assert.deepEqual((await mock.getCafe24Registration(unresolved.jobId)).warnings, []);
  db.mallConnections.cafe24.status = 'reconnect_required';
  assert.equal((await mock.listMallRegistrations('project-local')).recovery.jobId, unresolved.jobId);
  await mock.disconnectCafe24();
  assert.equal((await mock.listMallRegistrations('project-local')).recovery, null);
});

test('recovery includes pending running and duplicate suspected but excludes definitive outcomes', async (t) => {
  clock(t);
  await mock.startCafe24Install({ mallId: 'myshop' });
  const accepted = await mock.createCafe24Registration(payload());
  assert.equal((await mock.listMallRegistrations('project-local')).recovery?.jobId, accepted.jobId);
  db.mallRegistrations[0].view.status = 'running';
  assert.equal((await mock.listMallRegistrations('project-local')).recovery?.jobId, accepted.jobId);
  t.mock.timers.tick(2000); await Promise.resolve();
  assert.equal((await mock.listMallRegistrations('project-local')).recovery, null);
  await finish(t, payload({ price: 999 }));
  assert.equal((await mock.listMallRegistrations('project-local')).recovery, null);
  const uncertain = await finish(t, payload({ productName: 'unknown 니트' }));
  db.mallRegistrations.at(-1).view.outcome = 'duplicate_suspected';
  assert.equal((await mock.listMallRegistrations('project-local')).recovery?.jobId, uncertain.jobId);
  await finish(t, payload({ projectId: 'other-project', productName: 'unknown 다른 프로젝트' }));
  assert.equal((await mock.listMallRegistrations('project-local')).recovery?.jobId, uncertain.jobId);
});
