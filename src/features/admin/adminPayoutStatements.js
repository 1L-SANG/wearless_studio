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
    processing: { label: '지급 처리 중', variant: 'outline' },
    prepared: { label: '송금 전 확인', variant: 'outline' },
    transfer_started: { label: '송금 진행 중', variant: 'outline' },
    cancelled: { label: '송금 전 취소', variant: 'outline' },
  }[status] || { label: '예정', variant: 'outline' };
}

// ── 월말 정산 실행 · 실제 이체 기록 (2026-09-26) ────────────────────────────────

function seoulParts(now) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const pick = type => parts.find(part => part.type === type)?.value;
  return { year: pick('year'), month: pick('month'), day: pick('day') };
}

/** 서울 기준 이번 달 'YYYY-MM'. 이 달과 그 뒤는 아직 마감 전이라 월말 정산을 실행할 수 없다. */
export function currentSeoulMonth(now = new Date()) {
  const { year, month } = seoulParts(now);
  return `${year}-${month}`;
}

/** 서울 기준 오늘 'YYYY-MM-DD' — 이체일 입력의 기본값·상한. */
export function todaySeoulDate(now = new Date()) {
  const { year, month, day } = seoulParts(now);
  return `${year}-${month}-${day}`;
}

/** 마감 전 달이면 실행 가능 시각(다음 달 1일)을, 마감된 달이면 null. */
export function monthOpensOn(periodMonth, now = new Date()) {
  if (!/^\d{4}-\d{2}$/.test(String(periodMonth || ''))) return null;
  if (periodMonth < currentSeoulMonth(now)) return null;
  const [year, month] = periodMonth.split('-').map(Number);
  const next = new Date(Date.UTC(year, month, 1));
  return `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}-01`;
}

const won = value => `${Number(value || 0).toLocaleString('ko-KR')}원`;

/** 월말·중간 정산 실행 결과 한 줄. 돈이 움직였다고 읽힐 말은 쓰지 않는다. */
export function runOutcomeLabel(row) {
  // 중간 정산은 체인 미확정 정산을 건너뛰지 않고 남겨 둔다 — 몇 건이 남았는지 함께 말한다.
  const left = row?.unconfirmedCount > 0 ? ` (체인 미확정 ${row.unconfirmedCount}건은 담지 않았어요)` : '';
  switch (row?.outcome) {
    case 'prepared': return `지급 확인서 준비 ${won(row.amount)} · ${row.count}건 — 송금 시작 후 은행 앱에서 이체하고 참조번호를 기록해 주세요${left}`;
    case 'in_progress': return `이미 진행 중인 지급 확인서가 있어요 ${won(row.amount)} · ${row.count}건`;
    case 'nothing_due': return `새로 지급할 정산이 없어요${left}`;
    case 'held': return '보류 중이라 건너뛰었어요';
    case 'paid': return '이전 방식 지급 기록이 있어 건너뛰었어요';
    case 'unconfirmed': return `체인 기록이 확정되지 않은 정산 ${row.unconfirmedCount}건이 있어 건너뛰었어요`;
    case 'no_account': return `입금 계좌가 없어 건너뛰었어요 (${won(row.amount)} 대기)`;
    default: return '결과를 확인하지 못했어요';
  }
}

/** 이체 기록 폼 → 서버 본문. 금액은 숫자만, 참조번호는 앞뒤 공백 제거. 모자라면 null. */
export function transferRecordBody(form) {
  const transferReference = String(form?.reference || '').trim();
  const amount = Number(String(form?.amount ?? '').replace(/[^0-9]/g, ''));
  const transferredOn = String(form?.date || '');
  if (transferReference.length < 4 || !Number.isInteger(amount) || amount <= 0 || !/^\d{4}-\d{2}-\d{2}$/.test(transferredOn)) return null;
  return { transferReference, amount, transferredOn };
}

// ── 중간 정산 (2026-09-27) ─────────────────────────────────────────────────────

function seoulClock(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date);
  const pick = type => parts.find(part => part.type === type)?.value;
  return { month: Number(pick('month')), day: Number(pick('day')), time: `${pick('hour')}:${pick('minute')}` };
}

/** 기준 시각 표시 — '9/27 14:05'(KST). 중간 정산은 이 시각 전에 생긴 확정 정산만 담는다. */
export function cutoffLabel(value) {
  const clock = value ? seoulClock(value) : null;
  return clock ? `${clock.month}/${clock.day} ${clock.time}` : '-';
}

/** 중간 정산이 담은 기간 — '9/1–9/27분'. coveredThrough 는 서버가 준 마지막 날(KST, YYYY-MM-DD). */
export function interimRangeLabel(periodMonth, coveredThrough) {
  const month = /^(\d{4})-(\d{2})$/.exec(String(periodMonth || ''));
  const last = /^\d{4}-(\d{2})-(\d{2})$/.exec(String(coveredThrough || ''));
  if (!month || !last) return '';
  return `${Number(month[2])}/1–${Number(last[1])}/${Number(last[2])}분`;
}

/** 확인서 한 줄 머리말. 중간 정산이면 '중간 정산 · 9/1–9/27분', 아니면 ''. */
export function confirmationKindLabel(confirmation) {
  if (confirmation?.kind !== 'interim') return '';
  const range = interimRangeLabel(confirmation.periodMonth, confirmation.coveredThrough);
  return range ? `중간 정산 · ${range}` : '중간 정산';
}
