import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { recommendLegacyMatchClothing } from '../../src/mock/matchingRecommendation.js';

let vite, adapter, supabase, mock, savePublicAnalysisEdit;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ httpAdapter: adapter } = await vite.ssrLoadModule('/src/lib/api/httpAdapter.js'));
  ({ supabase } = await vite.ssrLoadModule('/src/lib/supabase.js'));
  ({ api: mock } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ savePublicAnalysisEdit } = await vite.ssrLoadModule('/src/lib/api/publicAnalysis.js'));
});
after(async () => { await vite?.close(); });
const fields = { matchClothing: [], outfitSelections: { version: 1, slots: { footwear: { itemId: null, origin: 'user' } } },
  outfitRecommendation: { version: 1, status: 'unavailable', reason: '', contextKey: 'empty', candidates: { footwear: [] } },
  outfitRecommendationHandoff: { signature: 'signed' } };
const capture = (t, response) => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url: String(url), ...options });
    return new Response(JSON.stringify(response), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return calls;
};

test('공개 최초 core 분석에서 실제 코디와 인계 정보를 보존한다', async (t) => {
  const calls = capture(t, { data: { clothingType: 'dress', ...fields } });
  const actual = await adapter.publicAnalyze({ colors: [] }, { parts: 'core', photos: [{ slot: 'Front', blob: new Blob(['photo']) }] });
  assert.deepEqual(actual.matchClothing, []);
  assert.deepEqual(actual.outfitSelections, fields.outfitSelections);
  assert.deepEqual(actual.outfitRecommendation, fields.outfitRecommendation);
  assert.deepEqual(actual.outfitRecommendationHandoff, fields.outfitRecommendationHandoff);
  assert.equal(calls.length, 1);
});

test('공개 문맥 수정 추천은 상품 사진과 현재 사용자의 빈 선택을 함께 보낸다', async (t) => {
  assert.equal(typeof adapter.publicRecommendOutfit, 'function');
  const calls = capture(t, { data: fields });
  const actual = await adapter.publicRecommendOutfit({ colors: [] }, { clothingType: 'dress', subCategory: '원피스',
    fit: 'regular', materials: [{ name: '면', ratio: 100 }],
    outfitSelections: fields.outfitSelections }, { photos: [{ slot: 'Front', blob: new Blob(['photo']) }] });
  assert.deepEqual(actual, fields);
  assert.match(calls[0].url, /\/v1\/public\/outfit-recommendations$/);
  assert.deepEqual(JSON.parse(calls[0].body.get('analysisContext')).outfitSelections, fields.outfitSelections);
  assert.equal(JSON.parse(calls[0].body.get('analysisContext')).fit, 'regular');
  assert.deepEqual(JSON.parse(calls[0].body.get('analysisContext')).materials, [{ name: '면', ratio: 100 }]);
  assert.deepEqual(calls[0].body.getAll('slots'), ['Front']);
});

test('선택 저장은 추천 후보와 서명 자료를 서버에 쓰지 않고 추천 API를 다시 부르지 않는다', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const calls = capture(t, { clothingType: 'dress', ...fields });
  await adapter.saveAnalysis('outfit-project', { outfitSelections: fields.outfitSelections,
    outfitRecommendation: { reason: 'forged' }, outfitRecommendationHandoff: { signature: 'forged' } });
  const writes = calls.filter((call) => call.method === 'PATCH');
  assert.equal(writes.length, 1);
  assert.deepEqual(JSON.parse(writes[0].body), { outfitSelections: fields.outfitSelections });
  assert.equal(calls.some((call) => call.url.includes('/outfit-recommendations')), false);
});

test('세부 종류 변경 저장 뒤에 전체 코디를 재추천한다', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const calls = capture(t, { clothingType: 'dress', subCategory: '미니 원피스', ...fields });
  await adapter.saveAnalysis('outfit-project', { subCategory: '원피스', customCategory: null });
  assert.deepEqual(calls.filter((call) => ['PATCH', 'POST'].includes(call.method)).map((call) => call.method), ['PATCH', 'POST']);
  assert.match(calls.at(-1).url, /\/analysis\/outfit-recommendations$/);
});

