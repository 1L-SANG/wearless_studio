import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { colorGroupForItem, colorGroupsForRole, filterOutfitItems } from '../../src/lib/outfitPresentation.js';

let vite, api;
const originalWindow = globalThis.window;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ mockAdapter: api } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
});
after(async () => {
  globalThis.window = originalWindow;
  await vite?.close();
});
beforeEach(async () => {
  globalThis.window = originalWindow;
  api.setOutfitScenario(undefined);
  api.setProductVisualsScenario?.(undefined);
  api.setCustomMatchScenario?.(undefined);
  api.clearCustomMatchDraft();
  await api.resetInputDraft();
});
const query = value => { globalThis.window = { location: { search: value } }; };
const analyze = async (clothingType = 'top') => {
  await api.saveAnalysis(null, { clothingType });
  return api.analyzeProduct(null, { product: { clothingType, colors: [] } });
};
const uploadCustom = async () => {
  const upload = await api.uploadPhoto(null, { filename: 'bottom.png', mime: 'image/png',
    blob: new Blob(['mock original'], { type: 'image/png' }), purpose: 'custom_match_source' });
  return { upload, added: await api.addCustomMatchItem(null, { assetIds: [upload.assetId] }) };
};

test('v18 fixtures are opt-in and ready produces three complete sets with optional props off', async () => {
  const baseline = await analyze();
  for (const role of ['footwear', 'bag', 'accessory']) assert.deepEqual(baseline.outfitRecommendation.candidates[role], []);
  query('?outfitDemo=ready');
  const result = await analyze();
  const recommendation = result.outfitRecommendation;
  assert.equal(recommendation.status, 'ready');
  assert.equal(recommendation.sets.length, 3);
  assert.equal(new Set(recommendation.sets.map(set => JSON.stringify(set.slots))).size, 3);
  for (const role of ['bottom', 'footwear']) {
    assert.equal(recommendation.candidates[role].filter(item => item.inSets).length, 3);
    for (const set of recommendation.sets) assert.ok(recommendation.candidates[role].some(item => item.id === set.slots[role]));
  }
  for (const role of ['footwear', 'bag', 'accessory']) {
    assert.ok(recommendation.candidates[role].every(item => item.isMockFixture && item.thumb.startsWith('data:image/svg+xml;')));
  }
  for (const item of recommendation.candidates.footwear) {
    const svg = decodeURIComponent(item.thumb.split(',')[1]);
    const [, width, height] = svg.match(/width='(\d+)' height='(\d+)'/);
    assert.equal(Number(width) / Number(height), item.ar, 'loading a shoe image must preserve its board aspect ratio');
  }
  assert.ok(result.outfitSelections.slots.footwear.itemId);
  assert.deepEqual(result.outfitSelections.optional, { bag: false, accessory: false });
  assert.equal(result.outfitSelections.slots.bag.itemId, null);
});

test('QA analysis uses the supplied product type without needing a prior analysis edit', async () => {
  query('?outfitDemo=ready');
  const result = await api.analyzeProduct(null, { product: { clothingType: 'bottom', colors: [] } });
  assert.equal(result.clothingType, 'bottom');
  assert.deepEqual(Object.keys(result.outfitRecommendation.candidates), ['top', 'footwear', 'bag', 'accessory']);
  assert.ok(result.matchClothing.every(item => item.clothingType === 'top'));
});

test('v18 analysis exposes the new chips and all six bottom lengths through the mock API', async () => {
  query('?outfitDemo=ready');
  await api.saveAnalysis(null, { targetGenders: ['women', 'men'] });
  const expected = {
    top: ['white', 'beige', 'gray', 'blue', 'black', 'color'],
    bottom: ['white', 'beige', 'gray', 'blue', 'black', 'color'],
    inner: ['white', 'beige', 'gray', 'blue', 'black', 'color'],
    footwear: ['white', 'brown', 'black'], bag: ['white', 'gray', 'brown', 'black'],
    accessory: ['white', 'silver', 'gold', 'brown', 'black'],
  };
  for (const kind of ['top', 'bottom', 'outer']) {
    const result = await analyze(kind);
    for (const [role, items] of Object.entries(result.outfitRecommendation.candidates)) {
      const groups = colorGroupsForRole(role).filter(group => items.some(item => colorGroupForItem(item, role) === group.value));
      assert.deepEqual(groups.map(group => group.value), expected[role], `${kind}/${role}`);
      for (const group of groups) assert.ok(filterOutfitItems(items, { role, color: group.value }).length);
    }
    if (kind === 'top') {
      for (const length of ['mini', 'short', 'knee', 'midi', 'maxi', 'full']) {
        assert.ok(result.matchClothing.some(item => item.length === length), length);
      }
    }
  }
});

