import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ALL_WEIGHTS, supportedWeights, weightOptions, nearestWeight, fontChangePatch, boldToggle, isBold,
} from '../../src/features/editor/fontWeights.js';

test('가변 폰트는 5단계 전부, 단일 굵기 폰트는 하나만 보여준다', () => {
  assert.deepEqual(weightOptions('Pretendard').map((w) => w.value), [300, 400, 500, 600, 700]);
  assert.deepEqual(weightOptions('Cormorant').map((w) => w.value), [300, 400, 500, 600, 700]);
  assert.deepEqual(weightOptions('Gowun Dodum').map((w) => w.value), [400]);
  assert.deepEqual(weightOptions('Cal Sans').map((w) => w.value), [600]);
  assert.deepEqual(weightOptions('Roboto Mono').map((w) => w.value), [500, 600]);
});

test('표에 없는 폰트·빈 폰트는 전체 굵기로 폴백한다(가변으로 간주)', () => {
  assert.deepEqual(supportedWeights('Unknown Font'), ALL_WEIGHTS.map((w) => w.value));
  assert.deepEqual(supportedWeights(undefined), [300, 400, 500, 600, 700]); // DEFAULT_FONT=Pretendard
});

test('nearestWeight 는 가장 가까운 지원 굵기로 붙인다', () => {
  assert.equal(nearestWeight('Gowun Dodum', 700), 400);
  assert.equal(nearestWeight('Cal Sans', 300), 600);
  assert.equal(nearestWeight('Roboto Mono', 300), 500);
  assert.equal(nearestWeight('Roboto Mono', 700), 600);
  assert.equal(nearestWeight('Pretendard', 600), 600);
  assert.equal(nearestWeight('Pretendard', undefined), 400);
});

test('폰트를 바꿀 때 못 주는 굵기는 함께 바꾸고, 줄 수 있으면 weight 키를 넣지 않는다', () => {
  assert.deepEqual(fontChangePatch({ weight: 700 }, 'Gowun Dodum'), { font: 'Gowun Dodum', weight: 400 });
  assert.deepEqual(fontChangePatch({ weight: 600 }, 'Cal Sans'), { font: 'Cal Sans' });
  assert.deepEqual(fontChangePatch({ weight: 400 }, 'Roboto Mono'), { font: 'Roboto Mono', weight: 500 });
  assert.deepEqual(fontChangePatch({}, 'Pretendard'), { font: 'Pretendard' });
  assert.deepEqual(fontChangePatch(null, 'Gowun Dodum'), { font: 'Gowun Dodum' });
});

test('볼드 토글은 폰트가 두 굵기 이상 줄 때만 살아 있다', () => {
  assert.deepEqual(boldToggle('Pretendard'), { regular: 400, bold: 700 });
  assert.deepEqual(boldToggle('Roboto Mono'), { regular: 500, bold: 600 });
  assert.equal(boldToggle('Gowun Dodum'), null);
  assert.equal(boldToggle('Cal Sans'), null);
});

test('isBold 는 그 폰트의 가장 굵은 값일 때만 켜진다', () => {
  assert.equal(isBold('Pretendard', 700), true);
  assert.equal(isBold('Pretendard', 600), false);
  assert.equal(isBold('Roboto Mono', 600), true);
  assert.equal(isBold('Gowun Dodum', 700), false);   // 토글 없음 → 절대 bold 아님
  assert.equal(isBold('Cal Sans', 600), false);
});
