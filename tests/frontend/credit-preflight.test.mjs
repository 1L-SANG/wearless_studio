import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  detailPageGenerationCreditShortfall,
  mannequinGenerationCreditShortfall,
} from '../../src/lib/creditPreflight.js';
import { CREDIT_COSTS } from '../../src/lib/limits.js';
import { createGenerationRelevantEditsSession } from '../../src/features/mannequin/generationRelevantEditsSession.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('analysis confirmation blocks a cached account below the mannequin generation cost', () => {
  assert.deepEqual(mannequinGenerationCreditShortfall({ credits: 1 }), {
    availableCredits: 1,
    requiredCredits: CREDIT_COSTS.mannequinGenerate,
    message: '크레딧이 부족해요 — 보유 1 · 필요 2. 충전 후 다시 시도해 주세요.',
  });
});

test('gender-change confirmation reserves only the new mannequin generation cost', () => {
  assert.equal(mannequinGenerationCreditShortfall({ credits: 2 }), null);
  assert.equal(
    mannequinGenerationCreditShortfall({ credits: 1 }).requiredCredits,
    CREDIT_COSTS.mannequinGenerate,
  );
});

test('detail-page confirmation multiplies AI cuts by the canonical per-cut cost', () => {
  const shortfall = detailPageGenerationCreditShortfall({ credits: 2 }, 3);
  assert.equal(shortfall.availableCredits, 2);
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

  assert.ok(mannequinGenerationCreditShortfall({ credits: 1 }));
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
