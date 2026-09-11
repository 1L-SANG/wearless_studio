/* Sparkline 의 순수 계산. JSX 없는 .js 로 떼어 둔 이유는 node 테스트가 직접 import 하기 위해서다
   (tests/frontend 는 번들러 없이 돈다). */

/* 커서 x 비율(0~1) → 가장 가까운 점의 인덱스. 점이 하나뿐이거나 없으면 0.
   비율은 잘라 낸다 — 커서가 차트 밖으로 살짝 나간 touchmove 에서 음수·1 초과가 들어온다. */
export function nearestIndex(ratio, count) {
  if (!Number.isFinite(count) || count <= 1) return 0;
  const r = Number.isFinite(ratio) ? Math.min(1, Math.max(0, ratio)) : 0;
  return Math.round(r * (count - 1));
}

/* 서버가 주는 KST 일자 문자열('2026-09-11') → '9/11'. 툴팁 한 줄에 들어가야 해서 연도는
   뺀다(기간이 최대 90일이라 연도가 헷갈릴 일이 없다). 모르는 모양은 그대로 돌려준다 —
   잘못된 값을 숨기면 데이터 문제를 늦게 안다. */
export function shortDate(value) {
  if (!value) return '-';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!m) return String(value);
  return `${Number(m[2])}/${Number(m[3])}`;
}
