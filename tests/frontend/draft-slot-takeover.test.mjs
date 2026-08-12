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

test('a token rotation storage event updates the next PUT without locking', async () => {
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

  assert.deepEqual(tokens, ['rotated-token']);
  assert.equal(sync.getToken(), 'rotated-token');
  assert.equal(sync.isLocked(), false);
  assert.deepEqual(conflicts, []);
  sync.dispose();
});

test('a 409 retries once with a newer stored token without reporting a conflict', async () => {
  const storage = new MapStorage([['wl_draftSlotToken', 'old-token']]);
  const tokens = [];
  const conflicts = [];
  const meta = { updatedAt: '2026-08-12T01:00:00Z', deviceLabel: 'Mac Chrome' };
  const sync = createDraftSlotSync({
    storage,
    adapter: {
      async putDraftSlot(body) {
        tokens.push(body.token);
        if (tokens.length === 1) throw tokenMismatch(meta);
        return { token: body.token, meta: { updatedAt: 'server-2' } };
      },
    },
  });
  sync.onConflict((conflict) => conflicts.push(conflict));
  storage.setItem('wl_draftSlotToken', 'rotated-token');

  sync.queue({ product: product('local'), localUpdatedAt: 'local-1' });
  await sync.flush();

  assert.deepEqual(tokens, ['old-token', 'rotated-token']);
  assert.equal(sync.isLocked(), false);
  assert.deepEqual(conflicts, []);
});

test('a repeated 409 after the newer-token retry locks and reports one conflict', async () => {
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

  assert.deepEqual(tokens, ['old-token', 'rotated-token']);
  assert.equal(sync.isLocked(), true);
  assert.deepEqual(conflicts, [meta]);
});

test('a meta-less 409 resets the token and recreates a deleted mock slot', async () => {
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
  await sync.flush();

  assert.deepEqual(tokens, [created.token, null]);
  assert.equal(sync.getToken(), 'token-2');
  assert.equal(storage.getItem('wl_draftSlotToken'), 'token-2');
  assert.equal(memory.get('token-2').holdsToken, true);
  assert.equal(memory.get('token-2', { full: true }).payload.product.name, 'local');
  assert.equal(sync.isLocked(), false);
  assert.deepEqual(conflicts, []);
});

test('a token storage event unlocks an already locked client and clears its conflict', async () => {
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

  assert.equal(sync.getToken(), 'rotated-token');
  assert.equal(sync.isLocked(), false);
  assert.deepEqual(conflicts, [meta, null]);
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

test('logged-in and mock entry use one priority-4 modal with local and remote timestamps', () => {
  const app = read('../../src/App.jsx');
  const shell = read('../../src/features/shell/shell.jsx');
  assert.match(app, /const slotEnabled = Boolean\(session\) \|\| isMockMode/);
  assert.match(app, /Promise\.all\(\[[\s\S]*?draftSlot\.get\(\)[\s\S]*?loadDraft\(\)/);
  assert.match(app, /id: 'local'[\s\S]*?formatDraftRelativeTime\(localMeta\.updatedAt\)/);
  assert.match(app, /id: 'remote'[\s\S]*?formatDraftRelativeTime\(slot\.meta\?\.updatedAt\)/);
  assert.match(app, /slot\.meta\?\.updatedAt !== draftSlot\.getServerSyncedAt\(\)/);
  assert.match(app, /draftSlot\.takeover\(\)[\s\S]*?draftSlot\.stage/);
  assert.match(shell, /sources\.map\(\(source\)[\s\S]*?새로 만들기/);
});

test('draft photo pending state remains in sync payloads but is not rendered as user copy', () => {
  const app = read('../../src/App.jsx');
  const input = read('../../src/features/product-input/ProductInput.jsx');
  const shell = read('../../src/features/shell/shell.jsx');
  const slot = read('../../src/lib/draftSlot.js');
  assert.match(slot, /photosPending/);
  assert.doesNotMatch(app, /photosPending:/);
  assert.doesNotMatch(input, /일부 사진은 아직 동기화 중/);
  assert.doesNotMatch(shell, /일부 사진은 아직 동기화 중/);
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
  assert.match(input, /promotionLocked && createPortal\(\([\s\S]*?className="input-promotion-lock"[\s\S]*?document\.body/);
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

  dispatchToken(value) {
    const oldValue = this.getItem('wl_draftSlotToken');
    if (value == null) this.removeItem('wl_draftSlotToken');
    else this.setItem('wl_draftSlotToken', value);
    for (const handler of this.storageListeners) {
      handler({
        key: 'wl_draftSlotToken',
        oldValue,
        newValue: value,
        storageArea: this,
      });
    }
  }
}
