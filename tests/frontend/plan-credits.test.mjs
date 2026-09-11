import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createServer } from 'vite';

import { AI_MODELS } from '../../src/features/analysis/aiModels.js';
import * as limits from '../../src/lib/limits.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('plan credit policy produces the owner-approved generation totals', () => {
  assert.deepEqual(limits.EXTENSION_MODEL_FEE, {
    free: 19, starter: 19, seller: 10, pro: 0,
  });
  assert.deepEqual(limits.FREE_MANNEQUIN_ADJUSTS, {
    free: 1, starter: 1, seller: 1, pro: 2,
  });
  assert.deepEqual([...limits.BASIC_VIRTUAL_MODEL_IDS], ['mA', 'mB']);

  for (const [plan, modelId, expected] of [
    ['starter', 'mA', 45],
    ['starter', 'mE', 64],
    ['seller', 'mE', 55],
    ['pro', 'mE', 45],
    ['mystery-plan', 'mE', 64],
    ['starter', 'face-market-uuid', 45],
    ['starter', null, 45],
  ]) {
    assert.equal(limits.mannequinGenerationTotal(plan, modelId), expected);
  }
});

test('all virtual models declare one of the two pricing tiers', () => {
  assert.equal(AI_MODELS.length, 14);
  assert.equal(AI_MODELS.filter(({ tier }) => tier === 'basic').length, 2);
  assert.equal(AI_MODELS.filter(({ tier }) => tier === 'extension').length, 12);
  assert.deepEqual(
    AI_MODELS.filter(({ tier }) => tier === 'basic').map(({ id }) => id),
    ['mA', 'mB'],
  );
  assert.ok(AI_MODELS.every(({ tier }) => tier === 'basic' || tier === 'extension'));
});

test('generation and regeneration labels expose the exact next charge', () => {
  for (const [plan, modelId, expected] of [
    ['starter', 'mA', '의류정보 확정 완료 · 45 크레딧'],
    ['starter', 'mE', '의류정보 확정 완료 · 64 크레딧'],
    ['seller', 'mE', '의류정보 확정 완료 · 55 크레딧'],
    ['pro', 'mE', '의류정보 확정 완료 · 45 크레딧'],
  ]) {
    assert.equal(
      limits.mannequinGenerationCtaLabel(limits.mannequinGenerationTotal(plan, modelId)),
      expected,
    );
  }

  assert.equal(limits.mannequinRegenerationCtaLabel({
    freeAdjusts: 1, usedAdjusts: 0, nextCost: 0,
  }), '수정 반영 · 무료 (남은 무료 수정 1회)');
  assert.equal(limits.mannequinRegenerationCtaLabel({
    freeAdjusts: 2, usedAdjusts: 1, nextCost: 0,
  }), '수정 반영 · 무료 (남은 무료 수정 1회)');
  assert.equal(limits.mannequinRegenerationCtaLabel({
    freeAdjusts: 1, usedAdjusts: 1, nextCost: 45,
  }), '수정 반영 · 45 크레딧');
});

test('mock credit quote matches the server read contract', async (t) => {
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, hmr: false },
  });
  t.after(() => server.close());
  const { api: mockApi } = await server.ssrLoadModule('/src/mock/api.js');
  assert.equal(typeof mockApi.getCreditQuote, 'function');
  assert.deepEqual(await mockApi.getCreditQuote('p1', { selectedModelId: 'mE' }), {
    plan: 'free',
    mannequinGenerate: {
      base: 45,
      extensionModelFee: 19,
      total: 64,
      selectedModelId: 'mE',
      extensionFeeAlreadyPaid: false,
    },
    mannequinRegenerate: {
      freeAdjusts: 1,
      usedAdjusts: 0,
      nextCost: 0,
    },
    storyboardPerCut: 19,
    editorImage: 19,
  });
});

