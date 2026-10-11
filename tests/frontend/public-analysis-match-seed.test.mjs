/* 첫 분석 직후 매칭 의류가 0개로 뜨던 사고(2026-10-10 오너 지적).

   공개 분석 응답은 기본 shape 를 베이스로 만들어져 matchClothing: [] 을 달고 온다. 그 응답을
   통째로 로컬 저장소(mock saveAnalysis)에 넘기면, 저장소가 추천기로 후보를 채운 직후 같은 호출의
   "빈 목록 = 비우기" 규칙(2026-10-06)이 그 후보를 지운다. 스텁 저장소로는 이 상호작용이 안 보여서
   실제 mock 어댑터로 고정한다. */
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let vite, mockApi, analyzePublicDraft, defaultAnalysisShape;
before(async () => {
  vite = await createServer({ configFile: false, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api: mockApi } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ analyzePublicDraft } = await vite.ssrLoadModule('/src/lib/api/publicAnalysis.js'));
  ({ defaultAnalysisShape } = await vite.ssrLoadModule('/src/lib/api/shapes.js'));
});
after(async () => { await vite?.close(); });

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
