/* 의존성 없는 꺾은선. 차트가 두 종류뿐이라 Recharts 를 admin 번들에 넣지 않는다.

   값이 전부 0 이면(초기 서비스에서 흔하다) 바닥에 붙은 직선을 그린다 — 0으로 나누지 않게. */
export function Sparkline({ points, height = 48, label }) {
  const values = points.map((p) => p.value);
  const max = Math.max(1, ...values);
  const width = Math.max(points.length - 1, 1);
  const d = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${i} ${height - (p.value / max) * height}`)
    .join(' ');
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-12 w-full"
      role="img"
      aria-label={label}
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
