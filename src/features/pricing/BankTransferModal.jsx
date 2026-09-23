/* =============================================================
   features/pricing/BankTransferModal — 계좌이체(무통장입금) 신청 창
   지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §1.2, §10.
   PG 심사 전 결제 경로: 사용자가 사업자 통장으로 입금하고 관리자가 확인해 지급한다.
   계좌 정보는 서버(/v1/bank-transfer/info)가 준다 — 여기 하드코딩하지 않는다.
   금액·크레딧은 서버가 신청 시점에 스냅샷하므로 이 창은 표시만 한다.
   ============================================================= */
import { useState } from 'react';
import { api } from '@/lib/api/index.js';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import s from './BankTransferModal.module.css';

const won = (n) => '₩' + Number(n).toLocaleString('ko-KR');
const num = (n) => Number(n).toLocaleString('ko-KR');

export const BANK_TRANSFER_NOTICE =
  '입금이 확인되면 크레딧이 지급돼요. 확인은 영업일 기준 하루 안에 해드려요. 3일 뒤에는 신청이 자동으로 닫히지만, 기한 안에 입금하셨다면 확인 후 지급해 드려요.';

function seoulDateTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

export function BankTransferModal({ plan, info, defaultEmail, extend = false, onClose, onSubmitted }) {
  const recurring = plan.kind === 'subscription';
  const [form, setForm] = useState({
    payerName: '', phone: '', taxInvoice: false, businessNo: '', businessName: '',
    representativeName: '', invoiceEmail: defaultEmail || '', note: '',
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(null);     // 서버가 돌려준 신청
  const [copied, setCopied] = useState(false);

  const set = (key) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
  };

  async function submit(e) {
    e.preventDefault();
    setError('');
    const payerName = form.payerName.trim();
    if (payerName.length < 2) { setError('입금자명(실명)을 2자 이상 적어 주세요.'); return; }
    if (form.taxInvoice) {
      if (form.businessNo.replace(/\D/g, '').length !== 10) { setError('사업자등록번호는 숫자 10자리예요.'); return; }
      if (!form.businessName.trim() || !form.representativeName.trim() || !form.invoiceEmail.trim()) {
        setError('세금계산서를 받으려면 상호, 대표자 성명, 받을 이메일이 필요해요.'); return;
      }
    }
    setPending(true);
    try {
      const result = await api.createBankTransferRequest({
        planCode: plan.code,
        payerName,
        phone: form.phone.trim() || null,
        taxInvoice: form.taxInvoice,
        businessNo: form.taxInvoice ? form.businessNo : null,
        businessName: form.taxInvoice ? form.businessName.trim() : null,
        representativeName: form.taxInvoice ? form.representativeName.trim() : null,
        invoiceEmail: form.taxInvoice ? form.invoiceEmail.trim() : null,
        note: form.note.trim() || null,
      });
      setDone(result.request);
      onSubmitted?.(result.request);
    } catch (err) {
      setError(err?.message || '신청을 접수하지 못했어요.');
    } finally {
      setPending(false);
    }
  }

  async function copyAccount() {
    const ok = await copyText(`${info.bank} ${info.account} ${info.holder}`);
    setCopied(ok);
    if (ok) setTimeout(() => setCopied(false), 1500);
  }

  const accountBlock = info && (
    <div className={s.account} aria-label="입금 계좌">
      <div>
        <div className={s.accountBank}>{info.bank}</div>
        <div className={s.accountNumber}>{info.account}</div>
        <div className={s.accountHolder}>예금주 {info.holder}</div>
      </div>
      <Button variant="ghost" size="sm" onClick={copyAccount} type="button">
        {copied ? '복사됨' : '복사'}
      </Button>
    </div>
  );

  return (
    <Modal onClose={onClose} narrow>
      <div className={s.wrap}>
        {done ? (
          <>
            <div className={s.doneHead}>
              <span className={s.doneIcon}><Icon name="check" size={16} stroke={3} /></span>
              <h2 className={s.title}>신청이 접수됐어요</h2>
            </div>
            <p className={s.desc}>
              아래 계좌로 <strong>{won(done.amount)}</strong>을 입금자명 <strong>{done.payerName}</strong>으로 보내 주세요.
              {' '}{seoulDateTime(done.expiresAt)}까지 입금해 주시면 확인 후 크레딧 {num(done.credits)}을 지급해 드려요.
            </p>
            {accountBlock}
            <p className={s.notice}>{BANK_TRANSFER_NOTICE}</p>
            <div className={s.actions}>
              <Button variant="primary" onClick={onClose} type="button">닫기</Button>
            </div>
          </>
        ) : (
          <form onSubmit={submit} className={s.form}>
            <h2 className={s.title}>{extend ? '이용권 1개월 연장 신청' : '계좌이체로 신청하기'}</h2>
            <div className={s.summary}>
              <div className={s.summaryRow}><span>상품</span><strong>{plan.name}{recurring ? ' · 1개월 이용권' : ' · 1회 충전'}</strong></div>
              <div className={s.summaryRow}><span>입금 금액</span><strong>{won(plan.price)}</strong></div>
              <div className={s.summaryRow}><span>지급 크레딧</span><strong>{num(plan.credits)} 크레딧</strong></div>
              {recurring && <div className={s.summaryHint}>이용권은 입금 확인일부터 1개월이에요. 자동 갱신은 없어요.</div>}
            </div>
            {accountBlock}

            <label className={s.field}>
              <span>입금자명 <em>실명, 필수</em></span>
              <input value={form.payerName} onChange={set('payerName')} maxLength={30} autoFocus placeholder="통장에 찍힐 이름" />
            </label>
            <label className={s.field}>
              <span>연락처 <em>선택</em></span>
              <input value={form.phone} onChange={set('phone')} maxLength={20} inputMode="tel" placeholder="010-0000-0000" />
            </label>
            <label className={s.check}>
              <input type="checkbox" checked={form.taxInvoice} onChange={set('taxInvoice')} />
              <span>세금계산서 필요</span>
            </label>
            {form.taxInvoice && (
              <div className={s.taxBox}>
                <label className={s.field}>
                  <span>사업자등록번호</span>
                  <input value={form.businessNo} onChange={set('businessNo')} maxLength={12} inputMode="numeric" placeholder="000-00-00000" />
                </label>
                <label className={s.field}>
                  <span>상호</span>
                  <input value={form.businessName} onChange={set('businessName')} maxLength={60} />
                </label>
                <label className={s.field}>
                  <span>대표자 성명</span>
                  <input value={form.representativeName} onChange={set('representativeName')} maxLength={30} />
                </label>
                <label className={s.field}>
                  <span>계산서 받을 이메일</span>
                  <input value={form.invoiceEmail} onChange={set('invoiceEmail')} maxLength={254} inputMode="email" />
                </label>
                <p className={s.hint}>세금계산서는 입금 확인 뒤 입력하신 이메일로 보내드려요.</p>
              </div>
            )}
            <label className={s.field}>
              <span>메모 <em>선택</em></span>
              <textarea value={form.note} onChange={set('note')} maxLength={200} rows={2} />
            </label>

            <p className={s.notice}>{BANK_TRANSFER_NOTICE}</p>
            {error && <p className={s.error} role="alert">{error}</p>}
            <div className={s.actions}>
              <Button variant="ghost" onClick={onClose} type="button" disabled={pending}>돌아가기</Button>
              <Button variant="primary" type="submit" disabled={pending}>
                {pending ? '접수하는 중…' : '신청하기'}
              </Button>
            </div>
          </form>
        )}
      </div>
    </Modal>
  );
}

export default BankTransferModal;
