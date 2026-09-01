/* =============================================================
   facemarket-landing/carousel/cssProjection.js
   three world 좌표 → CSS 픽셀. 원본은 PerspectiveCamera(fov 24, z 8.6)로
   씬을 봤고, 우리는 CSS perspective 로 같은 그림을 만든다.

   두 원근이 같은 이유: 원근 카메라의 배율은 D/(D-z), CSS perspective 의
   배율은 P/(P-zpx) 다. P = D·k, zpx = z·k 로 두면 두 식이 같아진다.
   그래서 배율은 계수 k 하나로 닫힌다 — 테스트가 이 불변식을 지킨다.

   다만 k 만으로는 안 닫히는 게 하나 있다: 두 좌표계는 y 축으로 손대칭이다.
   three 는 +y 가 위, CSS 는 +y 가 아래라 F = diag(1, -1, 1) 만큼 어긋나 있고,
   CSS 행렬은 M_css = F·M_three·F 로 옮겨야 한다. cardTransform 이 그 일을 한다.
   ============================================================= */

export const CAMERA_Z = 8.6;
export const CAMERA_FOV_DEG = 24;

/* 카메라가 z=0 평면에서 세로로 담는 world 높이. 스테이지 픽셀 높이를 이 값으로 나누면
   world→px 계수가 된다. */
export const VISIBLE_WORLD_HEIGHT =
  2 * CAMERA_Z * Math.tan(((CAMERA_FOV_DEG / 2) * Math.PI) / 180);

export function worldToPixelScale(stageHeightPx) {
  // 첫 렌더에는 ResizeObserver 가 아직 크기를 안 줘서 0/NaN 이 들어온다.
  // 여기서 막지 않으면 transform 문자열에 NaN 이 박혀 카드가 통째로 사라진다.
  if (!(stageHeightPx > 0)) return 0;
  return stageHeightPx / VISIBLE_WORLD_HEIGHT;
}

export function perspectivePx(stageHeightPx) {
  return CAMERA_Z * worldToPixelScale(stageHeightPx);
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
