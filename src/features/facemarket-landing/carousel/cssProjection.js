/* =============================================================
   facemarket-landing/carousel/cssProjection.js
   three world 좌표 → CSS 픽셀. 코덱스 포트 원본은 PerspectiveCamera(fov 24, z 8.6)로
   씬을 봤고, 우리는 CSS perspective 로 같은 그림을 만든다.

   카메라 거리는 8.6 이 아니라 11 이다. 사용자가 준 **원본 디자인 녹화**(화면 기록
   2026-09-01 오후 4.10.02.mov, 2248×1584)에서 카드 네 귀퉁이를 재고 핀홀 모델을
   맞추니(scratch: fit.py) 카메라 거리 10.5~11.1 에서만 잔차가 2px 안에 들어왔다 —
   8.6 으로는 offset 2 카드의 폭·양끝 높이(304 / 452·401px)를 동시에 못 맞춘다(잔차 11px).
   z=0 평면에서 보이는 세로 높이는 원래 카메라(8.6·fov 24)가 보던 3.656 을 그대로 둬서
   카드가 스테이지에서 차지하는 비율(2.3/3.656 = 63%)은 변하지 않는다 — 대신 fov 가
   24° → 18.9° 로 좁아진 셈이다. sceneLayout.js 의 깊이·회전 프로파일은 이 카메라 기준이라
   둘을 따로 바꾸면 안 된다.

   두 원근이 같은 이유: 원근 카메라의 배율은 D/(D-z), CSS perspective 의
   배율은 P/(P-zpx) 다. P = D·k, zpx = z·k 로 두면 두 식이 같아진다.
   그래서 배율은 계수 k 하나로 닫힌다 — 테스트가 이 불변식을 지킨다.

   다만 k 만으로는 안 닫히는 게 하나 있다: 두 좌표계는 y 축으로 손대칭이다.
   three 는 +y 가 위, CSS 는 +y 가 아래라 F = diag(1, -1, 1) 만큼 어긋나 있고,
   CSS 행렬은 M_css = F·M_three·F 로 옮겨야 한다. cardTransform 이 그 일을 한다.
   ============================================================= */

import { layoutForOffset } from './sceneLayout.js';

export const CAMERA_Z = 11;

/* 카메라가 z=0 평면에서 세로로 담는 world 높이 = 2 · 8.6 · tan(12°). 코덱스 원본 카메라가
   보던 값을 상수로 못박는다(머리말 참고). 스테이지 픽셀 높이를 이 값으로 나누면 world→px
   계수가 된다. */
export const VISIBLE_WORLD_HEIGHT = 2 * 8.6 * Math.tan((12 * Math.PI) / 180);

/* 지금 카메라의 세로 fov — 위 두 값에서 역산한다(≈ 18.9°). 참고용. */
export const CAMERA_FOV_DEG =
  (2 * Math.atan(VISIBLE_WORLD_HEIGHT / 2 / CAMERA_Z) * 180) / Math.PI;

export function worldToPixelScale(stageHeightPx) {
  // 첫 렌더에는 ResizeObserver 가 아직 크기를 안 줘서 0/NaN 이 들어온다.
  // 여기서 막지 않으면 transform 문자열에 NaN 이 박혀 카드가 통째로 사라진다.
  if (!(stageHeightPx > 0)) return 0;
  return stageHeightPx / VISIBLE_WORLD_HEIGHT;
}

export function perspectivePx(stageHeightPx) {
  return CAMERA_Z * worldToPixelScale(stageHeightPx);
}

/* 정지 상태에서 온전히 보이는 가장 바깥 카드(edgeFade − 1 칸)의 **바깥 가장자리**가 화면에서
   얼마나 멀리 나가는지 — halfSpan 은 z=0 기준 world 단위(× k = px), outerHeight 는 그 가장자리의
   높이(world, 원근 배율 포함). 프로파일(sceneLayout)에서 계산하므로 상수를 손대면 같이 움직인다. */
export function visibleArcExtent(metrics) {
  const outermost = Math.max(0, Math.ceil(metrics.edgeFade) - 1);
  const layout = layoutForOffset(outermost, metrics);
  const halfWidth = metrics.cardWidth / 2;
  const lean = Math.abs(layout.rotationY);
  const zOuter = layout.z + halfWidth * Math.sin(lean);
  const magnification = CAMERA_Z / (CAMERA_Z - zOuter);
  return {
    halfSpan: (layout.x + halfWidth * Math.cos(lean)) * magnification,
    outerHeight: metrics.cardHeight * magnification,
  };
}

