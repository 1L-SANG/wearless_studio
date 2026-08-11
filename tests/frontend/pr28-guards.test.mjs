import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createTrailingPatchScheduler,
  hasPatchFields,
  mergeColorMetadataWithPersistedImages,
  mergeLatestFailedAnalysisPatch,
  mergeProductOwnedAnalysisFields,
  persistAnalysisEdit,
  registerAnalysisEditSave,
  splitAnalysisEditPatch,
  waitForAnalysisEditSave,
} from '../../src/features/product-input/saveRouting.js';
import {
  clearInitialGenerationRequested,
  cutsExistedBeforeInitialGeneration,
  hadInitialGenerationRequest,
  markInitialGenerationRequested,
} from '../../src/features/mannequin/initialGenerationSession.js';
import { shouldAdoptRouteProject } from '../../src/lib/projectRoute.js';
import { poseExampleDirectionCompatible } from '../../src/lib/storyboardTaxonomy.js';

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

test('persistAnalysisEdit routes colors through product save before the analysis compatibility save', async () => {
  const calls = [];
  const colors = [{ id: 'color-1', swatchId: 'black', images: [] }];
  const api = {
    async saveProduct(projectId, patch) {
      calls.push(['product', projectId, patch]);
      return patch;
    },
    async saveAnalysis(projectId, patch) {
      calls.push(['analysis', projectId, patch]);
      return patch;
    },
  };

  await persistAnalysisEdit(api, 'p-colors', { colors });

  assert.deepEqual(calls, [
    ['product', 'p-colors', { colors }],
    ['analysis', 'p-colors', { colors }],
  ]);
});

test('the trailing color save collapses repeated swatches and flushes the latest patch', () => {
  let nextTimerId = 0;
  const timers = new Map();
  const commits = [];
  const scheduler = createTrailingPatchScheduler({
    commit: (patch) => commits.push(patch),
    setTimer: (callback) => {
      nextTimerId += 1;
      timers.set(nextTimerId, callback);
      return nextTimerId;
    },
    clearTimer: (timerId) => timers.delete(timerId),
  });

  scheduler.schedule({ colors: [{ swatchId: 'red' }] });
  scheduler.schedule({ colors: [{ swatchId: 'blue' }] });
  assert.equal(timers.size, 1);
  assert.deepEqual(commits, []);

  [...timers.values()][0]();
  timers.clear();
  assert.deepEqual(commits, [{ colors: [{ swatchId: 'blue' }] }]);

  scheduler.schedule({ colors: [{ swatchId: 'black' }] });
  assert.equal(scheduler.flush(), true);
  assert.equal(timers.size, 0);
  assert.deepEqual(commits.at(-1), { colors: [{ swatchId: 'black' }] });
  assert.equal(scheduler.flush(), false);
});

test('storyboard entry waits only for the same project color save barrier', async () => {
  let release;
  let p1Ready = false;
  const pending = new Promise((resolve) => { release = resolve; });
  registerAnalysisEditSave('p1', pending);

  const p1Wait = waitForAnalysisEditSave('p1').then(() => { p1Ready = true; });
  await Promise.resolve();
  assert.equal(p1Ready, false);
  await waitForAnalysisEditSave('p2');
  assert.equal(p1Ready, false, 'another project must not inherit p1 save latency');

  release();
  await p1Wait;
  assert.equal(p1Ready, true);
});

test('color metadata saves preserve server assets and discard local photo mutations', () => {
  const persisted = [
    { id: 'base', isBase: true, swatchId: 'black', images: [{ id: 'asset-front', slot: 'Front' }] },
    { id: 'extra', isBase: false, swatchId: 'white', images: [{ id: 'asset-extra', slot: 'Front' }] },
  ];
  const edited = [
    {
      id: 'base', isBase: true, swatchId: 'red',
      images: [{ id: 'local-img', src: 'blob:local-only', slot: 'Front' }],
    },
    {
      id: 'new-color', isBase: false, swatchId: 'blue',
      images: [{ id: 'local-new', src: 'blob:new-local-only', slot: 'Front' }],
    },
  ];

  assert.deepEqual(mergeColorMetadataWithPersistedImages(persisted, edited), [
    { id: 'base', isBase: true, swatchId: 'red', images: [{ id: 'asset-front', slot: 'Front' }] },
    { id: 'new-color', isBase: false, swatchId: 'blue', images: [] },
  ]);
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

test('pose example direction gate matches worn directions and mirror recipe', () => {
  assert.equal(poseExampleDirectionCompatible(
    { cutType: 'styling', direction: 'back' },
    { cutType: 'horizon', direction: 'back' },
  ), true);
  assert.equal(poseExampleDirectionCompatible(
    { cutType: 'styling', direction: 'back' },
    { cutType: 'horizon', direction: 'front' },
  ), false);
  assert.equal(poseExampleDirectionCompatible(
    { cutType: 'mirror', direction: 'front' },
    { cutType: 'mirror', direction: null },
  ), true);
  assert.equal(poseExampleDirectionCompatible(
    { cutType: 'styling', direction: 'front' },
    { cutType: 'mirror', direction: null },
  ), false);
});

test('디테일 자동 예시 배정도 예시 라벨로 방향을 결정한다', async () => {
  const { assignGenerationExamples } = await import('../../src/lib/generationExamples.js');
  const catalog = [{
    id: 'ex-auto-bd', cutType: 'product', shot: 'detail', direction: 'back',
    applicableClothingTypes: ['top'], gender: null, variants: ['all'], thumb: 't',
  }];
  const blocks = [{ id: 'b1', source: 'ai', cutType: 'product', shot: 'detail', direction: 'front' }];
  const out = assignGenerationExamples(blocks, {
    catalog, product: { clothingType: 'top' }, gender: 'women',
  });
  assert.equal(out.changed, true, '배정 자체가 안 되면 이 테스트는 아무것도 검증하지 못한다');
  assert.equal(out.blocks[0].exampleId, 'ex-auto-bd');
  assert.equal(out.blocks[0].direction, 'back'); // back 라벨 예시가 배정되면 방향도 back
});
