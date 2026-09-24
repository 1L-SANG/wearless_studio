/* =============================================================
   features/pricing/BankTransferModal — 계좌이체(무통장입금) 신청 창
   지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §1.2, §10.
   디자인: mockups/pricing_bank_ui_20260924/variant_C.html(오너 9/24 선택) — 넓은 화면은
   왼쪽 검정 주문 요약 + 오른쪽 입력 두 칸, 좁은 화면은 아래에서 올라오는 시트.
   입력은 입금자명 하나와 세금계산서 발행(선택)뿐이다. 연락처·메모 칸은 오너 지시로 뺐다.
   계좌 정보는 서버(/v1/bank-transfer/info)가 준다 — 여기 하드코딩하지 않는다.
   금액·크레딧은 서버가 신청 시점에 스냅샷하므로 이 창은 표시만 한다.

   공용 Modal(components/ui.jsx)을 쓰지 않는 이유: 그쪽은 폭 460px 고정에 `.modal p`
   전역 글자 규칙이 붙어 있어 두 칸 구성을 담을 수 없다. 포탈·Esc·바깥 클릭 규칙은 같다.
   ============================================================= */
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '@/lib/api/index.js';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import { CopyButton, PAYOUT_HOURS, PIcon, num, seoulShort } from './bankTransferParts.jsx';
import s from './BankTransferModal.module.css';

const digits = (v) => v.replace(/\D/g, '');

function Amount({ value, className }) {
  return <span className={className}><span className={s.cur}>₩</span>{num(value)}</span>;
}

function Summary({ plan, info }) {
  const recurring = plan.kind === 'subscription';
  return (
    <aside className={s.sum} aria-label="주문 요약">
      <p className={s.sumEyebrow}>주문 요약</p>
      <div className={s.sumProduct}>
        <strong>{plan.name}</strong>
        <span className={s.chipInk}>{recurring ? '1개월 이용권' : '1회 충전'}</span>
      </div>
      <p className={s.sumLabel}>입금 금액</p>
      <p className={s.sumAmount}><Amount value={plan.price} /></p>
      <dl className={s.sumRows}>
        <div><dt>지급 크레딧</dt><dd>{num(plan.credits)} 크레딧</dd></div>
        <div><dt>지급 시간대</dt><dd>{PAYOUT_HOURS}</dd></div>
      </dl>
      {info?.enabled && (
        <div className={s.acctCard}>
          <div className={s.acctTop}>
            <p className={s.acctLabel}>입금 계좌</p>
            <CopyButton text={`${info.bank} ${info.account}`} ariaLabel="계좌 복사" onInk styles={s} />
          </div>
          <p className={s.acctNo}>{info.bank} {info.account}</p>
          <p className={s.holder}>예금주 {info.holder}</p>
        </div>
      )}
    </aside>
  );
}

