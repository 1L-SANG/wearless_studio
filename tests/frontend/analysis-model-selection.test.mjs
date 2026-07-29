import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveSelectedModelId } from '../../src/features/analysis/modelSelection.js';

const aiModels = [
  { id: 'mA', gender: 'women' },
  { id: 'mB', gender: 'men' },
];

test('keeps a saved real model while the catalog is still loading', () => {
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'face-market-model-id',
    targetGenders: ['women'],
    models: [],
    modelsLoading: true,
    aiModels,
  }), 'face-market-model-id');
});

test('validates the saved real model after the catalog finishes loading', () => {
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'face-market-model-id',
    targetGenders: ['women'],
    models: [{ id: 'face-market-model-id', hasActiveLicense: true }],
    modelsLoading: false,
    aiModels,
  }), 'face-market-model-id');

  assert.equal(resolveSelectedModelId({
    selectedModelId: 'expired-model-id',
    targetGenders: ['men'],
    models: [],
    modelsLoading: false,
    aiModels,
  }), 'mB');
});
