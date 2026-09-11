import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  detailPageGenerationCreditShortfall,
  mannequinGenerationCreditShortfall,
} from '../../src/lib/creditPreflight.js';
import { CREDIT_COSTS, mannequinGenerationTotal, normalizePlanTier } from '../../src/lib/limits.js';
import { createGenerationRelevantEditsSession } from '../../src/features/mannequin/generationRelevantEditsSession.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

// Execute the actual ProductInput callback while keeping its render-time closure.
// The store can update without recreating onNext, including while the child awaits a save.
function inputCreditGuard({ state, renderedCost = 64, quote = null, modelId = 'mE' }) {
  const source = read('../../src/features/product-input/ProductInput.jsx');
  const start = source.indexOf('const guardMannequinCredits = () =>');
  const end = source.indexOf('// 콘티 이동은 아래에서 명시적으로 flush한다.', start);
  assert.ok(start >= 0 && end > start);
  const notices = [];
  const dependencies = {
    useAppStore: { getState: () => state },
    session: { user: { id: 'model-pricing-user' } },
    mannequinRequiredCredits: renderedCost,
    creditQuote: quote,
    analysis: { selectedModelId: modelId },
    generationWorkKind: 'none',
    mannequinGenerationCreditShortfall,
    mannequinGenerationTotal,
    normalizePlanTier,
    CREDIT_COSTS,
    setCreditShortfall: (shortfall) => notices.push(shortfall),
  };
  const guard = new Function(...Object.keys(dependencies),
    `${source.slice(start, end)}; return guardMannequinCredits;`)(...Object.values(dependencies));
  return { guard, notices };
}

test('restored extension draft uses a Pro account that arrives after the input rendered', () => {
  const state = { account: null };
  const { guard, notices } = inputCreditGuard({ state });
  assert.equal(guard(), true, 'an unresolved account must not block confirmation');
  state.account = { plan: 'pro', credits: 45 };
  assert.equal(guard(), true, 'the same callback must use the newly loaded Pro price');
  assert.deepEqual(notices, []);
});

test('input confirmation discards a previous plan quote after account changes', () => {
  const state = { account: { plan: 'pro', credits: 45 } };
  const { guard, notices } = inputCreditGuard({
    state, renderedCost: 45,
    quote: { plan: 'pro', mannequinGenerate: { selectedModelId: 'mE', total: 45 } },
  });
  state.account = { plan: 'starter', credits: 63 };
  assert.equal(guard(), false);
  assert.equal(notices[0].requiredCredits, 64);
  assert.equal(notices[0].availableCredits, 63);
});

test('input confirmation retains the server quote for the same plan and model', () => {
  const state = { account: { plan: 'seller', credits: 54 } };
  const { guard, notices } = inputCreditGuard({
    state, renderedCost: 55,
    quote: { plan: 'seller', mannequinGenerate: { selectedModelId: 'mE', total: 55 } },
  });
  assert.equal(guard(), false);
  assert.equal(notices[0].requiredCredits, 55);
  state.account = { plan: 'seller', credits: 55 };
  assert.equal(guard(), true);
});

test('input confirmation ignores a quote for a different selected model', () => {
  const state = { account: { plan: 'starter', credits: 63 } };
  const { guard, notices } = inputCreditGuard({
    state, renderedCost: 64,
    quote: { plan: 'starter', mannequinGenerate: { selectedModelId: 'mA', total: 45 } },
  });
  assert.equal(guard(), false);
  assert.equal(notices[0].requiredCredits, 64);
});

test('analysis confirmation blocks a cached account below the mannequin generation cost', () => {
  assert.deepEqual(mannequinGenerationCreditShortfall({ credits: 44 }), {
    availableCredits: 44,
    requiredCredits: CREDIT_COSTS.mannequinGenerate,
    message: '크레딧이 부족해요 — 보유 44 · 필요 45. 충전 후 다시 시도해 주세요.',
  });
});

test('gender-change confirmation reserves only the new mannequin generation cost', () => {
  assert.equal(mannequinGenerationCreditShortfall({ credits: 45 }), null);
  assert.equal(
    mannequinGenerationCreditShortfall({ credits: 44 }).requiredCredits,
    CREDIT_COSTS.mannequinGenerate,
  );
});

test('mannequin preflight accepts a quoted plan-aware total while preserving its default', () => {
  assert.deepEqual(mannequinGenerationCreditShortfall({ credits: 63 }, 64), {
    availableCredits: 63,
    requiredCredits: 64,
    message: '크레딧이 부족해요 — 보유 63 · 필요 64. 충전 후 다시 시도해 주세요.',
  });
  assert.equal(mannequinGenerationCreditShortfall({ credits: 64 }, 64), null);
  assert.equal(mannequinGenerationCreditShortfall({ credits: 45 }), null);
});

test('detail-page confirmation multiplies AI cuts by the canonical per-cut cost', () => {
  const shortfall = detailPageGenerationCreditShortfall({ credits: 56 }, 3);
  assert.equal(shortfall.availableCredits, 56);
  assert.equal(shortfall.requiredCredits, 3 * CREDIT_COSTS.storyboardPerCut);
});

test('an account with the exact required balance passes each paid gate', () => {
  assert.equal(mannequinGenerationCreditShortfall({
    credits: CREDIT_COSTS.mannequinGenerate,
  }), null);
  assert.equal(detailPageGenerationCreditShortfall({
    credits: 3 * CREDIT_COSTS.storyboardPerCut,
  }, 3), null);
});

