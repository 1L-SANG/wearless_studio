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
  assert.match(productInput, /protectedDetails:\s*productTruth\.protectedDetails \|\| \{\}/);
  assert.match(productInput, /disabled=\{truthBusy \|\| truthDirty \|\|/);
  assert.match(productInput, /수정 저장 후 승인/);
});