test('목 코디는 새 역할의 가짜 사진을 만들지 않고 기존 후보와 빈 역할만 반환한다', async () => {
  assert.equal(typeof mock.recommendOutfit, 'function');
  await mock.resetInputDraft();
  const actual = await mock.analyzeProduct(null, { product: { clothingType: 'top', colors: [] } });
  assert.ok(actual.outfitRecommendation);
  for (const slot of ['footwear', 'bag', 'accessory']) {
    assert.deepEqual(actual.outfitRecommendation.candidates[slot], []);
    assert.equal(actual.outfitSelections.slots[slot].itemId, null);
  }
});

for (const origin of ['ai', 'user', 'user-null']) {
  test(`목 스타일 변경은 AI 의류만 새 순위로 바꾸고 사용자 의도를 보존한다 (${origin})`, async () => {
    await mock.resetInputDraft();
    const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, swatchId: 'black' }] };
    const context = { clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'] };
    const items = recommendLegacyMatchClothing({ ...context, productColor: 'black' });
    const oldId = items.find((item) => item.selected).id;
    const expectedId = recommendLegacyMatchClothing({ ...context, styleTags: ['street'], productColor: 'black' })[0].id;
    assert.notEqual(oldId, expectedId, '새 스타일의 1순위가 달라야 한다');
    const choice = { itemId: origin === 'user-null' ? null : oldId, origin: origin === 'ai' ? 'ai' : 'user' };
    await mock.restoreInputDraft(product, { ...context, matchClothing: items.map((item) => ({ ...item,
      selected: item.id === choice.itemId })), outfitSelections: { version: 1, slots: { bottom: choice } } });
    const actual = await mock.saveAnalysis(null, { styleTags: ['street'] });
    const expected = origin === 'ai' ? { itemId: expectedId, origin: 'ai' } : choice;
    assert.deepEqual(actual.outfitSelections.slots.bottom, expected);
    assert.deepEqual(actual.matchClothing.filter((item) => item.selected).map((item) => item.id), expected.itemId ? [expected.itemId] : []);
  });
}

test('목 명시적 코디 재추천도 새 문맥으로 후보 순위와 AI 선택을 갱신한다', async () => {
  await mock.resetInputDraft();
  const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, swatchId: 'black' }] };
  const context = { clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'] };
  const items = recommendLegacyMatchClothing({ ...context, productColor: 'black' });
  await mock.restoreInputDraft(product, { ...context, matchClothing: items,
    outfitSelections: { version: 1, slots: { bottom: { itemId: items[0].id, origin: 'ai' } } } });
  const expected = recommendLegacyMatchClothing({ ...context, styleTags: ['street'], productColor: 'black' });
  const actual = await mock.recommendOutfit(null, { styleTags: ['street'] });
  assert.deepEqual(actual.matchClothing.map((item) => item.id), expected.map((item) => item.id));
  assert.equal(actual.outfitSelections.slots.bottom.itemId, expected[0].id);
});

test('공개 편집의 빈 서버 후보를 유지하고 최근 사용자 선택은 늦은 추천보다 우선한다', async () => {
  await mock.resetInputDraft();
  await mock.saveAnalysis(null, { clothingType: 'dress', ...fields }, { serverCatalog: true });
  let recommendations = 0;
  const remote = { publicRecommendOutfit: async (_product, context) => {
    recommendations++;
    assert.equal(context.outfitSelections.slots.footwear.itemId, null);
    await mock.saveAnalysis(null, { outfitSelections: { version: 1, slots: { footwear: { itemId: null, origin: 'user' }, bag: { itemId: null, origin: 'user' } } } }, { preserveCatalog: true });
    return { ...fields, outfitSelections: { version: 1, slots: { footwear: { itemId: 'shoe-ai', origin: 'ai' }, bag: { itemId: 'bag-ai', origin: 'ai' } } } };
  } };
  const selected = await savePublicAnalysisEdit({ outfitSelections: fields.outfitSelections }, { remote, local: mock });
  assert.equal(recommendations, 0);
  assert.deepEqual(selected.matchClothing, []);
  const updated = await savePublicAnalysisEdit({ subCategory: '롱 원피스' }, { remote, local: mock });
  assert.equal(recommendations, 1);
  assert.deepEqual(updated.matchClothing, []);
  assert.deepEqual(updated.outfitSelections.slots.bag, { itemId: null, origin: 'user' });
});

