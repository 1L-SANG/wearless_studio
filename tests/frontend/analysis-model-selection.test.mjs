import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isRealModelSelection,
  resolveSelectedModelId,
} from '../../src/features/analysis/modelSelection.js';

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

test('moves the AI model selection when the gender chip changes', () => {
  // 성별 칩이 남성이면 여성 AI 모델은 그리드에서 사라진다 → 남성 첫 모델로 이동
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'mA',
    targetGenders: ['men'],
    models: [],
    modelsLoading: false,
    aiModels,
  }), 'mB');
});

test('keeps the AI model selection when the gender still matches', () => {
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'mB',
    targetGenders: ['men'],
    models: [],
    modelsLoading: false,
    aiModels,
  }), 'mB');
});

test('keeps a licensed real model even when the gender chip changes', () => {
  // 실제 모델은 성별 칩으로 거르지 않는다(서버 카드에 성별 정보가 없음) — 선택 유지
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'face-market-model-id',
    targetGenders: ['men'],
    models: [{ id: 'face-market-model-id', hasActiveLicense: true }],
    modelsLoading: false,
    aiModels,
  }), 'face-market-model-id');
});

test('keeps the AI model when no gender chip is selected', () => {
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'mA',
    targetGenders: [],
    models: [],
    modelsLoading: false,
    aiModels,
  }), 'mA');
});

test('identifies only FaceMarket selections for the mannequin KRW surcharge label', () => {
  assert.equal(isRealModelSelection('mA'), false);
  assert.equal(isRealModelSelection('mE'), false);
  assert.equal(isRealModelSelection('face-market-model-id'), true);
  assert.equal(isRealModelSelection(null), false);
});
