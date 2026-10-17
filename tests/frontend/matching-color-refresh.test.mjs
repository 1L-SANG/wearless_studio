import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test, { before, after } from 'node:test';
import { createServer } from 'vite';
import { persistAnalysisEdit } from '../../src/features/product-input/saveRouting.js';
import { recommendLegacyMatchClothing } from '../../src/mock/matchingRecommendation.js';

let vite, api, DB;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(async () => { await vite?.close(); });

for (const projectId of [null, 'p-colors']) {
  test(`color edits rerank actual draft storage and preserve selection (${projectId})`, async () => {
    const options = { clothingType: 'top', targetGenders: ['men'], styleTags: ['basic'] };
    await api.saveProduct(projectId, { clothingType: 'top', colors: [{ id: 'base', isBase: true, swatchId: 'blue' }] });
    await api.saveAnalysis(projectId, options);
    const current = (await api.getAnalysis(projectId)).matchClothing;
    const colors = [{ id: 'base', isBase: true, swatchId: 'yellow', name: '옐로우' }];
    const expected = recommendLegacyMatchClothing({ ...options, productColor: 'yellow', current });
    assert.notDeepEqual(current.map((x) => x.id), expected.map((x) => x.id), 'fixture must change ordering');
    const saved = await persistAnalysisEdit(api, projectId, { colors });
    assert.deepEqual(saved.analysis.matchClothing.map((x) => x.id), expected.map((x) => x.id));
    assert.deepEqual(saved.analysis.matchClothing.filter((x) => x.selected).map((x) => x.id),
      current.filter((x) => x.selected).map((x) => x.id));
    assert.equal(DB.product.colors[0].swatchId, 'yellow');
    const refreshed = await api.refreshMatchClothing(projectId);
    assert.deepEqual(refreshed.matchClothing.map((x) => x.id), expected.map((x) => x.id));
  });
}

test('HTTP save and the analysis screen both treat a color edit as a recommendation refresh', () => {
  // 실행되는 production predicates를 읽는다. JSX를 렌더하지 않고 저장 분기 연결을 검사한다.
  const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const predicate = source.slice(source.indexOf('const isMatchRefresh ='), source.indexOf('function mergeAnalysisResult'));
  const httpRefresh = new Function(`${predicate}; return isMatchRefresh;`)();
  const form = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
  const line = form.split('\n').find((value) => value.startsWith('export const isMatchRecommendationPatch'));
  const screenRefresh = new Function(`${line.replace('export ', '')}; return isMatchRecommendationPatch;`)();
  for (const predicate of [httpRefresh, screenRefresh]) {
    assert.equal(predicate({ colors: [{ swatchId: 'yellow' }] }), true);
    assert.equal(predicate({ mdComment: '메모' }), false);
  }
});