test('새로고침 복원 뒤에도 실제 후보와 사용자가 비운 역할이 로컬 저장의 정본이다', async () => {
  assert.equal(typeof mock.restoreInputDraft, 'function');
  await mock.resetInputDraft();
  await mock.restoreInputDraft({ clothingType: 'dress', colors: [] }, { clothingType: 'dress', ...fields });
  const restored = await savePublicAnalysisEdit({ mdComment: '복원 뒤 수정' }, { local: mock,
    remote: { publicRecommendOutfit() { throw new Error('선택·메모만 저장할 때 호출 금지'); } } });
  assert.deepEqual(restored.matchClothing, []);
  assert.deepEqual(restored.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
});

test('상품이 소유하는 종류가 분석 저장 응답에 없어도 인증 코디 역할을 유지한다', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    const value = String(url).endsWith('/product') ? { clothingType: 'dress' }
      : { ...fields, subCategory: '원피스' };
    return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  const actual = await adapter.saveAnalysis('promoted-without-product-fields', { subCategory: '원피스' });
  assert.equal(actual.clothingType, 'dress');
  assert.deepEqual(actual.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
});

test('코디 상태 조회는 GET 또는 서명된 공개 조회만 쓰고 추천을 시작하지 않는다', async (t) => {
  assert.equal(typeof adapter.getOutfitRecommendation, 'function');
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const calls = capture(t, { data: fields, ...fields });
  await adapter.getOutfitRecommendation('project');
  await adapter.getOutfitRecommendation(null, { handoff: fields.outfitRecommendationHandoff });
  assert.match(calls[0].url, /\/projects\/project\/analysis\/outfit-recommendations$/);
  assert.notEqual(calls[0].method, 'POST');
  assert.match(calls[1].url, /\/public\/outfit-recommendations\/status$/);
  assert.deepEqual(JSON.parse(calls[1].body), { handoff: fields.outfitRecommendationHandoff });
});

test('목은 실제 후보로 세 벌, pending 완료, fallback, 역할 unavailable을 재현한다', async () => {
  assert.equal(typeof mock.setOutfitScenario, 'function');
  await mock.resetInputDraft();
  mock.setOutfitScenario({ status: 'pending', settleTo: 'ready', delayMs: 0 });
  const pending = await mock.analyzeProduct(null, { product: { clothingType: 'top', colors: [] } });
  assert.equal(pending.outfitRecommendation.status, 'pending');
  assert.equal(pending.outfitRecommendation.fallbackReady, true);
  assert.equal(pending.outfitRecommendation.sets.length, 3);
  assert.equal(pending.outfitRecommendation.roleStatus.footwear, 'unavailable');
  assert.equal(pending.outfitRecommendation.candidates.bottom.filter(x => x.inSets).length, 3);
  const chosen = { ...pending.outfitSelections, slots: { ...pending.outfitSelections.slots,
    bottom: { itemId: null, origin: 'user' } } };
  await mock.saveAnalysis(null, { outfitSelections: chosen }, { preserveCatalog: true });
  const ready = await mock.getOutfitRecommendation(null);
  assert.equal(ready.outfitRecommendation.status, 'ready');
  assert.equal(ready.outfitSelections.slots.bottom.itemId, null);
  mock.setOutfitScenario({ status: 'unavailable' });
  assert.deepEqual((await mock.recommendOutfit(null, {})).outfitRecommendation.sets, []);
  mock.setOutfitScenario({ status: 'fallback' });
  assert.equal((await mock.recommendOutfit(null, {})).outfitRecommendation.status, 'fallback');
  mock.setOutfitScenario(null);
});

test('같은 문맥 저장과 승격 재시도는 캐시 시간이 지나도 추천 POST를 만들지 않는다', async (t) => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const current = { clothingType: 'dress', subCategory: '롱 원피스', targetGenders: ['women'],
    styleTags: ['basic'], fit: 'regular', materials: [{ name: '면', ratio: 100 }], ...fields };
  const calls = capture(t, current);
  await adapter.saveAnalysis('same-draft', { ...current });
  await adapter.saveAnalysis('same-draft', { ...current, clothingType: null }, { recommendOutfit: false });
  assert.equal(calls.filter(call => call.method === 'POST').length, 0);
  assert.equal(calls.filter(call => call.method === 'PATCH').length, 2);
});

