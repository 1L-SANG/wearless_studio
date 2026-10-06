import test from 'node:test';
import assert from 'node:assert/strict';
import { checkDraftEntry, canAutoOpenInput } from '../../src/lib/draftEntryCheck.js';
import { createDraftEntryActions } from '../../src/lib/draftEntry.js';
import { createDraftSlotSync } from '../../src/lib/draftSlot.js';

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = () => new Promise(setImmediate);
const slot = (meta = {}) => ({ meta: { hasContent: true, updatedAt: 'v1', deviceLabel: 'iPhone Safari', photoCount: 2, ...meta }, payload: { product: { name: '상품', colors: [] } } });
const snapshot = (...images) => ({ product: { name: '상품', colors: [{ id: 'base', images }] }, localUpdatedAt: 'local' });
const photo = (id) => ({ id, src: `blob:${id}`, type: 'image/jpeg' });
const storage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};
function entryHarness() {
  const calls = [], errors = [];
  let visible = slot(), userId = 'alice', identityEpoch = 0, dialog, staged;
  const client = {
    async get() { calls.push('get'); return visible; },
    async takeover() { calls.push('takeover'); return visible; },
    async removeForNewFlow() { calls.push('remove'); },
    resetIdentity({ sameUser } = {}) { if (!sameUser) identityEpoch += 1; staged = null; },
    getIdentityEpoch: () => identityEpoch,
    getServerSyncedAt: () => null, hasPendingRemoval: () => false,
    stage(value) { calls.push('stage'); staged = value; },
  };
  const options = { slotClient: client, getUserId: () => userId, mobile: () => true,
    async beginProject() { calls.push('begin'); }, onChange: (next) => { dialog = next; },
    onError: (error) => errors.push(error), onOpened: (meta) => calls.push(['opened', meta]),
    onStarted: () => calls.push('started'), onKept: (meta) => calls.push(['kept', meta]) };
  const actions = createDraftEntryActions(options);
  return { client, actions, options, calls, errors, setSlot: (next) => { visible = next; },
    changeUser: () => { userId = 'bob'; }, reset: () => client.resetIdentity(),
    get dialog() { return dialog; }, get staged() { return staged; } };
}

test('F1 local-only choice rechecks and protects a newly arrived phone product', async () => {
  const h = entryHarness();
  await h.actions.openLocal({ draft: snapshot(photo('local')) });
  assert.equal(h.dialog.kind, 'confirm');
  assert.deepEqual(h.calls, ['get']);
  assert.equal(h.staged, undefined);
});

test('F2 D1 keep uses fresh PC writer and never takes over', async () => {
  const h = entryHarness();
  h.actions.showMobile(slot().meta, { entry: true });
  h.setSlot(slot({ deviceLabel: 'Mac Chrome', updatedAt: 'v2' }));
  await h.actions.keepMobile();
  assert.deepEqual(h.calls.map((call) => Array.isArray(call) ? call[0] : call), ['get', 'kept']);
  assert.equal(h.calls[1][1].updatedAt, 'v2');
  assert.equal(h.staged, undefined);
});

test('F2 D1 keep uses fresh phone writer and opens saved B', async () => {
  const h = entryHarness();
  h.actions.showMobile(slot({ deviceLabel: 'Mac Chrome' }).meta, { entry: true });
  h.setSlot(slot({ updatedAt: 'v2' }));
  await h.actions.keepMobile();
  assert.equal(h.calls.at(-1)[0], 'opened');
  assert.equal(h.calls.at(-1)[1].updatedAt, 'v2');
});

test('F2 gone D1 keep reports the existing cleaned-up path without staging', async () => {
  const h = entryHarness(); h.actions.showMobile(slot().meta, { entry: true }); h.setSlot(null);
  await h.actions.keepMobile();
  assert.deepEqual(h.calls, ['get']);
  assert.deepEqual(h.errors, ['저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.']);
});

