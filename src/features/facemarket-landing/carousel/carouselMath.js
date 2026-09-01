/* =============================================================
   facemarket-landing/carousel/carouselMath.js
   무한 루프 캐러셀의 인덱스 산술. DOM 을 모르는 순수 함수라
   node:test 로 직접 검증한다.
   spotlight 프로토타입(carouselMath.ts)에서 이식 — 상수는 원본 그대로.
   ============================================================= */

export const modulo = (value, count) => ((value % count) + count) % count;

/* 카드 index 가 현재 위치에서 몇 칸 떨어졌는지. 루프라 항상 최단 방향으로 답한다
   (14장에서 13 → 0 은 뒤로 13칸이 아니라 앞으로 1칸). */
export function shortestWrappedOffset(index, position, count) {
  const raw = index - modulo(position, count);
  return raw - Math.round(raw / count) * count;
}

/* 점을 눌러 특정 카드로 갈 때의 연속 목표값. 최단 방향으로 가되 누적 위치는 유지한다. */
export function targetForIndex(current, index, count) {
  return Number((current + shortestWrappedOffset(index, current, count)).toFixed(10));
}

/* 손을 뗄 때 관성을 반영해 가장 가까운 카드로 스냅한다. 0.24 는 원본 계수. */
export function snapTarget(position, velocityItemsPerSecond) {
  return Math.round(position + velocityItemsPerSecond * 0.24);
}

/* 누적 위치(target)는 자르지 않는다 — 원본 spotlight 에 있던 rebaseTarget
   (한 바퀴 단위로 되돌리기)은 여기서 의도적으로 뺐다.

   왜: CarouselStage 의 렌더 위치는 target 을 감쇠로 쫓아가는 연속값이고 리베이스되지
   않는다. target 만 705 → 5 로 접으면 화면은 그 700칸(=50바퀴)을 실제로 훑고 내려간다.
   layout 상 "같은 위치"인 것과 애니메이션이 같은 건 다르다.

   안 잘라도 되는 이유: 정밀도가 상하려면 |position| 이 1e15 근처여야 하는데, 한 칸이
   드래그 170px 이니 1.7e17px 를 한 방향으로 끌어야 나오는 값이다. 현실 위험이 아니다.
   되살릴 거면 target 과 CarouselStage 의 positionRef 를 같은 프레임에 같은 정수배만큼
   함께 접어야 한다(둘의 차이가 보존되어야 감쇠가 안 튄다). */
