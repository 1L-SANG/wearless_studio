import s from '@/components/NotFound.module.css';

// 바코드 막대 [x, 폭]. 그림 전용 장식이라 값에 의미는 없다.
const BARS = [[104, 2], [109.8, 1], [116.6, 1], [122.4, 1], [127.2, 2], [135, 1], [139.8, 2], [145.6, 3], [152.4, 2], [158.2, 1], [164, 1], [170.8, 1]];

export function HangTag({ label, children, footer, className = s.art }) {
  return (
    <svg className={className} viewBox="0 0 280 230" role="img" aria-label={label}>
      <g className={s.sway}>
        <path className={`${s.stroke} ${s.thin}`} d="M140 4 C 126 22 154 36 140 62" />
        <path className={`${s.stroke} ${s.paper}`} d="M84 70 L106 46 H174 L196 70 V214 Q196 222 188 222 H92 Q84 222 84 214 Z" />
        <circle className={`${s.stroke} ${s.hole}`} cx="140" cy="62" r="6" />
        <text className={`${s.mono} ${s.mutedFill}`} x="140" y="92" textAnchor="middle" fontSize="8" letterSpacing="2.4">WEARLESS</text>
        <path className={`${s.stroke} ${s.thin} ${s.dashed}`} d="M98 102 H182" />
        {children}
        {/* 세탁 기호 다섯 개: 물세탁, 표백, 건조기, 다림질, 드라이클리닝 */}
        {footer === undefined ? <g className={`${s.stroke} ${s.care}`}>
          <path d="M100 181 L101.5 189 H110.5 L112 181 M100 181 Q102 178.5 104 181 Q106 183.5 108 181 Q110 178.5 112 181" />
          <path d="M123 178.5 L129.5 189 H116.5 Z" />
          <rect x="134.5" y="178.5" width="11" height="11" rx="1.5" />
          <circle cx="140" cy="184" r="3" />
          <path d="M150.5 188.5 H163 L161.5 182.5 Q158 179.5 154 181.5 L150.5 188.5 M156 181.2 V178.5 H161" />
          <circle cx="174" cy="184" r="5.5" />
        </g> : footer}
        {BARS.map(([x, width]) => <rect key={x} className={s.inkFill} x={x} y="198" width={width} height="14" />)}
      </g>
    </svg>
  );
}
