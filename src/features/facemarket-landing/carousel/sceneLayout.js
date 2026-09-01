/* =============================================================
   facemarket-landing/carousel/sceneLayout.js
   카드 offset → 3D 배치값. spotlight 프로토타입(sceneLayout.ts) 이식.

   X_STEPS·Z_STEPS·ROT_STEPS 는 원본 레퍼런스 영상에서 프레임 단위로
   역산한 값이다(카드 크기·화면 위치의 초점거리 역투영). 임의로 만지면
   오목 아크가 무너진다 — 바깥 카드가 카메라 쪽으로 밀려나와 가운데 카드보다
   약 47% 크게 읽히는 게 이 디자인의 정체성이다.
   ============================================================= */

/* 스테이지가 납작하고 넓어서 aspect 가 화면 형태를 그대로 따라간다.
   폰 ~0.8, 태블릿 ~1.9, 데스크톱 3+ */
export function metricsForAspect(aspect) {
  if (aspect < 1.1) return { cardWidth: 2.05, cardHeight: 2.87, spacing: 1.95, depthScale: 0.55, edgeFade: 1.9 };
  if (aspect < 2.3) return { cardWidth: 1.7, cardHeight: 2.38, spacing: 1.98, depthScale: 0.8, edgeFade: 2.6 };
  return { cardWidth: 1.64, cardHeight: 2.3, spacing: 2.25, depthScale: 1, edgeFade: 4 };
}

const X_STEPS = [0, 1, 1.881, 2.3, 2.55];
const Z_STEPS = [0, 0.49, 1.19, 2.75, 3.2];
const ROT_STEPS = [0, 0.08, 0.26, 0.48, 0.55];

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
