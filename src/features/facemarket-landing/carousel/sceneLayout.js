/* =============================================================
   facemarket-landing/carousel/sceneLayout.js
   카드 offset → 3D 배치값. spotlight 프로토타입(sceneLayout.ts) 이식.

   X_STEPS·Z_STEPS·ROT_STEPS 는 사용자가 준 원본 스크린샷(이미지-1.jpg, 1668px 폭)에서
   카드 네 귀퉁이를 재고 핀홀 카메라(cssProjection.js, 거리 11)에 맞춘 값이다. 세 번 고쳤다:

   1. 코덱스 포트 원안 — 위치·크기는 맞았지만 회전이 1/3(offset 1: 4.6°, 2: 15°)이라 카드가
      종이처럼 납작했다("입체적으로 보여야 한다").
   2. 녹화 프레임 피팅 — 회전은 살았지만(17°/47°) 카드마다 크기가 **가운데부터** 커져
      이웃 카드 사이(gap)에서 위아래 실루엣이 계단처럼 꺾였다("라인 따라서 안 가고
      울퉁불퉁"). 코덱스가 덧붙인 rotateZ 기울기(±2 에서 1.6°)가 윗변을 눕혀 그걸 더 키웠다.
   3. 지금 — **접선 현(tangent chord)** 원칙. 원본은 카드 하나가 곡면의 한 조각이다:
      카드의 안쪽 가장자리 높이 = 이웃(더 안쪽) 카드의 바깥 가장자리 높이, 그래서 크기 성장은
      gap 에서 튀지 않고 카드 자체의 사다리꼴(회전)이 곡선을 그린다. 원본 실측(가운데 카드
      높이 = 1): offset 1 안 0.99·바깥 1.08 / offset 2 안 1.11·바깥 1.28 / offset 3 안 1.37.
      깊이는 높이에서 z = 11·(1 − 1/h), 회전은 양끝 깊이 차 2a·sinθ 에서 나온다 —
      θ ≈ 30° / 54° / 72°. 검증: 1668px 뷰포트에서 귀퉁이 좌표가 원본과 ±2%(≈5px) 안.

   임의로 만지면 오목 아크가 무너진다 — 바깥 카드가 카메라 쪽으로 밀려나와 가운데 카드보다
   크게 읽히고(offset 2 의 바깥 가장자리가 1.28배, offset 3 은 1.7배), 그 성장이 이웃 카드의
   가장자리와 이어지며 곡선을 그리는 게 이 디자인의 정체성이다.
   ============================================================= */

/* 스테이지가 납작하고 넓어서 aspect 가 화면 형태를 그대로 따라간다.
   폰 ~0.8, 태블릿 ~1.9, 데스크톱 3+ */
export function metricsForAspect(aspect) {
  if (aspect < 1.1) return { cardWidth: 2.05, cardHeight: 2.87, spacing: 1.95, depthScale: 0.55, edgeFade: 1.9 };
  if (aspect < 2.3) return { cardWidth: 1.7, cardHeight: 2.38, spacing: 1.98, depthScale: 0.8, edgeFade: 2.6 };
  return { cardWidth: 1.64, cardHeight: 2.3, spacing: 2.25, depthScale: 1, edgeFade: 4 };
}

/* |offset| 0..4. X 는 spacing 배수, Z 는 depthScale 배수(world), ROT 는 라디안.
   0·1·2 는 원본 스크린샷 실측(머리말 3), 3 은 화면 밖으로 반쯤 나간 카드의 보이는 안쪽
   가장자리(높이 1.37·x)로 역산, 4 는 추세 연장 — 4 는 edgeFade 밖이라 opacity 0 이고
   3→4 페이드 중에만 잠깐 보인다. X 는 이웃과의 화면 간격이 카드 폭의 0.4 배쯤 되도록 잡았다
   (원본 0.35~0.43). */
const X_STEPS = [0, 0.964, 1.733, 2.164, 2.53];
const Z_STEPS = [0, 0.41, 1.75, 3.75, 5.2];
const ROT_STEPS = [0, 0.52, 0.935, 1.25, 1.4];

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
    // 기울기(rotateZ)는 거의 없다 — 원본 스크린샷 실측 ≈ 0.3°(±2). 코덱스 값(0.014·거리,
    // 최대 0.04rad = 2.3°)은 카드 윗변을 눕히고 아랫변을 세워 위쪽 실루엣이 계단처럼 읽혔다.
    rotationZ: -direction * Math.min(distance * 0.003, 0.009) || 0,
    scale: 1,
    opacity: Math.max(0, Math.min(1, metrics.edgeFade - distance)),
  };
}