test('mock mannequin execution charges the snapshotted quote and preserves a free adjustment', async (t) => {
  const server = await createServer({
    configFile: false,
    logLevel: 'silent',
    root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, hmr: false },
  });
  t.after(() => server.close());
  const { api: mockApi } = await server.ssrLoadModule('/src/mock/api.js');

  const initialCredits = (await mockApi.getAccount()).credits;
  await mockApi.saveAnalysis('p1', { selectedModelId: 'mE' });
  const cancelledGeneration = mockApi.generateMannequins('p1');
  const cancellationRejection = assert.rejects(cancelledGeneration, { code: 'job_cancelled' });
  await mockApi.saveAnalysis('p1', { selectedModelId: 'mA' });
  const cancellation = await mockApi.cancelMannequinGeneration('p1');
  await cancellationRejection;
  assert.equal(cancellation.credits, initialCredits - 64, 'cancellation settles the start-time extended quote');
  assert.equal((await mockApi.getCreditQuote('p1', { selectedModelId: 'mE' }))
    .mannequinGenerate.extensionFeeAlreadyPaid, false);

  await mockApi.createProject();
  await mockApi.saveAnalysis('p1', { selectedModelId: 'mE' });
  const beforeSuccess = (await mockApi.getAccount()).credits;
  const generated = await mockApi.generateMannequins('p1');
  assert.equal(generated.credits, beforeSuccess - 64, 'successful extended generation charges its quoted total');

  const afterGenerateQuote = await mockApi.getCreditQuote('p1', { selectedModelId: 'mE' });
  assert.equal(afterGenerateQuote.mannequinGenerate.extensionFeeAlreadyPaid, true);
  assert.deepEqual(afterGenerateQuote.mannequinRegenerate, {
    freeAdjusts: 1,
    usedAdjusts: 0,
    nextCost: 0,
  });

  const freeRegeneration = await mockApi.regenerateMannequin('p1');
  assert.equal(freeRegeneration.credits, generated.credits, 'the first regeneration keeps the balance unchanged');
  assert.deepEqual((await mockApi.getCreditQuote('p1')).mannequinRegenerate, {
    freeAdjusts: 1,
    usedAdjusts: 1,
    nextCost: 45,
  });

  const paidRegeneration = await mockApi.regenerateMannequin('p1');
  assert.equal(paidRegeneration.credits, freeRegeneration.credits - 45);
});

test('http and screen wiring refreshes quotes without delaying local labels', () => {
  const http = read('../../src/lib/api/httpAdapter.js');
  const analysis = read('../../src/features/analysis/AnalysisForm.jsx');
  const productInput = read('../../src/features/product-input/ProductInput.jsx');
  const mannequin = read('../../src/features/mannequin/Mannequin.jsx');

  assert.match(http, /async getCreditQuote\(projectId, \{ selectedModelId \} = \{\}\)/);
  assert.match(http, /credit-quote\$\{selectedModelId \? `\?selectedModelId=\$\{encodeURIComponent\(selectedModelId\)\}` : ''\}/);
  assert.match(productInput, /api\.getCreditQuote\(analysisProjectId, \{ selectedModelId: analysis\?\.selectedModelId \}\)/);
  assert.match(analysis, /mannequinGenerationCtaLabel\(mannequinGenerationCost\)/);
  assert.match(analysis, /기본 · 무료/);
  assert.match(analysis, /extensionModelGroupLabel\(quotePlan\)/);
  assert.match(mannequin, /mannequinRegenerationCtaLabel\(regenerationQuote\)/);
  assert.match(productInput, /mannequinRegenerationCreditText\(creditQuote\?\.mannequinRegenerate\)/);
});

test('the free-adjust ledger action has a seller-facing history label', () => {
  const creditsHistory = read('../../src/features/credits/CreditsHistory.jsx');
  assert.match(creditsHistory, /'mannequinGenerate\.reserve': '마네킹 무료 수정'/);
});
