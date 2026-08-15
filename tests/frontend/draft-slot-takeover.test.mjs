import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  createDraftSlotSync,
  getDraftSlotDeviceLabel,
} from '../../src/lib/draftSlot.js';
import { createDraftSlotMemory } from '../../src/mock/draftSlotMemory.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const product = (name) => ({
  name,
  colors: [{ id: 'base', isBase: true, images: [{ id: `${name}-front`, slot: 'Front', src: 'https://img.test/front.jpg' }] }],
});
const tokenMismatch = (meta) => {
  const error = new Error(meta ? 'taken' : 'expired');
  error.status = 409;
  error.code = 'token_mismatch';
  error.meta = meta;
  return error;
};

async function waitFor(check, timeoutMs = 1500) {
  const startedAt = Date.now();
  while (!check()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitFor timeout');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

function fakeTimers() {
  let nextId = 1;
  const timers = new Map();
  return {
    setTimer(fn, ms) {
      const id = nextId++;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimer(id) { timers.delete(id); },
    runLatest() {
      const [id, timer] = [...timers.entries()].at(-1);
      timers.delete(id);
      timer.fn();
      return timer.ms;
    },
    count() { return timers.size; },
  };
}

test('slot PUT queue debounces to 500ms, keeps the latest snapshot, and serializes requests', async () => {
  const timers = fakeTimers();
  const calls = [];
  const releases = [];
  const sync = createDraftSlotSync({
    debounceMs: 500,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    storage: new MapStorage(),
    adapter: {
      putDraftSlot: (body) => {
        calls.push(body.payload.product.name);
        return new Promise((resolve) => releases.push(() => resolve({ token: 'owned' })));
      },
    },
  });

  sync.queue({ product: product('old'), localUpdatedAt: 'old' });
  sync.queue({ product: product('latest'), localUpdatedAt: 'latest' });
  assert.equal(timers.runLatest(), 500);
  await new Promise(setImmediate);
  assert.deepEqual(calls, ['latest']);

  sync.queue({ product: product('after'), localUpdatedAt: 'after' });
  timers.runLatest();
  await new Promise(setImmediate);
  assert.deepEqual(calls, ['latest']);
  releases.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(calls, ['latest', 'after']);
  releases.shift()();
  await sync.flush();
});

test('a 409 locks the client and takeover allows a local reclaim PUT', async () => {
  const storage = new MapStorage([['wl_draftSlotToken', 'old-token']]);
  const conflicts = [];
  let puts = 0;
  const sync = createDraftSlotSync({
    debounceMs: 0,
    storage,
    adapter: {
      async putDraftSlot() {
        puts += 1;
        if (puts === 1) {
          const error = new Error('taken');
          error.status = 409;
          error.code = 'token_mismatch';
          error.meta = { updatedAt: '2026-08-11T01:00:00Z', deviceLabel: 'iPhone Safari' };
          throw error;
        }
        return { token: 'new-token' };
      },
      async takeoverDraftSlot() {
        return { token: 'new-token', payload: { product: product('remote') }, meta: {} };
      },
    },
  });
  sync.onConflict((meta) => conflicts.push(meta));
  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(
    sync.flush(),
    (error) => error.status === 409 && error.code === 'token_mismatch',
  );
  assert.equal(sync.isLocked(), true);
  assert.equal(conflicts[0].deviceLabel, 'iPhone Safari');

  const takeover = await sync.takeover();
  assert.equal(takeover.payload.product.name, 'remote');
  assert.equal(sync.isLocked(), false);
  sync.queue({ product: product('local'), localUpdatedAt: 'local-2' });
  await sync.flush();
  assert.equal(puts, 2);
  assert.equal(storage.getItem('wl_draftSlotToken'), 'new-token');
});

test('a token rotation storage event locks this tab instead of borrowing another tab token', async () => {
  const storage = new EventedStorage([['wl_draftSlotToken', 'old-token']]);
  const tokens = [];
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot(body) {
        tokens.push(body.token);
        return { token: body.token, meta: { updatedAt: 'server-1' } };
      },
    },
  });
  sync.onConflict((meta) => conflicts.push(meta));

  storage.dispatchToken('rotated-token');
  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await sync.flush();

  assert.deepEqual(tokens, []);
  assert.equal(sync.getToken(), 'old-token');
  assert.equal(sync.isLocked(), true);
  assert.equal(conflicts[0].state, 'other-tab');
  sync.dispose();
});

