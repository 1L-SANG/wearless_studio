/* 첫 분석 직후 매칭 의류가 0개로 뜨던 사고(2026-10-10 오너 지적).

   공개 분석 응답은 기본 shape 를 베이스로 만들어져 matchClothing: [] 을 달고 온다. 그 응답을
   통째로 로컬 저장소(mock saveAnalysis)에 넘기면, 저장소가 추천기로 후보를 채운 직후 같은 호출의
   "빈 목록 = 비우기" 규칙(2026-10-06)이 그 후보를 지운다. 스텁 저장소로는 이 상호작용이 안 보여서
   실제 mock 어댑터로 고정한다. */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let vite, mockApi, analyzePublicDraft, defaultAnalysisShape, recommendLegacyMatchClothing;
before(async () => {
  vite = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api: mockApi } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ analyzePublicDraft } = await vite.ssrLoadModule('/src/lib/api/publicAnalysis.js'));
  ({ defaultAnalysisShape } = await vite.ssrLoadModule('/src/lib/api/shapes.js'));
  ({ recommendLegacyMatchClothing } = await vite.ssrLoadModule('/src/mock/matchingRecommendation.js'));
});
after(async () => { await vite?.close(); });

test('실서버의 첫 자동 코디와 빈 카탈로그를 목 추천으로 바꾸지 않는다', async () => {
  for (const candidates of [[{ id: 'server-pants', name: '실제 하의', selected: true, selOrder: 1 }], []]) {
    const response = { ...defaultAnalysisShape('top'), clothingType: 'top', matchClothing: candidates,
      outfitSelections: { version: 1, slots: { bottom: { itemId: candidates[0]?.id ?? null, origin: 'ai' } } },
      outfitRecommendation: { version: 1, status: candidates.length ? 'ready' : 'unavailable', reason: '', contextKey: 'server', candidates: { bottom: candidates, footwear: [] } },
      outfitRecommendationHandoff: { signature: 'server-signature' } };
    const actual = await analyzePublicDraft({ colors: [] }, {}, { remote: { publicAnalyze: async () => response }, local: mockApi });
    assert.deepEqual(actual.matchClothing, candidates);
    assert.deepEqual(actual.outfitSelections, response.outfitSelections);
    assert.deepEqual(actual.outfitRecommendation, response.outfitRecommendation);
    assert.deepEqual((await mockApi.getAnalysis()).matchClothing, candidates);
  }
});

test('첫 공개 분석 결과에 매칭 후보가 채워지고 하나가 기본 선택된다', async () => {
  const response = {
    ...defaultAnalysisShape('top'),
    clothingType: 'top', targetGenders: ['women'], styleTags: ['casual'],
  };
  assert.deepEqual(response.matchClothing, [], '전제: 응답은 빈 매칭 목록을 달고 온다');
  const remote = { publicAnalyze: async () => response };

  const out = await analyzePublicDraft({ colors: [] }, {}, { remote, local: mockApi });

  assert.ok(out.matchClothing.length > 0, '첫 진입 매칭 의류가 비어 있다');
  assert.ok(out.matchClothing.every((item) => item.clothingType === 'bottom'), '상의에는 하의 후보가 와야 한다');
  assert.equal(out.matchClothing.filter((item) => item.selected).length, 1, '기본 선택 하나');
});

test('새 실행과 사진 검사가 실패하면 공개 응답을 로컬에 저장하지 않는다', async () => {
  let writes = 0;
  const out = await analyzePublicDraft({}, { isCurrent: () => false }, { remote: { publicAnalyze: async () => ({ sellingPoints: ['stale'] }) }, local: { saveAnalysis: async () => { writes++; } } });
  assert.equal(writes, 0); assert.equal(out, null);
});

test('첫 공개 분석은 이전 목 색상이 아니라 실제 입력한 상품색으로 추천한다', async () => {
  await mockApi.saveProduct(null, { clothingType: 'top', colors: [{ id: 'old', isBase: true, swatchId: 'black' }] });
  const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, swatchId: 'yellow', name: '옐로우',
    images: [{ id: 'actual-photo', slot: 'Front', src: 'blob:actual' }] }] };
  const response = { ...defaultAnalysisShape('top'), clothingType: 'top', targetGenders: ['men'], styleTags: ['basic'],
    swatchSuggestions: [{ colorGroupId: 'base', swatchId: 'yellow', colorName: '옐로우' }] };
  const actual = await analyzePublicDraft(product, {}, { remote: { publicAnalyze: async () => response }, local: mockApi });
  const expected = recommendLegacyMatchClothing({ clothingType: 'top', targetGenders: ['men'], styleTags: ['basic'], productColor: 'yellow' });
  assert.deepEqual(actual.matchClothing.map((x) => x.id), expected.map((x) => x.id));
  assert.equal('colors' in actual, false, '추천 계산에 사용한 사진은 AI 응답에 섞지 않는다');
  assert.deepEqual((await mockApi.getProduct()).colors, product.colors);
});

test('공개 분석 저장 직전에 취소되면 상품색과 사진도 이전 값으로 유지한다', async () => {
  const previous = await mockApi.getProduct();
  let checks = 0;
  await analyzePublicDraft({ colors: [{ swatchId: 'purple', images: [] }] }, { isCurrent: () => ++checks === 1 }, {
    remote: { publicAnalyze: async () => ({ clothingType: 'top', styleTags: ['basic'] }) }, local: mockApi,
  });
  assert.deepEqual(await mockApi.getProduct(), previous);
});

test('새 분석의 기본 선택은 목 샘플 선택을 이어받지 않고 새 추천 1위로 정한다', async () => {
  await mockApi.resetInputDraft();
  const response = { ...defaultAnalysisShape('top'), clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'] };
  const actual = await analyzePublicDraft({ colors: [{ isBase: true, swatchId: 'yellow' }] }, {}, {
    remote: { publicAnalyze: async () => response }, local: mockApi,
  });
  assert.deepEqual(actual.matchClothing.filter((x) => x.selected).map((x) => x.id), [actual.matchClothing[0].id]);
});
