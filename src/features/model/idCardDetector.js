/* 자동 셔터 판정 — 라이브러리 없이 캔버스 그레이스케일만으로.

   OpenCV.js(~8MB)를 끌어오지 않는다(설계 §3-3). 필요한 건 세 가지뿐이고,
   전부 가이드 테두리 안쪽 밴드의 밝기/경계만 보면 판정된다:
     · fill  — 카드가 가이드를 충분히 채웠나 (밝은 화소 비율)
     · tilt  — 반듯한가 (위/아래 경계의 x 위치 차이)
     · sharp — 흔들리지 않았나 (경계의 기울기 세기)

   자동 셔터는 편의이지 관문이 아니다 — 이게 안 잡혀도 사용자는 수동으로 찍을 수
   있어야 한다(설계 §6). 그래서 임계는 "확실할 때만 찍는" 쪽으로 보수적으로 둔다. */
import { guideRectInFrame } from './idCardGeometry.js';

const DEFAULTS = { fillMin: 0.75, tiltMax: 0.06, sharpMin: 12 };

/* 한 행에서 밝은 구간의 시작·끝 x 를 찾는다. 카드가 배경보다 밝다는 전제 —
   어두운 배경(책상) 위 신분증이 일반적이다. 반대 경우는 자동이 안 잡히고
   수동 셔터로 간다(그래서 수동이 항상 열려 있어야 한다). */
function brightSpan(gray, width, y, x0, x1, threshold) {
  let first = -1, last = -1;
  for (let x = x0; x < x1; x++) {
    if (gray[y * width + x] >= threshold) { if (first < 0) first = x; last = x; }
  }
  return first < 0 ? null : { first, last };
}

export function scoreFrame(gray, width, height, opts = {}) {
  const { fillMin, tiltMax, sharpMin } = { ...DEFAULTS, ...opts };
  const g = guideRectInFrame(width, height);

  // 임계는 가이드 안 밝기 분포에서 잡는다(조명이 달라도 따라간다).
  let min = 255, max = 0, sum = 0, n = 0;
  for (let y = g.y; y < g.y + g.h; y++) {
    for (let x = g.x; x < g.x + g.w; x++) {
      const v = gray[y * width + x];
      if (v < min) min = v;
      if (v > max) max = v;
      sum += v; n++;
    }
  }
  const threshold = (min + max) / 2;
  const contrast = max - min;

  let bright = 0;
  for (let y = g.y; y < g.y + g.h; y++) {
    for (let x = g.x; x < g.x + g.w; x++) if (gray[y * width + x] >= threshold) bright++;
  }
  const fill = n ? bright / n : 0;

  // 위/아래 경계의 x 시작점이 얼마나 어긋나는지 = 기울기
  const top = brightSpan(gray, width, g.y + Math.round(g.h * 0.1), g.x, g.x + g.w, threshold);
  const bottom = brightSpan(gray, width, g.y + Math.round(g.h * 0.9), g.x, g.x + g.w, threshold);
  const tilt = top && bottom ? Math.abs(top.first - bottom.first) / g.w : 1;

  const sharp = contrast;
  const ok = fill >= fillMin && tilt <= tiltMax && sharp >= sharpMin;
  return { fill, tilt, sharp, ok };
}

/* 연속 N프레임 ok 여야 찍는다. 손이 지나가거나 순간적으로 맞은 프레임 하나로
   찍히면 흐린 사진이 남는다. */
export function createShutterGate({ needed = 5 } = {}) {
  let streak = 0;
  return {
    push(score) {
      streak = score?.ok ? streak + 1 : 0;
      return streak >= needed;
    },
    reset() { streak = 0; },
  };
}