test('a 409 never retries a stale snapshot under a newer stored token', async () => {
  const storage = new MapStorage([['wl_draftSlotToken', 'old-token']]);
  const tokens = [];
  const conflicts = [];
  const meta = { updatedAt: '2026-08-12T01:00:00Z', deviceLabel: 'Mac Chrome' };
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot(body) {
        tokens.push(body.token);
        throw tokenMismatch(meta);
      },
    },
  });
  sync.onConflict((conflict) => conflicts.push(conflict));
  storage.setItem('wl_draftSlotToken', 'rotated-token');

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(sync.flush(), (error) => error.status === 409);

  assert.deepEqual(tokens, ['old-token']);
  assert.equal(sync.getToken(), 'old-token');
  assert.equal(sync.isLocked(), true);
  assert.deepEqual(conflicts, [meta]);
});

test('a locked client does not send more snapshots before an explicit takeover', async () => {
  const storage = new MapStorage([['wl_draftSlotToken', 'old-token']]);
  const tokens = [];
  const conflicts = [];
  const meta = { updatedAt: '2026-08-12T02:00:00Z', deviceLabel: 'iPhone Safari' };
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot(body) {
        tokens.push(body.token);
        throw tokenMismatch(meta);
      },
    },
  });
  sync.onConflict((conflict) => conflicts.push(conflict));
  storage.setItem('wl_draftSlotToken', 'rotated-token');

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(
    sync.flush(),
    (error) => error.status === 409 && error.code === 'token_mismatch',
  );

  sync.queue({ product: product('newer-local'), localUpdatedAt: 'local-2' });
  await assert.rejects(sync.flush(), (error) => error.status === 409);

  assert.deepEqual(tokens, ['old-token']);
  assert.equal(sync.isLocked(), true);
  assert.deepEqual(conflicts, [meta]);
});

test('a meta-less 409 waits for explicit confirmation before recreating a deleted slot', async () => {
  let sequence = 0;
  const memory = createDraftSlotMemory({ tokenFactory: () => `token-${++sequence}` });
  const created = memory.put({ payload: { product: product('remote') } });
  memory.remove(created.token);
  const storage = new MapStorage([['wl_draftSlotToken', created.token]]);
  const tokens = [];
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot(body) {
        tokens.push(body.token);
        return memory.put(body);
      },
    },
  });
  sync.onConflict((conflict) => conflicts.push(conflict));

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(sync.flush(), (error) => error.status === 409 && error.meta == null);

  assert.deepEqual(tokens, [created.token]);
  assert.equal(sync.getToken(), null);
  assert.equal(storage.getItem('wl_draftSlotToken'), null);
  assert.equal(sync.isLocked(), true);
  assert.equal(conflicts[0].state, 'gone');

  assert.equal(sync.restartAfterGone(), true);
  sync.queue({ product: product('local'), localUpdatedAt: 'local-2' });
  await sync.flush();

  assert.deepEqual(tokens, [created.token, null]);
  assert.equal(sync.getToken(), 'token-2');
  assert.equal(storage.getItem('wl_draftSlotToken'), 'token-2');
  assert.equal(memory.get('token-2').holdsToken, true);
  assert.equal(memory.get('token-2', { full: true }).payload.product.name, 'local');
  assert.equal(sync.isLocked(), false);
  assert.deepEqual(conflicts, [{ state: 'gone' }, null]);
});

test('a token storage event never unlocks an already locked client', async () => {
  const storage = new EventedStorage([['wl_draftSlotToken', 'old-token']]);
  const meta = { updatedAt: '2026-08-12T03:00:00Z', deviceLabel: 'iPhone Safari' };
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot() { throw tokenMismatch(meta); },
    },
  });
  sync.onConflict((conflict) => conflicts.push(conflict));
  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(sync.flush(), (error) => error.status === 409);
  assert.equal(sync.isLocked(), true);

  storage.dispatchToken('rotated-token');

  assert.equal(sync.getToken(), 'old-token');
  assert.equal(sync.isLocked(), true);
  assert.deepEqual(conflicts, [meta, { state: 'other-tab', deviceLabel: '이 브라우저의 다른 탭' }]);
  sync.dispose();
});

test('a late PUT response cannot replace a token written by another tab', async () => {
  const storage = new EventedStorage([['wl_draftSlotToken', 'old-token']]);
  let releasePut;
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      putDraftSlot: () => new Promise((resolve) => { releasePut = resolve; }),
    },
  });

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  const flushing = sync.flush();
  await new Promise(setImmediate);
  storage.dispatchToken('new-tab-token');
  releasePut({ token: 'late-old-token', meta: { updatedAt: 'server-old' } });
  await flushing;

  assert.equal(storage.getItem('wl_draftSlotToken'), 'new-tab-token');
  assert.equal(sync.getToken(), 'old-token');
  assert.equal(sync.isLocked(), true);
  sync.dispose();
});

