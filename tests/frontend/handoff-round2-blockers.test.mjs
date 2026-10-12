import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createDraftSlotSync } from '../../src/lib/draftSlot.js';

const tick = () => new Promise(setImmediate);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const snapshot = (name, images = []) => ({ product: { name, colors: [{ id: 'base', images }] }, localUpdatedAt: name });
const photo = { id: 'photo', src: 'blob:photo', type: 'image/jpeg' };
function client(t, adapter, values = []) {
  const storage = new Map(values);
  const sync = createDraftSlotSync({ adapter, storage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key),
  }, setTimer: () => null, clearTimer() {} });
  t.after(() => sync.dispose());
  sync.setUserId('alice');
  return sync;
}

for (const change of ['setUserId', 'resetIdentity']) {
  test(`R2-1 pending Alice PUT cannot run after ${change} and Bob login`, async (t) => {
    const response = deferred(), puts = [];
    const sync = client(t, { putDraftSlot(body, options) {
      puts.push({ body, options });
      return puts.length === 1 ? response.promise : Promise.resolve({ token: 'bob-token' });
    } });
    sync.queue(snapshot('alice-first')); const first = sync.flush(); await tick();
    sync.queue(snapshot('alice-second')); const second = sync.flush();
    if (change === 'resetIdentity') sync.resetIdentity();
    sync.setUserId('bob');
    response.resolve({ token: 'alice-token' }); await Promise.all([first, second]);
    assert.deepEqual(puts.map(({ body }) => body.payload.product.name), ['alice-first']);
    assert.equal(puts[0].options.expectedUserId, 'alice');
    assert.equal(sync.getToken(), null);
    sync.queue(snapshot('bob-new')); await sync.flush();
    assert.equal(puts.at(-1).options.expectedUserId, 'bob');
    assert.equal(puts.at(-1).body.payload.product.name, 'bob-new');
  });
}

test('R2-1 same-user reset cancels pending takeover ownership', async (t) => {
  const response = deferred();
  const sync = client(t, { takeoverDraftSlot: () => response.promise });
  const opening = sync.takeover(); sync.resetIdentity({ sameUser: true });
  response.resolve({ token: 'stale-token', payload: snapshot('old') });
  assert.equal(await opening, null);
  assert.equal(sync.getToken(), null);
});

// 실제 HTTP 함수와 슬롯 메서드를 실행하고 세션 및 fetch 경계만 대체한다.
function httpHarness() {
  const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const httpSource = source.slice(source.indexOf('export async function http('), source.indexOf('\n/* 톤 에디터')).replace('export ', '');
  const methodSource = source.slice(source.indexOf('  async putDraftSlot('), source.indexOf('  async discardDraftSlotPhoto('));
  const response = deferred(), requests = [];
  const bindings = { clientEvents: { getAccountGeneration: () => 0, bindGeneration: (value) => value }, supabase: { auth: { getSession: () => response.promise } },
    networkError: (code, message) => Object.assign(new Error(message), { code }),
    browserOrigin: () => 'test', isBrowserOffline: () => false, readDeviceToken: () => null,
    BASE_URL: '', DEVICE_HEADER: 'test', absolutizeAssetUrls: (value) => value,
    fetch: async (...args) => { requests.push(args); return new Response('{}'); }, console,
  };
  const adapter = new Function(...Object.keys(bindings), `${httpSource}; return { ${methodSource} };`)(...Object.values(bindings));
  return { adapter, response, requests };
}
for (const method of ['putDraftSlot', 'takeoverDraftSlot', 'deleteDraftSlot']) {
  test(`R2-1 HTTP ${method} never fetches with a different session user`, async () => {
    const h = httpHarness();
    const request = h.adapter[method](method === 'deleteDraftSlot' ? 'alice-token' : {}, { expectedUserId: 'alice' });
    const rejected = assert.rejects(request, { code: 'auth_user_changed' });
    h.response.resolve({ data: { session: { user: { id: 'bob' }, access_token: 'bob' } } });
    await rejected;
    assert.deepEqual(h.requests, []);
  });
}