function DoneView({ done, plan, info, onClose, titleId }) {
  const recurring = done.kind === 'subscription';
  return (
    <>
      <aside className={s.sum} aria-label="진행 상황">
        <p className={s.stateInk}><span className={s.dotPending} aria-hidden="true" />입금 확인 중</p>
        <div className={s.sumProduct}>
          <strong>{plan.name}</strong>
          <span className={s.chipInk}>{recurring ? '1개월 이용권' : '1회 충전'}</span>
        </div>
        <p className={s.sumSub}>{num(done.credits)} 크레딧</p>
        <ol className={s.steps}>
          <li className={s.stepDone}>
            <span className={s.stDot}><PIcon name="check" size={14} stroke={3} /></span>
            <div><b>신청 접수</b><span className={s.t}>{seoulShort(done.createdAt)}</span></div>
          </li>
          <li className={s.stepNow}>
            <span className={s.stDot}><span className={`${s.dotPending} ${s.dotSm}`} /></span>
            <div><b>입금</b><span className={s.t}>{seoulShort(done.expiresAt)}까지</span></div>
          </li>
          <li className={s.stepNext}>
            <span className={s.stDot} />
            <div><b>크레딧 지급</b><span className={s.t}>{PAYOUT_HOURS}</span></div>
          </li>
        </ol>
      </aside>
      <header className={s.head}>
        <span className={s.grab} aria-hidden="true" />
        <span className={s.headIcon} aria-hidden="true"><PIcon name="bank" size={22} /></span>
        <h2 id={titleId} className={s.title}>이제 입금만 하면 돼요</h2>
        <p className={s.sub}>아래 정보 그대로 보내 주세요</p>
        <button type="button" className={s.close} onClick={onClose} aria-label="닫기"><PIcon name="x" size={20} /></button>
      </header>
      <div className={s.body}>
        <dl className={s.receipt}>
          <div className={s.rRow}>
            <dt>입금 금액</dt>
            <dd><Amount value={done.amount} className={s.money} /></dd>
            <CopyButton text={done.amount} ariaLabel="금액 복사" styles={s} />
          </div>
          {info?.enabled && (
            <div className={s.rRow}>
              <dt>입금 계좌</dt>
              <dd><span className={s.acctNoPlain}>{info.bank} {info.account}</span><p className={s.holderPlain}>예금주 {info.holder}</p></dd>
              <CopyButton text={`${info.bank} ${info.account}`} ariaLabel="계좌 복사" styles={s} />
            </div>
          )}
          <div className={s.rRow}><dt>입금자명</dt><dd>{done.payerName}</dd></div>
          <div className={s.rRow}><dt>입금 기한</dt><dd>{seoulShort(done.expiresAt)}까지</dd></div>
        </dl>
        <ul className={s.notes}>
          <li><PIcon name="info" size={15} />기한 안에 보냈다면 확인이 늦어도 지급해요</li>
          <li><PIcon name="top" size={15} />요금제 화면 맨 위에서 다시 볼 수 있어요</li>
        </ul>
        <div className={s.foot}>
          <button type="button" className={s.primary} onClick={onClose}>확인</button>
        </div>
      </div>
    </>
  );
}