test('a missing account bypasses the preflight for anonymous users', () => {
  assert.equal(mannequinGenerationCreditShortfall(null), null);
  assert.equal(detailPageGenerationCreditShortfall(null, 10), null);
});

test('an unknown AI cut count bypasses the detail-page preflight', () => {
  assert.equal(detailPageGenerationCreditShortfall({ credits: 0 }, null), null);
  assert.equal(detailPageGenerationCreditShortfall({ credits: 0 }, undefined), null);
});

test('a known zero-AI-cut storyboard is a valid zero-cost entry', () => {
  assert.equal(detailPageGenerationCreditShortfall({ credits: 0 }, 0), null);
});

test('the shared modal renders the exact owner copy and both enabled actions', () => {
  const modal = read('../../src/features/credits/CreditShortfallModal.jsx');
  assert.match(modal, /<p>\{shortfall\.message\}<\/p>/);
  assert.match(modal, /navigate\('\/pricing'\)/);
  assert.match(modal, />충전하러 가기<\/Button>/);
  assert.match(modal, />닫기<\/Button>/);
  assert.doesNotMatch(modal, /disabled/);
});

test('the three UI gates return before paid work and leave dirty consumption to generation success', () => {
  const productInput = read('../../src/features/product-input/ProductInput.jsx');
  const mannequin = read('../../src/features/mannequin/Mannequin.jsx');

  const storyboardGate = productInput.slice(
    productInput.indexOf('const goToStoryboard = async (opts) =>'),
    productInput.indexOf('const queueAnalysisPatch =', productInput.indexOf('const goToStoryboard = async (opts) =>')),
  );
  const storyboardGuard = storyboardGate.indexOf('guardMannequinCredits()');
  const storyboardRedirect = storyboardGate.indexOf('redirectingRef.current = true');
  assert.ok(storyboardGuard >= 0 && storyboardRedirect >= 0 && storyboardGuard < storyboardRedirect);
  assert.match(storyboardGate, /if \(!guardMannequinCredits\(\)\) return;/);

  const runningGate = productInput.slice(
    productInput.indexOf('const confirmRunningRelevantPatch = async () =>'),
    productInput.indexOf('useEffect(() => {', productInput.indexOf('const confirmRunningRelevantPatch = async () =>')),
  );
  const runningGuard = runningGate.indexOf('guardMannequinCredits()');
  const runningCancel = runningGate.indexOf('api.cancelMannequinGeneration');
  assert.ok(runningGuard >= 0 && runningCancel >= 0 && runningGuard < runningCancel);
  assert.match(runningGate, /if \(!guardMannequinCredits\(\)\) return;/);

  const completedApply = productInput.indexOf(
    'const patch = pendingRelevantPatch;',
    productInput.indexOf('{pendingRelevantPatch && !creditShortfall && ('),
  );
  const completedGate = productInput.slice(
    productInput.lastIndexOf('<Button variant="ghost"', completedApply),
    productInput.indexOf('</Button>', completedApply),
  );
  assert.ok(completedGate.indexOf('guardMannequinCredits()') >= 0);
  assert.ok(completedGate.indexOf('guardMannequinCredits()') < completedGate.indexOf('applyAnalysisPatch(patch)'));
  assert.match(completedGate, /if \(!guardMannequinCredits\(\)\) return;/);

  const detailGate = mannequin.slice(
    mannequin.indexOf('const onCta = async () =>'),
    mannequin.indexOf('const regenerateActive =', mannequin.indexOf('const onCta = async () =>')),
  );
  const detailGuard = detailGate.indexOf('detailPageGenerationCreditShortfall');
  const regenerateBranch = detailGate.indexOf('if (needsRegen)');
  const profileSave = detailGate.indexOf('api.saveAnalysis');
  const detailNavigate = detailGate.indexOf("navigate('/create/generating')");
  assert.ok(regenerateBranch >= 0 && regenerateBranch < detailGuard);
  assert.ok(detailGuard >= 0 && profileSave >= 0 && detailGuard < profileSave);
  assert.ok(detailNavigate >= 0 && detailGuard < detailNavigate);
  assert.match(
    detailGate,
    /if \(shortfall\) \{\s*setCreditShortfall\(shortfall\);\s*return;\s*\}/,
  );
  assert.doesNotMatch(storyboardGate, /clearGenerationRelevantEdits/);
  assert.doesNotMatch(detailGate.slice(0, detailGate.indexOf('api.saveAnalysis')), /clearGenerationRelevantEdits/);
});

test('an insufficient preflight leaves an existing mannequin dirty revision untouched', () => {
  const dirty = createGenerationRelevantEditsSession({
    storage: null,
    clearInitialRequested: () => {},
  });
  const projectId = 'p-credit-shortfall';
  dirty.mark(projectId);
  const revision = dirty.readRevision(projectId);

  assert.ok(mannequinGenerationCreditShortfall({ credits: 44 }));
  assert.equal(dirty.readRevision(projectId), revision);

  const productInput = read('../../src/features/product-input/ProductInput.jsx');
  const guardBody = productInput.slice(
    productInput.indexOf('const guardMannequinCredits = () =>'),
    productInput.indexOf('// 콘티 이동은 아래에서 명시적으로 flush한다.'),
  );
  assert.match(guardBody, /setCreditShortfall\(shortfall\)/);
  assert.match(guardBody, /if \(!shortfall\) return true;[\s\S]*?return false;/);
  assert.doesNotMatch(guardBody, /clearGenerationRelevantEdits|applyAnalysisPatch/);
});
