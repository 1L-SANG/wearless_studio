import test from 'node:test';
import assert from 'node:assert/strict';
import { createDraftSlotSync } from '../../src/lib/draftSlot.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const tick = () => new Promise(setImmediate);
const photo = (id) => ({ id, src: `blob:${id}`, type: 'image/jpeg', name: `${id}.jpg` });
const asset = (id) => ({ assetId: id, url: `https://img.test/${id}.jpg` });
const snapshot = (...images) => ({
  product: { name: '상품', colors: [{ id: 'base', images }] },
  localUpdatedAt: 'local-1',
});
const payloadImages = (body) => body.payload.product.colors.flatMap((color) => color.images);
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const observe = (promise) => {
  let result;
  promise.then((value) => { result = value; });
  return () => result;
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
    run(ms) {
      const entry = [...timers].find(([, timer]) => timer.ms === ms);
      assert.ok(entry, `${ms}ms timer scheduled`);
      timers.delete(entry[0]);
      entry[1].fn();
    },
    delays() { return [...timers.values()].map((timer) => timer.ms); },
  };
}

class MapStorage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

function setup(t, adapter = {}) {
  const timers = fakeTimers();
  const storage = new MapStorage();
  const puts = [];
  const uploads = [];
  const discarded = [];
  let time = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ blob: async () => new Blob(['photo'], { type: 'image/jpeg' }) });
  const sync = createDraftSlotSync({
    storage,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    now: () => time,
    adapter: {
      async putDraftSlot(body) {
        puts.push(body);
        return adapter.putDraftSlot ? adapter.putDraftSlot(body) : { token: 'owned' };
      },
      async uploadDraftSlotPhoto(body) {
        uploads.push(body);
        return adapter.uploadDraftSlotPhoto ? adapter.uploadDraftSlotPhoto(body) : asset(`asset-${uploads.length}`);
      },
      async discardDraftSlotPhoto(id) {
        discarded.push(id);
        return adapter.discardDraftSlotPhoto?.(id);
      },
      getDraftSlot: adapter.getDraftSlot,
      takeoverDraftSlot: adapter.takeoverDraftSlot,
    },
  });
  t.after(() => {
    sync.discard();
    sync.dispose();
    globalThis.fetch = originalFetch;
  });
  return { sync, timers, storage, puts, uploads, discarded, advance: (ms) => { time += ms; } };
}

test('saveAll waits for every delayed upload and counts the images in the successful payload', async (t) => {
  const front = deferred();
  const back = deferred();
  const { sync, timers, puts } = setup(t, {
    uploadDraftSlotPhoto: ({ filename }) => filename === 'front.jpg' ? front.promise : back.promise,
  });
  const remote = { id: 'remote', src: 'https://img.test/remote.jpg' };
  const saving = sync.saveAll(snapshot(photo('front'), photo('back'), remote));
  const result = observe(saving);
  timers.run(500);
  await sync.flush();
  assert.equal(puts[0].photosPending, true);
  assert.deepEqual(payloadImages(puts[0]), [remote]);
  assert.equal(result(), undefined);

  front.resolve(asset('front-asset'));
  await tick();
  timers.run(500);
  await sync.flush();
  assert.equal(puts[1].photosPending, true);
  assert.equal(result(), undefined);

  back.resolve(asset('back-asset'));
  await tick();
  assert.equal(result(), undefined);
  timers.run(500);
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 3 });
  assert.equal(puts[2].photosPending, false);
  assert.deepEqual(payloadImages(puts[2]).map((image) => image.id), ['front-asset', 'back-asset', 'remote']);
});

