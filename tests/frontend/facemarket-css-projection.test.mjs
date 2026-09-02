import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CAMERA_Z,
  VISIBLE_WORLD_HEIGHT,
  cardTransform,
  fillScale,
  perspectivePx,
  visibleArcExtent,
  worldToPixelScale,
} from '../../src/features/facemarket-landing/carousel/cssProjection.js';
import {
  layoutForOffset,
  metricsForAspect,
} from '../../src/features/facemarket-landing/carousel/sceneLayout.js';

/* transform 문자열에서 한 항의 수치만 뽑는다 — 문자열 전체를 비교하지 않고
   부호만 따질 때 쓴다. */
function term(transform, name) {
  const match = new RegExp(`${name}\\(([-\\d.]+)(?:px|rad)\\)`).exec(transform);
  assert.ok(match, `${name} 항이 없다: ${transform}`);
  return Number(match[1]);
}

function translateY(transform) {
  const match = /translate3d\([-\d.]+px, ([-\d.]+)px,/.exec(transform);
  assert.ok(match, `translate3d 항이 없다: ${transform}`);
  return Number(match[1]);
}

test('z=0 평면에서 보이는 세로 높이는 코덱스 원본 카메라 값(2 · 8.6 · tan 12°)이다', () => {
  // 카메라 거리를 11 로 물렸어도(cssProjection.js 머리말) 이 값은 그대로다 —
  // 카드가 스테이지에서 차지하는 비율(2.3 / 3.656)이 여기에 걸려 있다.
  assert.ok(Math.abs(VISIBLE_WORLD_HEIGHT - 3.65597) < 0.0001);
  assert.equal(CAMERA_Z, 11);
});

test('스테이지 높이가 world→px 계수를 결정한다', () => {
  const k = worldToPixelScale(VISIBLE_WORLD_HEIGHT * 100);
  assert.ok(Math.abs(k - 100) < 1e-9);
});

test('높이가 없으면 계수도 0이다 — 첫 렌더에서 NaN 이 나오지 않게', () => {
  assert.equal(worldToPixelScale(0), 0);
  assert.equal(worldToPixelScale(-10), 0);
  assert.equal(worldToPixelScale(Number.NaN), 0);
});

test('perspective 는 카메라 거리를 그대로 픽셀로 옮긴 값이다', () => {
  const height = 500;
  assert.ok(Math.abs(perspectivePx(height) / worldToPixelScale(height) - CAMERA_Z) < 1e-9);
});

test('CSS 배율이 three 배율과 같다 — 이 변환의 존재 이유', () => {
  const height = 640;
  const k = worldToPixelScale(height);
  const P = perspectivePx(height);

  for (const z of [0, 0.49, 1.19, 2.75, 3.2]) {
    const three = CAMERA_Z / (CAMERA_Z - z);      // 원근 카메라 배율
    const css = P / (P - z * k);                  // CSS perspective 배율
    assert.ok(Math.abs(three - css) < 1e-9, `z=${z} 에서 배율이 어긋난다`);
  }
});

test('가운데 카드는 변환이 원점이다', () => {
  const layout = { x: 0, y: 0, z: 0, rotationY: 0, rotationZ: 0 };
  assert.equal(
    cardTransform(layout, 100),
    'translate3d(0.00px, 0.00px, 0.00px) rotateY(0.0000rad) rotateZ(0.0000rad)',
  );
});

test('배치값에 계수를 곱해 픽셀 변환 문자열을 만든다', () => {
  // rotateZ 만 부호가 뒤집혀 나온다 — 아래 손대칭 테스트가 이유를 못 박는다.
  const layout = { x: 2.25, y: 0, z: 1.19, rotationY: -0.26, rotationZ: -0.028 };
  assert.equal(
    cardTransform(layout, 100),
    'translate3d(225.00px, 0.00px, 119.00px) rotateY(-0.2600rad) rotateZ(0.0280rad)',
  );
});

/* ---- three(+y 위) → CSS(+y 아래) 손대칭 ----------------------------------
   두 좌표계는 F = diag(1, -1, 1) 만큼 어긋나 있다. CSS 행렬은 F·M·F 라
   rotateZ 와 이동 y 는 부호가 뒤집히고, rotateY 와 이동 x·z 는 그대로다.
   부호를 안 뒤집으면 배율 불변식은 그대로 통과하면서 화면의 부채꼴만
   좌우 반전된다 — 그래서 배율 테스트와 별도로 여기서 못 박는다. */

test('rotateZ 는 sceneLayout 의 rotationZ 와 부호가 반대다', () => {
  const metrics = metricsForAspect(3);

  for (const offset of [1, 2, 3, 4, -1, -2, -3, -4]) {
    const layout = layoutForOffset(offset, metrics);
    assert.notEqual(layout.rotationZ, 0, `offset ${offset} 은 기울어져 있어야 표본이 된다`);

    const rotateZ = term(cardTransform(layout, 100), 'rotateZ');
    assert.ok(
      rotateZ * layout.rotationZ < 0,
      `offset ${offset}: rotationZ ${layout.rotationZ} 인데 rotateZ 가 ${rotateZ} 다`,
    );
    assert.ok(Math.abs(rotateZ + layout.rotationZ) < 1e-9);
  }
});

test('오른쪽 카드는 화면상 시계방향으로 기운다 — 원본 three 씬과 같은 방향', () => {
  const metrics = metricsForAspect(3);
  const right = cardTransform(layoutForOffset(2, metrics), 100);
  const left = cardTransform(layoutForOffset(-2, metrics), 100);

  // three 의 rotationZ 는 오른쪽 카드가 음수(= +y 위 기준 시계방향).
  // CSS 는 +y 가 아래라 같은 그림이 양수로 나와야 한다.
  assert.ok(term(right, 'rotateZ') > 0, '오른쪽 카드가 화면상 시계방향이 아니다');
  assert.ok(term(left, 'rotateZ') < 0, '왼쪽 카드가 화면상 반시계방향이 아니다');
  assert.equal(term(right, 'rotateZ'), -term(left, 'rotateZ'));
});

test('rotateY 는 뒤집지 않는다 — Ry 는 y 손대칭에 불변이다', () => {
  const metrics = metricsForAspect(3);

  for (const offset of [1, 2, 3, 4, -1, -2, -3, -4]) {
    const layout = layoutForOffset(offset, metrics);
    const rotateY = term(cardTransform(layout, 100), 'rotateY');
    assert.ok(
      Math.abs(rotateY - layout.rotationY) < 1e-9,
      `offset ${offset}: rotateY 가 rotationY(${layout.rotationY}) 와 달라졌다 — ${rotateY}`,
    );
  }
});

test('이동 y 는 부호가 뒤집힌다 — world 에서 위로 올린 카드는 화면에서 위로 간다', () => {
  // layoutForOffset 은 아직 y:0 만 내보내지만, y 를 쓰는 순간 위아래가 뒤집히면 안 된다.
  const up = { x: 0, y: 0.4, z: 0, rotationY: 0, rotationZ: 0 };
  const down = { x: 0, y: -0.4, z: 0, rotationY: 0, rotationZ: 0 };

  assert.equal(translateY(cardTransform(up, 100)), -40);   // CSS 는 음수가 위
  assert.equal(translateY(cardTransform(down, 100)), 40);
});

test('x·z 는 손대칭이 건드리지 않는 축이라 부호가 그대로다', () => {
  const layout = { x: -2.25, y: 0, z: 1.19, rotationY: 0.26, rotationZ: 0.028 };
  assert.equal(
    cardTransform(layout, 100),
    'translate3d(-225.00px, 0.00px, 119.00px) rotateY(0.2600rad) rotateZ(-0.0280rad)',
  );
});

test('반올림하면 0인 음수가 "-0.0000" 으로 새지 않는다', () => {
  // 부호를 뒤집으면서 생기는 표기. 그려지는 결과는 같지만 출력이 흔들리면 안 된다.
  const layout = { x: 0, y: 1e-7, z: 0, rotationY: -1e-7, rotationZ: 1e-7 };
  assert.equal(
    cardTransform(layout, 100),
    'translate3d(0.00px, 0.00px, 0.00px) rotateY(0.0000rad) rotateZ(0.0000rad)',
  );
});

/* ---- 폭 맞춤 계수(fillScale) — 데스크톱에서 보이는 5장이 폭을 채운다 ---- */

test('visibleArcExtent 는 온전히 보이는 가장 바깥 카드(edgeFade−1)의 바깥 가장자리다', () => {
  const metrics = metricsForAspect(3.5);           // 데스크톱: edgeFade 3 → offset 2
  const layout = layoutForOffset(2, metrics);
  const half = metrics.cardWidth / 2;
  const zOuter = layout.z + half * Math.sin(Math.abs(layout.rotationY));
  const m = CAMERA_Z / (CAMERA_Z - zOuter);
  const { halfSpan, outerHeight } = visibleArcExtent(metrics);
  assert.ok(Math.abs(halfSpan - (layout.x + half * Math.cos(Math.abs(layout.rotationY))) * m) < 1e-9);
  assert.ok(Math.abs(outerHeight - metrics.cardHeight * m) < 1e-9);
  assert.ok(halfSpan > 5 && halfSpan < 6.5, `halfSpan ${halfSpan}`);   // 접선 현 프로파일 기준 ≈ 5.6
});

test('폭이 남는 데스크톱에서는 5장 아크가 좌우 3% 여백을 빼고 폭에 딱 찬다', () => {
  const metrics = metricsForAspect(2000 / 544);
  const k = fillScale(2000, 544, metrics);
  const { halfSpan } = visibleArcExtent(metrics);
  assert.ok(Math.abs(halfSpan * k - (1000 - 60)) < 1e-6);
  assert.ok(k > worldToPixelScale(544), '높이 기준(3.656 로 나눈 값)보다 커야 폭이 찬다');
});

test('낮고 넓은 창에서는 바깥 카드가 행을 14% 넘는 지점에서 멈춘다', () => {
  const metrics = metricsForAspect(1280 / 225);
  const k = fillScale(1280, 225, metrics);
  const { halfSpan, outerHeight } = visibleArcExtent(metrics);
  assert.ok(Math.abs(outerHeight * k - 225 * 1.14) < 1e-6);
  assert.ok(halfSpan * k < 640 - 38, '폭을 다 못 채우는 게 맞다 — 높이가 상한');
});

test('좁고 높은 창에서는 높이 기준보다 작아져 5장이 잘리지 않고 들어온다', () => {
  const metrics = metricsForAspect(900 / 380);     // 데스크톱 버킷이지만 폭이 모자란다
  const k = fillScale(900, 380, metrics);
  assert.ok(k < worldToPixelScale(380));
  assert.ok(Math.abs(visibleArcExtent(metrics).halfSpan * k - (450 - 27)) < 1e-6);
});

test('폰·태블릿 버킷은 높이 기준 그대로다', () => {
  assert.equal(fillScale(390, 600, metricsForAspect(0.65)), worldToPixelScale(600));
  assert.equal(fillScale(800, 420, metricsForAspect(1.9)), worldToPixelScale(420));
});

test('fillScale 은 크기가 아직 없으면 0 이다(첫 렌더 방어)', () => {
  assert.equal(fillScale(0, 0, metricsForAspect(3)), 0);
  assert.equal(fillScale(800, 0, metricsForAspect(3)), 0);
});
