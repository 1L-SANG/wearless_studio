import test from 'node:test';
import assert from 'node:assert/strict';
import { baseProductPhoto, mergeDraftCustomVisual, startVisualPolling, currentDraftVisuals } from '../../src/lib/draftVisuals.js';

const product = { colors: [{ images: [{ id: 'other', slot: 'Front', src: 'other' }] },
  { isBase: true, images: [{ id: 'back', slot: 'Back', src: 'back' }, { id: 'front', slot: 'Front', src: 'raw' }] }] };

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key) };
}

test('board original follows server base-colour Front selection', () => {
  assert.equal(baseProductPhoto(product).id, 'front');
});
test('replaced source hides previous ready image before the next effect', () => {
  const visuals = { product: { sourceAssetIds: ['old'], front: { tidyStatus: 'ready', tidyUrl: 'old-result' } } };
  assert.equal(currentDraftVisuals(visuals, { product: { sourceAssetIds: ['front'] } }).product, null);
});
test('custom display refresh preserves selection and edits and ignores removed items', () => {
  const analysis = { mdComment: 'edited', outfitSelections: { slots: { bottom: { itemId: 'mine', origin: 'user' } } },
    matchClothing: [{ id: 'mine', isCustom: true, selected: true, selOrder: 2, name: 'edited', boardUrl: null }] };
  const next = mergeDraftCustomVisual(analysis, { localId: 'mine', boardUrl: 'tidy', thumbnailUrl: 'thumb', cutoutStatus: 'ready' });
  assert.equal(next.matchClothing[0].selected, true);
  assert.equal(next.matchClothing[0].selOrder, 2);
  assert.equal(next.matchClothing[0].name, 'edited');
  assert.equal(next.matchClothing[0].boardUrl, 'tidy');
  assert.equal(next.outfitSelections, analysis.outfitSelections);
  assert.equal(next.mdComment, 'edited');
  assert.equal(mergeDraftCustomVisual(analysis, { localId: 'gone', boardUrl: 'stale' }), analysis);
});

test('polling uses 5 seconds and stops after terminal result', async () => {
  const scheduled = []; const seen = []; let calls = 0;
  const stop = startVisualPolling({ query: async () => ({ product: { state: ++calls === 1 ? 'processing' : 'ready' } }),
    onResult: (r) => seen.push(r), setTimer: (fn, ms) => { scheduled.push({ fn, ms }); return 1; }, clearTimer() {} });
  await new Promise(setImmediate);
  assert.equal(scheduled[0].ms, 5000);
  await scheduled.shift().fn();
  assert.equal(calls, 2);
  assert.equal(scheduled.length, 0);
  stop();
});

test('polling has a bounded error budget and cancels in-flight responses', async () => {
  const scheduled = []; let calls = 0; let seen = 0;
  const options = { query: async () => { calls++; throw Error('offline'); }, onResult: () => seen++,
    setTimer: (fn) => { scheduled.push(fn); return 1; }, clearTimer() {}, maxErrors: 3 };
  const stop = startVisualPolling(options);
  await new Promise(setImmediate);
  while (scheduled.length) await scheduled.shift()();
  assert.equal(calls, 3);
  assert.equal(seen, 0);
  stop();
  let resolve;
  const canceled = startVisualPolling({ ...options, query: () => new Promise((r) => { resolve = r; }) });
  canceled(); resolve({ product: { state: 'ready' } });
  await new Promise(setImmediate);
  assert.equal(seen, 0);
});

test('off and absent jobs stop polling without manufacturing prepare requests', async () => {
  for (const response of [{ state: 'off' }, { product: null, custom: null }]) {
    let scheduled = 0;
    startVisualPolling({ query: async () => response, onResult() {}, setTimer() { scheduled++; } });
    await new Promise(setImmediate);
    assert.equal(scheduled, 0);
  }
});