for (const change of ['user', 'epoch']) {
  for (const action of ['remote', 'local', 'keep', 'discard', 'next']) {
    test(`F3 ${action} drops a fresh-read response after ${change} changes`, async () => {
      const h = entryHarness(), response = deferred();
      h.client.get = () => { h.calls.push('get'); return response.promise; };
      if (action === 'keep' || action === 'discard') h.actions.showMobile(slot().meta, { entry: true });
      const running = action === 'remote' ? h.actions.openRemote(slot())
        : action === 'local' ? h.actions.openLocal({ draft: snapshot(photo('local')) })
          : action === 'keep' ? h.actions.keepMobile() : action === 'discard' ? h.actions.confirm() : h.actions.nextMobile();
      if (change === 'user') h.changeUser(); else h.reset();
      response.resolve(slot()); await running;
      assert.deepEqual(h.calls, ['get']);
      assert.equal(h.staged, change === 'epoch' ? null : undefined);
      assert.deepEqual(h.errors, []);
    });
  }
  for (const point of ['takeover', 'begin', 'remove']) {
    test(`F3 late ${point} response cannot stage or open after ${change} changes`, async () => {
      const h = entryHarness(), response = deferred();
      h.client[point === 'remove' ? 'removeForNewFlow' : 'takeover'] = () => { h.calls.push(point); return response.promise; };
      if (point === 'begin') {
        h.client.takeover = async () => slot();
        h.options.beginProject = () => response.promise;
      }
      const actions = createDraftEntryActions(h.options);
      if (point === 'remove') actions.showMobile(slot().meta, { entry: true });
      const running = point === 'remove' ? actions.confirm() : actions.openRemote();
      await tick();
      if (change === 'user') h.changeUser(); else h.reset();
      response.resolve(slot()); await running;
      assert.ok(!h.calls.some((call) => call === 'stage' || call === 'started' || Array.isArray(call)));
      assert.deepEqual(h.errors, []);
    });
  }
}

test('F3 resetIdentity clears staged payload and a delayed service takeover token', async (t) => {
  const response = deferred();
  const client = createDraftSlotSync({ storage: storage(), adapter: { takeoverDraftSlot: () => response.promise } });
  t.after(() => client.dispose());
  client.stage(slot());
  const opening = client.takeover();
  client.resetIdentity();
  response.resolve({ ...slot(), token: 'alice-token' });
  assert.equal(await opening, null);
  assert.equal(client.hasStaged(), false);
  assert.equal(client.getToken(), null);
});

test('F3 cancellation while busy invalidates a late response', async () => {
  const h = entryHarness(), response = deferred();
  h.client.takeover = () => response.promise;
  const opening = h.actions.openRemote(); await tick(); h.actions.cancel();
  response.resolve(slot()); await opening;
  assert.equal(h.staged, undefined);
  assert.deepEqual(h.errors, []);
});

function syncHarness(t, overrides = {}) {
  const puts = [], discarded = [], uploaded = [];
  const client = createDraftSlotSync({ storage: storage(), setTimer: () => null, clearTimer() {}, adapter: {
    async uploadDraftSlotPhoto(body) { uploaded.push(body); return { assetId: `new-${uploaded.length}`, url: `https://img.test/new-${uploaded.length}.jpg` }; },
    async putDraftSlot(body) { puts.push(body); return { token: 'owned', meta: { updatedAt: 'v1' } }; },
    async deleteDraftSlot() {}, async discardDraftSlotPhoto(id) { discarded.push(id); }, ...overrides,
  } });
  t.mock.method(globalThis, 'fetch', async (src) => new Response(new Blob(['photo'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } }));
  t.after(() => client.dispose());
  return { client, puts, discarded, uploaded };
}

test('F4 gone recovery fetches asset bytes and uploads every recoverable remote as a new photo', async (t) => {
  const h = syncHarness(t), requests = [];
  t.mock.method(globalThis, 'fetch', async (src) => {
    requests.push(src);
    return new Response(new Blob(['photo'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } });
  });
  const src = '/v1/assets/11111111-1111-4111-8111-111111111111/file';
  const recovered = await h.client.recoverGonePhotos(snapshot({ id: 'remote', src }));
  const saving = h.client.saveAll(recovered); for (let i = 0; i < 5; i++) { await tick(); await h.client.flush(); }
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
  assert.equal(requests[0], src.replace('/file', '/bytes'));
  assert.equal(h.uploaded.length, 1);
  assert.equal(h.puts.at(-1).payload.product.colors[0].images[0].id, 'new-1');
  URL.revokeObjectURL(recovered.product.colors[0].images[0].src);
});