/* 바깥 카드가 스테이지 행을 넘어도 되는 비율. 행은 세로로 안 자르므로(CarouselStage.module.css)
   위아래 여백으로 조금 나가도 되지만, 히어로 제목·메타 바 글자와 겹치기 전에 멈춰야 한다 —
   바깥 카드는 화면 좌우 끝에 있고 제목·힌트는 가운데라 14% 까지는 실측상 안 닿는다(1280×720). */
const OUTER_OVERFLOW = 1.14;

/* 폭 3% 는 비워 둔다 — 셸 좌우 패딩(--fm-pad ≈ 3.1vw)과 같은 눈금. */
function edgeMargin(stageWidthPx) {
  return Math.max(20, stageWidthPx * 0.03);
}

/* world→px 계수. 원래는 스테이지 **높이**만 봤다(worldToPixelScale) — 원본 three 카메라가
   세로 fov 로 씬을 담는 방식 그대로. 그러면 폭이 남는 화면(2000×1135: 카드 5장이 폭의 84%)에서
   양옆이 비고, 사용자가 "5장이 자연스럽게 화면을 꽉 채우게"를 요구했다.

   데스크톱 버킷에서는 폭에서도 잰다: 온전히 보이는 5장의 아크(visibleArcExtent)가 좌우 여백을
   뺀 폭에 딱 차는 계수(byWidth). 다만 그 계수로 바깥 카드(±2, 원근 1.29배)가 행을 OUTER_OVERFLOW
   넘게 커지면 거기서 멈춘다(cap) — 낮고 넓은 창(1280×720)이 이 경우다. 높이 기준값(byHeight)
   보다 작아지는 것도 허용한다: 좁고 높은 창에서 5장이 잘리는 대신 작아져서 들어온다.
   폰·태블릿 버킷은 그대로 높이 기준이다 — 가운데 한두 장이 폭을 이미 채우고 있다. */
export function fillScale(stageWidthPx, stageHeightPx, metrics) {
  const byHeight = worldToPixelScale(stageHeightPx);
  if (!(byHeight > 0) || !(stageWidthPx > 0) || !metrics || metrics.edgeFade < 3) return byHeight;

  const { halfSpan, outerHeight } = visibleArcExtent(metrics);
  if (!(halfSpan > 0) || !(outerHeight > 0)) return byHeight;

  const byWidth = (stageWidthPx / 2 - edgeMargin(stageWidthPx)) / halfSpan;
  const cap = (stageHeightPx * OUTER_OVERFLOW) / outerHeight;
  return Math.min(byWidth, cap);
}

/* toFixed 는 -0.00003 처럼 반올림하면 0이 되는 음수를 "-0.0000" 으로 찍는다.
   y·rotationZ 를 뒤집으면서 새로 생기는 표기라 0 으로 정규화한다 —
   그려지는 결과는 같지만 출력이 부호 때문에 흔들리지 않게. */
function stripNegativeZero(text) {
  return /^-0(\.0*)?$/.test(text) ? text.slice(1) : text;
}

/* three world 배치값을 CSS transform 문자열로. 손대칭(F = diag(1,-1,1)) 때문에
   항마다 다르게 옮겨진다 — F·M·F 를 풀면 이렇게 된다:
     · 이동 y  → 부호 반전   (F 가 y 성분을 뒤집는다)
     · rotateZ → 부호 반전   (F·Rz(θ)·F = Rz(-θ))
     · rotateY → 그대로      (F·Ry(θ)·F = Ry(θ))
     · 이동 x·z → 그대로     (F 가 손대지 않는 축)
   합성 translate·rotateY·rotateZ 는 사이사이 F·F 가 상쇄되므로 항별로 뒤집으면 된다.

   rotateY 를 같이 뒤집으면 안 된다. Ry 는 손대칭에 불변이라, 뒤집는 순간
   카드가 반대쪽을 보면서 오목 아크가 볼록으로 뒤집힌다.
   rotateZ 를 안 뒤집으면 바깥 카드의 기울기만 원본과 좌우 반전된 부채꼴이 된다
   (offset +2 는 화면상 시계방향이어야 하는데 반시계로 기운다). */
export function cardTransform(layout, k) {
  const px = (value) => stripNegativeZero((value * k).toFixed(2));
  const rad = (value) => stripNegativeZero(value.toFixed(4));
  return (
    `translate3d(${px(layout.x)}px, ${px(-layout.y)}px, ${px(layout.z)}px)` +
    ` rotateY(${rad(layout.rotationY)}rad) rotateZ(${rad(-layout.rotationZ)}rad)`
  );
}
