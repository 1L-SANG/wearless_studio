/* 신분증 규격 좌표.

   주민등록증은 ISO/IEC 7810 ID-1(85.6 × 53.98mm)이고 주민등록번호 줄의 위치도
   카드 안에서 고정이다. 그래서 카드가 화면 가이드를 채우도록 찍게 하면 번호의
   픽셀 좌표가 계산으로 나온다 — 사용자가 검은 박스를 끌 필요가 없고, 서버가
   "그 자리가 실제로 덮였는지" 검사할 수 있다(설계 §2).

   이 좌표가 틀리면 마스크가 엉뚱한 데 찍혀 주민번호가 그대로 올라간다.
   그래서 상수는 여기 한 곳에만 두고 테스트로 굳힌다. 카드 규격이 개정되면
   여기만 고친다. */

export const CARD_ASPECT = 85.6 / 53.98;   // ≈ 1.5858

/* 카드 기준 주민등록번호 영역(0~1 비율). 가로는 번호 전체를 놓치지 않게 넉넉히,
   세로는 그 한 줄만. v1 의 드래그 기본값(DEFAULT_MASK_RATIOS.rrc)을 출발점으로
   삼되, 가이드 촬영에서는 카드가 프레임을 정확히 채우므로 더 좁게 잡아도 안전하다. */
export const RRN_REGION = Object.freeze({ xr: 0.06, yr: 0.58, wr: 0.62, hr: 0.14 });

/* 프레임 안에서 카드 비율을 유지하는 가장 큰 사각형을 잡고, fill 만큼 줄여 여백을 둔다.
   여백이 필요한 이유: 카드 모서리가 프레임에 딱 붙으면 사용자가 맞추기 어렵고,
   경계 검출도 프레임 가장자리와 카드 경계를 구분하기 힘들어진다. */
export function guideRectInFrame(frameW, frameH, fill = 0.86) {
  const byWidth = frameW / CARD_ASPECT <= frameH;
  const w = (byWidth ? frameW : frameH * CARD_ASPECT) * fill;
  const h = w / CARD_ASPECT;
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round((frameH - h) / 2),
    w: Math.round(w),
    h: Math.round(h),
  };
}

export function rrnRectInFrame(frameW, frameH, fill) {
  const g = guideRectInFrame(frameW, frameH, fill);
  return {
    x: Math.round(g.x + RRN_REGION.xr * g.w),
    y: Math.round(g.y + RRN_REGION.yr * g.h),
    w: Math.round(RRN_REGION.wr * g.w),
    h: Math.round(RRN_REGION.hr * g.h),
  };
}

/* 가이드 박스를 프레임 대비 백분율로. 화면에 그릴 때 쓴다.

   오버레이를 CSS 로 따로 그리면 안 된다 — 사용자가 맞춘 네모와 마스크를 칠하는
   네모가 어긋나면 기하는 맞는데 번호가 안 가려진다. 비디오를 width:100%; height:auto
   로 두면 표시 박스가 곧 프레임이라, 이 백분율이 어떤 배율에서도 정확히 겹친다. */
export function guideRectPercent(frameW, frameH, fill) {
  const g = guideRectInFrame(frameW, frameH, fill);
  return {
    left: (g.x / frameW) * 100,
    top: (g.y / frameH) * 100,
    width: (g.w / frameW) * 100,
    height: (g.h / frameH) * 100,
  };
}