test('an upload finishing during the first PUT waits for the second PUT response', async (t) => {
  const upload = deferred();
  const firstPut = deferred();
  const secondPut = deferred();
  let requests = 0;
  const { sync, timers, puts } = setup(t, {
    uploadDraftSlotPhoto: () => upload.promise,
    putDraftSlot: () => ++requests === 1 ? firstPut.promise : secondPut.promise,
  });
  const saving = sync.saveAll(snapshot(photo('front')));
  const result = observe(saving);
  timers.run(500);
  await tick();
  assert.equal(puts[0].photosPending, true);

  upload.resolve(asset('front-asset'));
  await tick();
  timers.run(500);
  firstPut.resolve({ token: 'owned' });
  await tick();
  assert.equal(puts.length, 2);
  assert.equal(puts[1].photosPending, false);
  assert.equal(result(), undefined);

  secondPut.resolve({ token: 'owned' });
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
});

test('upload failure resolves failed immediately and a later saveAll can succeed after the background retry', async (t) => {
  const upload = deferred();
  let attempts = 0;
  const { sync, timers } = setup(t, {
    uploadDraftSlotPhoto: () => ++attempts === 1 ? upload.promise : asset('recovered'),
  });
  const draft = snapshot(photo('front'));
  const saving = sync.saveAll(draft);
  await tick();
  upload.reject(new Error('upload offline'));
  assert.deepEqual(await saving, { status: 'failed', photoCount: 0 });
  assert.ok(timers.delays().includes(2000));

  timers.run(2000);
  await tick();
  const retrying = sync.saveAll(draft);
  await sync.flush();
  assert.deepEqual(await retrying, { status: 'saved', photoCount: 1 });
  assert.equal(attempts, 2);
});

test('saveAll immediately retries a failed upload without waiting for its retry timer', async (t) => {
  const replacement = deferred();
  let attempts = 0;
  const { sync, timers, puts } = setup(t, {
    uploadDraftSlotPhoto() {
      attempts += 1;
      if (attempts === 1) throw new Error('upload offline');
      return replacement.promise;
    },
  });
  const draft = snapshot(photo('front'));
  assert.deepEqual(await sync.saveAll(draft), { status: 'failed', photoCount: 0 });
  assert.ok(timers.delays().includes(2000));

  const retrying = sync.saveAll(draft);
  const result = observe(retrying);
  await tick();
  assert.equal(attempts, 2);
  assert.equal(timers.delays().includes(2000), false);
  assert.equal(result(), undefined);
  await sync.flush();
  assert.equal(result(), undefined);

  replacement.resolve(asset('recovered'));
  await tick();
  await sync.flush();
  assert.deepEqual(await retrying, { status: 'saved', photoCount: 1 });
  assert.equal(payloadImages(puts.at(-1))[0].id, 'recovered');
});

test('saveAll immediately fails an unreadable source without retrying it', async (t) => {
  const { sync, timers, uploads } = setup(t);
  let reads = 0;
  globalThis.fetch = async () => {
    reads += 1;
    throw new TypeError('Failed to fetch');
  };
  const draft = snapshot(photo('revoked'));
  assert.deepEqual(await sync.saveAll(draft), { status: 'failed', photoCount: 0 });
  assert.equal(timers.delays().includes(2000), false);

  assert.deepEqual(await sync.saveAll(draft), { status: 'failed', photoCount: 0 });
  assert.equal(reads, 1);
  assert.equal(uploads.length, 0);
  assert.equal(timers.delays().includes(2000), false);
});

test('a failed upload is reported without waiting for another pending upload', async (t) => {
  const front = deferred();
  const back = deferred();
  const { sync } = setup(t, {
    uploadDraftSlotPhoto: ({ filename }) => filename === 'front.jpg' ? front.promise : back.promise,
  });
  const saving = sync.saveAll(snapshot(photo('front'), photo('back')));
  await tick();
  front.reject(new Error('failed'));
  assert.deepEqual(await saving, { status: 'failed', photoCount: 0 });
  back.resolve(asset('back'));
  await tick();
});

