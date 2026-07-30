// 마네킹 베이스 체형(가슴·힙 볼륨) — 서버 server/app/agents/mannequin_body.py 와 수동 미러.
// 값은 프롬프트에 들어가지 않는다. 어떤 베이스 마네킹 이미지를 쓸지 고르는 데만 쓰인다.
// (fitAxes.js ↔ fit_axes.py 와 동일한 미러 규약 — 한쪽을 고치면 다른 쪽도 고친다.)
export const BODY_LEVELS = Object.freeze([
  Object.freeze({ value: 'slim', label: '슬림' }),
  Object.freeze({ value: 'regular', label: '보통' }),
  Object.freeze({ value: 'volume', label: '볼륨' }),
]);

export const DEFAULT_BODY_LEVEL = 'regular';

// 여성 베이스에만 적용 — 남성은 체형 매트릭스가 없어 null.
// 카탈로그 밖 값은 조용히 기본값으로 떨어지고, 항상 두 축이 채워진 객체를 돌려준다.
export function normalizeMannequinBody(raw, gender) {
  if (gender !== 'women') return null;
  const src = raw && typeof raw === 'object' ? raw : {};
  const level = (v) => (BODY_LEVELS.some((o) => o.value === v) ? v : DEFAULT_BODY_LEVEL);
  return { bust: level(src.bust), hip: level(src.hip) };
}
