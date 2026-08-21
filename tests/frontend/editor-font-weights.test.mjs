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

// ---- Codex 리뷰 반영 (2026-08-21) ----
import { cssWeight, supportedWeights as _sw } from '../../src/features/editor/fontWeights.js';
import { activeTextPreset, quickStylePatch } from '../../src/features/editor/presets/textPresets.js';
import { buildFrameBlock } from '../../src/features/editor/editorLibrary.js';

test('cssWeight: Cal Sans 는 저장값 600 을 브라우저가 실제로 가진 face 400 으로 그린다', () => {
  assert.equal(cssWeight('Cal Sans', 600), 400);
  assert.equal(cssWeight('Cal Sans', 700), 400);      // 옛 문서의 어떤 값이든 face 는 하나
  assert.equal(cssWeight('Cal Sans', undefined), 400);
});

test('cssWeight: 못 주는 굵기(옛 문서 900 등)는 가장 가까운 지원 굵기로 그린다', () => {
  assert.equal(cssWeight('Roboto Mono', 900), 600);
  assert.equal(cssWeight('Roboto Mono', 700), 600);
  assert.equal(cssWeight('Gowun Dodum', 700), 400);
  assert.equal(cssWeight('Pretendard', 700), 700);     // 가변 폰트는 그대로
  assert.equal(cssWeight(undefined, undefined), 400);
});

test('빠른 스타일: 단일 굵기 폰트에 적용해도 활성 칩이 켜진다', () => {
  // 패널이 하는 일 그대로: 프리셋 패치의 weight 를 현재 폰트가 줄 수 있는 값으로 붙인다
  for (const font of ['Gowun Dodum', 'Cal Sans', 'Roboto Mono']) {
    for (const key of ['headline', 'subtitle', 'body']) {
      const patch = quickStylePatch(key);
      const style = { font, ...patch, weight: nearestWeight(font, patch.weight) };
      assert.equal(activeTextPreset(style), key, `${font} / ${key}`);
    }
  }
});

test('내장 프레임 템플릿의 모든 텍스트 굵기는 그 폰트가 실제로 주는 값이다', () => {
  let seq = 0; const idFn = () => `t${seq++}`;
  const ids = ['kiwi-17'];
  for (const id of ids) {
    const block = buildFrameBlock(id, idFn);
    for (const el of block.elements.filter((e) => e.type === 'text')) {
      const font = el.style.font || 'Pretendard';
      assert.ok(_sw(font).includes(el.style.weight),
        `${id}: "${el.text}" ${font} ${el.style.weight} 은 지원 굵기 ${_sw(font).join('/')} 밖`);
    }
  }
});
