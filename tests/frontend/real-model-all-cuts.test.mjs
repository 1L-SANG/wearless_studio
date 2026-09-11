import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  REAL_FACE_CUT_TYPES,
  realFaceAllowedCut,
  realFaceCutCount,
  realModelFeeLabel,
} from '../../src/features/analysis/modelSelection.js';

// 실제 모델을 고르면 모든 착용 컷에 그 얼굴이 들어간다(2026-09-11 사용자 결정).
// 서버(facemarket.REAL_IDENTITY_CUT_TYPES)와 같은 규칙이어야 한다 — 프런트가 더 막으면
// 셀러가 쓸 수 있는 컷을 못 쓰고, 더 열면 서버에서 409 를 만난다.

test('착용 컷과 룩북 인물 교체는 전부 실제 얼굴', () => {
  for (const cut of ['horizon', 'styling', 'mirror', 'base_edit']) {
    assert.equal(realFaceAllowedCut(cut), true, cut);
  }
});

test('사람이 없는 컷은 열리지 않는다', () => {
  assert.equal(realFaceAllowedCut('product'), false);
  assert.equal(realFaceAllowedCut('detail'), false);
  assert.equal(realFaceAllowedCut(undefined), false);
});

test('모델의 동의 값은 판정에 끼어들지 않는다', () => {
  // 두 번째 인자를 줘도 결과가 달라지지 않는다(옛 시그니처로 돌아가면 여기서 깨진다).
  assert.equal(realFaceAllowedCut('styling', { optLocationCuts: false }), true);
  assert.equal(realFaceAllowedCut('base_edit', {}), true);
  assert.equal(realFaceAllowedCut.length, 1);
});

test('컷 수 = 실제 얼굴이 들어가는 컷', () => {
  const blocks = [
    { cutType: 'horizon' }, { cutType: 'styling' },
    { cutType: 'mirror' }, { cutType: 'product' },
  ];
  assert.equal(realFaceCutCount(blocks), 3);
  assert.equal(realFaceCutCount([]), 0);
  assert.equal(realFaceCutCount(undefined), 0);
});

test('실제 얼굴 컷이 하나라도 있으면 상세페이지 1건 표준가를 표시한다', () => {
  const models = [{ id: 'real-2' }];
  assert.equal(realModelFeeLabel('real-2', models, 1), ' + 실제 모델 ₩14,900');
  assert.equal(realModelFeeLabel('real-2', models, 3), ' + 실제 모델 ₩14,900');
  assert.equal(realModelFeeLabel('real-2', models, 0), '');
  assert.equal(realModelFeeLabel('mA', models, 3), '');     // 가상 모델은 무료
});

test('서버와 같은 컷 목록', () => {
  const source = readFileSync(new URL('../../server/app/facemarket.py', import.meta.url), 'utf8');
  const line = source
    .split('\n')
    .find((row) => row.startsWith('REAL_IDENTITY_CUT_TYPES'));
  assert.ok(line, 'server REAL_IDENTITY_CUT_TYPES not found');
  for (const cut of REAL_FACE_CUT_TYPES) {
    assert.ok(
      line.includes(`"${cut}"`) || source.includes(`_STYLING_CUT_TYPES = frozenset({"styling", "mirror"})`),
      cut,
    );
  }
});

test('스타일링 대역(가상 모델) 개념이 사라졌다', async () => {
  const mod = await import('../../src/features/analysis/modelSelection.js');
  for (const name of ['needsStylingStandIn', 'resolveStylingModelId', 'stylingModelPatchForAnalysis',
    'CONSENT_CUT_TYPES']) {
    assert.equal(mod[name], undefined, name);
  }
});

test('분석 확정은 가상 대역 없이 통과한다', () => {
  const form = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
  // 대역 선택 칸과 그 필수 검사가 모두 사라졌다.
  assert.doesNotMatch(form, /장소·스타일링 컷 모델/);
  assert.doesNotMatch(form, /실제 모델은 스튜디오\(호리존\) 컷에만 나와요/);
  assert.doesNotMatch(form, /needsStylingStandIn/);
  assert.doesNotMatch(form, /장소·스타일링 컷에 쓸 가상 모델을 골라 주세요/);
  // 실제 모델에 남는 필수 입력은 브랜드 유형 하나뿐이다.
  assert.match(form, /실제 모델을 사용할 브랜드 유형을 선택해 주세요\./);
});

test('가상 모델 동작은 그대로다', async () => {
  const { isRealModelSelection, resolveSelectedModelId } = await import(
    '../../src/features/analysis/modelSelection.js');
  assert.equal(isRealModelSelection('mA'), false);
  assert.equal(realModelFeeLabel('mA', [{ id: 'mA' }], 3), '');
  assert.equal(resolveSelectedModelId({
    selectedModelId: 'mA',
    targetGenders: ['women'],
    models: [],
    modelsLoading: false,
    aiModels: [{ id: 'mA', gender: 'women' }, { id: 'mB', gender: 'men' }],
  }), 'mA');
});
