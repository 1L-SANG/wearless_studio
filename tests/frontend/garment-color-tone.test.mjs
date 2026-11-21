import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  clampSlider, createTonePreview, normalizeSliders, opaqueMedianSrgb8, srgb8ToLinear, toneLinear, toneSrgb8,
} from '../../src/lib/garmentColorTone.js';

const fixtureUrl = new URL('../../server/tests/fixtures/garment_color_tone_js_reference.json', import.meta.url);

test('색 연산이 서버 대조 예시와 같다(명세 §2)', async (t) => {
  let fixture;
  try { fixture = JSON.parse(await readFile(fixtureUrl, 'utf8')); } catch { t.skip('서버 대조 예시가 아직 이 브랜치에 없다'); return; }
  for (const item of fixture.cases) {
    const out = toneLinear(...item.linear, item.sliders);
    out.forEach((value, index) => assert.ok(Math.abs(value - item.out[index]) < 1e-12, JSON.stringify(item)));
  }
});

test('슬라이더 값은 정수 -100~100으로 맞추고 작은 값도 지킨다(0 붙이기는 끌 때만 화면이 한다)', () => {
  assert.equal(clampSlider(2.6), 3);
  assert.equal(clampSlider(-1), -1);
  assert.equal(clampSlider(4), 4);
  assert.equal(clampSlider(240), 100);
  assert.equal(clampSlider('x'), 0);
  assert.deepEqual(normalizeSliders({ chroma: 12.4, warmth: -180 }), { chroma: 12, lightness: 0, warmth: -100 });
});

test('같은 값을 걸어도 밝은 곳과 그늘의 밝기 비율이 남는다', () => {
  const sliders = { chroma: 0, lightness: -60, warmth: 40 };
  const lit = toneLinear(0.6, 0.6, 0.6, sliders);
  const shade = toneLinear(0.15, 0.15, 0.15, sliders);
  lit.forEach((value, index) => assert.ok(Math.abs(value / shade[index] - 4) < 1e-9));
});

test('미리보기는 투명 픽셀을 건드리지 않고 불투명 픽셀만 바꾼다', () => {
  const data = new Uint8ClampedArray([200, 190, 170, 255, 10, 20, 30, 0]);
  const preview = createTonePreview({ data, width: 2, height: 1 });
  const out = { data: new Uint8ClampedArray(8) };
  preview.render({ chroma: 0, lightness: 50, warmth: 0 }, out);
  assert.deepEqual([...out.data.slice(4)], [10, 20, 30, 0]);
  const expected = toneSrgb8([200, 190, 170], { lightness: 50 }).map(Math.round);
  [...out.data.slice(0, 3)].forEach((value, index) => assert.ok(Math.abs(value - expected[index]) <= 1));
  assert.equal(out.data[3], 255);
});

test('원본 색은 알파 200 이상 픽셀의 채널별 중앙값이다', () => {
  const data = new Uint8ClampedArray([
    10, 10, 10, 255, 20, 30, 40, 255, 30, 50, 70, 255, 250, 250, 250, 100,
  ]);
  assert.deepEqual(opaqueMedianSrgb8({ data, width: 4, height: 1 }), [20, 30, 40]);
  assert.equal(opaqueMedianSrgb8({ data: new Uint8ClampedArray([1, 2, 3, 0]), width: 1, height: 1 }), null);
  assert.equal(srgb8ToLinear(255), 1);
});