test('같은 공개 문맥 저장도 추천을 다시 시작하지 않는다', async () => {
  await mock.resetInputDraft();
  const current = { clothingType: 'dress', subCategory: '롱 원피스', ...fields };
  await mock.restoreInputDraft({ clothingType: 'dress', colors: [] }, current);
  let calls = 0;
  await savePublicAnalysisEdit({ clothingType: 'dress', subCategory: '롱 원피스' }, {
    local: mock, remote: { publicRecommendOutfit: async () => { calls++; return fields; } },
  });
  assert.equal(calls, 0);
});

test('소품 추천은 인증 역할 JSON 또는 공개 사진·현재 분석·handoff multipart로 요청한다', async t => {
  assert.equal(typeof adapter.recommendOutfitProps, 'function');
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const calls = capture(t, { data: fields, ...fields });
  await adapter.recommendOutfitProps('props-project', 'bag');
  assert.deepEqual(JSON.parse(calls[0].body), { role: 'bag' });
  assert.match(calls[0].url, /\/analysis\/outfit-props-recommendations$/);
  await adapter.recommendOutfitProps(null, 'accessory', { product: { colors: [] },
    analysis: { clothingType: 'top', ...fields }, photos: [{ slot: 'Front', blob: new Blob(['photo']) }] });
  assert.equal(calls[1].body.get('role'), 'accessory');
  assert.match(calls[1].url, /\/public\/outfit-props-recommendations$/);
  assert.deepEqual(JSON.parse(calls[1].body.get('handoff')), fields.outfitRecommendationHandoff);
  assert.deepEqual(JSON.parse(calls[1].body.get('analysisContext')).outfitSelections, fields.outfitSelections);
  assert.deepEqual(calls[1].body.getAll('slots'), ['Front']);
});

const multiAccessories = { version: 1, optional: { accessory: true }, slots: {
  accessory: { itemId: 'ear', extraIds: ['neck', 'hat'], origin: 'user' },
} };
test('runtime capability read uses a public GET, missing endpoint safely means legacy support', async t => {
  supabase.auth.getSession = async () => ({ data: { session: null } });
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url: String(url), ...options });
    return new Response('{}', { status: 404, headers: { 'Content-Type': 'application/json' } });
  });
  assert.deepEqual(await adapter.getOutfitCapabilities(), { props: false, maxAccessories: 1 });
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/v1\/public\/outfit-capabilities$/);
  assert.equal(calls[0].method, 'GET');
});

test('capability read forwards abort and does not turn cancellation into enabled support', async t => {
  supabase.auth.getSession = async () => ({ data: { session: null } });
  const controller = new AbortController(); controller.abort();
  await assert.rejects(adapter.getOutfitCapabilities({ signal: controller.signal }), { name: 'AbortError' });
});

test('save rejects an old API that silently strips accessory extras and preserves requested selection', async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const original = structuredClone(multiAccessories);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url: String(url), ...options });
    const value = String(url).endsWith('/outfit-capabilities') ? { props: true, maxAccessories: 3 }
      : { clothingType: 'top', outfitSelections: { ...multiAccessories, slots: { accessory: { itemId: 'ear', origin: 'user' } } } };
    return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  await assert.rejects(adapter.saveAnalysis('old-api-multi', { outfitSelections: multiAccessories }), { code: 'outfit_selection_not_saved' });
  assert.equal(calls.filter(call => call.method === 'PATCH').length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/outfit-capabilities')).length, 1);
  assert.deepEqual(multiAccessories, original);
});

