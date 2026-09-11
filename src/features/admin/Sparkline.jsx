/* 의존성 없는 꺾은선. 차트가 세 종류뿐이라 Recharts 를 admin 번들에 넣지 않는다.

   값이 전부 0 이면(초기 서비스에서 흔하다) 바닥에 붙은 직선을 그린다 — 0으로 나누지 않게.

   호버: 커서(또는 터치) x 위치에 가장 가까운 날짜를 골라 세로 가이드선·점·툴팁(날짜 · 값)을
   그린다. 강조 요소는 SVG 안이 아니라 **HTML 오버레이**다 — 이 SVG 는 preserveAspectRatio="none"
   으로 가로만 늘어나서 SVG <circle>·<text> 는 찌그러진다. 퍼센트 좌표로 얹으면 스케일과 무관하다. */
import { useState } from 'react';
import { cn } from '@/lib/adminCn.js';
import { nearestIndex, shortDate } from './sparklineMath.js';

const defaultFormat = (v) => String(v);

export function Sparkline({ points, height = 48, label, format = defaultFormat }) {
  const [active, setActive] = useState(null);
  const values = points.map((p) => p.value);
  const max = Math.max(1, ...values);
  const width = Math.max(points.length - 1, 1);
  const d = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${i} ${height - (p.value / max) * height}`)
    .join(' ');

  const locate = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (!rect.width) return;
    const clientX = e.touches?.[0]?.clientX ?? e.clientX;
    setActive(nearestIndex((clientX - rect.left) / rect.width, points.length));
  };
  const clear = () => setActive(null);

  const point = active === null ? null : points[active];
  // 점이 하나면 x=0 에 둔다 — (n-1) 로 나누면 0/0 이다.
  const leftPct = point && points.length > 1 ? (active / (points.length - 1)) * 100 : 0;
  const topPct = point ? (1 - point.value / max) * 100 : 0;

  return (
    <div
      className="relative h-12 w-full"
      onMouseMove={locate}
      onMouseLeave={clear}
      onTouchStart={locate}
      onTouchMove={locate}
      onTouchEnd={clear}
    >
      {/* 선은 muted, 강조(점·가이드선)는 primary — 호버한 지점이 색으로 구분되게. */}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-full w-full text-muted-foreground"
        role="img"
        aria-label={label}
      >
        <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      {point && (
        <>
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 w-px bg-primary/40"
            style={{ left: `${leftPct}%` }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-background"
            style={{ left: `${leftPct}%`, top: `${topPct}%` }}
          />
          {/* 오른쪽 40% 구간에서는 툴팁을 커서 왼쪽으로 뒤집어 카드 밖으로 안 나가게 한다. */}
          <div
            role="status"
            className={cn(
              'pointer-events-none absolute top-0 z-10 -translate-y-full whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm',
              leftPct > 60 ? '-translate-x-full' : '',
            )}
            style={{ left: `${leftPct}%` }}
          >
            <span className="text-muted-foreground">{shortDate(point.date)}</span>
            {' · '}
            {format(point.value)}
          </div>
        </>
      )}
    </div>
  );
}
