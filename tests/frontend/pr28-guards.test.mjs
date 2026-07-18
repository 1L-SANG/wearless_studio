import test from 'node:test';
import assert from 'node:assert/strict';

import {
  hasPatchFields,
  mergeLatestFailedAnalysisPatch,
  mergeProductOwnedAnalysisFields,
  persistAnalysisEdit,
  splitAnalysisEditPatch,
} from '../../src/features/product-input/saveRouting.js';
import {
  clearInitialGenerationRequested,
  cutsExistedBeforeInitialGeneration,
  hadInitialGenerationRequest,
  markInitialGenerationRequested,
} from '../../src/features/mannequin/initialGenerationSession.js';
import { shouldAdoptRouteProject } from '../../src/lib/projectRoute.js';

test('splitAnalysisEditPatch routes product-owned fields away from saveAnalysis', () => {
  const { productPatch, analysisPatch } = splitAnalysisEditPatch({
    clothingType: 'bottom',
    measurements: [{ key: 'waist', value: 32 }],
    measurementsUnknown: false,
    uploadComplete: null,
    subCategory: 'jeans',
    fit: 'regular',
  });

  assert.deepEqual(productPatch, {
    clothingType: 'bottom',
    measurements: [{ key: 'waist', value: 32 }],
    measurementsUnknown: false,
  });
  assert.deepEqual(analysisPatch, {
    subCategory: 'jeans',
    fit: 'regular',
  });
});

test('splitAnalysisEditPatch skips ProductPatch fields that reject explicit null', () => {
  const { productPatch, analysisPatch } = splitAnalysisEditPatch({
    clothingType: null,
    measurements: null,
    measurementsUnknown: null,
    fit: 'slim',
  });

  assert.deepEqual(productPatch, { clothingType: null });
  assert.deepEqual(analysisPatch, { fit: 'slim' });
});

test('persistAnalysisEdit saves the product source of truth before the analysis compatibility shape', async () => {
  const calls = [];
  const api = {
    async saveProduct(projectId, patch) {
      calls.push(['product', projectId, patch]);
      return { ...patch };
    },
    async saveAnalysis(projectId, patch) {
      calls.push(['analysis', projectId, patch]);
      return { ...patch, matchClothing: ['fresh'] };
    },
  };

  const saved = await persistAnalysisEdit(api, 'p1', {
    clothingType: 'dress',
    subCategory: 'mini',
  });

  assert.deepEqual(calls, [
    ['product', 'p1', { clothingType: 'dress' }],
    ['analysis', 'p1', { clothingType: 'dress', subCategory: 'mini' }],
  ]);
  assert.deepEqual(saved.analysis.matchClothing, ['fresh']);
});

test('persistAnalysisEdit keeps anonymous mock analysis updates intact', async () => {
  const calls = [];
  const api = {
    async saveProduct() { throw new Error('anonymous edits must not call saveProduct'); },
    async saveAnalysis(projectId, patch) {
      calls.push([projectId, patch]);
      return patch;
    },
  };

  await persistAnalysisEdit(api, null, { clothingType: 'dress', subCategory: 'mini' });
  assert.deepEqual(calls, [[null, { clothingType: 'dress', subCategory: 'mini' }]]);
});

test('persistAnalysisEdit rejects before analysis when the product source of truth fails', async () => {
  let analysisCalled = false;
  const api = {
    async saveProduct() { throw new Error('product save failed'); },
    async saveAnalysis() { analysisCalled = true; },
  };

  await assert.rejects(
    persistAnalysisEdit(api, 'p1', { clothingType: 'dress', subCategory: 'mini' }),
    /product save failed/,
  );
  assert.equal(analysisCalled, false);
});

test('mergeLatestFailedAnalysisPatch retries the newest value after an older queued save fails', () => {
  assert.deepEqual(
    mergeLatestFailedAnalysisPatch(
      { clothingType: 'outer', fit: 'regular' },
      { clothingType: 'dress' },
      { clothingType: 'top', targetGenders: ['women'] },
    ),
    { clothingType: 'top', fit: 'regular', targetGenders: ['women'] },
  );
});

test('mergeProductOwnedAnalysisFields uses product as the display source of truth', () => {
  assert.deepEqual(
    mergeProductOwnedAnalysisFields(
      { clothingType: 'top', measurements: [], measurementsUnknown: false, fit: 'over' },
      { clothingType: 'outer', measurements: [{ key: 'length', value: 80 }], measurementsUnknown: true },
    ),
    { clothingType: 'outer', measurements: [{ key: 'length', value: 80 }], measurementsUnknown: true, fit: 'over' },
  );
});

test('initial generation session flag prevents recovered first cuts from being treated as pre-existing', () => {
  const store = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => { store.set(key, String(value)); },
    removeItem: (key) => { store.delete(key); },
  };

  markInitialGenerationRequested('p1');
  assert.equal(hadInitialGenerationRequest('p1'), true);
  assert.equal(cutsExistedBeforeInitialGeneration('p1', [{ id: 'cut1' }]), false);

  clearInitialGenerationRequested('p1');
  assert.equal(hadInitialGenerationRequest('p1'), false);
  assert.equal(cutsExistedBeforeInitialGeneration('p1', [{ id: 'cut1' }]), true);
});

test('editor route project id is adopted when it differs from store', () => {
  assert.equal(shouldAdoptRouteProject(null, 'p2'), true);
  assert.equal(shouldAdoptRouteProject('p1', 'p2'), true);
  assert.equal(shouldAdoptRouteProject('p2', 'p2'), false);
  assert.equal(shouldAdoptRouteProject('p2', ''), false);
});

test('hasPatchFields is false only for empty or missing patches', () => {
  assert.equal(hasPatchFields(null), false);
  assert.equal(hasPatchFields({}), false);
  assert.equal(hasPatchFields({ clothingType: null }), true);
});
