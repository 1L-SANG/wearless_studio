import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const adapter = readFileSync(
  new URL('../../src/lib/api/httpAdapter.js', import.meta.url),
  'utf8',
);
const analysisForm = readFileSync(
  new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url),
  'utf8',
);
const productInput = readFileSync(
  new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url),
  'utf8',
);

test('analysis keeps structural counts returned by the product analyst', () => {
  assert.match(adapter, /buttonCount:\s*ai\.buttonCount \?\? null/);
  assert.match(adapter, /pocketCount:\s*ai\.pocketCount \?\? null/);
});

test('saved analysis without matching items hydrates the catalog and exposes failures', () => {
  assert.match(adapter, /async getAnalysis\(projectId\)/);
  assert.match(adapter, /!\(saved\.matchClothing \|\| \[\]\)\.length/);
  assert.match(adapter, /saved\.matchClothing = await recommendMatchHttp\(projectId, saved, \[\]\)/);
  assert.match(adapter, /saved\.matchClothingLoadFailed = true/);
  assert.match(analysisForm, /매칭 의류 다시 불러오기/);
  assert.match(analysisForm, /onChange\(\{ styleTags: \[\.\.\.\(a\.styleTags \|\| \[\]\)\] \}\)/);
});

test('seller must save corrected button and pocket facts before approval', () => {
  assert.match(productInput, /editTruthCount\('buttonCount', e\.target\.value, 30\)/);
  assert.match(productInput, /editTruthCount\('pocketCount', e\.target\.value, 12\)/);
  assert.match(productInput, /garmentSpec:\s*productTruth\.garmentSpec \|\| \{\}/);
  assert.match(productInput, /patternSpec,/);
  assert.match(productInput, /pattern:\s*patternProtectionForSpec\(patternSpec\)/);
  assert.match(productInput, /disabled=\{truthBusy \|\| truthDirty \|\|/);
  assert.match(productInput, /수정 저장 후 승인/);
});

test('seller can correct Product Truth pattern before approval', () => {
  assert.match(productInput, /const TRUTH_PATTERN_TYPES = \['UNKNOWN', 'SOLID', 'STRIPE', 'CHECK', 'PLAID', 'PRINT', 'OTHER'\]/);
  assert.match(productInput, /ALWAYS_FINE_PATTERN_TYPES = new Set\(\['UNKNOWN', 'STRIPE', 'CHECK', 'PLAID'\]\)/);
  assert.match(productInput, /NEVER_FINE_PATTERN_TYPES = new Set\(\['SOLID'\]\)/);
  assert.match(productInput, /editTruthPatternType\(e\.target\.value\)/);
  assert.match(productInput, /editTruthFinePattern\(e\.target\.checked\)/);
  assert.match(productInput, /패턴이 미확인 상태라 패턴 보호를 보수적으로 켭니다/);
});

test('existing project hydration restores Product Truth without starting analysis', () => {
  const hydrationStart = productInput.indexOf('// cold input 은 라우트 계층이 stale flow 를 먼저 비운다.');
  const hydrationEnd = productInput.indexOf('}, [loadAttempt]);', hydrationStart);
  assert.notEqual(hydrationStart, -1);
  assert.notEqual(hydrationEnd, -1);

  const hydration = productInput.slice(hydrationStart, hydrationEnd);
  assert.match(
    hydration,
    /editingProjectId && api\.getProductTruth\s*\?\s*api\.getProductTruth\(editingProjectId\)\.catch\(\(error\) =>/,
  );
  assert.match(hydration, /if \(error\?\.status === 404\) return null/);
  assert.match(hydration, /throw error/);
  assert.match(hydration, /setProductTruth\(existingProductTruth \|\| null\)/);
  assert.doesNotMatch(hydration, /api\.analyzeProduct/);
});
