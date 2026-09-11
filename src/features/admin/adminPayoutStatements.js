export function previousSeoulMonth(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit' }).formatToParts(now);
  const year = Number(parts.find(part => part.type === 'year')?.value);
  const month = Number(parts.find(part => part.type === 'month')?.value);
  const previous = new Date(Date.UTC(year, month - 2, 1));
  return `${previous.getUTCFullYear()}-${String(previous.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function replacePayoutStatement(items, updated) {
  return items.map(item => item.modelId === updated.modelId && item.periodMonth === updated.periodMonth ? updated : item);
}

export function payoutAdminStatus(status) {
  return {
    scheduled: { label: '예정', variant: 'outline' },
    paid: { label: '지급 완료', variant: 'secondary' },
    held: { label: '보류', variant: 'destructive' },
  }[status] || { label: '예정', variant: 'outline' };
}
