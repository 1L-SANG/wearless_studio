import { useCallback, useEffect, useRef, useState } from 'react';
import { adminListPayoutStatements, adminRevealPayoutAccount, adminSetPayoutStatementStatus } from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/admin-ui/table.jsx';
import { payoutAdminStatus, previousSeoulMonth, replacePayoutStatement } from './adminPayoutStatements.js';

const won = value => `${Number(value || 0).toLocaleString('ko-KR')}원`;

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

  const clearReveal = useCallback(() => {
    revealVersion.current += 1;
    if (maskTimer.current) window.clearTimeout(maskTimer.current);
    maskTimer.current = null;
    setRevealed(null);
  }, []);

  const disposeReveal = useCallback(() => {
    revealVersion.current += 1;
    if (maskTimer.current) window.clearTimeout(maskTimer.current);
    maskTimer.current = null;
    revealing.current = false;
  }, []);

  const load = useCallback(async selectedMonth => {
    const version = ++listVersion.current;
    listing.current = true;
    setItems(null);
    setListError('');
    try {
      const payload = await adminListPayoutStatements({ month: selectedMonth });
      if (alive.current && version === listVersion.current) setItems(Array.isArray(payload?.items) ? payload.items : []);
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
      disposeReveal();
    };
  }, [month, load, disposeReveal]);

  const changeMonth = event => {
    if (saving.current || revealing.current) return;
    clearReveal();
    setRowError(null);
    setMonth(event.target.value);
  };

  const changeStatus = async (item, status, note) => {
    if (saving.current || revealing.current || listing.current) return;
    saving.current = true;
    const key = `${item.modelId}:${item.periodMonth}`;
    setBusyId(key);
    setRowError(null);
    try {
      const updated = await adminSetPayoutStatementStatus(item.modelId, item.periodMonth, status, note);
      if (alive.current) setItems(previous => replacePayoutStatement(previous || [], updated));
    } catch (error) {
      if (alive.current) setRowError({ key, status, message: error.message || '지급 상태를 바꾸지 못했어요.' });
    } finally {
      saving.current = false;
      if (alive.current) setBusyId(null);
    }
  };

  const reveal = async item => {
    if (saving.current || revealing.current || listing.current) return;
    if (!window.confirm('계좌번호 전체를 표시하면 감사 기록에 남아요')) return;
    revealing.current = true;
    clearReveal();
    const version = ++revealVersion.current;
    const key = `${item.modelId}:${item.periodMonth}`;
    setBusyId(key);
    setRowError(null);
    try {
      const account = await adminRevealPayoutAccount(item.modelId);
      if (!alive.current || version !== revealVersion.current) return;
      setRevealed({ modelId: item.modelId, accountNumber: account.accountNumber });
      maskTimer.current = window.setTimeout(() => {
        if (alive.current && version === revealVersion.current) setRevealed(null);
        maskTimer.current = null;
      }, 60000);
    } catch (error) {
      if (alive.current && version === revealVersion.current) setRowError({ key, message: error.message || '계좌번호를 불러오지 못했어요.' });
    } finally {
      revealing.current = false;
      if (alive.current && version === revealVersion.current) setBusyId(null);
    }
  };

  const total = (items || []).reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
  return <div className="flex min-w-0 flex-col gap-5">
    <div><h1 className="text-lg font-semibold tracking-tight">지급 명세</h1><p className="mt-1 text-sm text-muted-foreground">월별 정산액과 입금 계좌를 확인하고 지급 상태를 기록해요.</p></div>
    <label className="flex max-w-xs flex-col gap-1 text-sm">정산 월<Input type="month" value={month} disabled={busyId !== null} onChange={changeMonth} /></label>
    {listError && <div role="alert" className="flex items-center gap-3 text-sm"><p>{listError}</p><Button size="sm" variant="outline" onClick={() => load(month)}>다시 시도</Button></div>}
    <Card className="min-w-0"><CardContent className="p-0">
      {!items && !listError && <Skeleton className="h-64" aria-label="지급 명세를 불러오고 있어요" />}
      {items && <Table><TableHeader><TableRow><TableHead>모델명</TableHead><TableHead>건수</TableHead><TableHead>금액</TableHead><TableHead>계좌</TableHead><TableHead>상태</TableHead><TableHead>동작</TableHead></TableRow></TableHeader>
        <TableBody>{items.map(item => {
          const key = `${item.modelId}:${item.periodMonth}`;
          const status = payoutAdminStatus(item.status);
          const hasAccount = Boolean(item.bankName && item.accountMasked && item.holderName);
          const isRevealed = revealed?.modelId === item.modelId;
          return <TableRow key={key}><TableCell>{item.modelName || item.modelId}</TableCell><TableCell>{item.count}건</TableCell><TableCell>{won(item.amount)}</TableCell>
            <TableCell><div className="flex min-w-52 flex-col items-start gap-1"><span>{hasAccount ? `${item.bankName} · ${isRevealed ? revealed.accountNumber : item.accountMasked} · ${item.holderName}` : '미등록'}</span>
              {hasAccount && <div className="flex gap-1">{isRevealed ? <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(revealed.accountNumber)}>복사</Button>
                : <Button size="sm" variant="ghost" disabled={busyId !== null} onClick={() => reveal(item)}>계좌 보기</Button>}</div>}
              {rowError?.key === key && !rowError.status && <p role="alert" className="text-xs text-destructive">{rowError.message}</p>}</div></TableCell>
            <TableCell><Badge variant={status.variant}>{status.label}</Badge></TableCell>
            <TableCell><div className="flex min-w-40 flex-wrap gap-1">
              {item.status === 'paid' ? <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => changeStatus(item, 'scheduled')}>{busyId === key ? '처리 중...' : '예정으로 되돌리기'}</Button>
                : <><Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => changeStatus(item, 'paid')}>{busyId === key ? '처리 중...' : '지급 완료'}</Button>
                  {item.status !== 'held' && <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => changeStatus(item, 'held')}>보류</Button>}
                  {item.status === 'held' && <Button size="sm" variant="outline" disabled={busyId !== null} onClick={() => changeStatus(item, 'scheduled')}>예정으로 되돌리기</Button>}</>}
              {rowError?.key === key && rowError.status && <><p role="alert" className="w-full text-xs text-destructive">{rowError.message}</p><Button size="sm" variant="ghost" disabled={busyId !== null} onClick={() => changeStatus(item, rowError.status)}>변경 다시 시도</Button></>}
            </div></TableCell></TableRow>;
        })}
          {items.length === 0 && <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">이 달에는 정산 내역이 없어요.</TableCell></TableRow>}
        </TableBody></Table>}
    </CardContent></Card>
    {items && <p className="text-sm text-muted-foreground">모델 {items.length}명 · 총 {won(total)}</p>}
  </div>;
}
