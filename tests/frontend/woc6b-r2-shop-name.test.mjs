import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { QueryClient } from '@tanstack/react-query';

let vite, selectName, queryOptions;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ selectFrameShopName: selectName } = await vite.ssrLoadModule('/src/lib/frameShopName.js').catch(() => ({})));
  ({ storyboardShopQueryOptions: queryOptions } = await vite.ssrLoadModule('/src/features/storyboard/storyboardShopName.js').catch(() => ({})));
});
after(() => vite?.close());
const connection = (mall, shopName, status = 'connected') => ({ mall, shopName, status });

test('R2 shop name comes only from connected malls with deterministic storefront priority', () => {
  assert.equal(typeof selectName, 'function');
  const items = [connection('zigzag', '지그재그몰'), connection('cafe24', '  자사몰  ')];
  assert.equal(selectName({ items }), '자사몰');
  assert.equal(selectName({ items: [...items].reverse() }), '자사몰');
  assert.equal(selectName({ items: [connection('cafe24', ' '), connection('zigzag', '마켓몰')] }), '마켓몰');
  for (const status of ['revoked', 'reconnect_required', 'none', 'unavailable']) {
    assert.equal(selectName({ items: [connection('cafe24', '실명처럼 보이는 값', status), connection('zigzag', '마켓몰')] }), '마켓몰');
  }
  for (const value of [null, undefined, {}, { name: '회원 실명', display_name: '회원 실명' },
    { items: [connection('cafe24', 123)] }, { items: [connection('cafe24', null)] }, { items: [] }]) assert.equal(selectName(value), null);
});

test('R2 storyboard mall query shares account cache and only enables fetching for a selected frame', async () => {
  assert.equal(typeof queryOptions, 'function');
  const { api } = await vite.ssrLoadModule('/src/lib/api/index.js');
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  DB.account.name = '회원 실명'; DB.mallConnections = { cafe24: connection('cafe24', '데일리 몰') };
  const client = new QueryClient(), key = ['mall-connections', 'owner-a'];
  try {
    const disabled = queryOptions('owner-a', false);
    assert.equal(disabled.enabled, false); assert.equal(queryOptions(null, true).enabled, false);
    assert.deepEqual(disabled.queryKey, key);
    const cached = { items: [connection('cafe24', '이미 읽은 몰')] };
    client.setQueryData(key, cached);
    const original = api.listMalls;
    let calls = 0;
    api.listMalls = async (...args) => { calls++; return original(...args); };
    try {
      const data = await client.fetchQuery(queryOptions('owner-a', true));
      assert.deepEqual(data, cached); assert.equal(calls, 0, 'fresh existing settings/header cache reused');
      client.removeQueries({ queryKey: key });
      const loaded = await client.fetchQuery(queryOptions('owner-a', true));
      assert.equal(selectName(loaded), '데일리 몰'); assert.equal(calls, 1);
    } finally { api.listMalls = original; }
  } finally { client.clear(); }
});
