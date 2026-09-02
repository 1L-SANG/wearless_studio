import test from 'node:test';
import assert from 'node:assert/strict';

import {
  modulo,
  shortestWrappedOffset,
  snapTarget,
  targetForIndex,
} from '../../src/features/facemarket-landing/carousel/carouselMath.js';
import {
  layoutForOffset,
  metricsForAspect,
} from '../../src/features/facemarket-landing/carousel/sceneLayout.js';

/* 산술 검증용 고정값이다. 기대값(13→0 이 앞으로 1칸 등)이 전부 14 기준 손계산이라
   목록에서 끌어오지 않는다. "랜딩이 정말 14장인가"는 데이터 쪽 계약이라
   facemarket-landing-models.test.mjs 가 LANDING_MODELS 로 못 박는다. */
const COUNT = 14;

test('음수 위치를 카드 수 안으로 정규화한다', () => {
  assert.equal(modulo(-1, COUNT), 13);
  assert.equal(modulo(14, COUNT), 0);
});

test('루프 경계에서 최단 방향을 고른다', () => {
  // 마지막 카드에서 첫 카드는 앞으로 1칸이지 뒤로 13칸이 아니다.
  assert.equal(shortestWrappedOffset(0, 13, COUNT), 1);
  assert.equal(shortestWrappedOffset(13, 0, COUNT), -1);
});

test('연속 목표도 최단 방향으로 잡는다', () => {
  assert.equal(targetForIndex(13.2, 0, COUNT), 14);
  assert.equal(targetForIndex(0.2, 13, COUNT), -1);
});

test('스냅 전에 속도를 반영한다', () => {
  assert.equal(snapTarget(2.2, 1.4), 3);
  assert.equal(snapTarget(2.2, -1.4), 2);
});

/* 원본에 있던 rebaseTarget(누적 위치 되감기) 테스트는 지웠다. 그 함수를 쓰는 코드가
   없는데 테스트만 남으면 "있지도 않은 안전장치가 커버됐다"고 읽힌다.
   왜 안 쓰는지는 carouselMath.js 하단 주석에 적었다. */

test('가운데 카드는 정면이고 원점이다', () => {
  const layout = layoutForOffset(0, metricsForAspect(1.6));
  assert.equal(layout.x, 0);
  assert.equal(layout.z, 0);
  assert.equal(layout.rotationY, 0);
  assert.equal(layout.scale, 1);
});

test('양옆 카드는 가운데를 향해 돌아선다', () => {
  const metrics = metricsForAspect(1.6);
  assert.ok(layoutForOffset(-2, metrics).rotationY > 0);
  assert.ok(layoutForOffset(2, metrics).rotationY < 0);
});

/* 원본 녹화 실측(sceneLayout.js 머리말): offset 1 ≈ 17°(0.30rad), offset 2 ≈ 47°, offset 3 ≈ 66°.
   예전 기대값 '이웃은 거의 정면(< 0.15)'은 코덱스 포트의 납작한 프로파일을 박제한 것이라 지웠다. */
test('이웃부터 눈에 띄게 돌아서고 바깥으로 갈수록 더 돌아선다', () => {
  const metrics = metricsForAspect(3.5);
  const at = (offset) => Math.abs(layoutForOffset(offset, metrics).rotationY);
  assert.ok(at(1) > 0.2 && at(1) < 0.45, `offset 1: ${at(1)}`);
  assert.ok(at(2) > 0.7 && at(2) < 1.0, `offset 2: ${at(2)}`);
  assert.ok(at(3) > 1.0 && at(3) < Math.PI / 2, `offset 3: ${at(3)}`);
  assert.ok(at(1) < at(2) && at(2) < at(3));
});

test('오목 아크 — 바깥으로 갈수록 카메라 쪽으로 나온다', () => {
  const metrics = metricsForAspect(3.5);
  assert.ok(layoutForOffset(2, metrics).z > 0);
  assert.ok(layoutForOffset(3, metrics).z > layoutForOffset(1, metrics).z);
});

test('세로 화면은 간격이 좁고 가로 화면은 넓다', () => {
  assert.ok(metricsForAspect(0.55).spacing < metricsForAspect(3.5).spacing);
  assert.ok(metricsForAspect(3.5).spacing > 2);
});

test('종횡비 구간 경계값이 원본 그대로다', () => {
  assert.equal(metricsForAspect(1.09).cardWidth, 2.05);
  assert.equal(metricsForAspect(1.1).cardWidth, 1.7);
  assert.equal(metricsForAspect(2.29).cardWidth, 1.7);
  assert.equal(metricsForAspect(2.3).cardWidth, 1.64);
});

test('가장자리 페이드 밖 카드는 불투명도가 0이다', () => {
  const metrics = metricsForAspect(0.8);   // edgeFade 1.9
  assert.equal(layoutForOffset(3, metrics).opacity, 0);
  assert.ok(layoutForOffset(1, metrics).opacity > 0);
});