test('mock board art uses transparent prop SVGs and garment thumbnail fallbacks', async () => {
  query('?outfitDemo=ready');
  const result = await analyze();
  assert.ok(result.matchClothing.every(item => item.boardUrl === null));
  assert.ok(result.outfitRecommendation.candidates.bottom.every(item => item.boardUrl === null));
  for (const role of ['footwear', 'bag', 'accessory']) {
    const items = result.outfitRecommendation.candidates[role];
    assert.ok(items.some(item => item.boardUrl?.startsWith('data:image/svg+xml;')));
    assert.ok(items.some(item => item.boardUrl === null));
  }
});

test('custom mock exposes supplied tidy art only after cleanup is ready', async (t) => {
  let now = 0; t.mock.method(Date, 'now', () => now);
  await analyze();
  api.setCustomMatchScenario({ status: 'processing', settleTo: 'ready', delayMs: 1000, tidyUrl: '/local-transparent-fixture.png' });
  const { added } = await uploadCustom();
  assert.equal(added.item.boardUrl, null);
  now = 1000;
  const ready = (await api.refreshMatchClothing(null)).matchClothing.find(item => item.isCustom);
  assert.equal(ready.boardUrl, '/local-transparent-fixture.png');
});

test('outer QA sets include inner candidates and role sets stay valid for all product types', async () => {
  query('?outfitDemo=ready');
  for (const [type, roles] of [
    ['outer', ['inner', 'bottom', 'footwear', 'bag', 'accessory']],
    ['bottom', ['top', 'footwear', 'bag', 'accessory']],
    ['dress', ['footwear', 'bag', 'accessory']],
  ]) {
    const result = await analyze(type);
    assert.deepEqual(Object.keys(result.outfitRecommendation.candidates), roles);
    for (const set of result.outfitRecommendation.sets) {
      assert.deepEqual(Object.keys(set.slots), roles.filter(role => !['bag', 'accessory'].includes(role)));
      assert.ok(Object.values(set.slots).every(Boolean));
    }
    if (type === 'outer') assert.ok(result.outfitSelections.slots.inner.itemId);
  }
});

test('query cases distinguish one set, missing shoes, unavailable and fallback', async () => {
  for (const [mode, status, count] of [['oneSet', 'ready', 1], ['noShoes', 'ready', 3],
    ['unavailable', 'unavailable', 0], ['fallback', 'fallback', 3]]) {
    query(`?outfitDemo=${mode}`);
    const { outfitRecommendation: recommendation } = await analyze();
    assert.equal(recommendation.status, status, mode);
    assert.equal(recommendation.sets.length, count, mode);
    if (mode === 'noShoes') {
      assert.equal(recommendation.roleStatus.footwear, 'unavailable');
      assert.deepEqual(recommendation.candidates.footwear, []);
      assert.ok(recommendation.sets.every(set => set.slots.footwear === null));
    }
  }
});

