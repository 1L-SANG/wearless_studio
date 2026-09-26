export function payoutStatementStatusLabel(status) {
  // simulated(2026-09-26): 스텁 제공자가 처리한 건. 돈이 가지 않았으므로 "지급 완료"라고 말하지 않는다.
  return { scheduled: '예정', paid: '지급 완료', held: '보류', processing: '지급 처리 중', prepared: '송금 전 확인', transfer_started: '송금 진행 중', cancelled: '송금 전 취소', simulated: '시뮬레이션 · 실제 이체 없음' }[status] || '예정';
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
  const rows = confirmations.filter(item => item.status !== 'cancelled').map(item => {
    // 스텁(simulated)이 paid 로 만든 건은 이체일도 참조번호도 실제가 아니다 — 지급 완료로 보이지 않게.
    const simulatedPaid = item.simulated && item.status === 'paid';
    // 실제 이체는 관리자가 은행 앱에서 옮겨 적은 이체일(transferredOn)이 지급일이다(2026-09-26).
    const paidDate = item.transferredOn || actualPayoutDate(item.paidAt);
    return {
      ...item, key: item.id, periodMonth: statement.periodMonth,
      status: simulatedPaid ? 'simulated' : item.status,
      displayDate: simulatedPaid ? '-' : item.status === 'paid' ? paidDate : statement.scheduledFor || '-',
      reference: !item.simulated && item.status === 'paid' ? item.transferReferenceMasked || null : null,
    };
  });
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
