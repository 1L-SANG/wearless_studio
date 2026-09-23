/* 계좌이체 확인 — 사용자가 신청한 무통장입금을 보고 입금을 확인해 지급하거나 거절한다.
   지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §4.

   지급은 신청 ID 로 멱등이다. 응답을 못 받았으면 같은 행의 '입금 확인'을 그대로 다시 눌러도
   두 번 지급되지 않는다(서버가 저장된 결과를 돌려준다). 세금계산서는 여기서 발행하지 않는다 —
   필요 표시와 사업자 정보를 보여주고, 발행은 운영자가 홈택스에서 한 뒤 메모에 남긴다. */
import { Fragment, useCallback, useEffect, useState } from 'react';
import {
  adminConfirmBankTransfer, adminListBankTransfers, adminRejectBankTransfer,
} from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Textarea } from '@/components/admin-ui/textarea.jsx';
import { useToast } from '@/components/ui.jsx';
import { seoulDateKey } from '@/lib/datetime.js';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';

const STATUS_FILTERS = [
  { value: 'requested', label: '확인 대기' },
  { value: 'paid', label: '지급 완료' },
  { value: 'expired', label: '기한 지남' },
  { value: 'rejected', label: '거절' },
  { value: 'canceled', label: '사용자 취소' },
  { value: 'all', label: '전체' },
];

const STATUS_LABEL = {
  requested: '확인 대기', paid: '지급 완료', expired: '기한 지남', rejected: '거절', canceled: '사용자 취소',
};
const STATUS_VARIANT = {
  requested: 'default', paid: 'secondary', expired: 'outline', rejected: 'destructive', canceled: 'outline',
};

const won = (n) => `${Number(n).toLocaleString('ko-KR')}원`;
const num = (n) => Number(n).toLocaleString('ko-KR');
const day = (iso) => (iso ? seoulDateKey(iso) : '-');

function todayKey() {
  return new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
}

function ProductCell({ item }) {
  return (
    <div className="flex flex-col">
      <span className="font-medium">{item.planName || item.planCode}{item.kind === 'subscription' ? ' · 1개월' : ' · 충전'}</span>
      <span className="text-xs text-muted-foreground">{won(item.amount)} · {num(item.credits)} 크레딧</span>
    </div>
  );
}