test('product preparation waits only for the base Front upload and carries its server ID', { timeout: 5000 }, async () => {
  const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (src) => new Response(new Blob([src], { type: 'image/jpeg' }));
  const releases = new Map(); const calls = [];
  let notifyUploadsStarted; const uploadsStarted = new Promise((resolve) => { notifyUploadsStarted = resolve; });
  const values = new Map();
  const storage = { getItem(key) { return values.get(key) ?? null; }, setItem(key, value) { values.set(key, value); }, removeItem(key) { values.delete(key); } };
  const sync = createDraftSlotSync({ storage, debounceMs: 0, adapter: {
    uploadDraftSlotPhoto: ({ filename }) => new Promise((resolve) => {
      releases.set(filename, () => resolve({ assetId: `server-${filename}`, url: `https://image.test/${filename}` }));
      if (releases.size === 2) notifyUploadsStarted();
    }),
    putDraftSlot: async () => ({ token: 'owned' }),
    prepareDraftVisuals: async (token, body) => { calls.push({ token, body }); return { jobId: 'job', state: 'processing' }; },
  } });
  try {
    sync.setUserId('seller'); sync.setVisualsEnabled('seller', true); sync.activate();
    const product = { colors: [{ isBase: true, images: [
      { id: 'f', slot: 'Front', src: 'blob:front' }, { id: 'b', slot: 'Back', src: 'blob:back' }] }] };
    sync.queue({ product });
    const preparing = sync.prepareProductVisuals(product);
    await uploadsStarted;
    releases.get('f.jpg')();
    await preparing;
    assert.deepEqual(calls, [{ token: 'owned', body: { role: 'product', sourceAssetIds: ['server-f.jpg'] } }]);
    assert.equal(sync.getVisualHandoff(product).product.jobId, 'job');
    const replaced = structuredClone(product); replaced.colors[0].images[0].src = 'blob:replacement';
    assert.equal(sync.getVisualHandoff(replaced).product, undefined);
    releases.get('b.jpg')();
  } finally { sync.dispose(); globalThis.fetch = originalFetch; }
});

test('restoring a saved draft keeps its prepared Front without uploading or paying again', { timeout: 5000 }, async () => {
  const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let saved;
  const sync = createDraftSlotSync({ storage, debounceMs: 0, adapter: {
    uploadDraftSlotPhoto() { throw Error('prepared Front must reuse its owned upload'); },
    prepareDraftVisuals() { throw Error('restoration must keep the existing job'); },
    putDraftSlot: async (body) => { saved = body.payload; return { token: 'owned' }; },
  } });
  const original = { colors: [{ isBase: true, images: [{ id: 'front', slot: 'Front', src: 'blob:old' }] }] };
  const restored = structuredClone(original); restored.colors[0].images[0].src = 'blob:restored';
  const snapshot = { product: original, visuals: { product: { imageId: 'front', src: 'blob:old',
    sourceUrl: 'https://image.test/owned-original', sourceAssetIds: ['owned-upload'], jobId: 'prepared-job' } } };
  try {
    sync.setUserId('seller'); sync.setVisualsEnabled('seller', true); sync.activate();
    sync.adoptVisualHandoff(snapshot, restored);
    sync.queue({ product: restored });
    await sync.flush();
    await sync.prepareProductVisuals(restored);
    assert.equal(saved.product.colors[0].images[0].id, 'owned-upload');
    assert.equal(saved.draftVisuals.product.jobId, 'prepared-job');
    assert.equal(sync.getVisualHandoff(restored).product.jobId, 'prepared-job');
  } finally { sync.dispose(); }
});

test('off and missing account capability prevent every product/custom preparation API', { timeout: 5000 }, async () => {
  const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
  for (const enabled of [undefined, false]) {
    let calls = 0;
    const sync = createDraftSlotSync({ storage: memoryStorage(), adapter: {
      prepareDraftVisuals() { calls++; }, getDraftVisuals() { calls++; }, uploadDraftSlotPhoto() { calls++; },
    } });
    try {
      sync.setUserId('seller'); sync.setVisualsEnabled('seller', enabled);
      await sync.prepareProductVisuals(product);
      await sync.prepareCustomVisuals({ localId: 'mine', uploads: [{ blob: new Blob(['photo']) }] });
      assert.equal(sync.isVisualsEnabled(), false);
      assert.equal(calls, 0);
      sync.setVisualsEnabled('other-account', true);
      assert.equal(sync.isVisualsEnabled(), false);
      sync.setVisualsEnabled('seller', true);
      sync.setUserId('new-seller');
      assert.equal(sync.isVisualsEnabled(), false);
    } finally { sync.dispose(); }
  }
});

