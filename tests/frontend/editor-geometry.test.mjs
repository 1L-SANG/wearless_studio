import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clampDragDelta,
  clampElementRect,
  expandBlockHeights,
  getBlockRenderHeight,
  pointMissesTextLines,
} from '../../src/features/editor/editorGeometry.js';

test('editor geometry keeps derived heights and selections inside the canvas', () => {
  assert.equal(getBlockRenderHeight({ h: 660, elements: [{ y: 640, h: 120 }] }), 810);
  assert.equal(expandBlockHeights([{ h: 660, elements: [{ y: 640, h: 120 }] }])[0].h, 810);

  const group = {
    a: { x: 20, y: 10, w: 100 },
    b: { x: 300, y: 40, w: 200 },
  };
  assert.deepEqual(clampDragDelta(group, [-50, -30]), [-20, -10]);
  assert.deepEqual(clampDragDelta(group, [600, 0]), [500, 0]);

  assert.deepEqual(clampElementRect(-10, -20, 110, 120), { x: 0, y: 0, w: 100, h: 100 });
  assert.deepEqual(clampElementRect(950, 20, 100, 40), { x: 950, y: 20, w: 50, h: 40 });
});

test('pointMissesTextLines treats the empty width beside a short line as not-the-text', () => {
  // 880 폭 상자 안에 실제로는 260px 짜리 한 줄만 그려진 경우
  const line = [{ left: 60, right: 320, top: 700, bottom: 728 }];
  assert.equal(pointMissesTextLines(line, 180, 714), false, '글자 위 → 텍스트가 받는다');
  assert.equal(pointMissesTextLines(line, 600, 714), true, '줄 오른쪽 흰 공간 → 블록이 받는다');
  assert.equal(pointMissesTextLines(line, 180, 760), true, '줄 아래 → 블록이 받는다');
});

test('pointMissesTextLines checks every line of a wrapped paragraph', () => {
  const wrapped = [
    { left: 60, right: 900, top: 700, bottom: 728 },
    { left: 60, right: 400, top: 732, bottom: 760 },
  ];
  assert.equal(pointMissesTextLines(wrapped, 800, 714), false, '첫 줄은 끝까지 글자다');
  assert.equal(pointMissesTextLines(wrapped, 800, 746), true, '둘째 줄은 짧아 그 오른쪽은 빈 곳');
});

test('pointMissesTextLines forgives a couple of pixels at the glyph edge', () => {
  const line = [{ left: 60, right: 320, top: 700, bottom: 728 }];
  assert.equal(pointMissesTextLines(line, 321, 714), false, '경계 1px 밖은 아직 글자로 친다');
  assert.equal(pointMissesTextLines(line, 340, 714), true, '충분히 벗어나면 빈 곳');
});

test('pointMissesTextLines leaves an empty text element clickable across its box', () => {
  assert.equal(pointMissesTextLines([], 500, 700), false);
  assert.equal(pointMissesTextLines(undefined, 500, 700), false);
});
