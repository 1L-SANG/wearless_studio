import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
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
  ({ api: mock } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());
const keys = { accessKey: 'local-access-12345678', secretKey: 'local-secret-abcdefgh' };

test('listMalls maps 404 to disabled', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('{}', { status: 404 }));
  assert.deepEqual(await adapter.listMalls(), { items: [], disabled: true });
});

test('mall http adapter sends exact route contracts without logging keys', async (t) => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response(options.method === 'DELETE' ? null : '{}', { status: options.method === 'DELETE' ? 204 : 200 });
  });
  await adapter.connectZigzag(keys);
  await adapter.rotateZigzag(keys);
  await adapter.disconnectZigzag();
  await adapter.listZigzagAddresses();
  await adapter.saveMallDefaults('zigzag', { baseRevision: 0, taxType: 'FREE' });
  assert.deepEqual(calls.map(({ url, options }) => [url, options.method]), [
    ['/v1/malls/zigzag/connect', 'POST'], ['/v1/malls/zigzag/rotate', 'POST'],
    ['/v1/malls/zigzag', 'DELETE'], ['/v1/malls/zigzag/addresses', 'GET'], ['/v1/malls/zigzag/registration-defaults', 'PUT'],
  ]);
  assert.deepEqual(JSON.parse(calls[0].options.body), keys);
});

test('mock zigzag defaults to unavailable', async () => {
  db.mallConnections = {};
  assert.equal((await mock.listMalls()).items.find((item) => item.mall === 'zigzag').status, 'unavailable');
});

test('mock mall connect succeeds and responses never contain keys', async () => {
  const result = await mock.connectZigzag(keys);
  assert.equal(result.shopName, '데일리 니트 스토어');
  assert.equal(result.keyHint, '12345678');
  assert.equal(result.status, 'connected');
  for (const value of [result, await mock.listMalls(), db.mallConnections]) {
    assert.ok(Object.values(keys).every((key) => !JSON.stringify(value).includes(key)));
  }
});

test('mock never exposes complete credentials as a key hint', async () => {
  for (const credentials of [{ accessKey: 'short123', secretKey: 'local-fixture-secret' },
    { accessKey: 'local-fixture-tail1234', secretKey: 'tail1234' }]) {
    assert.equal((await mock.connectZigzag(credentials)).keyHint, null);
    assert.equal((await mock.listMalls()).items[0].keyHint, null);
  }
});

test('mock rejects bad and noperm keys while rotate keeps the connection', async () => {
  await mock.connectZigzag(keys);
  for (const [prefix, code] of [['bad', 'invalid_key'], ['noperm', 'missing_permission']]) {
    await assert.rejects(mock.rotateZigzag({ ...keys, accessKey: `${prefix}-fixture` }), (error) => error.code === code);
  }
  assert.equal((await mock.listMalls()).items[0].keyHint, '12345678');
});

test('mock disconnect preserves partial defaults for reconnect', async () => {
  db.mallDefaults = {}; await mock.connectZigzag(keys);
  await mock.saveMallDefaults('zigzag', { baseRevision: 1, returns: { partialFee: 3000 } });
  await mock.saveMallDefaults('zigzag', { baseRevision: 2, returns: { totalFee: 6000, exchangeFee: 6000 } });
  await mock.disconnectZigzag();
  assert.equal((await mock.listMalls()).items[0].status, 'unavailable');
  await mock.connectZigzag(keys);
  const saved = await mock.getMallDefaults('zigzag');
  assert.equal(saved.returns.partialFee, 3000);
  assert.ok(saved.missingItems.includes('shipping.feeType')); assert.ok(saved.missingItems.includes('disclosure.country')); assert.ok(!saved.missingItems.some((key) => key.startsWith('returns.')));
  assert.equal('defaults' in (await mock.listMalls()).items[0], false);
});

test('mock mall revisions reject conflicts and foreign addresses while preserving own sales', async () => {
  db.mallDefaults = {}; db.mallConnections = {};
  await mock.connectZigzag(keys);
  await mock.saveMallDefaults('zigzag', { baseRevision: 1, shippingDays: 3 });
  const first = await mock.saveMallDefaults('zigzag', { baseRevision: 2, addresses: { returnAddressId: 'mock-shipping' } });
  assert.equal(first.revision, 3);
  await assert.rejects(mock.saveMallDefaults('zigzag', { baseRevision: 2, addresses: {} }), (error) => error.code === 'defaults_conflict' && error.status === 409);
  await assert.rejects(mock.saveMallDefaults('zigzag', { baseRevision: 3, addresses: { returnAddressId: 'foreign-address' } }), (error) => error.code === 'address_not_found');
  await mock.disconnectZigzag();
  await mock.connectZigzag({ ...keys, accessKey: 'other-fixture-12345678' });
  const changed = await mock.getMallDefaults('zigzag');
  assert.deepEqual(changed.addresses, {}); assert.equal(changed.revision, 4); assert.equal(changed.shippingDays, 3);
});
