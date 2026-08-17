import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isRealModelSelection,
  realModelFeeLabel,
  resolveSelectedModelId,
} from '../../src/features/analysis/modelSelection.js';
import { AI_MODELS } from '../../src/features/analysis/aiModels.js';

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

// 카탈로그에 있는 가상모델은 하나도 빠짐없이 무료로 판정돼야 한다. 2026-08-17 에
// mF~mN 9인을 그리드에만 넣고 무료 집합에 안 넣어, 선택하면 '+ 실제 모델 이용료 별도'
// 라는 없는 요금이 CTA 에 붙었다. 목록 전체를 훑어 그 사고가 다시 나면 여기서 깨진다.
test('every catalog AI model is free — no fabricated licensing surcharge', () => {
  assert.ok(AI_MODELS.length >= 14, 'catalog shrank unexpectedly');
  for (const model of AI_MODELS) {
    assert.equal(isRealModelSelection(model.id), false, `${model.id} misread as a real model`);
    assert.equal(realModelFeeLabel(model.id, []), '', `${model.id} shows a fee label`);
  }
});

test('catalog rows are well formed and unique', () => {
  const ids = new Set();
  const thumbs = new Set();
  for (const { id, displayName, gender, thumb } of AI_MODELS) {
    assert.match(id, /^m[A-Z]$/, `bad id ${id}`);
    assert.ok(displayName && displayName.trim(), `${id} has no displayName`);
    assert.ok(gender === 'women' || gender === 'men', `${id} has bad gender ${gender}`);
    assert.equal(thumb, `/models/${gender}/${thumb.split('/').pop()}`, `${id} thumb/gender mismatch`);
    assert.ok(!ids.has(id), `duplicate id ${id}`);
    assert.ok(!thumbs.has(thumb), `duplicate thumb ${thumb} on ${id}`);
    ids.add(id);
    thumbs.add(thumb);
  }
});

test('formats the selected FaceMarket catalog unit price and falls back when unknown', () => {
  const models = [{ id: 'face-market-model-id', unitPrice: 7300 }];
  assert.equal(
    realModelFeeLabel('face-market-model-id', models),
    ' + 실제 모델 ₩7,300',
  );
  assert.equal(realModelFeeLabel('missing-model-id', models), ' + 실제 모델 이용료 별도');
  assert.equal(
    realModelFeeLabel('price-pending', [{ id: 'price-pending', unitPrice: null }]),
    ' + 실제 모델 이용료 별도',
  );
  assert.equal(realModelFeeLabel('mA', models), '');
});
