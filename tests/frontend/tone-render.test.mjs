import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EXPOSURE_RANGE,
  SATURATION_RANGE,
  applyTone,
  clampExposure,
  clampSaturation,
  isNeutral,
  maskAlphaFrom,
  toneParams,
} from '../../src/lib/toneRender.js';

/* 합성 픽셀로 계약을 못 박는다. 실제 마네킹컷으로는 "옷 밖이 안 변했다"를 눈으로만 확인할 수
   있는데, 이 기능의 약속은 눈이 아니라 바이트 단위다. */

const rgba = (pixels) => Uint8ClampedArray.from(pixels.flat());
const run = (src, mask, sat, exp) => {
  const out = new Uint8ClampedArray(src.length);
  applyTone(src, Uint8Array.from(mask), out, sat, exp);
  return out;
};

test('마스크 밖 픽셀은 바이트 단위로 원본과 같다', () => {
  const src = rgba([[200, 40, 40, 255], [10, 120, 200, 255], [250, 250, 250, 255]]);
  const out = run(src, [255, 0, 0], 30, 20);
  assert.deepEqual([...out.slice(4)], [...src.slice(4)], '마스크가 0인 두 픽셀은 불변');
  assert.notDeepEqual([...out.slice(0, 3)], [...src.slice(0, 3)], '마스크 안은 바뀐다');
});

test('조정 0이면 전체가 원본과 완전히 같다', () => {
  const src = rgba([[13, 200, 77, 255], [255, 0, 0, 255], [0, 0, 0, 255], [128, 128, 128, 255]]);
  const out = run(src, [255, 255, 128, 0], 0, 0);
  assert.deepEqual([...out], [...src]);
});

test('색감을 올리면 채도만 커지고 색상은 그대로다', () => {
  const src = rgba([[180, 60, 60, 255]]);
  const up = run(src, [255], 20, 0);
  const down = run(src, [255], -20, 0);
  const chroma = (p) => Math.max(p[0], p[1], p[2]) - Math.min(p[0], p[1], p[2]);
  assert.ok(chroma(up) > chroma(src), '진하게 = 채도 증가');
  assert.ok(chroma(down) < chroma(src), '연하게 = 채도 감소');
  // 색상 순서(R > G ≈ B)가 유지되어야 빨강이 빨강으로 남는다.
  assert.ok(up[0] > up[1] && up[0] > up[2]);
  assert.ok(Math.abs(up[1] - up[2]) <= 2, '중립 축 둘레 보간이라 G·B 균형이 유지된다');
});

test('색감 -100은 완전 흑백이다 (포토샵 관례)', () => {
  const src = rgba([[200, 40, 40, 255]]);
  const out = run(src, [255], -100, 0);
  assert.ok(Math.abs(out[0] - out[1]) <= 1 && Math.abs(out[1] - out[2]) <= 1,
    '채널이 같아야 무채색이다');
});

test('무채색 픽셀은 색감 슬라이더에 반응하지 않는다', () => {
  const src = rgba([[128, 128, 128, 255]]);
  assert.deepEqual([...run(src, [255], 30, 0)], [...src]);
  assert.deepEqual([...run(src, [255], -30, 0)], [...src]);
});

test('밝기는 방향대로만 움직인다', () => {
  const src = rgba([[100, 100, 100, 255]]);
  assert.ok(run(src, [255], 0, 15)[0] > 100);
  assert.ok(run(src, [255], 0, -15)[0] < 100);
});

test('알파 채널은 절대 바뀌지 않는다', () => {
  const src = rgba([[10, 20, 30, 200]]);
  assert.equal(run(src, [255], 30, 20)[3], 200);
});

test('슬라이더 범위는 엄격히 잘린다', () => {
  assert.equal(clampSaturation(999), SATURATION_RANGE);
  assert.equal(clampExposure(-999), -EXPOSURE_RANGE);
  assert.equal(clampSaturation('x'), 0);
  assert.equal(toneParams(SATURATION_RANGE, 0).factor.toFixed(2), '2.00');
  assert.equal(toneParams(-SATURATION_RANGE, 0).factor.toFixed(2), '0.00');
  assert.equal(toneParams(0, EXPOSURE_RANGE).ev.toFixed(2), '1.00');
  assert.equal(toneParams(0, -EXPOSURE_RANGE).ev.toFixed(2), '-1.00');
  assert.ok(isNeutral(0, 0) && !isNeutral(1, 0) && !isNeutral(0, -1));
});

test('극단값에서도 색상이 돌지 않는다 (색역 처리)', () => {
  // 이미 포화에 가까운 빨강을 더 진하게 → 채널 클립이면 색상이 주황으로 돈다.
  const src = rgba([[250, 20, 20, 255]]);
  const out = run(src, [255], SATURATION_RANGE, 0);
  assert.ok(out[0] >= out[1] && out[0] >= out[2]);
  assert.ok(Math.abs(out[1] - out[2]) <= 2, 'G·B 가 갈라지면 색상이 돈 것');
  for (const v of out) assert.ok(v >= 0 && v <= 255);
});

test('경계 반투명 알파는 원본과 조정본 사이에 놓인다', () => {
  const src = rgba([[200, 40, 40, 255]]);
  const full = run(src, [255], 20, 0)[0];
  const half = run(src, [128], 20, 0)[0];
  const lo = Math.min(src[0], full), hi = Math.max(src[0], full);
  assert.ok(half >= lo && half <= hi);
});

test('maskAlphaFrom 은 회색조 PNG 의 R 채널을 픽셀당 알파로 뽑는다', () => {
  const imageData = { width: 2, height: 1, data: Uint8ClampedArray.from([255, 255, 255, 255, 0, 0, 0, 255]) };
  assert.deepEqual([...maskAlphaFrom(imageData)], [255, 0]);
});
