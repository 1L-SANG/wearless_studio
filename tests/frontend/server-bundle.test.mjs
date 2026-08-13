import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  COLOR_HARMONY,
  colorHarmonyScore,
  diversifyTopTwo,
  productColorFrom,
  recommendMatchingItems,
} from '../../src/mock/matchingRecommendation.js';
import { seedMatchingItems } from '../../src/mock/seedMatchingItems.js';
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

test('mock color harmony mirrors the server map and symmetric neutral fallback', () => {
  assert.equal(COLOR_HARMONY.size, 91);
  assert.equal(colorHarmonyScore('navy', 'beige'), 0.92);
  assert.equal(colorHarmonyScore('beige', 'navy'), 0.92);
  assert.equal(colorHarmonyScore('ultraviolet', 'khaki'), 0.5);
  assert.equal(colorHarmonyScore('navy', null), 0.5);
  assert.equal(productColorFrom(
    { colors: [{ isBase: true }] },
    { swatchSuggestions: [{ swatchId: 'ivory' }, { swatchId: 'red' }] },
  ), 'ivory');
});

test('mock combines normalized style affinity with product color and supports weight-zero rollback', () => {
  const items = [
    {
      id: 'black', clothingType: 'bottom', gender: 'women', isActive: true,
      styleTags: ['daily'], colorGroup: 'black', colorBrightness: 0, sortOrder: 1,
    },
    {
      id: 'beige', clothingType: 'bottom', gender: 'women', isActive: true,
      styleTags: ['minimal'], colorGroup: 'beige', colorBrightness: 80, sortOrder: 2,
    },
  ];
  const colorRanked = recommendMatchingItems({
    clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'],
    productColor: 'navy', items,
  });
  const styleOnly = recommendMatchingItems({
    clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'],
    productColor: 'navy', colorWeight: 0, items,
  });
  assert.deepEqual(colorRanked.map((item) => item.id), ['beige', 'black']);
  assert.deepEqual(styleOnly.map((item) => item.id), ['black', 'beige']);
});

test('mock matching seed keeps the server retagging inputs in parity', async () => {
  const serverSeed = JSON.parse(await read('server/seed/matching_items.json'));
  const serverTags = new Map(serverSeed.map((item) => [item.id, item.styleTags]));
  assert.equal(seedMatchingItems.length, serverSeed.length);
  for (const item of seedMatchingItems) {
    assert.deepEqual(item.styleTags, serverTags.get(item.id), item.id);
  }
});
