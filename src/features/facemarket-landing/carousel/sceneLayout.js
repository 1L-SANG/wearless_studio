/* =============================================================
   facemarket-landing/carousel/sceneLayout.js
   카드 offset → 3D 배치값. spotlight 프로토타입(sceneLayout.ts) 이식.

   X_STEPS·Z_STEPS·ROT_STEPS 는 사용자가 준 **원본 디자인 녹화**(화면 기록 2026-09-01
   오후 4.10.02.mov)에서 카드 네 귀퉁이를 재고 핀홀 카메라(cssProjection.js, 거리 11)에
   맞춘 값이다. 코덱스 포트가 갖고 있던 프로파일은 위치·크기는 맞았지만 회전이 1/3 이었다
   (offset 1: 4.6°, offset 2: 15°) — 그래서 카드가 평면에 붙은 종이처럼 납작하게 읽혔고,
   사용자가 "입체적으로 보여야 한다"고 되돌려 보냈다. 실측은 offset 1 ≈ 17°, offset 2 ≈ 47°,
   가장자리(offset 3)는 안쪽 가장자리 높이·위치로 역산해 ≈ 66° 다.

   임의로 만지면 오목 아크가 무너진다 — 바깥 카드가 카메라 쪽으로 밀려나와 가운데 카드보다
   크게 읽히고(offset 2 의 바깥 가장자리가 1.27배, offset 3 은 1.68배) 동시에 뚜렷한
   사다리꼴로 돌아서는 게 이 디자인의 정체성이다.
   ============================================================= */

/* 스테이지가 납작하고 넓어서 aspect 가 화면 형태를 그대로 따라간다.
   폰 ~0.8, 태블릿 ~1.9, 데스크톱 3+ */
export function metricsForAspect(aspect) {
  if (aspect < 1.1) return { cardWidth: 2.05, cardHeight: 2.87, spacing: 1.95, depthScale: 0.55, edgeFade: 1.9 };
  if (aspect < 2.3) return { cardWidth: 1.7, cardHeight: 2.38, spacing: 1.98, depthScale: 0.8, edgeFade: 2.6 };
  return { cardWidth: 1.64, cardHeight: 2.3, spacing: 2.25, depthScale: 1, edgeFade: 4 };
}

/* |offset| 0..4. X 는 spacing 배수, Z 는 depthScale 배수(world), ROT 는 라디안.
   0·1·2 는 녹화 실측 피팅값(잔차 ≤ 3px @2x), 3 은 화면 밖으로 반쯤 나간 카드의 보이는
   안쪽 가장자리(높이·x)로 역산, 4 는 추세 연장 — 4 는 edgeFade 밖이라 opacity 0 이고
   3→4 페이드 중에만 잠깐 보인다. */
const X_STEPS = [0, 0.982, 1.809, 2.31, 2.64];
const Z_STEPS = [0, 0.67, 1.73, 3.69, 4.8];
const ROT_STEPS = [0, 0.3, 0.84, 1.15, 1.3];

/* |offset| 0..4 로 키가 잡힌 프로파일을 선형 보간한다 — 드래그 중 중간값이 필요해서. */
function sampleProfile(steps, distance) {
  const last = steps.length - 1;
  if (distance >= last) return steps[last];

  const index = Math.floor(distance);
  const t = distance - index;
  return steps[index] + (steps[index + 1] - steps[index]) * t;
}

export function layoutForOffset(offset, metrics) {
  const distance = Math.abs(offset);
  const direction = offset === 0 ? 0 : Math.sign(offset);

  return {
    // 오목 아크 — 가운데 카드가 가장 멀어 작게 읽히고, 이웃이 카메라 쪽으로 나오며 커진다.
    // 크기 변화는 원근이 만들므로 scale 은 1로 둔다. x 는 그 깊이를 미리 보정한 값이라
    // 화면상 카드 간격이 고르게 유지된다.
    x: direction * sampleProfile(X_STEPS, distance) * metrics.spacing,
    y: 0,
    z: sampleProfile(Z_STEPS, distance) * metrics.depthScale,
    rotationY: -direction * sampleProfile(ROT_STEPS, distance) || 0,
    rotationZ: -direction * Math.min(distance * 0.014, 0.04) || 0,
    scale: 1,
    opacity: Math.max(0, Math.min(1, metrics.edgeFade - distance)),
  };
}
