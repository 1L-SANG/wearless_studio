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
  await sync.flush();
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
  memory.remove();
  assert.equal(memory.get(takeover.token), null);
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
