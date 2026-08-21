/* =============================================================
   editor/fontWeights — 폰트별 "실제로 있는" 굵기만 고르게 한다.

   왜: 굵기 피커는 5단계(Light~Bold)를 모든 폰트에 똑같이 보여줬다. 그런데 Gowun Dodum·
   Cal Sans 는 한 굵기뿐이고 Roboto Mono 는 CDN 에서 500·600 만 받는다. 없는 굵기를 고르면
   브라우저가 획을 억지로 두껍게 만든 합성 굵기가 그려져, 화면에서는 그럴듯해 보여도
   "고른 것과 다른 것"이 최종 PNG 에 찍힌다. 고를 수 있는 것만 보여주는 게 정직하다.

   이 표가 유일한 출처다 — 굵기 피커, 볼드 토글, 빠른 스타일(큰 제목 600 등) 적용이
   전부 여기로 물어본다. 폰트를 추가·교체하면 이 표만 고치면 된다.
   (근거: tokens.css @font-face 의 font-weight 범위, index.html 의 Google Fonts 쿼리)
   ============================================================= */

export const ALL_WEIGHTS = [
  { value: 300, label: 'Light' },
  { value: 400, label: 'Regular' },
  { value: 500, label: 'Medium' },
  { value: 600, label: 'SemiBold' },
  { value: 700, label: 'Bold' },
];

// 폰트 → 실제 제공 굵기. 표에 없는 폰트는 ALL_WEIGHTS(가변 폰트로 간주).
const SUPPORTED = {
  'Pretendard': [300, 400, 500, 600, 700],   // 가변 45~930
  'Cormorant': [300, 400, 500, 600, 700],    // 가변 300~700
  'Roboto Mono': [500, 600],                 // index.html 쿼리가 받는 두 굵기
  'Cal Sans': [600],                         // 단일 굵기 — 저장값은 600(아래 CSS_FACE 참고)
  'Gowun Dodum': [400],                      // Regular 단일
};

// 저장 굵기 → 브라우저가 실제로 가진 CSS face. Cal Sans 는 디자인상 "600 으로 읽히는" 굵기라
// 프리셋 8곳이 weight:600 으로 저장하지만, Google Fonts 가 주는 face 는 font-weight:400 하나다
// (Codex 리뷰 2026-08-21). 저장값을 400 으로 바꾸면 기존 문서·프리셋 매칭이 전부 흔들리므로,
// 저장은 600 그대로 두고 **그릴 때만** 400 으로 보내 브라우저가 합성 볼드를 만들지 않게 한다.
const CSS_FACE = {
  'Cal Sans': { 600: 400 },
};

export const DEFAULT_FONT = 'Pretendard';

/** 이 폰트가 실제로 제공하는 굵기 값 배열(오름차순). */
export function supportedWeights(font) {
  return SUPPORTED[font || DEFAULT_FONT] || ALL_WEIGHTS.map((w) => w.value);
}

/** 피커에 보여줄 옵션 — ALL_WEIGHTS 에서 지원되는 것만 남긴다. */
export function weightOptions(font) {
  const ok = new Set(supportedWeights(font));
  return ALL_WEIGHTS.filter((w) => ok.has(w.value));
}

/** 원하는 굵기를 이 폰트가 줄 수 있는 가장 가까운 굵기로. 동률이면 더 가는 쪽(합성 볼드보다
    합성 라이트가 덜 튄다는 뜻이 아니라, 결정적이어야 해서 고정한 규칙). */
export function nearestWeight(font, wanted) {
  const list = supportedWeights(font);
  const target = Number(wanted) || 400;
  return list.reduce((best, w) => (Math.abs(w - target) < Math.abs(best - target) ? w : best), list[0]);
}

/** 폰트 바꿀 때 스타일 패치 — 현재 굵기를 새 폰트가 못 주면 가장 가까운 것으로 함께 바꾼다.
    굵기가 그대로면 weight 키를 아예 넣지 않아 불필요한 히스토리 엔트리를 만들지 않는다. */
export function fontChangePatch(style, nextFont) {
  const current = (style && style.weight) || 400;
  const next = nearestWeight(nextFont, current);
  return next === current ? { font: nextFont } : { font: nextFont, weight: next };
}

/** 볼드 토글 — 이 폰트에서 "굵게"로 갈 수 있는 값과 "보통"으로 돌아올 값.
    굵기가 하나뿐이면 null(토글 불가 → 버튼 비활성). */
export function boldToggle(font) {
  const list = supportedWeights(font);
  if (list.length < 2) return null;
  const regular = nearestWeight(font, 400);
  const bold = list[list.length - 1];
  return bold === regular ? null : { regular, bold };
}

/** 렌더러가 CSS font-weight 에 넣을 값. 폰트가 못 주는 굵기(옛 문서·템플릿의 900 등)는 가장
    가까운 지원 굵기로, 그 뒤 CSS_FACE 매핑이 있으면 브라우저가 실제로 가진 face 로 보낸다.
    저장 상태는 건드리지 않는다 — 화면과 PNG 에 합성 굵기가 찍히는 것만 막는다. */
export function cssWeight(font, weight) {
  const snapped = nearestWeight(font, weight);
  const map = CSS_FACE[font || DEFAULT_FONT];
  return (map && map[snapped]) || snapped;
}

/** 현재 스타일이 "굵게" 상태인가 — 이 폰트의 가장 굵은 값이면서 보통과 다를 때. */
export function isBold(font, weight) {
  const t = boldToggle(font);
  return !!t && Number(weight || 400) >= t.bold;
}
