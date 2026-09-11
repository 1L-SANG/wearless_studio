import { seoulDateKey } from '../../../lib/datetime.js';

function monthFor(value) {
  const key = seoulDateKey(value, '');
  return key ? key.slice(0, 7) : '';
}

export function usageMonths(rows = [], now = new Date()) {
  const months = new Set([monthFor(now)]);
  rows.forEach(row => {
    const month = monthFor(row?.createdAt);
    if (month) months.add(month);
  });
  return [...months].filter(Boolean).sort((a, b) => b.localeCompare(a));
}

export function defaultUsageMonth(_rows = [], now = new Date()) {
  return monthFor(now);
}

export function rowsForMonth(rows = [], month) {
  return rows
    .filter(row => monthFor(row?.createdAt) === month)
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

export function usageDate(value) {
  const key = seoulDateKey(value, '');
  return key ? key.replaceAll('-', '.') : '-';
}

export function monthLabel(month) {
  const [year, value] = String(month).split('-');
  return `${year}년 ${Number(value)}월`;
}

function digits(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('ko-KR') : '-';
}

export function settlementLineParts(summary) {
  return [
    { text: '이번 달 정산 금액: ', strong: false },
    { text: digits(summary?.monthAmount), strong: true },
    { text: '원 / ', strong: false },
    { text: digits(summary?.monthCount), strong: true },
    { text: '건', strong: false },
  ];
}