test('F4 only unfetchable remote bytes are removed and counted, local bytes survive', async (t) => {
  const h = syncHarness(t);
  t.mock.method(globalThis, 'fetch', async (src) => src === '/missing.jpg' ? new Response('', { status: 404 })
    : new Response(new Blob(['photo'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } }));
  const recovered = await h.client.recoverGonePhotos(snapshot(photo('local'), { id: 'lost', src: '/missing.jpg' }));
  const saving = h.client.saveAll(recovered); for (let i = 0; i < 5; i++) { await tick(); await h.client.flush(); }
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1, missingPhotoCount: 1 });
  assert.equal(recovered.product.colors[0].images[0].id, 'local');
});

for (const path of ['remove', 'photo-delete']) {
  test(`F5 ${path} retains assets included in a successful PUT`, async (t) => {
    const h = syncHarness(t);
    const saving = h.client.saveAll(snapshot(photo('saved'))); for (let i = 0; i < 5; i++) { await tick(); await h.client.flush(); } await saving;
    if (path === 'remove') await h.client.remove(); else { h.client.queue(snapshot()); await h.client.flush(); }
    assert.deepEqual(h.discarded, []);
  });
}

test('F5 never-synced upload is discarded immediately on removal', async (t) => {
  const h = syncHarness(t);
  h.client.queue(snapshot(photo('unsaved'))); await tick();
  await h.client.remove();
  assert.deepEqual(h.discarded, ['new-1']);
});

test('F5 removing an image during an in-flight successful PUT retains it', async (t) => {
  const response = deferred();
  const h = syncHarness(t, { putDraftSlot: () => response.promise });
  h.client.queue(snapshot(photo('pending'))); for (let i = 0; i < 5; i++) await tick();
  const writing = h.client.flush(); await tick();
  h.client.queue(snapshot());
  assert.deepEqual(h.discarded, []);
  response.resolve({ token: 'owned' }); await writing; await tick();
  assert.deepEqual(h.discarded, []);
});

test('F8 each attempt times out at 8s, silently retries after 1s then 2s', async () => {
  const timers = [], cleared = [], calls = [];
  const checking = checkDraftEntry({ get() { calls.push('get'); return new Promise(() => {}); } }, {
    setTimer(fn, ms) { const timer = { fn, ms }; timers.push(timer); return timer; }, clearTimer: (id) => cleared.push(id),
  });
  const failed = assert.rejects(checking, /entry_timeout/);
  assert.equal(timers[0].ms, 8000); timers.shift().fn(); await tick();
  assert.equal(timers[0].ms, 1000); timers.shift().fn(); await tick();
  assert.equal(timers[0].ms, 8000); timers.shift().fn(); await tick();
  assert.equal(timers[0].ms, 2000); timers.shift().fn(); await tick();
  assert.equal(timers[0].ms, 8000); timers.shift().fn(); await failed;
  assert.deepEqual(calls, ['get', 'get', 'get']);
  assert.equal(cleared.length, 3);
});

test('F8 silent retry can recover without showing failure and cancels its timeout', async () => {
  const delays = [], cleared = [];
  let attempts = 0;
  const result = await checkDraftEntry({ async get() { if (++attempts < 3) throw new Error('offline'); return slot(); } }, {
    setTimer(fn, ms) { delays.push(ms); if (ms !== 8000) setImmediate(fn); return ms; }, clearTimer: (id) => cleared.push(id),
  });
  assert.equal(result.meta.updatedAt, 'v1');
  assert.deepEqual(delays, [8000, 1000, 8000, 2000, 8000]);
  assert.deepEqual(cleared, [8000, 8000, 8000]);
});

test('own candidate policy requires current-user server time and exactly one candidate', () => {
  const client = { hasOtherTabOwner: () => false, getSyncedUserId: () => 'seller', getServerSyncedAt: () => 'v1' };
  const source = { id: 'remote', meta: slot().meta };
  const options = { userId: 'seller', flow: {}, slotClient: client };
  assert.equal(canAutoOpenInput([source], options), true);
  for (const sources of [[{ ...source, photosPending: true }], [source, { id: 'flow' }], [{ ...source, meta: slot({ updatedAt: 'other-device' }).meta }]]) {
    assert.equal(canAutoOpenInput(sources, options), false);
  }
  client.hasOtherTabOwner = () => true;
  assert.equal(canAutoOpenInput([source], options), false);
  client.hasOtherTabOwner = () => false; client.getSyncedUserId = () => null;
  assert.equal(canAutoOpenInput([source], options), false);
});