test('GET recovers a lost handoff from current server source IDs and persists it', { timeout: 5000 }, async () => {
  const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
  let saved;
  const sync = createDraftSlotSync({ storage: memoryStorage(), adapter: { putDraftSlot: async (body) => { saved = body.payload; return { token: 'owned' }; } } });
  const restored = { colors: [{ isBase: true, images: [{ id: 'remote-front', slot: 'Front', src: 'https://image.test/front' }] }] };
  try {
    sync.setUserId('seller'); sync.setVisualsEnabled('seller', true);
    assert.deepEqual(sync.getVisualSources({ colors: [] }), {});
    sync.adoptVisualHandoff({ customMatch: { localId: 'mine', sources: [{ id: 'remote-custom', src: 'https://image.test/mine' }] } }, restored);
    sync.queue({ product: restored }); await sync.flush();
    assert.deepEqual(sync.getVisualHandoff(restored), {});
    const result = { product: { jobId: 'lost-front', sourceAssetIds: ['remote-front'], state: 'ready' },
      custom: { jobId: 'lost-custom', localId: 'mine', sourceAssetIds: ['remote-custom'], state: 'ready' } };
    assert.equal(sync.recoverVisualHandoff(result, restored, 'mine').product.jobId, 'lost-front');
    await sync.flush();
    assert.equal(sync.getVisualHandoff(restored, 'mine').product.jobId, 'lost-front');
    assert.equal(sync.getVisualHandoff(restored, 'mine').custom.jobId, 'lost-custom');
    assert.equal(saved.draftVisuals.product.jobId, 'lost-front');
    assert.equal(saved.draftVisuals.custom.jobId, 'lost-custom');
    const replaced = structuredClone(restored); replaced.colors[0].images[0].id = 'replacement';
    assert.equal(sync.recoverVisualHandoff(result, replaced, 'other-custom').product, null);
    assert.equal(sync.getVisualHandoff(replaced, 'other-custom').custom, undefined);
  } finally { sync.dispose(); }
});

for (const role of ['product', 'custom']) {
  test(`confirmation bounds a hung ${role} preparation at 3s and aborts its late response`, { timeout: 5000 }, async () => {
    const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
    const timers = new Map(); let id = 0; let release; let options;
    let started; const pending = new Promise((resolve) => { started = resolve; });
    const hang = (...args) => { options = args.at(-1); started(); return new Promise((resolve) => { release = resolve; }); };
    const sync = createDraftSlotSync({ storage: memoryStorage(), setTimer: (fn, ms) => { timers.set(++id, { fn, ms }); return id; },
      clearTimer: (key) => timers.delete(key), adapter: { putDraftSlot: async () => ({ token: 'owned' }),
        prepareDraftVisuals: hang, uploadDraftSlotPhoto: hang, deleteDraftSlot: async () => null } });
    const remote = { colors: [{ images: [{ id: 'front', src: 'https://image.test/front', slot: 'Front' }] }] };
    try {
      sync.setUserId('seller'); sync.setVisualsEnabled('seller', true);
      sync.queue({ product: remote, analysis: { matchClothing: [{ id: 'mine', isCustom: true }] } });
      await sync.flush();
      const preparing = role === 'product' ? sync.prepareProductVisuals(remote)
        : sync.prepareCustomVisuals({ localId: 'mine', uploads: [{ blob: new Blob(['photo']), mime: 'image/png', filename: 'mine.png' }] });
      await pending;
      const settling = sync.settleVisualPreparations();
      const timeout = [...timers.values()].find((timer) => timer.ms === 3000);
      assert.ok(timeout);
      timeout.fn(); await settling;
      assert.equal(options.signal.aborted, true);
      assert.equal(options.isCurrent(), false);
      release(role === 'product' ? { jobId: 'late-job' } : { assetId: 'late-upload', url: 'https://image.test/late' });
      await preparing;
      assert.deepEqual(sync.getVisualHandoff(remote, 'mine'), {});
      await sync.remove({ forPromotion: true });
    } finally { sync.dispose(); }
  });
}

test('confirmation cancellation aborts outstanding preparations before the deadline', { timeout: 5000 }, async () => {
  const { createDraftSlotSync } = await import('../../src/lib/draftSlot.js');
  const sync = createDraftSlotSync();
  let release; let signal;
  sync.runProductVisualPreparation = (_, currentSignal) => { signal = currentSignal; return new Promise((resolve) => { release = resolve; }); };
  try {
    sync.setUserId('seller'); sync.setVisualsEnabled('seller', true);
    const preparing = sync.prepareProductVisuals(product);
    const controller = new AbortController();
    const settling = sync.settleVisualPreparations({ signal: controller.signal });
    controller.abort(); await settling;
    assert.equal(signal.aborted, true);
    release(); await preparing;
  } finally { sync.dispose(); }
});
