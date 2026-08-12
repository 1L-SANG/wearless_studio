import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { diversifyTopTwo } from '../../src/mock/matchingRecommendation.js';
import { analyzePublicDraft } from '../../src/lib/api/publicAnalysis.js';

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), 'utf8');

test('http public input analysis uses multipart and prefers optional Bearer rate-limit identity', async () => {
  const [indexSource, adapterSource] = await Promise.all([
    read('src/lib/api/index.js'),
    read('src/lib/api/httpAdapter.js'),
  ]);

  assert.match(indexSource, /analyzePublicDraft\(options\?\.product \|\| await mockAdapter\.getProduct\(projectId\), options/);
  assert.match(adapterSource, /publicHttp\('\/v1\/public\/analyze', form/);
  assert.match(adapterSource, /const \{ data \} = await supabase\.auth\.getSession\(\)/);
  assert.match(adapterSource, /headers: token \? \{ Authorization: `Bearer \$\{token\}` \} : undefined/);
  assert.match(adapterSource, /form\.append\('images', blob/);
  assert.match(adapterSource, /form\.append\('slots', photo\.slot/);
  assert.match(adapterSource, /colors\.find\(\(color\) => color\.isBase\)/);
});

test('public AI fields are saved into the local draft before returning matching candidates', async () => {
  const seen = {};
  const result = await analyzePublicDraft(
    { colors: [] }, {},
    {
      remote: { publicAnalyze: async () => ({ clothingType: 'top', styleTags: ['daily'] }) },
      local: {
        saveAnalysis: async (projectId, analysis) => {
          seen.projectId = projectId;
          seen.analysis = analysis;
          return { ...analysis, matchClothing: [{ id: 'ranked-main' }, { id: 'ranked-sub' }] };
        },
      },
    },
  );

  assert.equal(seen.projectId, null);
  assert.deepEqual(seen.analysis.styleTags, ['daily']);
  assert.deepEqual(result.matchClothing.map((item) => item.id), ['ranked-main', 'ranked-sub']);
});

test('mock matching diversifies the second candidate deterministically', () => {
  const ranked = [
    { id: 'a', colorGroup: 'black' },
    { id: 'b', colorGroup: 'black' },
    { id: 'c', colorGroup: 'beige' },
    { id: 'd', colorGroup: 'blue' },
  ];
  assert.deepEqual(diversifyTopTwo(ranked).map((item) => item.id), ['a', 'c', 'b', 'd']);
  assert.deepEqual(
    diversifyTopTwo(ranked.map((item) => ({ ...item, colorGroup: 'black' }))).map((item) => item.id),
    ['a', 'b', 'c', 'd'],
  );
});