function ConfirmForm({ item, onDone, onClose }) {
  const { push } = useToast();
  const [paidAt, setPaidAt] = useState(todayKey());
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const expired = item.status === 'expired';

  const submit = async (event) => {
    event.preventDefault();
    if (!paidAt) { setError('입금일을 입력해 주세요.'); return; }
    if (expired && !note.trim()) { setError('기한이 지난 신청은 확인 사유를 적어 주세요.'); return; }
    setSaving(true);
    setError(null);
    try {
      const result = await adminConfirmBankTransfer(item.id, { paidAt, adminNote: note.trim() || null });
      push(result.idempotent
        ? '이미 지급된 신청이에요. 저장된 결과를 보여드려요.'
        : `지급 완료 · ${num(result.credits)} 크레딧${result.endsAt ? ` · ${day(result.endsAt)}까지` : ''}`);
      onDone();
    } catch (e) {
      const message = e.status ? e.message
        : '지급 결과를 확인하지 못했어요. 같은 신청에 입금 확인을 다시 눌러도 두 번 지급되지 않아요.';
      setError(message);
      push(message, { icon: 'alertCircle' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 whitespace-normal p-3">
      <div>
        <h3 className="text-base font-semibold">입금 확인</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {item.email || '이메일 없음'} · 입금자 {item.payerName} · {won(item.amount)}
        </p>
      </div>
      <fieldset disabled={saving} className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          실제 입금일
          <Input type="date" value={paidAt} onChange={(e) => setPaidAt(e.target.value)} required />
        </label>
        <label className="flex flex-col gap-1.5 text-sm sm:col-span-2">
          관리자 메모{expired ? ' (기한 지남: 확인 사유 필수)' : ' (선택, 세금계산서 발행일·승인번호 등)'}
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} />
        </label>
      </fieldset>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={saving}>닫기</Button>
        <Button type="submit" size="sm" disabled={saving}>{saving ? '지급하는 중…' : '입금 확인하고 지급'}</Button>
      </div>
    </form>
  );
}

function RejectForm({ item, onDone, onClose }) {
  const { push } = useToast();
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    if (!reason.trim()) { setError('거절 사유를 적어 주세요.'); return; }
    setSaving(true);
    setError(null);
    try {
      await adminRejectBankTransfer(item.id, reason.trim());
      push('신청을 거절했어요.');
      onDone();
    } catch (e) {
      setError(e.message);
      push(e.message, { icon: 'alertCircle' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 whitespace-normal p-3">
      <h3 className="text-base font-semibold">신청 거절</h3>
      <label className="flex flex-col gap-1.5 text-sm">
        사유(필수)
        <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={300} disabled={saving} />
      </label>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={saving}>닫기</Button>
        <Button type="submit" variant="destructive" size="sm" disabled={saving}>{saving ? '처리 중…' : '거절'}</Button>
      </div>
    </form>
  );
}

function TaxInvoiceDetails({ item }) {
  if (!item.taxInvoice) return <span className="text-muted-foreground">불필요</span>;
  return (
    <details>
      <summary className="cursor-pointer font-medium">필요</summary>
      <div className="mt-1 flex flex-col text-xs text-muted-foreground">
        <span>사업자번호 {item.businessNo || '-'}</span>
        <span>상호 {item.businessName || '-'}</span>
        <span>대표자 {item.representativeName || '-'}</span>
        <span className="break-all">이메일 {item.invoiceEmail || '-'}</span>
      </div>
    </details>
  );
}

export function AdminBankTransfers() {
  const [status, setStatus] = useState('requested');
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);
  const [action, setAction] = useState(null);   // { id, kind: 'confirm' | 'reject' }

  const load = useCallback(() => {
    let active = true;
    setError(null);
    adminListBankTransfers({ status })
      .then((data) => { if (active) setItems(data.items); })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [status]);

  useEffect(() => load(), [load, reload]);

  const done = () => { setAction(null); setReload((n) => n + 1); };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">계좌이체 확인</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          사용자가 신청한 무통장입금이에요. 통장에서 입금을 확인한 뒤 '입금 확인'을 누르면 크레딧과 요금제가 지급돼요.
          세금계산서는 홈택스에서 직접 발행하고 발행일을 메모에 남겨 주세요.
        </p>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="상태 필터">
        {STATUS_FILTERS.map((f) => (
          <Button key={f.value} type="button" size="sm" variant={status === f.value ? 'default' : 'outline'}
            onClick={() => { setStatus(f.value); setAction(null); }}>
            {f.label}
          </Button>
        ))}
        <Button type="button" size="sm" variant="ghost" onClick={() => setReload((n) => n + 1)}>새로고침</Button>
      </div>
      <Card>
        <CardContent className="p-0">
          {error && (
            <div className="flex items-center gap-3 p-4">
              <p role="alert" className="text-sm text-destructive">{error}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>다시 시도</Button>
            </div>
          )}
          {!items && !error && <div className="p-4"><Skeleton className="h-24 w-full" /></div>}
          {items?.length === 0 && <p role="status" className="p-4 text-sm text-muted-foreground">해당하는 신청이 없어요.</p>}
          {items?.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>신청일</TableHead>
                  <TableHead>사용자</TableHead>
                  <TableHead>상품</TableHead>
                  <TableHead>입금자명 · 연락처</TableHead>
                  <TableHead>세금계산서</TableHead>
                  <TableHead>기한 · 상태</TableHead>
                  <TableHead className="text-right">처리</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <Fragment key={item.id}>
                    <TableRow>
                      <TableCell className="whitespace-nowrap">{day(item.createdAt)}</TableCell>
                      <TableCell className="max-w-[220px] break-all">
                        <div>{item.email || '이메일 없음'}</div>
                        {item.displayName && <div className="text-xs text-muted-foreground">{item.displayName}</div>}
                      </TableCell>
                      <TableCell><ProductCell item={item} /></TableCell>
                      <TableCell>
                        <div className="font-medium">{item.payerName}</div>
                        <div className="text-xs text-muted-foreground">{item.phone || '-'}</div>
                        {item.note && <div className="mt-1 text-xs text-muted-foreground">메모: {item.note}</div>}
                      </TableCell>
                      <TableCell><TaxInvoiceDetails item={item} /></TableCell>
                      <TableCell className="whitespace-nowrap">
                        <div className="text-xs text-muted-foreground">{day(item.expiresAt)}까지</div>
                        <Badge variant={STATUS_VARIANT[item.status] || 'outline'}>{STATUS_LABEL[item.status] || item.status}</Badge>
                        {item.status === 'paid' && (
                          <div className="mt-1 text-xs text-muted-foreground">입금 {day(item.paidAt)} · 확인 {day(item.confirmedAt)}</div>
                        )}
                        {item.adminNote && <div className="mt-1 max-w-[220px] whitespace-normal text-xs text-muted-foreground">{item.adminNote}</div>}
                      </TableCell>
                      <TableCell className="text-right">
                        {(item.status === 'requested' || item.status === 'expired') && (
                          <div className="flex justify-end gap-2">
                            <Button type="button" size="sm" onClick={() => setAction({ id: item.id, kind: 'confirm' })}>입금 확인</Button>
                            <Button type="button" size="sm" variant="outline" onClick={() => setAction({ id: item.id, kind: 'reject' })}>거절</Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                    {action?.id === item.id && (
                      <TableRow>
                        <TableCell colSpan={7} className="bg-muted/40">
                          {action.kind === 'confirm'
                            ? <ConfirmForm item={item} onDone={done} onClose={() => setAction(null)} />
                            : <RejectForm item={item} onDone={done} onClose={() => setAction(null)} />}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default AdminBankTransfers;