test('slot delete captures its token before another tab changes shared storage', async () => {
  const storage = new EventedStorage([['wl_draftSlotToken', 'old-token']]);
  let releasePut;
  const deleted = [];
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      putDraftSlot: () => new Promise((resolve) => { releasePut = resolve; }),
      async deleteDraftSlot(token) { deleted.push(token); },
    },
  });

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  const flushing = sync.flush();
  await new Promise(setImmediate);
  const removing = sync.remove();
  storage.dispatchToken('new-tab-token');
  releasePut({ token: 'old-token', meta: { updatedAt: 'server-old' } });
  await flushing;
  await removing;

  assert.deepEqual(deleted, ['old-token']);
  sync.dispose();
});

test('ownership check reports a deleted remote slot as an explicit gone state', async () => {
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage: new MapStorage([['wl_draftSlotToken', 'deleted-token']]),
    adapter: { async getDraftSlot() { return null; } },
  });
  sync.onConflict((meta) => conflicts.push(meta));

  assert.equal(await sync.checkOwnership(), null);
  assert.equal(sync.isLocked(), true);
  assert.equal(sync.getToken(), null);
  assert.deepEqual(conflicts, [{ state: 'gone' }]);
});

test('only one browser tab can claim the local writer role', async () => {
  const storage = new MapStorage();
  const first = createDraftSlotSync({ storage, documentId: 'tab-a' });
  const second = createDraftSlotSync({ storage, documentId: 'tab-b' });
  const conflicts = [];
  second.onConflict((meta) => conflicts.push(meta));

  assert.equal(first.activate(), true);
  assert.equal(second.activate(), false);
  assert.equal(second.isLocked(), true);
  // 화면 잠금 안내는 소유 탭이 살아있다고 응답(ping→alive)한 뒤에야 알린다
  await waitFor(() => conflicts.length > 0);
  assert.equal(conflicts[0].state, 'other-tab');

  first.dispose();
  second.dispose();
});

test('a probe timeout quietly reclaims a crashed tab owner without a lock screen', () => {
  const timers = fakeTimers();
  const storage = new MapStorage([['wl_draftSlotOwnerTab', 'dead-tab']]);
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage, documentId: 'tab-live', setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  sync.onConflict((meta) => conflicts.push(meta));

  assert.equal(sync.activate(), false);
  assert.equal(sync.isLocked(), true);                 // 확인하는 동안 슬롯 쓰기만 멈춘다
  assert.equal(conflicts.filter(Boolean).length, 0);   // 잠금 화면 안내는 아직 없다
  assert.equal(timers.runLatest(), 450);               // 응답 없음 — 죽은 탭의 잔재
  assert.equal(sync.isLocked(), false);
  assert.equal(storage.getItem('wl_draftSlotOwnerTab'), 'tab-live');
  assert.equal(conflicts.filter(Boolean).length, 0);
  sync.dispose();
});

test('an owner release event dissolves an other-tab lock without user action', () => {
  const storage = new EventedStorage([['wl_draftSlotOwnerTab', 'tab-a']]);
  const conflicts = [];
  const sync = createDraftSlotSync({ storage, documentId: 'tab-b' });
  sync.onConflict((meta) => conflicts.push(meta));

  storage.dispatch('wl_draftSlotOwnerTab', 'tab-a2');  // 다른 탭이 소유권을 새로 씀 → 잠금
  assert.equal(sync.isLocked(), true);
  storage.dispatch('wl_draftSlotOwnerTab', null);      // 그 탭이 정상 종료(pagehide)
  assert.equal(sync.isLocked(), false);
  assert.equal(storage.getItem('wl_draftSlotOwnerTab'), 'tab-b');
  sync.dispose();
});

test('storage events refresh both local and server sync timestamps', () => {
  const storage = new EventedStorage([
    ['wl_draftSlotSyncedAt', 'local-old'],
    ['wl_draftSlotServerSyncedAt', 'server-old'],
  ]);
  const sync = createDraftSlotSync({ storage });

  storage.dispatch('wl_draftSlotSyncedAt', 'local-new');
  storage.dispatch('wl_draftSlotServerSyncedAt', 'server-new');

  assert.equal(sync.getSyncedAt(), 'local-new');
  assert.equal(sync.getServerSyncedAt(), 'server-new');
  sync.dispose();
});