test('a non-conflict final PUT failure resolves failed and preserves PUT retry', async (t) => {
  let offline = true;
  const { sync, timers, puts } = setup(t, {
    putDraftSlot() {
      if (offline) throw new Error('PUT offline');
      return { token: 'owned' };
    },
  });
  const draft = snapshot(photo('front'));
  const saving = sync.saveAll(draft);
  await tick();
  timers.run(500);
  assert.deepEqual(await saving, { status: 'failed', photoCount: 0 });
  assert.equal(puts[0].photosPending, false);
  assert.equal(payloadImages(puts[0]).length, 1);
  assert.ok(timers.delays().includes(2000));

  offline = false;
  timers.run(2000);
  await sync.flush();
  const retrying = sync.saveAll(draft);
  await sync.flush();
  assert.deepEqual(await retrying, { status: 'saved', photoCount: 1 });
});

for (const meta of [{ deviceLabel: 'PC', updatedAt: 'server-2' }, null]) {
  test(`saveAll resolves locked on token_mismatch with ${meta ? 'metadata' : 'null metadata'}`, async (t) => {
    const upload = deferred();
    const { sync, timers } = setup(t, {
      uploadDraftSlotPhoto: () => upload.promise,
      putDraftSlot() {
        throw Object.assign(new Error('taken'), { status: 409, code: 'token_mismatch', meta });
      },
    });
    const saving = sync.saveAll(snapshot(photo('front')));
    timers.run(500);
    assert.deepEqual(await saving, { status: meta ? 'locked' : 'gone', photoCount: 0 });
    assert.equal(sync.isLocked(), true);
    assert.deepEqual(timers.delays(), []);
    assert.deepEqual(await sync.saveAll(snapshot(photo('front'))), { status: meta ? 'locked' : 'gone', photoCount: 0 });
    upload.resolve(asset('late'));
    await tick();
  });
}

test('ownership loss resolves locked even while an upload is still pending', async (t) => {
  const upload = deferred();
  const { sync } = setup(t, {
    uploadDraftSlotPhoto: () => upload.promise,
    getDraftSlot: async () => ({ holdsToken: false, meta: { deviceLabel: 'PC' } }),
  });
  const saving = sync.saveAll(snapshot(photo('front')));
  await sync.flush();
  await sync.checkOwnership();
  assert.deepEqual(await saving, { status: 'locked', photoCount: 0 });
  upload.resolve(asset('late'));
  await tick();
});

test('a PUT retry discards an uploaded record older than 24 hours and re-uploads before saving', async (t) => {
  const replacement = deferred();
  let attempts = 0;
  let offline = true;
  const { sync, timers, puts, uploads, discarded, advance } = setup(t, {
    uploadDraftSlotPhoto: () => ++attempts === 1 ? asset('stale') : replacement.promise,
    discardDraftSlotPhoto() { throw new Error('discard offline'); },
    putDraftSlot() {
      if (offline) throw new Error('PUT offline');
      return { token: 'owned' };
    },
  });
  const draft = snapshot(photo('front'));
  sync.queue(draft);
  await tick();
  await assert.rejects(sync.flush(), /PUT offline/);
  assert.equal(payloadImages(puts[0])[0].id, 'stale');

  advance(DAY_MS + 1);
  offline = false;
  timers.run(2000);
  await sync.flush();
  await tick();
  assert.deepEqual(discarded, ['stale']);
  assert.equal(uploads.length, 2);
  assert.equal(uploads[1].filename, 'front.jpg');
  assert.equal(await uploads[1].blob.text(), 'photo');
  assert.deepEqual(payloadImages(puts[1]), []);
  assert.equal(puts[1].photosPending, true);

  const saving = sync.saveAll(draft);
  const result = observe(saving);
  await sync.flush();
  assert.equal(result(), undefined);
  replacement.resolve(asset('fresh'));
  await tick();
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
  assert.equal(payloadImages(puts.at(-1))[0].id, 'fresh');
});

test('an uploaded record exactly 24 hours old is still referenced', async (t) => {
  let offline = true;
  const { sync, uploads, puts, discarded, advance } = setup(t, {
    putDraftSlot() {
      if (offline) throw new Error('PUT offline');
      return { token: 'owned' };
    },
  });
  const draft = snapshot(photo('front'));
  sync.queue(draft);
  await tick();
  await assert.rejects(sync.flush(), /PUT offline/);
  advance(DAY_MS);
  offline = false;
  const saving = sync.saveAll(draft);
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
  assert.equal(payloadImages(puts.at(-1))[0].id, 'asset-1');
  assert.equal(uploads.length, 1);
  assert.deepEqual(discarded, []);
});

