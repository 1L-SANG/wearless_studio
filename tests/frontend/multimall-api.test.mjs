import test, { before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createServer } from 'vite';

let vite, http, mock, db, boundary;
before(async () => {
  const root = new URL('../..', import.meta.url).pathname;
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    define: { 'import.meta.env.VITE_API_MODE': '"http"' },
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': `${root}/src` } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ httpAdapter: http } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
  ({ buildHttpApi: boundary } = await vite.ssrLoadModule('/src/lib/api/index.js').then((m) => ({ buildHttpApi: () => m.api })));
  const { supabase } = await vite.ssrLoadModule('/src/lib/supabase.js');
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'local-session' } } });
});
beforeEach(() => {
  db.mallConnections = {}; db.mallDefaults = {}; db.mallSalesDefaults = null;
  db.mallRegistrations = []; db.mallRegistrationBatches = {};
  db.detailPublications = [{ publicationId: 'publication-local', projectId: 'project-local', status: 'ready', editorRevision: 4, files: [{ index: 0 }] }];
});
after(() => vite?.close());
const draft = (patch = {}) => ({ projectId: 'project-local', publicationId: 'publication-local', editorRevision: 4,
  selectedMalls: ['cafe24', 'zigzag'], productName: '니트', basePrice: 39000, defaultQuantity: 2, sizes: ['FREE'], mainAssetId: 'asset-local', ...patch });
const names = ['getSalesDefaults', 'saveSalesDefaults', 'getMallDefaults', 'saveMallDefaults', 'getMallRegistrationCatalog',
  'preflightMallRegistration', 'createMallRegistrationBatch', 'getMallRegistrationBatch', 'getMallRegistration', 'verifyMallRegistration', 'resumeMallRegistration'];

test('services use one batch contract and preserve request identity', async (t) => {
  const calls = [], request = { batchId: randomUUID(), draft: draft() }, key = randomUUID();
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push([String(url), options]); return new Response('{}'); });
  for (const name of names) assert.equal(typeof http[name], 'function', name);
  await http.getSalesDefaults(); await http.saveSalesDefaults({ baseRevision: 0, shippingDays: 3 });
  await http.getMallDefaults('zigzag'); await http.saveMallDefaults('zigzag', { baseRevision: 0, addresses: { returnAddressId: 'a1' } });
  await http.getMallRegistrationCatalog('cafe24'); await http.preflightMallRegistration(request.draft);
  await http.createMallRegistrationBatch(request); await http.createMallRegistrationBatch(request);
  await http.getMallRegistrationBatch(request.batchId); await http.getMallRegistration('job-one');
  await http.verifyMallRegistration('job-one'); await http.resumeMallRegistration('job-one', { requestKey: key });
  await http.listMallRegistrations('project-local');
  assert.deepEqual(calls.map(([url, options]) => [url, options.method]), [
    ['/v1/malls/sales-defaults', 'GET'], ['/v1/malls/sales-defaults', 'PUT'],
    ['/v1/malls/zigzag/registration-defaults', 'GET'], ['/v1/malls/zigzag/registration-defaults', 'PUT'],
    ['/v1/malls/cafe24/registration-catalog', 'GET'], ['/v1/malls/registrations/preflight', 'POST'],
    ['/v1/malls/registration-batches', 'POST'], ['/v1/malls/registration-batches', 'POST'],
    [`/v1/malls/registration-batches/${request.batchId}`, 'GET'], ['/v1/malls/registrations/job-one', 'GET'],
    ['/v1/malls/registrations/job-one/verify', 'POST'], ['/v1/malls/registrations/job-one/resume', 'POST'],
    ['/v1/projects/project-local/mall-registrations', 'GET'],
  ]);
  assert.equal(calls[6][1].body, calls[7][1].body);
  assert.deepEqual(JSON.parse(calls[6][1].body), request);
  assert.deepEqual(JSON.parse(calls[11][1].body), { requestKey: key });
  for (const name of ['saveZigzagDefaults', 'listCafe24Categories', 'createCafe24Registration', 'getCafe24Registration', 'verifyCafe24Registration']) {
    assert.equal(name in http, false); assert.equal(name in mock, false);
  }
});

