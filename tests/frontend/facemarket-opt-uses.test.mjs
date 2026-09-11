import test from 'node:test';
import assert from 'node:assert/strict';

import {
  needsStylingStandIn,
  realFaceAllowedCut,
  realFaceCutCount,
  realModelFeeLabel,
} from '../../src/features/analysis/modelSelection.js';

// 모델의 선택 동의(계약 v2 초안 제3조 2항). 서버 게이트와 같은 규칙이어야 한다 —
// 프런트가 더 열어 주면 셀러가 409 를 만나고, 더 막으면 동의한 모델이 안 팔린다.
const NO_CONSENT = { id: 'real-1' };
const LOCATION = { id: 'real-2', optLocationCuts: true };
const LOOKBOOK = { id: 'real-3', optLookbookPersonReplace: true };

test('스튜디오 컷은 동의와 무관하게 실제 얼굴', () => {
  assert.equal(realFaceAllowedCut('horizon', NO_CONSENT), true);
  assert.equal(realFaceAllowedCut('horizon', undefined), true);
});

test('장소 컷은 동의한 모델만', () => {
  for (const cut of ['styling', 'mirror']) {
    assert.equal(realFaceAllowedCut(cut, NO_CONSENT), false);
    assert.equal(realFaceAllowedCut(cut, LOCATION), true);
    assert.equal(realFaceAllowedCut(cut, LOOKBOOK), false);
  }
});

test('룩북 인물 교체는 그 항목에 동의한 모델만', () => {
  assert.equal(realFaceAllowedCut('base_edit', NO_CONSENT), false);
  assert.equal(realFaceAllowedCut('base_edit', LOCATION), false);
  assert.equal(realFaceAllowedCut('base_edit', LOOKBOOK), true);
});

test('사람이 없는 컷은 열리지 않는다', () => {
  assert.equal(realFaceAllowedCut('product', LOCATION), false);
  assert.equal(realFaceAllowedCut('detail', LOOKBOOK), false);
});

test('컷 수 = 실제 얼굴이 들어가는 컷', () => {
  const blocks = [
    { cutType: 'horizon' }, { cutType: 'styling' },
    { cutType: 'mirror' }, { cutType: 'product' },
  ];
  assert.equal(realFaceCutCount(blocks, NO_CONSENT), 1);   // 예전과 같음
  assert.equal(realFaceCutCount(blocks, LOCATION), 3);
  assert.equal(realFaceCutCount(blocks, undefined), 1);
});

test('실제 얼굴 컷이 하나라도 있으면 상세페이지 1건 표준가를 표시한다', () => {
  const models = [{ id: 'real-2' }];
  assert.equal(realModelFeeLabel('real-2', models, 1), ' + 실제 모델 ₩14,900');
  assert.equal(realModelFeeLabel('real-2', models, 3), ' + 실제 모델 ₩14,900');
  assert.equal(realModelFeeLabel('real-2', models, 0), '');
  assert.equal(realModelFeeLabel('mA', models, 3), '');     // 가상 모델은 무료
});

test('스타일링 대역은 동의하지 않은 모델에만 필요하다', () => {
  assert.equal(needsStylingStandIn('real-1', NO_CONSENT), true);
  assert.equal(needsStylingStandIn('real-2', LOCATION), false);
  assert.equal(needsStylingStandIn('mA', undefined), false);   // 가상 모델은 원래 불필요
  assert.equal(needsStylingStandIn('real-1', undefined), true);  // 카탈로그 로딩 중이면 안전하게 요구
});