export function BankTransferModal({ plan, info, defaultEmail, extend = false, onClose, onSubmitted }) {
  const titleId = useId();
  const [form, setForm] = useState({
    payerName: '', taxInvoice: false, businessNo: '', businessName: '',
    representativeName: '', invoiceEmail: defaultEmail || '',
  });
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState('');
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(null);     // 서버가 돌려준 신청
  const nameRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    nameRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const set = (key) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
    setErrors((prev) => (prev[key] ? { ...prev, [key]: '' } : prev));
  };

  function validate() {
    const next = {};
    if (form.payerName.trim().length < 2) next.payerName = '입금자명을 2자 이상 적어 주세요';
    if (form.taxInvoice) {
      if (digits(form.businessNo).length !== 10) next.businessNo = '숫자 10자리로 적어 주세요';
      if (!form.businessName.trim()) next.businessName = '상호를 적어 주세요';
      if (!form.representativeName.trim()) next.representativeName = '대표자 성명을 적어 주세요';
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.invoiceEmail.trim())) next.invoiceEmail = '이메일 형식을 확인해 주세요';
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function submit(e) {
    e.preventDefault();
    setServerError('');
    if (!validate()) return;
    setPending(true);
    try {
      const result = await api.createBankTransferRequest({
        planCode: plan.code,
        payerName: form.payerName.trim(),
        phone: null,
        taxInvoice: form.taxInvoice,
        businessNo: form.taxInvoice ? form.businessNo : null,
        businessName: form.taxInvoice ? form.businessName.trim() : null,
        representativeName: form.taxInvoice ? form.representativeName.trim() : null,
        invoiceEmail: form.taxInvoice ? form.invoiceEmail.trim() : null,
        note: null,
      });
      setDone(result.request);
      onSubmitted?.(result.request);
    } catch (err) {
      setServerError(err?.message || '신청을 접수하지 못했어요.');
    } finally {
      setPending(false);
    }
  }

  const field = (key, label, props = {}) => (
    <div className={s.field}>
      <label htmlFor={`${titleId}-${key}`}>{label}</label>
      <input id={`${titleId}-${key}`} className={`${s.input}${errors[key] ? ' ' + s.inputError : ''}`}
        value={form[key]} onChange={set(key)} aria-invalid={Boolean(errors[key])}
        aria-describedby={errors[key] ? `${titleId}-${key}-err` : undefined} {...props} />
      {errors[key] && <p className={s.err} id={`${titleId}-${key}-err`}><PIcon name="alert" size={15} />{errors[key]}</p>}
    </div>
  );

  return createPortal(
    <div className={s.overlay} onClick={onClose}>
      <div className={`${s.dialog}${done ? ' ' + s.isDone : ''}`} role="dialog" aria-modal="true"
        aria-labelledby={titleId} onClick={(e) => e.stopPropagation()}>
        {done ? (
          <DoneView done={done} plan={plan} info={info} onClose={onClose} titleId={titleId} />
        ) : (
          <>
            <Summary plan={plan} info={info} />
            <header className={s.head}>
              <span className={s.grab} aria-hidden="true" />
              <h2 id={titleId} className={s.title}>{extend ? '이용권 1개월 연장 신청' : '계좌이체로 신청'}</h2>
              <button type="button" className={s.close} onClick={onClose} aria-label="닫기"><PIcon name="x" size={20} /></button>
            </header>
            <form className={s.body} onSubmit={submit} noValidate>
              <div className={s.field}>
                <label htmlFor={`${titleId}-payerName`}>입금자명 <span className={s.req}>필수</span></label>
                <input id={`${titleId}-payerName`} ref={nameRef}
                  className={`${s.input}${errors.payerName ? ' ' + s.inputError : ''}`}
                  value={form.payerName} onChange={set('payerName')} maxLength={30} placeholder="실명" autoComplete="name"
                  aria-invalid={Boolean(errors.payerName)}
                  aria-describedby={errors.payerName ? `${titleId}-payerName-err` : `${titleId}-payerName-help`} />
                {errors.payerName
                  ? <p className={s.err} id={`${titleId}-payerName-err`}><PIcon name="alert" size={15} />{errors.payerName}</p>
                  : <p className={s.help} id={`${titleId}-payerName-help`}>통장에 찍힐 이름 그대로 적어 주세요</p>}
              </div>

              <div className={s.foot}>
                <label className={s.taxToggle}>
                  <input type="checkbox" checked={form.taxInvoice} onChange={set('taxInvoice')} />
                  <span className={s.box}><PIcon name="check" size={12} stroke={3} /></span>
                  세금계산서 발행
                </label>
                {form.taxInvoice && (
                  <div className={s.taxBody}>
                    {field('businessNo', '사업자등록번호', { inputMode: 'numeric', maxLength: 12, placeholder: '000-00-00000' })}
                    {field('businessName', '상호', { maxLength: 60 })}
                    {field('representativeName', '대표자 성명', { maxLength: 30 })}
                    {field('invoiceEmail', '받을 이메일', { inputMode: 'email', maxLength: 254 })}
                  </div>
                )}
                <p className={s.due}><PIcon name="clock" size={16} />신청 후 3일 안에 입금해 주세요</p>
                {serverError && <p className={s.err} role="alert"><PIcon name="alert" size={15} />{serverError}</p>}
                <button type="submit" className={s.primary} disabled={pending}>
                  {pending ? '접수하는 중…' : '신청하기'}
                </button>
                <p className={s.consent}>
                  결제하면 <a href={WEARLESS_LEGAL_URLS.terms}>이용약관</a>과 <a href={WEARLESS_LEGAL_URLS.refund}>환불 정책</a>에 동의하는 것으로 봐요.
                </p>
              </div>
            </form>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}

export default BankTransferModal;