test('slot delete drains an in-flight PUT so the PUT cannot recreate the deleted slot', async () => {
  let releasePut;
  let deleted = false;
  const sync = createDraftSlotSync({
    debounceMs: 0,
    storage: new MapStorage(),
    adapter: {
      putDraftSlot: () => new Promise((resolve) => { releasePut = resolve; }),
      deleteDraftSlot: async () => { deleted = true; },
    },
  });
  sync.queue({ product: product('race'), localUpdatedAt: 'race' });
  const flushing = sync.flush();
  await new Promise(setImmediate);
  const removing = sync.remove();
  await new Promise(setImmediate);
  assert.equal(deleted, false);
  releasePut({ token: 'owned', meta: { updatedAt: 'server-1' } });
  await flushing;
  await removing;
  assert.equal(deleted, true);
});

test('an uploaded photo stays pending until the slot PUT retry is accepted', async () => {
  const timers = fakeTimers();
  const pendingStates = [];
  let puts = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ blob: async () => new Blob(['photo'], { type: 'image/jpeg' }) });
  try {
    const sync = createDraftSlotSync({
      debounceMs: 0,
      setTimer: timers.setTimer,
      clearTimer: timers.clearTimer,
      storage: new MapStorage(),
      adapter: {
        async uploadDraftSlotPhoto() {
          return { assetId: 'asset-1', url: 'https://img.test/asset-1.jpg' };
        },
        async putDraftSlot() {
          puts += 1;
          if (puts === 1) throw new Error('temporary network failure');
          return { token: 'owned', meta: { updatedAt: 'server-2' } };
        },
      },
    });
    sync.onPhotosPending((pending) => pendingStates.push(pending));
    sync.queue({
      product: {
        name: 'photo',
        colors: [{ id: 'base', images: [{ id: 'image-1', src: 'blob:photo', type: 'image/jpeg' }] }],
      },
      localUpdatedAt: 'local-1',
    });
    await new Promise(setImmediate);

    await assert.rejects(sync.flush(), /temporary network failure/);
    assert.equal(pendingStates.at(-1), true);
    assert.equal(timers.runLatest(), 2000);
    await new Promise(setImmediate);
    await sync.flush();

    assert.equal(puts, 2);
    assert.equal(pendingStates.at(-1), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a photo removed during upload is explicitly discarded after upload completes', async () => {
  let releaseUpload;
  const discarded = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ blob: async () => new Blob(['photo'], { type: 'image/jpeg' }) });
  try {
    const sync = createDraftSlotSync({
      debounceMs: 500,
      storage: new MapStorage(),
      adapter: {
        uploadDraftSlotPhoto: () => new Promise((resolve) => { releaseUpload = resolve; }),
        async discardDraftSlotPhoto(assetId) { discarded.push(assetId); },
        async putDraftSlot() { return { token: 'owned' }; },
      },
    });
    sync.queue({
      product: {
        name: 'photo',
        colors: [{ id: 'base', images: [{ id: 'image-1', src: 'blob:photo', type: 'image/jpeg' }] }],
      },
    });
    await new Promise(setImmediate);
    sync.queue({ product: { name: 'photo', colors: [{ id: 'base', images: [] }] } });
    releaseUpload({ assetId: 'orphan-1', url: 'https://img.test/orphan-1.jpg' });
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.deepEqual(discarded, ['orphan-1']);
    sync.discard();
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('mock slot supports create, mismatch, takeover invalidation, and delete', () => {
  let sequence = 0;
  const memory = createDraftSlotMemory({
    tokenFactory: () => `token-${++sequence}`,
    now: () => '2026-08-11T00:00:00Z',
  });
  const created = memory.put({ payload: { product: product('one') }, deviceLabel: 'Mac Chrome' });
  assert.equal(memory.get(created.token).holdsToken, true);
  assert.equal(memory.get(created.token).meta.photoCount, 1);
  memory.simulateConflict({ deviceLabel: 'iPhone Safari' });
  assert.throws(
    () => memory.put({ payload: { product: product('lost') }, token: created.token }),
    (error) => error.status === 409 && error.code === 'token_mismatch',
  );
  const takeover = memory.takeover();
  assert.notEqual(takeover.token, created.token);
  assert.equal(takeover.payload.product.name, 'one');
  memory.remove(takeover.token);
  assert.equal(memory.get(takeover.token), null);
});

test('two clients cannot update or delete with a token invalidated by takeover', () => {
  let sequence = 0;
  const memory = createDraftSlotMemory({ tokenFactory: () => `token-${++sequence}` });
  const first = memory.put({ payload: { product: product('first') } });
  const second = memory.takeover();

  assert.throws(
    () => memory.remove(first.token),
    (error) => error.status === 409 && error.code === 'token_mismatch',
  );
  assert.equal(memory.get(second.token).holdsToken, true);
  memory.remove(second.token);
  assert.throws(
    () => memory.put({ payload: { product: product('stale') }, token: first.token }),
    (error) => error.status === 409 && error.code === 'token_mismatch',
  );
  assert.equal(memory.get(second.token), null);
});

test('an explicit new flow takes ownership before deleting the remote slot', async () => {
  const calls = [];
  const sync = createDraftSlotSync({
    storage: new MapStorage([['wl_draftSlotToken', 'stale-token']]),
    adapter: {
      async takeoverDraftSlot() {
        calls.push('takeover');
        return { token: 'active-token', payload: { product: product('remote') }, meta: {} };
      },
      async deleteDraftSlot(token) { calls.push(`delete:${token}`); },
    },
  });

  await sync.removeForNewFlow();

  assert.deepEqual(calls, ['takeover', 'delete:active-token']);
});

test('an explicit new flow can discard after the previous PUT was rejected', async () => {
  const calls = [];
  const meta = { updatedAt: '2026-08-12T04:00:00Z', deviceLabel: 'iPhone Safari' };
  const sync = createDraftSlotSync({
    storage: new MapStorage([['wl_draftSlotToken', 'stale-token']]),
    adapter: {
      async putDraftSlot() { throw tokenMismatch(meta); },
      async takeoverDraftSlot() {
        calls.push('takeover');
        return { token: 'active-token', payload: { product: product('remote') }, meta };
      },
      async deleteDraftSlot(token) { calls.push(`delete:${token}`); },
    },
  });
  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await assert.rejects(sync.flush(), (error) => error.status === 409);

  await sync.removeForNewFlow();

  assert.deepEqual(calls, ['takeover', 'delete:active-token']);
});

test('a takeover with no server slot clears the stale token so the next PUT creates fresh', async () => {
  const tokens = [];
  const storage = new MapStorage([['wl_draftSlotToken', 'stale-token']]);
  const sync = createDraftSlotSync({
    debounceMs: 0,
    storage,
    adapter: {
      async takeoverDraftSlot() { return null; },   // 서버 204 — 슬롯 없음
      async putDraftSlot(body) {
        tokens.push(body.token);
        return { token: 'fresh', meta: { updatedAt: 'server-1' } };
      },
    },
  });

  assert.equal(await sync.takeover(), null);
  assert.equal(sync.getToken(), null);
  assert.equal(storage.getItem('wl_draftSlotToken'), null);

  sync.queue({ product: product('fresh'), localUpdatedAt: 'local-1' });
  await sync.flush();
  assert.deepEqual(tokens, [null]);   // 옛 토큰으로 409(gone 오발)를 만들지 않는다
  assert.equal(sync.getToken(), 'fresh');
});

test('a failed new-flow delete defers removal instead of blocking, then settles before the next PUT', async () => {
  const calls = [];
  const storage = new MapStorage([['wl_draftSlotToken', 'stale-token']]);
  let serverDown = true;
  const sync = createDraftSlotSync({
    debounceMs: 0,
    storage,
    adapter: {
      async takeoverDraftSlot() {
        if (serverDown) throw new Error('network down');
        calls.push('takeover');
        return { token: 'grabbed', payload: {}, meta: { updatedAt: '2026-08-14T00:00:00Z' } };
      },
      async getDraftSlot() {
        calls.push('get');
        return { meta: { updatedAt: '2026-08-01T00:00:00Z' } };
      },
      async deleteDraftSlot(token) { calls.push(`delete:${token}`); },
      async putDraftSlot(body) {
        calls.push(`put:${body.token}`);
        return { token: 'fresh', meta: { updatedAt: 'server-1' } };
      },
    },
  });

  assert.equal(await sync.removeForNewFlow(), false);   // 새로 시작을 막지 않는다
  assert.equal(sync.getToken(), null);
  assert.ok(storage.getItem('wl_draftSlotPendingRemove'));

  serverDown = false;
  sync.queue({ product: product('fresh'), localUpdatedAt: 'local-1' });
  await sync.flush();
  assert.deepEqual(calls, ['get', 'takeover', 'delete:grabbed', 'put:null']);
  assert.equal(storage.getItem('wl_draftSlotPendingRemove'), null);
});

test('a deferred removal is abandoned when another device rewrote the slot after the decision', async () => {
  const calls = [];
  const storage = new MapStorage([['wl_draftSlotPendingRemove', '2026-08-01T00:00:00Z']]);
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async getDraftSlot() { calls.push('get'); return { meta: { updatedAt: '2026-08-02T00:00:00Z' } }; },
      async takeoverDraftSlot() { calls.push('takeover'); return { token: 'grabbed' }; },
      async deleteDraftSlot(token) { calls.push(`delete:${token}`); },
    },
  });

  await sync.retryPendingRemoval();
  assert.deepEqual(calls, ['get']);   // 결정 이후의 새 작업을 지우지 않는다
  assert.equal(storage.getItem('wl_draftSlotPendingRemove'), null);
});