test('pending clock starts at recommendation, survives selection saves, and late ready needs apply', async (t) => {
  let now = 100;
  t.mock.method(Date, 'now', () => now);
  api.setOutfitScenario({ fixture: 'v18', status: 'pending', settleTo: 'ready', delayMs: 2000 });
  now = 50000;
  const pending = await analyze();
  assert.equal(pending.outfitRecommendation.status, 'pending');
  assert.equal((await api.getOutfitRecommendation(null)).outfitRecommendation.status, 'pending');
  assert.equal(pending.outfitRecommendation.fallbackReady, true);
  const savedSelections = structuredClone(pending.outfitSelections);
  savedSelections.slots.footwear = { itemId: null, origin: 'user' };
  now += 1999;
  await api.saveAnalysis(null, { outfitSelections: savedSelections });
  assert.equal((await api.getOutfitRecommendation(null)).outfitRecommendation.status, 'pending');
  now += 1;
  const ready = await api.getOutfitRecommendation(null);
  assert.equal(ready.outfitRecommendation.status, 'ready');
  assert.deepEqual(ready.outfitSelections, savedSelections);
  assert.notEqual(ready.outfitRecommendation.sets[0].slots.bottom, pending.outfitRecommendation.sets[0].slots.bottom);
  const applied = await api.saveAnalysis(null, { outfitSetIndex: 0 });
  assert.equal(applied.outfitSelections.slots.bottom.itemId, ready.outfitRecommendation.sets[0].slots.bottom);
  assert.deepEqual(applied.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
  assert.equal(applied.outfitSelections.optional.bag, false);
  assert.equal(applied.outfitRecommendation.status, 'ready');
  now += 10000;
  const next = await api.recommendOutfit(null, { styleTags: ['street'] });
  assert.equal(next.outfitRecommendation.status, 'pending');
  assert.equal((await api.getOutfitRecommendation(null)).outfitRecommendation.status, 'pending');
  assert.notEqual(next.outfitRecommendation.contextKey, pending.outfitRecommendation.contextKey);
});

test('pending and late URL scenarios settle after their own start, including module idle time', async (t) => {
  let now = 100000;
  t.mock.method(Date, 'now', () => now);
  for (const [mode, duration] of [['pending', 12000], ['late', 18000]]) {
    query(`?outfitDemo=${mode}`);
    const pending = await analyze();
    assert.equal(pending.outfitRecommendation.status, 'pending');
    now += duration - 1;
    assert.equal((await api.getOutfitRecommendation(null)).outfitRecommendation.status, 'pending');
    now++;
    assert.equal((await api.getOutfitRecommendation(null)).outfitRecommendation.status, 'ready');
  }
});

test('product visual processing supports anonymous mock drafts and settles once with supplied URLs', async (t) => {
  assert.equal(typeof api.setProductVisualsScenario, 'function');
  let now = 500;
  t.mock.method(Date, 'now', () => now);
  api.setProductVisualsScenario({ status: 'processing', settleTo: 'ready', delayMs: 3000,
    cutoutUrl: 'cutout-original', tidyUrl: 'tidy-result' });
  now += 50000;
  const first = await api.getProductVisuals(null);
  assert.equal(first.front.tidyStatus, 'processing');
  assert.equal(first.front.cutoutUrl, 'cutout-original');
  now += 2999;
  assert.equal((await api.getProductVisuals(null)).front.tidyStatus, 'processing');
  now++;
  const ready = await api.getProductVisuals(null);
  assert.equal(ready.front.tidyStatus, 'ready');
  assert.equal(ready.front.tidyUrl, 'tidy-result');
  now += 10000;
  assert.deepEqual(await api.getProductVisuals(null), ready);
});

test('tidy query covers ready, processing, failed and off while preserving an explicit absent cutout', async () => {
  for (const status of ['ready', 'processing', 'failed', 'off']) {
    query(`?tidyDemo=${status}`);
    assert.equal((await api.getProductVisuals(null)).front.tidyStatus, status);
  }
  api.setProductVisualsScenario({ status: 'off', cutoutUrl: null });
  const originalOnly = await api.getProductVisuals(null);
  assert.equal(originalOnly.front.cutoutUrl, null);
  assert.equal(originalOnly.front.tidyStatus, 'off');
});

test('custom cleanup keeps the original and selection until elapsed, preserving status through mapping', async (t) => {
  assert.equal(typeof api.setCustomMatchScenario, 'function');
  let now = 1000;
  t.mock.method(Date, 'now', () => now);
  await analyze();
  api.setCustomMatchScenario({ delayMs: 12000, status: 'processing', settleTo: 'ready' });
  now += 50000;
  const before = await api.getAnalysis(null);
  const { upload, added } = await uploadCustom();
  assert.equal(added.item.cutoutStatus, 'processing');
  assert.equal(added.item.thumb, upload.url);
  assert.equal(added.item.selected, false);
  assert.deepEqual(added.analysis.outfitSelections, before.outfitSelections);
  now += 11999;
  const waiting = (await api.refreshMatchClothing(null)).matchClothing.find(item => item.isCustom);
  assert.equal(waiting.cutoutStatus, 'processing');
  assert.deepEqual(waiting.sourceAssetIds, [upload.assetId]);
  assert.equal(waiting.thumb, upload.url);
  now++;
  const finished = await api.refreshMatchClothing(null);
  const tidy = finished.matchClothing.find(item => item.isCustom);
  assert.equal(tidy.cutoutStatus, 'ready');
  assert.notEqual(tidy.thumb, upload.url);
  assert.deepEqual(tidy.sourceAssetIds, [upload.assetId]);
  assert.deepEqual(finished.outfitSelections, before.outfitSelections);
  assert.equal(tidy.selected, false);
  assert.equal((await api.refreshMatchClothing(null)).matchClothing.find(item => item.isCustom).thumb, tidy.thumb);
  assert.equal(api.getCustomMatchDraft().uploads.length, 1);
});

test('customCleanMs query controls 7, 12 and 18 second cleanup even with preserved server catalogs', async (t) => {
  let now = 1000;
  t.mock.method(Date, 'now', () => now);
  for (const duration of [7000, 12000, 18000]) {
    await api.resetInputDraft();
    api.clearCustomMatchDraft();
    query(`?customCleanMs=${duration}`);
    const { upload, added } = await uploadCustom();
    assert.equal(added.item.cutoutStatus, 'processing');
    now += duration - 1;
    assert.equal((await api.refreshMatchClothing(null, { preserveCatalog: true })).matchClothing.find(item => item.isCustom).cutoutStatus, 'processing');
    now++;
    const ready = (await api.refreshMatchClothing(null, { preserveCatalog: true })).matchClothing.find(item => item.isCustom);
    assert.equal(ready.cutoutStatus, 'ready');
    assert.deepEqual(ready.sourceAssetIds, [upload.assetId]);
  }
});

test('QA props start only on demand, cache picks and expose four accessory categories including hats', async () => {
  query('?outfitDemo=ready');
  const initial = await analyze();
  assert.equal(initial.outfitRecommendation.props?.accessory, undefined);
  assert.ok(initial.outfitRecommendation.sets.every(set => !('bag' in set.slots) && !('accessory' in set.slots)));
  const categories = new Set(initial.outfitRecommendation.candidates.accessory.map(item => item.category));
  assert.ok(categories.size >= 4); assert.ok(categories.has('모자'));
  const result = await api.recommendOutfitProps(null, 'accessory');
  assert.equal(result.outfitRecommendation.props.accessory.status, 'ready');
  assert.equal(result.outfitRecommendation.props.accessory.picks.length, 3);
  const saved = await api.saveAnalysis(null, { outfitSelections: { ...initial.outfitSelections,
    optional: { bag: false, accessory: true } } });
  assert.deepEqual(saved.outfitRecommendation.props.accessory, result.outfitRecommendation.props.accessory);
  const cached = await api.recommendOutfitProps(null, 'accessory');
  assert.deepEqual(cached.outfitRecommendation.props.accessory, result.outfitRecommendation.props.accessory);
});

test('ready QA demo keeps props pending for 1500ms when mock latency is enabled', async t => {
  const latency = process.env.MOCK_API_LATENCY_SCALE;
  const isolated = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  t.after(() => isolated.close());
  let mockApi, fixture;
  try {
    process.env.MOCK_API_LATENCY_SCALE = '1';
    ({ api: mockApi } = await isolated.ssrLoadModule('/src/mock/api.js'));
    fixture = await isolated.ssrLoadModule('/src/mock/outfitV18Fixtures.js');
  } finally { if (latency == null) delete process.env.MOCK_API_LATENCY_SCALE; else process.env.MOCK_API_LATENCY_SCALE = latency; }
  const candidates = fixture.v18FixtureCandidates({ clothingType: 'dress' });
  const recommendation = fixture.v18FixtureRecommendation(candidates, { status: 'ready', contextKey: 'clock' });
  mockApi.setOutfitScenario({ fixture: 'v18', status: 'ready' });
  await mockApi.restoreInputDraft(null, { clothingType: 'dress', matchClothing: [], outfitRecommendation: recommendation });
  let now = 0; t.mock.method(Date, 'now', () => now);
  const pending = await mockApi.recommendOutfitProps(null, 'accessory');
  assert.equal(pending.outfitRecommendation.props.accessory.status, 'pending');
  assert.equal(pending.outfitRecommendation.props.accessory.picks.length, 3);
  now = 1499;
  assert.equal((await mockApi.getOutfitRecommendation(null)).outfitRecommendation.props.accessory.status, 'pending');
  now = 1500;
  assert.equal((await mockApi.getOutfitRecommendation(null)).outfitRecommendation.props.accessory.status, 'ready');
});

test('mock explicit set command saves supplied visible picks even when server recommendation has advanced', async () => {
  query('?outfitDemo=ready'); const analysis = await analyze();
  const originalSet = analysis.outfitRecommendation.sets[0];
  const bottom = analysis.outfitRecommendation.candidates.bottom.find(item => item.id !== originalSet.slots.bottom);
  const accessories = analysis.outfitRecommendation.candidates.accessory;
  await api.saveAnalysis(null, { outfitSelections: { ...analysis.outfitSelections, optional: { bag: false, accessory: true },
    slots: { ...analysis.outfitSelections.slots, accessory: { itemId: accessories[1].id, extraIds: [accessories[0].id, accessories[2].id], origin: 'user' } } } }, { preserveCatalog: true });
  const exact = { ...analysis.outfitSelections, setIndex: 1, optional: { bag: false, accessory: true }, slots: {
    ...analysis.outfitSelections.slots, bottom: { itemId: bottom.id, origin: 'ai' },
    accessory: { itemId: accessories[0].id, origin: 'ai' }, footwear: { itemId: null, origin: 'user' } } };
  const saved = await api.saveAnalysis(null, { outfitSetIndex: 1, outfitSelections: exact });
  assert.equal(saved.outfitSelections.slots.bottom.itemId, bottom.id);
  assert.deepEqual(saved.outfitSelections.slots.accessory, exact.slots.accessory);
  assert.deepEqual(saved.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
});