test('promotion rejects old API truncation even after a new node advertised support', async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  t.mock.method(globalThis, 'fetch', async (url) => new Response(JSON.stringify(String(url).endsWith('/outfit-capabilities')
    ? { props: true, maxAccessories: 3 }
    : { promoted: true, outfitSelections: { ...multiAccessories, slots: { accessory: { itemId: 'ear', origin: 'user' } } } }),
  { status: 200, headers: { 'Content-Type': 'application/json' } }));
  assert.deepEqual(await adapter.getOutfitCapabilities(), { props: true, maxAccessories: 3 });
  await assert.rejects(adapter.promoteOutfitRecommendation('mixed-node', { signature: 'handoff' }, multiAccessories), { code: 'outfit_selection_not_saved' });
});

test('save and promotion accept all three echoed accessories including disabled remembered choices', async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const choices = { ...multiAccessories, optional: { accessory: false } };
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url: String(url), ...options });
    const value = String(url).endsWith('/outfit-capabilities') ? { props: true, maxAccessories: 3 }
      : { clothingType: 'top', outfitSelections: choices };
    return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  assert.deepEqual((await adapter.saveAnalysis('new-api-multi', { outfitSelections: choices })).outfitSelections, choices);
  assert.deepEqual((await adapter.promoteOutfitRecommendation('new-api-multi', { signature: 'handoff' }, choices)).outfitSelections, choices);
  assert.equal(calls.filter(call => call.url.endsWith('/outfit-capabilities')).length, 2);
});

for (const readiness of ['missing', 'warming', 'changed']) for (const operation of ['save', 'promotion']) {
  test(`multi ${operation} checks fresh ${readiness} readiness before any write`, async t => {
    supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
    const calls = []; let capabilityReads = 0;
    const original = structuredClone(multiAccessories);
    t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
      const path = String(url); calls.push({ path, ...options });
      const capabilities = path.endsWith('/outfit-capabilities');
      const earlierReady = capabilities && readiness === 'changed' && capabilityReads++ === 0;
      const status = capabilities && readiness === 'missing' ? 404 : 200;
      const value = capabilities ? earlierReady ? { props: true, maxAccessories: 3 } : { props: false, maxAccessories: 1 }
        : { clothingType: 'top', outfitSelections: multiAccessories, outfitRecommendation: { contextKey: 'saved' } };
      return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
    });
    if (readiness === 'changed') assert.equal((await adapter.getOutfitCapabilities()).maxAccessories, 3);
    const request = operation === 'save'
      ? adapter.saveAnalysis('warming-multi', { outfitSelections: multiAccessories }, { recommendOutfit: false })
      : adapter.promoteOutfitRecommendation('warming-multi', { signature: 'signed' }, multiAccessories);
    await assert.rejects(request, { code: 'outfit_selection_not_saved' });
    assert.equal(calls.filter(call => ['PATCH', 'POST'].includes(call.method)).length, 0);
    assert.equal(calls.filter(call => call.path.endsWith('/outfit-capabilities')).length, readiness === 'changed' ? 2 : 1);
    assert.deepEqual(multiAccessories, original);
  });
}

test('single accessory save and promotion do not query release readiness', async t => {
  supabase.auth.getSession = async () => ({ data: { session: { access_token: 'test' } } });
  const choices = { ...multiAccessories, slots: { accessory: { itemId: 'ear', origin: 'user' } } };
  const calls = capture(t, { clothingType: 'top', outfitSelections: choices });
  await adapter.saveAnalysis('single-ready', { outfitSelections: choices }, { recommendOutfit: false });
  await adapter.promoteOutfitRecommendation('single-ready', { signature: 'signed' }, choices);
  assert.equal(calls.some(call => call.url.endsWith('/outfit-capabilities')), false);
});
