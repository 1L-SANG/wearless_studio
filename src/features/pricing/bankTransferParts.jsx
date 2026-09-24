/* =============================================================
   features/pricing/bankTransferParts — 계좌이체 화면 공용 조각
   요금제 화면(상태 배지·입금 확인 중 카드)과 신청 창이 같이 쓴다.
   시안: mockups/pricing_bank_ui_20260924/variant_C.html(오너 9/24 선택).
   ============================================================= */
import { useEffect, useRef, useState } from 'react';

// 16px 선 아이콘. 요금제 화면 전용이라 공용 Icon 사전에 넣지 않는다.
const PATHS = {
  check: <path d="M20 6 9 17l-5-5" />,
  copy: <><rect x="9" y="9" width="12" height="12" rx="2.5" /><path d="M15 9V5.5A2.5 2.5 0 0 0 12.5 3h-7A2.5 2.5 0 0 0 3 5.5v7A2.5 2.5 0 0 0 5.5 15H9" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  lock: <><rect x="4.5" y="10.5" width="15" height="10" rx="2.5" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /></>,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  bank: <><path d="M3 9.5 12 4l9 5.5" /><path d="M5.5 10v7.5M10 10v7.5M14 10v7.5M18.5 10v7.5" /><path d="M3.5 20.5h17" /></>,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.5v.01" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5M12 7.5v.01" /></>,
  top: <><rect x="3.5" y="4" width="17" height="16" rx="2.5" /><path d="M3.5 9h17" /></>,
};

export function PIcon({ name, size = 16, stroke = 1.9, className }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

export const won = (n) => '₩' + Number(n).toLocaleString('ko-KR');
export const num = (n) => Number(n).toLocaleString('ko-KR');

// '9/27(일) 19:40' — 은행 앱에 옮겨 적기 쉬운 짧은 꼴. 서울 시간 기준.
export function seoulShort(iso) {
  if (!iso) return '';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', weekday: 'short',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${parts.month}/${parts.day}(${parts.weekday}) ${parts.hour}:${parts.minute}`;
}

// 크레딧 지급 시간대. 서버 지급은 사람이 하므로 화면 약속과 운영 시간이 같아야 한다(오너 9/24).
export const PAYOUT_HOURS = '10:00 ~ 22:00';

// 누르면 1.5초 동안 '복사했어요'로 바뀌는 알약 버튼. onInk 는 검정 면 위에 놓일 때.
export function CopyButton({ text, label = '복사', ariaLabel, onInk = false, className = '', styles }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(String(text));
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch { /* 권한 거부 — 값은 화면에 그대로 보이니 조용히 둔다 */ }
  }
  return (
    <button type="button" onClick={copy} aria-label={ariaLabel || label}
      className={[styles.copy, onInk ? styles.copyOnInk : '', copied ? styles.copied : '', className].filter(Boolean).join(' ')}>
      <PIcon name={copied ? 'check' : 'copy'} size={14} />
      <span>{copied ? '복사했어요' : label}</span>
    </button>
  );
}
