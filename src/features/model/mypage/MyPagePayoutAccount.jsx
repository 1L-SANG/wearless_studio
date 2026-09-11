import { useEffect, useRef, useState } from 'react';
import { ChevronRight, FileText } from 'lucide-react';
import { MyPageDialog } from './MyPageDialog.jsx';
import { PAYOUT_ACCOUNT_API_READY, PAYOUT_BANKS, getPayoutAccount, savePayoutAccount, validatePayoutAccount, normalizePayoutAccount, maskAccountNumber, payoutAccountLabel } from './payoutAccount.js';
import s from './MyPage.module.css';

export function usePayoutAccount(modelId, enabled) {
  const [account, setAccount] = useState(null);
  const [phase, setPhase] = useState('loading');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!PAYOUT_ACCOUNT_API_READY || !enabled) return;
    let alive = true;
    setPhase('loading');
    getPayoutAccount().then(value => {
      if (alive) { setAccount(value); setPhase('ready'); setError(''); }
    }).catch(cause => {
      if (alive) { setPhase('error'); setError(cause.message || '입금 계좌를 불러오지 못했어요.'); }
    });
    return () => { alive = false; };
  }, [modelId, enabled, attempt]);
  return { account, phase, error, setAccount, retry: () => setAttempt(value => value + 1) };
}

export function AccountShortcut({ bank, onOpen }) {
  return <button type="button" className={`${s.quietButton} ${s.accountShortcut}`} disabled={!PAYOUT_ACCOUNT_API_READY || bank.phase !== 'ready'} onClick={onOpen}>
    <FileText className={s.icon} aria-hidden="true" />{bank.account ? '입금 계좌 관리' : '입금 계좌 등록'}
    {!PAYOUT_ACCOUNT_API_READY && <span>준비 중</span>}<ChevronRight className={s.icon} aria-hidden="true" />
  </button>;
}

export function BankSection({ bank, onOpen }) {
  return <section className={s.bankSection} aria-label="입금 계좌"><div className={s.bankHeading}>
    <span className={s.bankIcon}><FileText className={s.icon} aria-hidden="true" /></span>
    <div><h3>입금 계좌</h3><p>{!PAYOUT_ACCOUNT_API_READY ? '입금 계좌 등록은 준비 중이에요. 열리면 여기에서 등록할 수 있어요.'
      : bank.phase === 'loading' ? '입금 계좌를 불러오고 있어요.' : bank.phase === 'error' ? bank.error
      : bank.account ? payoutAccountLabel(bank.account) : '정산금을 받을 본인 명의 계좌를 등록해 주세요.'}</p></div>
    {PAYOUT_ACCOUNT_API_READY && bank.phase === 'error' ? <button type="button" className={s.quietButton} onClick={bank.retry}>다시 불러오기</button>
      : <button type="button" className={s.quietButton} disabled={!PAYOUT_ACCOUNT_API_READY || bank.phase !== 'ready'} onClick={onOpen}>{bank.account ? '계좌 변경' : '계좌 등록'}</button>}
  </div></section>;
}

export function PayoutAccountDialog({ account, model, onClose, onSaved }) {
  const [form, setForm] = useState({ bankCode: account?.bankCode || '', accountNumber: '', holderName: account?.holderName || model?.displayName || '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const saving = useRef(false);
  const update = (field, value) => { setForm(previous => ({ ...previous, [field]: value })); setError(''); };
  const save = async event => {
    event.preventDefault();
    if (saving.current || !PAYOUT_ACCOUNT_API_READY) return;
    if (!/^[0-9-]{8,24}$/.test(form.accountNumber) || !validatePayoutAccount(form)) {
      setError('은행, 계좌번호 8~16자리, 예금주를 확인해 주세요.'); return;
    }
    saving.current = true;
    setBusy(true);
    setError('');
    try {
      const normalized = normalizePayoutAccount(form);
      const updated = await savePayoutAccount(normalized);
      onSaved({ bankCode: updated?.bankCode || normalized.bankCode, holderName: updated?.holderName || normalized.holderName,
        accountNumberMasked: updated?.accountNumberMasked || maskAccountNumber(normalized.accountNumber) });
    } catch (cause) { setError(cause.message || '입금 계좌를 저장하지 못했어요. 다시 시도해 주세요.'); }
    finally { saving.current = false; setBusy(false); }
  };
  return <MyPageDialog title={account ? '입금 계좌 변경' : '입금 계좌 등록'} busy={busy} onClose={onClose}>
    <form onSubmit={save} noValidate><p>정산금을 받을 본인 명의 계좌를 등록해 주세요.</p>
      <fieldset className={s.bankFields} disabled={busy || !PAYOUT_ACCOUNT_API_READY}>
        <label className={s.bankField}>은행<select value={form.bankCode} onChange={event => update('bankCode', event.target.value)}>
          <option value="">은행 선택</option>{PAYOUT_BANKS.map(bank => <option value={bank.code} key={bank.code}>{bank.name}</option>)}</select></label>
        <label className={s.bankField}>계좌번호<input type="text" inputMode="numeric" autoComplete="off" pattern="[0-9-]{8,24}" maxLength={24}
          placeholder={account?.accountNumberMasked || '숫자와 하이픈으로 입력'} value={form.accountNumber} onChange={event => update('accountNumber', event.target.value)} /></label>
        <label className={s.bankField}>예금주<input type="text" autoComplete="off" maxLength={40} value={form.holderName} onChange={event => update('holderName', event.target.value)} /></label>
      </fieldset>
      {error && <p role="alert" className={s.error}>{error}</p>}
      <div className={s.actionRow}><button type="button" className={s.quietButton} disabled={busy} onClick={onClose}>취소</button>
        <button type="submit" className={s.primaryButton} disabled={busy || !PAYOUT_ACCOUNT_API_READY}>{account ? '변경하기' : '등록하기'}</button></div>
    </form>
  </MyPageDialog>;
}