test('an identity reset aborts an in-flight deferred removal before it can delete', async () => {
  const calls = [];
  let releaseGet;
  const storage = new MapStorage([['wl_draftSlotPendingRemove', '2026-08-01T00:00:00Z']]);
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      getDraftSlot: () => new Promise((resolve) => { releaseGet = resolve; }),
      async takeoverDraftSlot() { calls.push('takeover'); return { token: 'grabbed' }; },
      async deleteDraftSlot(token) { calls.push(`delete:${token}`); },
    },
  });

  const retrying = sync.retryPendingRemoval();
  await new Promise(setImmediate);
  sync.resetIdentity();               // 로그아웃 — 이 뒤의 삭제는 다음 계정 슬롯을 지우게 된다
  releaseGet({ meta: { updatedAt: '2026-07-01T00:00:00Z' } });
  await retrying;

  assert.deepEqual(calls, []);        // takeover/delete 모두 실행되면 안 된다
});

test('an edit made during a dead-tab probe is requeued after the quiet reclaim', async () => {
  const timers = fakeTimers();
  const puts = [];
  const storage = new MapStorage([['wl_draftSlotOwnerTab', 'dead-tab']]);
  const sync = createDraftSlotSync({
    storage,
    documentId: 'tab-live',
    debounceMs: 0,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    adapter: {
      async putDraftSlot(body) {
        puts.push(body.payload.product.name);
        return { token: 'owned', meta: { updatedAt: 'server-1' } };
      },
    },
  });

  assert.equal(sync.activate(), false);
  sync.queue({ product: product('first-edit'), localUpdatedAt: 'local-1' });   // 생존 확인 중의 첫 편집
  assert.equal(timers.runLatest(), 450);   // 응답 없음 — 조용한 인수 + 드롭된 편집 재큐잉
  assert.equal(sync.isLocked(), false);
  assert.equal(timers.runLatest(), 0);     // 재큐잉된 스냅샷의 디바운스 발화
  await sync.flush();

  assert.deepEqual(puts, ['first-edit']);
  sync.dispose();
});