test('http mode never falls back to mock for missing registration functions', async (t) => {
  let fakeCalls = 0;
  for (const name of names) t.mock.method(mock, name, async () => { fakeCalls++; return { fake: true }; });
  t.mock.method(console, 'error', () => {});
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { code: 'not_found', message: '없어요' } }), { status: 404 }));
  const api = boundary();
  for (const [name, args] of [['getSalesDefaults', []], ['getMallRegistrationCatalog', ['cafe24']],
    ['createMallRegistrationBatch', [{ batchId: randomUUID(), draft: draft() }]], ['getMallRegistration', ['job-one']]]) {
    await assert.rejects(api[name](...args), (error) => error.status === 404);
  }
  assert.equal(fakeCalls, 0); assert.equal(db.mallRegistrations.length, 0);
  const root = new URL('../..', import.meta.url).pathname;
  const missing = await createServer({ configFile: false, root, logLevel: 'silent',
    define: { 'import.meta.env.VITE_API_MODE': '"http"' }, resolve: { alias: { '@': `${root}/src` } },
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    plugins: [{ name: 'missing-registration-services', enforce: 'pre',
      load(id) { if (id.endsWith('/src/lib/api/httpAdapter.js')) return 'export const httpAdapter = {}; export function http() { throw new Error("no http service"); }'; },
    }],
  });
  try {
    const { api: incomplete } = await missing.ssrLoadModule('/src/lib/api/index.js');
    for (const name of names) assert.throws(() => incomplete[name](), /http 모드에서 아직 구현되지 않았어요/);
  } finally { await missing.close(); }
});

test('mock reproduces blocked mall and mixed result contracts', async () => {
  const empty = await mock.getSalesDefaults();
  assert.deepEqual(empty, { revision: 0, shipping: {}, returns: {}, disclosure: {}, taxType: 'TAX', productOrigin: 'domestic', shippingDays: 2, listingMode: 'HIDDEN', missingItems: ['shipping', 'returns', 'disclosure'] });
  const saved = await mock.saveSalesDefaults({ baseRevision: 0, shipping: { feeType: 'FREE', jejuFee: 0, isolatedFee: 0 }, disclosure: { manufacturer: '니트' } });
  assert.equal(saved.revision, 1); assert.equal(saved.shipping.baseFee, 0);
  await assert.rejects(mock.saveSalesDefaults({ baseRevision: 0, shippingDays: 5 }), (e) => e.code === 'defaults_conflict' && e.status === 409);
  for (const patch of [{ shipping: { baseFee: true } }, { shipping: { baseFee: 2147483648 } }, { shippingDays: 0 }, { disclosure: { phone: null } }, { addresses: {} }]) {
    await assert.rejects(mock.saveSalesDefaults({ baseRevision: 1, ...patch }), (e) => e.status === 422);
  }
  await mock.startCafe24Install({ mallId: 'myshop' });
  const cafe = (await mock.listMalls()).items.find((i) => i.mall === 'cafe24');
  assert.deepEqual(cafe.registration, { status: 'available' }); assert.ok(cafe.connectionVersion); assert.equal(cafe.addressesRevision, 0);
  const catalog = await mock.getMallRegistrationCatalog('cafe24');
  assert.equal(catalog.complete, true); assert.equal(catalog.capabilities.shippingSource, 'mall');
  assert.ok(catalog.categories[0].categoryId); assert.equal('categoryNo' in catalog.categories[0], false);
  assert.equal((await mock.getMallRegistrationCatalog('zigzag')).status, 'preparing');
  const blocked = await mock.preflightMallRegistration(draft({ publicationId: null }));
  assert.deepEqual(blocked.readyMalls, []); assert.equal(blocked.items[0].issues[0].code, 'publication_required');
  const preflight = await mock.preflightMallRegistration(draft());
  assert.deepEqual(preflight.readyMalls, ['cafe24']); assert.deepEqual(preflight.blockedMalls, ['zigzag']);
  assert.equal(preflight.items[1].status, 'preparing'); assert.ok(preflight.items[0].sourceVersions);
  const confirmed = draft({ confirmedSourcesByMall: { cafe24: preflight.items[0].sourceVersions } });
  const request = { batchId: randomUUID(), draft: confirmed };
  const receipt = await mock.createMallRegistrationBatch(request);
  assert.equal(receipt.readyCount, 1); assert.equal(receipt.items[1].status, 'blocked');
  assert.deepEqual(await mock.createMallRegistrationBatch(request), receipt);
  await assert.rejects(mock.createMallRegistrationBatch({ ...request, draft: { ...confirmed, basePrice: 40000 } }), (e) => e.code === 'idempotency_key_reused');
  const jobId = receipt.items[0].jobId;
  await new Promise((resolve) => setTimeout(resolve, 5));
  const view = await mock.getMallRegistration(jobId);
  assert.equal(view.mall, 'cafe24'); assert.equal(view.productId != null, true); assert.equal(view.setupStatus, 'complete');
  assert.equal(view.retryAction, 'none'); assert.equal('productNo' in view, false);
  assert.deepEqual((await mock.getMallRegistrationBatch(request.batchId)).items[0].registration, view);
  assert.deepEqual((await mock.listMallRegistrations('project-local')).recoveries, { cafe24: [], zigzag: [] });
});
