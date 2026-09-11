export function payoutStatementStatusLabel(status) {
  return { scheduled: '예정', paid: '지급 완료', held: '보류' }[status] || '예정';
}

export function payoutMonthLabel(periodMonth) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(periodMonth || ''));
  return match ? `${Number(match[1])}년 ${Number(match[2])}월` : '-';
}

export function payoutDateLabel(date) {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(String(date || ''));
  return match ? `${Number(match[1])}월 ${Number(match[2])}일` : '-';
}

export function nextPayoutLabel(nextPayout) {
  if (!nextPayout) return '';
  const date = payoutDateLabel(nextPayout.scheduledFor);
  const month = payoutMonthLabel(nextPayout.periodMonth);
  if (date === '-' || month === '-') return '';
  return `다음 지급 ${date} · ${month}분 ${(Number(nextPayout.amount) || 0).toLocaleString('ko-KR')}원`;
}
