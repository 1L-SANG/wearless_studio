import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isRealModelSelection,
  realModelFeeLabel,
  resolveSelectedModelId,
} from '../../src/features/analysis/modelSelection.js';
import { AI_MODELS } from '../../src/features/analysis/aiModels.js';
import { modelSection, selectionEffect } from './helpers/analysis-model-entry.mjs';

test('analysis offers only AI models with no real-model tab or brand category', async () => {
  const { nodes, changes } = await modelSection({ selectedModelId: 'mA', targetGenders: ['women'] });
  assert.equal(nodes.some((node) => node.type === 'chips'), false);
  assert.equal(nodes.some((node) => node.props?.className === 'fm-use-category'), false);
  const cards = nodes.filter((node) => node.props?.onClick);
  assert.equal(cards.length, 10);
  for (const card of cards) card.props.onClick();
  assert.deepEqual(changes.map((patch) => patch.selectedModelId), AI_MODELS.filter((m) => m.gender === 'women').map((m) => m.id));
  assert.ok(nodes.some((node) => node.props?.children === '가상 인물 모델이에요 · 라이선스 비용 없이 바로 쓸 수 있어요.'));
});

for (const [gender, expected] of [['women', 'mA'], ['men', 'mB'], ['unisex', 'mA']]) {
  test(`saved real model becomes a basic AI model once for ${gender}`, () => {
    const a = { selectedModelId: '11111111-1111-1111-1111-111111111111', targetGenders: [gender] };
    const patches = [], messages = [];
    const run = selectionEffect(a, (patch) => patches.push(patch), { push: (message) => messages.push(message) });
    run();
    run(); // StrictMode can replay the mount effect before the parent updates.
    assert.deepEqual(patches, [{ selectedModelId: expected }]);
    assert.deepEqual(messages, ['실제 모델은 지금 제공하지 않아 AI 모델로 바꿨어요.']);
    Object.assign(a, patches[0]);
    run();
    assert.equal(patches.length, 1);
    assert.equal(messages.length, 1);
  });
}

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
  assert.equal(isRealModelSelection('mF'), false);
  assert.equal(isRealModelSelection('face-market-model-id'), true);
  assert.equal(isRealModelSelection(null), false);
});

// 카탈로그에 있는 가상모델은 하나도 빠짐없이 무료로 판정돼야 한다. 2026-08-17 에
// mF~mN 9인을 그리드에만 넣고 무료 집합에 안 넣어, 선택하면 '+ 실제 모델 이용료 별도'
// 라는 없는 요금이 CTA 에 붙었다. 목록 전체를 훑어 그 사고가 다시 나면 여기서 깨진다.
test('every catalog AI model is free — no fabricated licensing surcharge', () => {
  assert.equal(AI_MODELS.length, 13);
  assert.equal(AI_MODELS.some(({ id }) => id === 'mE'), false);
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

test('formats the fixed FaceMarket price regardless of the catalog unit price', () => {
  const models = [{ id: 'face-market-model-id', unitPrice: 7300 }];
  assert.equal(
    realModelFeeLabel('face-market-model-id', models),
    ' + 실제 모델 ₩14,900',
  );
  assert.equal(
    realModelFeeLabel('face-market-model-id', models, 3),
    ' + 실제 모델 ₩14,900',
  );
  assert.equal(realModelFeeLabel('face-market-model-id', models, 0), '');
  assert.equal(realModelFeeLabel('missing-model-id', models), ' + 실제 모델 이용료 별도');
  assert.equal(
    realModelFeeLabel('price-pending', [{ id: 'price-pending', unitPrice: null }]),
    ' + 실제 모델 ₩14,900',
  );
  assert.equal(realModelFeeLabel('mA', models), '');
});

test('unavailable virtual model selection falls back to an available model without a real-model fee', () => {
  for (const selectedModelId of ['mE', 'mZ']) {
    assert.equal(resolveSelectedModelId({
      selectedModelId, targetGenders: ['women'], models: [],
      modelsLoading: false, aiModels: AI_MODELS,
    }), 'mA');
    assert.equal(isRealModelSelection(selectedModelId), false);
    assert.equal(realModelFeeLabel(selectedModelId, []), '');
  }
});
