export function payoutStatementStatusLabel(status) {
  return { scheduled: '예정', paid: '지급 완료', held: '보류', processing: '지급 처리 중', prepared: '송금 전 확인', transfer_started: '송금 진행 중', cancelled: '송금 전 취소' }[status] || '예정';
}

export function actualPayoutDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-');
}

export function payoutDisplayRows(statement) {
  const confirmations = statement.confirmations || [];
  if (!confirmations.length) {
    const rows = [{ ...statement, key: statement.periodMonth,
      displayDate: statement.status === 'paid' ? actualPayoutDate(statement.paidAt) : statement.scheduledFor || '-' }];
    if (statement.legacyPaid && statement.unpaidAmount > 0) rows.push({ ...statement,
      key: `${statement.periodMonth}:unreconciled`, amount: statement.unpaidAmount, count: statement.unpaidCount,
      status: 'held', displayDate: '-', needsReconciliation: true });
    return rows;
  }
  const rows = confirmations.filter(item => item.status !== 'cancelled').map(item => ({
    ...item, key: item.id, periodMonth: statement.periodMonth,
    displayDate: item.status === 'paid' ? actualPayoutDate(item.paidAt) : statement.scheduledFor || '-',
  }));
  if (statement.unpaidCount > 0) rows.push({ ...statement, key: `${statement.periodMonth}:unpaid`,
    amount: statement.unpaidAmount, count: statement.unpaidCount, status: statement.status === 'held' ? 'held' : 'scheduled',
    displayDate: statement.scheduledFor || '-', additional: true });
  return rows;
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
