import { useCallback, useEffect, useRef, useState } from 'react';
import { adminListPayoutStatements, adminSetPayoutStatementStatus, adminConfirmPayoutStatement, adminAdvancePayoutConfirmation, adminRevealPayoutConfirmation } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/admin-ui/table.jsx';
import { payoutAdminStatus, previousSeoulMonth } from './adminPayoutStatements.js';
import { actualPayoutDate } from '../model/mypage/payoutStatements.js';

const won = value => `${Number(value || 0).toLocaleString('ko-KR')}원`;
const rowKey = item => `${item.modelId}:${item.periodMonth}`;

export function AdminPayoutStatements() {
  const [month, setMonth] = useState(previousSeoulMonth);
  const [items, setItems] = useState(null);
  const [listError, setListError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [rowError, setRowError] = useState(null);
  const [revealed, setRevealed] = useState(null);
  const alive = useRef(false);
  const listVersion = useRef(0);
  const listing = useRef(false);
  const saving = useRef(false);
  const revealing = useRef(false);
  const revealVersion = useRef(0);
  const maskTimer = useRef(null);
  const confirmationIds = useRef(new Map());
  const viewer = useRef('');

  const clearReveal = useCallback(() => {
    revealVersion.current += 1;
    if (maskTimer.current) window.clearTimeout(maskTimer.current);
    maskTimer.current = null;
    setRevealed(null);
  }, []);

  const load = useCallback(async selectedMonth => {
    const version = ++listVersion.current;
    listing.current = true;
    setItems(null);
    setListError('');
    try {
      const payload = await adminListPayoutStatements({ month: selectedMonth });
      if (alive.current && version === listVersion.current) {
        viewer.current = payload.viewerId || '';
        setItems(Array.isArray(payload.items) ? payload.items : []);
      }
    } catch (error) {
      if (alive.current && version === listVersion.current) setListError(error.message || '지급 명세를 불러오지 못했어요.');
    } finally {
      if (alive.current && version === listVersion.current) listing.current = false;
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load(month);
    return () => {
      alive.current = false;
      listVersion.current += 1;
      revealVersion.current += 1;
      revealing.current = false;
      if (maskTimer.current) window.clearTimeout(maskTimer.current);
    };
  }, [month, load]);

  const changeMonth = event => {
    if (saving.current || revealing.current) return;
    clearReveal();
    setRowError(null);
    setMonth(event.target.value);
  };

  const rememberId = (item, fresh = false) => {
    const key = `fm-payout-confirmation:${viewer.current}:${rowKey(item)}`;
    let id = fresh ? null : confirmationIds.current.get(key);
    try { if (!fresh && !id) id = window.sessionStorage?.getItem(key); } catch { /* Server recovery remains available. */ }
    id ||= crypto.randomUUID();
    confirmationIds.current.set(key, id);
    try { window.sessionStorage?.setItem(key, id); } catch { /* Keep the same opaque ID in memory. */ }
    return id;
  };

  const mutate = async (item, operation) => {
    if (saving.current || revealing.current || listing.current) return;
    saving.current = true;
    setBusyId(rowKey(item));
    setRowError(null);
    clearReveal();
    try {
      await operation();
      if (alive.current) await load(item.periodMonth);
    } catch (error) {
      if (alive.current) setRowError({ key: rowKey(item), message: `${error.message || '응답을 확인하지 못했어요.'} 상태를 다시 확인해 주세요. 이미 송금했다면 다시 송금하지 마세요.` });
    } finally {
      saving.current = false;
      if (alive.current) setBusyId(null);
    }
  };

  const prepare = item => mutate(item, async () => {
    const result = await adminConfirmPayoutStatement(item.modelId, item.periodMonth, rememberId(item));
    if (result.status === 'cancelled' || result.status === 'paid') {
      rememberId(item, true);
      // A later click may create a new confirmation; never repeat automatically.
      throw new Error('이 확인은 이미 끝났어요. 상태를 확인한 후 새 지급 확인을 눌러 주세요.');
    }
  });

  const advance = (item, confirmation, action) => {
    const message = action === 'start'
      ? `${won(confirmation.amount)}을 ${confirmation.bankName} ${confirmation.accountMasked} ${confirmation.holderName} 계좌로 송금할 준비가 됐나요? 시작 후 계좌번호를 확인하고 한 번만 송금해 주세요.`
      : action === 'paid' ? `${won(confirmation.amount)}을 실제 송금한 사실을 확인하고 지급 완료로 기록할까요?`
        : '아직 송금하지 않은 확인 건을 취소할까요?';
    if (!window.confirm(message)) return;
    return mutate(item, async () => {
      await adminAdvancePayoutConfirmation(confirmation.id, action);
      if (action === 'cancel') rememberId(item, true);
    });
  };

  const reveal = async (item, confirmation) => {
    if (saving.current || revealing.current || listing.current) return;
    if (!window.confirm('계좌번호 전체를 표시하면 감사 기록에 남아요')) return;
    revealing.current = true;
    clearReveal();
    const version = ++revealVersion.current;
    setBusyId(rowKey(item));
    setRowError(null);
    try {
      const account = await adminRevealPayoutConfirmation(confirmation.id);
      if (!alive.current || version !== revealVersion.current) return;
      setRevealed({ ...account, key: confirmation?.id || rowKey(item) });
      maskTimer.current = window.setTimeout(() => {
        if (alive.current && version === revealVersion.current) setRevealed(null);
        maskTimer.current = null;
      }, 60000);
    } catch (error) {
      if (alive.current && version === revealVersion.current) setRowError({ key: rowKey(item), message: error.message || '계좌번호를 불러오지 못했어요.' });
    } finally {
      revealing.current = false;
      if (alive.current && version === revealVersion.current) setBusyId(null);
    }
  };

  const accountCell = (item, confirmation) => {
    const source = confirmation || item;
    const key = confirmation?.id || rowKey(item);
    const full = revealed?.key === key ? revealed : null;
    const account = full || source;
    const mayReveal = confirmation?.canManage && ['transfer_started', 'paid'].includes(confirmation.status);
    return <div className="flex min-w-52 flex-col items-start gap-1">
      <span>{account.bankName ? `${account.bankName} · ${full ? full.accountNumber : account.accountMasked} · ${account.holderName}` : '미등록'}</span>
      {account.bankName && mayReveal && (full
        ? <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(full.accountNumber)}>복사</Button>
        : <Button size="sm" variant="ghost" disabled={busyId !== null} onClick={() => reveal(item, confirmation)}>계좌 보기</Button>)}
    </div>;
  };

  return <div className="flex min-w-0 flex-col gap-5">
    <div><h1 className="text-lg font-semibold tracking-tight">지급 명세</h1><p className="mt-1 text-sm text-muted-foreground">금액과 계좌를 확인한 담당자가 수동 송금하고 결과를 기록해요.</p></div>
    <label className="flex max-w-xs flex-col gap-1 text-sm">정산 월<Input type="month" value={month} disabled={busyId !== null} onChange={changeMonth} /></label>
    <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => { clearReveal(); load(month); }}>상태 다시 확인</Button>
    {listError && <p role="alert" className="text-sm">{listError}</p>}
    <Card className="min-w-0"><CardContent className="p-0">
      {!items && !listError && <Skeleton className="h-64" aria-label="지급 명세를 불러오고 있어요" />}
      {items && <Table><TableHeader><TableRow><TableHead>모델명</TableHead><TableHead>건수</TableHead><TableHead>금액</TableHead><TableHead>계좌</TableHead><TableHead>상태</TableHead><TableHead>동작</TableHead></TableRow></TableHeader>
        <TableBody>{items.map(item => {
          const confirmations = item.confirmations || [];
          const active = confirmations.some(value => ['prepared', 'transfer_started'].includes(value.status));
          const unpaid = item.unpaidAmount ?? (item.status === 'paid' ? 0 : item.amount);
          const status = payoutAdminStatus(item.status);
          return <TableRow key={rowKey(item)}><TableCell>{item.modelName || item.modelId}</TableCell><TableCell>{item.unpaidCount ?? item.count}건</TableCell>
            <TableCell>{won(unpaid)}<span className="block text-xs text-muted-foreground">{confirmations.length ? '추가 미지급' : '미지급'}</span></TableCell>
            <TableCell>{active ? <span>지급 확인 건에 저장된 계좌를 사용해 주세요.</span> : accountCell(item)}</TableCell><TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
            <TableCell><div className="flex min-w-40 flex-col items-start gap-2">
              {item.legacyPaid && <p>기존 지급 {won(item.amount)} · {actualPayoutDate(item.paidAt)}. 추가 지급은 기존 정산 항목 확인이 필요해요.</p>}
              {!active && !item.legacyPaid && item.status !== 'paid' && <div className="flex gap-1">
                <Button size="sm" variant="outline" disabled={busyId !== null || item.open || item.status === 'held' || unpaid <= 0 || !item.bankName} onClick={() => prepare(item)}>지급 확인</Button>
                <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => mutate(item, () => adminSetPayoutStatementStatus(item.modelId, item.periodMonth, item.status === 'held' ? 'scheduled' : 'held', undefined, confirmations.at(-1)?.id))}>{item.status === 'held' ? '보류 해제' : '보류'}</Button>
              </div>}
              {item.open && <span>이번 달은 집계 중이에요.</span>}
              {confirmations.map(confirmation => <div key={confirmation.id} className="flex flex-col gap-1 border-t pt-2">
                <span>{won(confirmation.amount)} · {confirmation.count}건 · {payoutAdminStatus(confirmation.status).label}</span>
                {confirmation.status === 'paid' && <span>지급일 {actualPayoutDate(confirmation.paidAt)}</span>}
                {confirmation.status !== 'cancelled' && accountCell(item, confirmation)}
                {!confirmation.canManage && ['prepared', 'transfer_started'].includes(confirmation.status) && <span>확인한 담당 관리자가 처리 중이에요.</span>}
                {confirmation.canManage && confirmation.status === 'prepared' && <div className="flex gap-1">
                  <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => advance(item, confirmation, 'start')}>송금 시작</Button>
                  <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => advance(item, confirmation, 'cancel')}>확인 취소</Button>
                </div>}
                {confirmation.status === 'transfer_started' && <><span>송금 여부가 불확실하면 은행 내역을 확인해 주세요. 다시 송금하지 마세요.</span>
                  {confirmation.canManage && <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => advance(item, confirmation, 'paid')}>지급 완료 기록</Button>}</>}
              </div>)}
              {rowError?.key === rowKey(item) && <p role="alert" className="text-xs text-destructive">{rowError.message}</p>}
            </div></TableCell></TableRow>;
        })}{items.length === 0 && <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">이 달에는 정산 내역이 없어요.</TableCell></TableRow>}</TableBody>
      </Table>}
    </CardContent></Card>
    {items && <p className="text-sm text-muted-foreground">모델 {items.length}명 · 미지급 {won(items.reduce((sum, item) => sum + (item.unpaidAmount ?? (item.status === 'paid' ? 0 : Number(item.amount) || 0)), 0))}</p>}
  </div>;
}