test('a new flow treats an already-gone slot as removed instead of locking', async () => {
  const storage = new MapStorage([['wl_draftSlotToken', 'stale-token']]);
  const conflicts = [];
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async takeoverDraftSlot() { return { token: 'grabbed', payload: {}, meta: {} }; },
      async deleteDraftSlot() { throw tokenMismatch(null); },
    },
  });
  sync.onConflict((meta) => conflicts.push(meta));

  assert.equal(await sync.removeForNewFlow(), true);
  assert.equal(sync.isLocked(), false);
  assert.equal(sync.getToken(), null);
});

test('logout invalidates an in-flight failure so it cannot schedule an old-draft retry', async () => {
  const timers = fakeTimers();
  let rejectPut;
  const sync = createDraftSlotSync({
    storage: new MapStorage(),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    adapter: {
      putDraftSlot: () => new Promise((_resolve, reject) => { rejectPut = reject; }),
    },
  });
  sync.queue({ product: product('private'), localUpdatedAt: 'local-1' });
  const flushing = sync.flush();
  await new Promise(setImmediate);
  sync.resetIdentity();
  rejectPut(new Error('offline'));
  await assert.rejects(flushing, /offline/);
  await new Promise(setImmediate);

  assert.equal(timers.count(), 0);
  assert.equal(sync.getToken(), null);
});