for (const flow of [{}, { userId: 'other', lastUsedAt: 1000 }, { userId: 'seller', lastUsedAt: 1000 }, { userId: 'seller', lastUsedAt: 86401000 }]) {
  test(`flow auto-open checks user and 24h freshness: ${JSON.stringify(flow)}`, () => {
    const result = canAutoOpenInput([{ id: 'flow' }], { userId: 'seller', flow, now: 86401000, slotClient: { hasOtherTabOwner: () => false } });
    assert.equal(result, flow.lastUsedAt === 86401000);
  });
}

for (const reason of ['other-tab', 'changed-writer']) {
  test(`automatic open rechecks ${reason} immediately before takeover`, async () => {
    const h = entryHarness();
    h.client.getSyncedUserId = () => 'alice';
    h.client.getServerSyncedAt = () => 'v1';
    h.client.hasOtherTabOwner = () => reason === 'other-tab';
    if (reason === 'changed-writer') h.setSlot(slot({ updatedAt: 'v2' }));
    await h.actions.openRemote(slot(), { automatic: true });
    assert.deepEqual(h.calls, ['get']);
    assert.equal(h.staged, undefined);
  });
}

test('F3 service removeForNewFlow drops a delayed takeover across identity reset', async (t) => {
  const response = deferred(), deleted = [];
  const client = createDraftSlotSync({ storage: storage(), adapter: {
    takeoverDraftSlot: () => response.promise, deleteDraftSlot: async (token) => deleted.push(token),
  } });
  t.after(() => client.dispose());
  const removing = client.removeForNewFlow(); client.resetIdentity();
  response.resolve({ ...slot(), token: 'alice-token' });
  assert.equal(await removing, false);
  assert.deepEqual(deleted, []);
});

test('automatic takeover never claims a tab owner that appeared while its response was pending', async (t) => {
  const response = deferred(), browserStorage = storage(), calls = [];
  browserStorage.setItem('wl_draftSlotServerSyncedAt', 'v1');
  browserStorage.setItem('wl_draftSlotSyncedUser', 'alice');
  const client = createDraftSlotSync({ storage: browserStorage, documentId: 'entry-tab', adapter: {
    getDraftSlot: async () => slot(), takeoverDraftSlot: () => response.promise,
  } });
  client.setUserId('alice'); t.after(() => client.dispose());
  const actions = createDraftEntryActions({ slotClient: client, getUserId: () => 'alice',
    beginProject: async () => calls.push('begin'), onOpened: () => calls.push('opened'),
    onStarted() {}, onChange() {}, onError: (error) => calls.push(error),
  });
  const opening = actions.openRemote(slot(), { automatic: true }); await tick();
  browserStorage.setItem('wl_draftSlotOwnerTab', 'live-other-tab');
  response.resolve({ ...slot(), token: 'late-token' }); await opening;
  assert.equal(client.getToken(), null);
  assert.equal(client.hasStaged(), false);
  assert.equal(browserStorage.getItem('wl_draftSlotOwnerTab'), 'live-other-tab');
  assert.deepEqual(calls, []);
});

test('F8 unchecked upload clears delayed removal while preserving another tab token and owner', (t) => {
  const browserStorage = storage();
  const client = createDraftSlotSync({ storage: browserStorage, documentId: 'entry-tab' });
  t.after(() => client.dispose());
  browserStorage.setItem('wl_draftSlotToken', 'other-tab-token');
  browserStorage.setItem('wl_draftSlotOwnerTab', 'other-tab');
  browserStorage.setItem('wl_draftSlotPendingRemove', 'old-decision');
  assert.equal(client.continueWithoutSlotCheck(), true);
  assert.equal(client.hasPendingRemoval(), false);
  assert.equal(browserStorage.getItem('wl_draftSlotToken'), 'other-tab-token');
  assert.equal(browserStorage.getItem('wl_draftSlotOwnerTab'), 'other-tab');
});

test('F8 unchecked upload remains blocked with a held token', (t) => {
  const browserStorage = storage(); browserStorage.setItem('wl_draftSlotToken', 'owned');
  const client = createDraftSlotSync({ storage: browserStorage }); t.after(() => client.dispose());
  assert.equal(client.continueWithoutSlotCheck(), false);
  assert.equal(client.getToken(), 'owned');
});