test('R2-5 HTTP session lookup cannot send an old PUT after pause and resume', async (t) => {
  const h = httpHarness(), sync = client(t, h.adapter);
  sync.queue(snapshot('before-check')); const writing = sync.flush();
  const rejected = assert.rejects(writing, { code: 'draft_request_cancelled' }); await tick();
  sync.pauseEntryWrites(); sync.resumeEntryWrites();
  h.response.resolve({ data: { session: { user: { id: 'alice' }, access_token: 'alice' } } });
  await rejected;
  assert.deepEqual(h.requests, []);
});

test('R2-3 a photo excluded by the HTTP account check was never sent and is discarded immediately', async (t) => {
  const h = httpHarness(), discarded = [];
  t.mock.method(globalThis, 'fetch', async () => new Response(new Blob(['photo'], { type: 'image/jpeg' })));
  const sync = client(t, { ...h.adapter,
    uploadDraftSlotPhoto: async () => ({ assetId: 'never-sent', url: '/photo.jpg' }),
    discardDraftSlotPhoto: async (id) => discarded.push(id),
  });
  sync.queue(snapshot('phone', [photo])); for (let i = 0; i < 5; i++) await tick();
  const writing = sync.flush(); const rejected = assert.rejects(writing, { code: 'auth_user_changed' }); await tick();
  sync.queue(snapshot('deleted'));
  h.response.resolve({ data: { session: { user: { id: 'bob' }, access_token: 'bob' } } });
  await rejected; await tick();
  assert.deepEqual(h.requests, []);
  assert.deepEqual(discarded, ['never-sent']);
});

for (const error of [new Error('response lost'), Object.assign(new Error('timeout'), { name: 'TimeoutError' }), Object.assign(new Error('aborted'), { name: 'AbortError' })]) {
  test(`R2-3 sent photo survives unknown PUT result: ${error.name}/${error.message}`, async (t) => {
    const response = deferred(), discarded = [], puts = [];
    t.mock.method(globalThis, 'fetch', async () => new Response(new Blob(['photo'], { type: 'image/jpeg' })));
    const sync = client(t, {
      uploadDraftSlotPhoto: async () => ({ assetId: 'possibly-saved', url: '/photo.jpg' }),
      putDraftSlot(body) { puts.push(body); return response.promise; },
      discardDraftSlotPhoto: async (id) => discarded.push(id),
    });
    sync.queue(snapshot('phone', [photo])); for (let i = 0; i < 5; i++) await tick();
    const writing = sync.flush(); const rejected = assert.rejects(writing, error); await tick();
    assert.equal(puts[0].payload.product.colors[0].images[0].id, 'possibly-saved');
    sync.queue(snapshot('deleted')); response.reject(error); await rejected; await tick();
    assert.deepEqual(discarded, []);
  });
}

test('R2-5 queued promise-chain write cannot run after entry pause is resumed', async (t) => {
  const response = deferred(), puts = [];
  const sync = client(t, { putDraftSlot(body) {
    puts.push(body.payload.product.name);
    return puts.length === 1 ? response.promise : Promise.resolve({ token: 'owned' });
  } }, [['wl_draftSlotToken', 'owned']]);
  sync.queue(snapshot('first')); const first = sync.flush(); await tick();
  sync.queue(snapshot('stale-second')); const second = sync.flush();
  sync.pauseEntryWrites(); sync.queue(snapshot('during-check')); sync.resumeEntryWrites();
  response.resolve({ token: 'owned' }); await Promise.all([first, second]);
  assert.deepEqual(puts, ['first']);
  sync.queue(snapshot('fresh-choice')); await sync.flush();
  assert.deepEqual(puts, ['first', 'fresh-choice']);
});