test('logged-in and mock entry use one priority-4 modal with local and remote timestamps', () => {
  const app = read('../../src/App.jsx');
  const shell = read('../../src/features/shell/shell.jsx');
  assert.match(app, /const slotEnabled = Boolean\(session\) \|\| isMockMode/);
  assert.match(app, /Promise\.all\(\[[\s\S]*?draftSlot\.get\(\)[\s\S]*?loadDraft\(\)/);
  assert.match(app, /id: 'local'[\s\S]*?formatDraftRelativeTime\(localMeta\.updatedAt\)/);
  assert.match(app, /id: 'remote'[\s\S]*?formatDraftRelativeTime\(slot\.meta\?\.updatedAt\)/);
  assert.match(app, /slot\.meta\?\.updatedAt !== draftSlot\.getServerSyncedAt\(\)/);
  assert.match(app, /draftSlot\.takeover\(\)[\s\S]*?draftSlot\.stage/);
  assert.match(shell, /sources\.map\(\(source\)[\s\S]*?새로 시작하기/);
});

test('a device-local draft opens without waiting for the server, and phantom slots stay hidden', () => {
  const app = read('../../src/App.jsx');
  const input = read('../../src/features/product-input/ProductInput.jsx');
  // 로컬 카드 열기: 복원을 먼저 확정하고 작업권 인수는 뒤에서 — 서버 장애가 복원을 막지 않는다
  assert.match(app, /draftSlot\.stage\(\{ payload: draft, meta: source\.meta \}\);\s*\n\s*void draftSlot\.takeover\(\)\.catch/);
  // 사진·이름·분석이 전무한 슬롯(팬텀)과 지연 삭제 중인 슬롯은 이어가기 카드로 권하지 않는다
  assert.match(app, /slot\.meta\?\.hasContent !== false/);
  assert.match(app, /draftSlot\.hasPendingRemoval\(\)/);
  // 지연 삭제는 슬롯 조회 전에 결론을 기다린다 — 포기됐다면 그 슬롯 카드를 정상적으로 권한다
  assert.match(app, /await draftSlot\.retryPendingRemoval\(\)/);
  // 내용을 전부 지운 로컬 임시저장도 카드로 권하지 않는다(서버 hasContent 와 같은 기준)
  assert.match(app, /localHasContent/);
  // 같은 브라우저의 더 새로운 로컬 저장이 있으면 카드 두 장을 한 장으로 합친다 —
  // 판정은 UA 라벨이 아니라 슬롯의 마지막 쓰기 주체(serverSyncedAt 일치)로 한다
  assert.match(app, /sameDeviceNewerLocal/);
  assert.match(app, /slot\.meta\?\.updatedAt === draftSlot\.getServerSyncedAt\(\)/);
  // 빈 화면은 임시저장을 만들지 않고, 빈 화면 위의 gone 잠금은 조용히 스스로 풀린다
  assert.match(input, /if \(!hasContent && !draftHadContentRef\.current\) return/);
  assert.match(input, /draftSlot\.restartAfterGone\(\);\s*\n\s*return;/);
});

test('photo pending is hidden while editing but warned when a remote draft may omit photos', () => {
  const app = read('../../src/App.jsx');
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const shell = read('../../src/features/shell/shell.jsx');
  const slot = read('../../src/lib/draftSlot.js');
  assert.match(slot, /photosPending/);
  assert.match(app, /photosPending: Boolean\(slot\.meta\?\.photosPending\)/);
  assert.doesNotMatch(input, /일부 사진은 아직 동기화 중/);
  assert.match(shell, /사진 몇 장은 아직 저장 중이라 빠져 있을 수 있어요/);
});

test('resume choice dismissal cannot silently choose or take over a draft', () => {
  const app = read('../../src/App.jsx');
  const routeChoice = app.slice(app.indexOf("if (entryDecision === 'ask')"), app.indexOf("if (entryDecision !== 'continue')"));
  assert.doesNotMatch(routeChoice, /onClose/);
  assert.doesNotMatch(routeChoice, /chooseSource/);
});

test('input entry transition leaves the persistent header and background unobstructed', () => {
  const app = read('../../src/App.jsx');
  const inputRoute = app.slice(app.indexOf('function ProductInputRoute()'), app.indexOf('function RootRedirect()'));
  assert.match(inputRoute, /if \(entryDecision !== 'continue'\) return null/);
  assert.doesNotMatch(inputRoute, /이동하고 있어요/);
});

test('logout clears slot identity and remounts input for the next user', () => {
  const app = read('../../src/App.jsx');
  const auth = read('../../src/features/auth/AuthProvider.jsx');
  assert.match(auth, /draftSlot\.resetIdentity\(\)/);
  assert.match(auth, /await useAppStore\.getState\(\)\.beginProject\(\)\.catch/);
  assert.match(auth, /setSigningOut\(true\)/);
  assert.match(app, /if \(signingOut\) return/);
  assert.match(app, /generation\}:\$\{session\?\.user\?\.id \|\| 'guest'/);
});

test('promotion happens only at confirmation and cleans the slot before flow lock and navigation', () => {
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const submit = input.slice(input.indexOf('const submit = async () =>'), input.indexOf('const nameCard ='));
  const confirm = input.slice(input.indexOf('const goToStoryboard = async (opts) =>'), input.indexOf('const queueAnalysisPatch ='));
  assert.match(submit, /api\.analyzeProduct\(null, \{ product \}\)/);
  assert.doesNotMatch(submit, /ensureProject|createProject|uploadProductPhotos|saveProduct/);
  assert.match(confirm, /promoteDraftToProject\(draft\)/);
  assert.ok(confirm.indexOf('await draftSlot.remove()') < confirm.indexOf('confirmProductInfo(projectId)'));
  assert.ok(confirm.indexOf('confirmProductInfo(projectId)') < confirm.indexOf("navigate('/create/storyboard'"));
});

test('mock UI confirmation locks editing and flow navigation before asynchronous saves', () => {
  const form = read('../../src/features/analysis/AnalysisForm.jsx');
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const shell = read('../../src/features/shell/shell.jsx');
  const confirmAction = form.slice(form.indexOf('const confirmAnalysis = async () =>'), form.indexOf('// 인물 모델 카탈로그'));
  const promotion = input.slice(input.indexOf('const goToStoryboard = async (opts) =>'), input.indexOf('const queueAnalysisPatch ='));

  assert.ok(confirmAction.indexOf('onConfirmingChange?.(true)') < confirmAction.indexOf('await composeModeSaveRef.current'));
  assert.match(confirmAction, /finally \{[\s\S]*?onConfirmingChange\?\.\(false\)/);
  assert.ok(promotion.indexOf('setFlowPromotionLocked(true)') < promotion.indexOf('colorSaveSchedulerRef.current.flush()'));
  assert.match(promotion, /latestProductRef\.current[\s\S]*?latestAnalysisRef\.current[\s\S]*?latestComposeModeRef\.current/);
  // 확정 대기는 도착 오버레이와 같은 전환 화면으로 표시된다 (2026-08-14 사용자 결정)
  assert.match(input, /promotionLocked && createPortal\(\([\s\S]*?className="input-promotion-transition"[\s\S]*?document\.body/);
  assert.match(shell, /disabled=\{inputPromotionLocked\}/);
  assert.match(shell, /if \(inputPromotionLocked\) return/);
});

test('late promotion results are ignored after unmount or a new project generation', () => {
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const promotion = input.slice(input.indexOf('const goToStoryboard = async (opts) =>'), input.indexOf('const queueAnalysisPatch ='));
  assert.match(promotion, /mountedRef\.current[\s\S]*?promotionRunRef\.current === runId/);
  assert.match(promotion, /projectGeneration === projectGeneration/);
  assert.ok(promotion.indexOf('if (!isCurrentRun()) return') < promotion.indexOf('adoptProject(projectId'));
});

test('slot photos use the frozen draft_slot asset purpose and expose pending state', () => {
  const slot = read('../../src/lib/draftSlot.js');
  const adapter = read('../../src/lib/api/httpAdapter.js');
  assert.match(slot, /api\.uploadDraftSlotPhoto\(\{/);
  assert.match(adapter, /uploadPhoto\(null, \{ \.\.\.photo, purpose: 'draft_slot' \}/);
  assert.match(slot, /photosPending/);
});

test('device labels combine the simple platform and browser names', () => {
  assert.equal(getDraftSlotDeviceLabel('Mozilla/5.0 (iPhone) CriOS/126.0'), 'iPhone Chrome');
  assert.equal(getDraftSlotDeviceLabel('Mozilla/5.0 (Macintosh) Version/17 Safari/605.1'), 'Mac Safari');
  assert.equal(getDraftSlotDeviceLabel('Mozilla/5.0 (Windows NT 10.0) Firefox/128.0'), 'Windows Firefox');
});

test('ProductInput keeps every hook above its loading early returns', () => {
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const earlyReturn = input.indexOf('if (loadError) return');
  assert.ok(earlyReturn > 0);
  const afterEarlyReturn = input.slice(earlyReturn);
  assert.doesNotMatch(afterEarlyReturn, /\buse(?:State|Effect|Ref)\s*\(/);
  assert.doesNotMatch(afterEarlyReturn, /\buseAppStore\s*\(\s*\(/);
});

class MapStorage {
  constructor(entries = []) { this.values = new Map(entries); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

class EventedStorage extends MapStorage {
  constructor(entries = []) {
    super(entries);
    this.storageListeners = new Set();
  }

  addEventListener(type, handler) {
    if (type === 'storage') this.storageListeners.add(handler);
  }

  removeEventListener(type, handler) {
    if (type === 'storage') this.storageListeners.delete(handler);
  }

  dispatch(key, value) {
    const oldValue = this.getItem(key);
    if (value == null) this.removeItem(key);
    else this.setItem(key, value);
    for (const handler of this.storageListeners) {
      handler({
        key,
        oldValue,
        newValue: value,
        storageArea: this,
      });
    }
  }

  dispatchToken(value) { this.dispatch('wl_draftSlotToken', value); }
}