test('a synced record older than 24 hours is reused without discarding or re-uploading', async (t) => {
  const { sync, puts, uploads, discarded, advance } = setup(t);
  const draft = snapshot(photo('front'));
  const first = sync.saveAll(draft);
  await tick();
  await sync.flush();
  assert.deepEqual(await first, { status: 'saved', photoCount: 1 });

  advance(8 * DAY_MS);
  const second = sync.saveAll(draft);
  await sync.flush();
  assert.deepEqual(await second, { status: 'saved', photoCount: 1 });
  assert.equal(uploads.length, 1);
  assert.deepEqual(discarded, []);
  assert.equal(payloadImages(puts.at(-1))[0].id, 'asset-1');
});

test('an old PUT response cannot mark its stale replacement as synced', async (t) => {
  const firstPut = deferred();
  const secondPut = deferred();
  let requests = 0;
  const { sync, timers, puts, advance, discarded } = setup(t, {
    putDraftSlot: () => ++requests === 1 ? firstPut.promise : secondPut.promise,
  });
  const draft = snapshot(photo('front'));
  const saving = sync.saveAll(draft);
  const result = observe(saving);
  await tick();
  timers.run(500);
  await tick();
  advance(DAY_MS + 1);
  sync.queue(draft);
  await tick();
  assert.deepEqual(discarded, []);
  timers.run(500);
  firstPut.resolve({ token: 'owned' });
  await tick();
  assert.equal(result(), undefined);
  assert.equal(payloadImages(puts[1])[0].id, 'asset-2');
  secondPut.resolve({ token: 'owned' });
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
});

test('a successful PUT started before saveAll cannot complete that call', async (t) => {
  const oldPut = deferred();
  const newPut = deferred();
  let requests = 0;
  const { sync, timers } = setup(t, {
    putDraftSlot: () => ++requests === 1 ? oldPut.promise : newPut.promise,
  });
  const draft = snapshot({ id: 'remote', src: 'https://img.test/remote.jpg' });
  sync.queue(draft);
  timers.run(500);
  await tick();
  const saving = sync.saveAll(draft);
  const result = observe(saving);
  oldPut.resolve({ token: 'owned' });
  await tick();
  assert.equal(result(), undefined);
  timers.run(500);
  await tick();
  newPut.resolve({ token: 'owned' });
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
});

test('a success rejected by responseIsCurrent cannot complete saveAll', async (t) => {
  const firstPut = deferred();
  let requests = 0;
  const { sync, timers, storage } = setup(t, {
    putDraftSlot: () => ++requests === 1 ? firstPut.promise : { token: 'owned' },
  });
  const draft = snapshot(photo('front'));
  const saving = sync.saveAll(draft);
  const result = observe(saving);
  await tick();
  timers.run(500);
  await tick();
  storage.setItem('wl_draftSlotToken', 'other');
  firstPut.resolve({ token: 'stale' });
  await tick();
  assert.equal(result(), undefined);
  assert.equal(sync.getToken(), null);
  storage.removeItem('wl_draftSlotToken');
  sync.queue(draft);
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
});

test('a later snapshot containing all required photos completes concurrent saveAll calls', async (t) => {
  const { sync } = setup(t);
  const first = sync.saveAll(snapshot(photo('front')));
  const second = sync.saveAll(snapshot(photo('front'), photo('back')));
  await tick();
  await sync.flush();
  assert.deepEqual(await first, { status: 'saved', photoCount: 2 });
  assert.deepEqual(await second, { status: 'saved', photoCount: 2 });
});

test('a later snapshot missing a required photo cannot complete saveAll', async (t) => {
  const { sync } = setup(t);
  const draft = snapshot(photo('front'));
  const saving = sync.saveAll(draft);
  const result = observe(saving);
  await tick();
  sync.queue(snapshot());
  await sync.flush();
  assert.equal(result(), undefined);
  sync.queue(draft);
  await tick();
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
});

test('an empty product saves with zero photos', async (t) => {
  const { sync } = setup(t);
  const saving = sync.saveAll(snapshot());
  await sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 0 });
});

for (const mode of ['ownership', 'takeover']) {
  test(`gone ${mode} reuploads local photos and drops remote-only references`, async (t) => {
    const h = setup(t, { getDraftSlot: async () => null, takeoverDraftSlot: async () => null });
    const remote = { id: 'remote', src: 'https://img.test/old.jpg' };
    const draft = snapshot(photo('front'), remote);
    const first = h.sync.saveAll(draft);
    await tick(); await h.sync.flush(); await first;
    if (mode === 'ownership') {
      await h.sync.checkOwnership();
      assert.equal((await h.sync.saveAll(draft)).status, 'gone');
      h.sync.restartAfterGone();
    } else await h.sync.takeover();
    const saving = h.sync.saveAll(draft);
    const result = observe(saving);
    await tick(); await h.sync.flush();
    assert.equal(h.uploads.length, 2);
    assert.deepEqual(payloadImages(h.puts.at(-1)).map((image) => image.id), ['asset-2']);
    assert.deepEqual(result(), { status: 'saved', photoCount: 1, missingPhotoCount: 1 });
  });
}

test('late pre-gone upload cannot replace a fresh upload of the same image', async (t) => {
  const old = deferred(), fresh = deferred();
  let count = 0;
  const h = setup(t, { getDraftSlot: async () => null,
    uploadDraftSlotPhoto: () => ++count === 1 ? old.promise : fresh.promise });
  const draft = snapshot(photo('front'));
  h.sync.queue(draft); await h.sync.flush(); await tick();
  await h.sync.checkOwnership(); h.sync.restartAfterGone();
  const saving = h.sync.saveAll(draft), result = observe(saving);
  await tick();
  old.resolve(asset('stale')); await tick(); await h.sync.flush();
  assert.equal(result(), undefined);
  assert.deepEqual(payloadImages(h.puts.at(-1)), []);
  fresh.resolve(asset('fresh')); await tick(); await h.sync.flush();
  assert.deepEqual(await saving, { status: 'saved', photoCount: 1 });
  assert.equal(payloadImages(h.puts.at(-1))[0].id, 'fresh');
});

test('a delayed empty takeover restarts the snapshot that began uploading while it waited', async (t) => {
  const takeover = deferred(), old = deferred(), fresh = deferred();
  let count = 0;
  const h = setup(t, { takeoverDraftSlot: () => takeover.promise,
    uploadDraftSlotPhoto: () => ++count === 1 ? old.promise : fresh.promise });
  const taking = h.sync.takeover();
  const saving = h.sync.saveAll(snapshot(photo('front'))), result = observe(saving);
  await h.sync.flush(); await tick();
  takeover.resolve(null); await taking; await tick();
  assert.equal(count, 2);
  assert.equal(result()?.status, 'gone');
  old.resolve(asset('stale')); fresh.resolve(asset('fresh'));
  await tick(); await h.sync.flush();
  assert.equal(payloadImages(h.puts.at(-1))[0].id, 'fresh');
  assert.equal(h.puts.at(-1).photosPending, false);
});

test('visible retry restarts failed uploads before the retry timer fires', async (t) => {
  let count = 0;
  const h = setup(t, { uploadDraftSlotPhoto: () => {
    if (++count === 1) throw new Error('offline');
    return asset('fresh');
  } });
  assert.equal((await h.sync.saveAll(snapshot(photo('front')))).status, 'failed');
  h.sync.retryFailedUploads();
  await tick(); await h.sync.flush();
  assert.equal(count, 2);
  assert.equal(h.timers.delays().includes(2000), false);
  assert.equal(payloadImages(h.puts.at(-1))[0].id, 'fresh');
});
