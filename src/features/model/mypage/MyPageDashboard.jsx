import { useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Info } from 'lucide-react';
import { MyPageActivity } from './MyPageActivity.jsx';
import { MyPageCertificate, ProfileHero } from './MyPageCertificate.jsx';
import { MyPageConditions } from './MyPageConditions.jsx';
import { MyPageDialog } from './MyPageDialog.jsx';
import { EarningsFigures, MyPageEarnings, NextPayout, useMyPageSettlements } from './MyPageEarnings.jsx';
import { MyPageUsage } from './MyPageUsage.jsx';
import { AccountShortcut, BankSection, PayoutAccountDialog, usePayoutAccount } from './MyPagePayoutAccount.jsx';
import { PAYOUT_ACCOUNT_API_READY } from './payoutAccount.js';
import { defaultUsageMonth } from './usageMonths.js';
import { isRegistrationJourney, registrationCard, tabFromHash } from './mypageState.js';
import s from './MyPage.module.css';

const tabs = [{ id: 'usage', label: '사용된 페이지' }, { id: 'payout', label: '정산' }, { id: 'license', label: '라이선스' }];
const steps = ['본인 확인', '사진과 조건', '라이선스', '검수', '프로필 확정'];

export function RegistrationProgress({ journey, enrollment }) {
  const card = registrationCard(journey, enrollment);
  return <><div className={s.onboardingCard}><p className={s.eyebrow}>등록 진행</p><h2>{card.title}</h2><p>{card.description}</p>
    <Link className={s.primaryButton} to={card.to}>{card.label}</Link></div>
    <ol className={s.progressList}>{steps.map((label, index) => <li key={label} className={card.currentStep === index + 1 ? s.currentStep : undefined}
      aria-current={card.currentStep === index + 1 ? 'step' : undefined}>{String(index + 1).padStart(2, '0')} {label}</li>)}</ol>
  </>;
}

export function ActiveDashboard({ journey, enrollment, model, license, onModelChange, onLicenseChange }) {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = tabFromHash(location.hash);
  const tabRefs = useRef({});
  const [month, setMonth] = useState(() => defaultUsageMonth([]));
  const [certificate, setCertificate] = useState(false);
  const [activityDialog, setActivityDialog] = useState(null);
  const [accountDialog, setAccountDialog] = useState(false);
  const registering = isRegistrationJourney(journey);
  const data = useMyPageSettlements(model?.id, !registering);
  const bank = usePayoutAccount(model?.id, !registering);
  const chooseTab = id => navigate({ hash: `#${id}` }, { replace: true });
  const keyTab = event => {
    const current = tabs.findIndex(item => item.id === tab);
    let next;
    if (event.key === 'ArrowRight') next = (current + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (current + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    chooseTab(tabs[next].id);
    tabRefs.current[tabs[next].id]?.focus();
  };
  const openAccount = () => { if (PAYOUT_ACCOUNT_API_READY && bank.phase === 'ready') setAccountDialog(true); };
  return <>
    <ProfileHero model={model} license={license} onCertificate={() => setCertificate(true)} />
    <MyPageActivity journey={journey} model={model} license={license} dialog={activityDialog} onDialogChange={setActivityDialog}
      onModelChange={onModelChange} onLicenseChange={onLicenseChange} />
    {!registering && !data.loading && (data.summaryError || data.rowsError || data.statementsError) && <div className={s.stateMessage}><Info className={s.icon} aria-hidden="true" />
      <p>정산 정보만 잠시 확인할 수 없어요.</p><button type="button" className={s.quietButton} onClick={data.retry}>다시 불러오기</button></div>}
    {!registering && <div className={s.settlementStrip}>
      <EarningsFigures summary={data.summary} /><div className={s.transferNote}><p>매달 10일 자동이체</p><NextPayout nextPayout={data.statements?.nextPayout} /></div><AccountShortcut bank={bank} onOpen={openAccount} />
    </div>}
    <div className={s.cardTabs} role="tablist" aria-label="마이페이지 항목">{tabs.map(item => <button key={item.id} type="button" className={s.tab}
      id={`tab-${item.id}`} role="tab" aria-controls={`panel-${item.id}`} aria-selected={item.id === tab} tabIndex={item.id === tab ? 0 : -1}
      ref={element => { tabRefs.current[item.id] = element; }} onKeyDown={keyTab} onClick={() => chooseTab(item.id)}>{item.label}</button>)}</div>
    <section className={s.tabContent} id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
      {tab === 'license' ? <MyPageConditions license={license} model={model} revoked={journey.flag === 'revoked'} registering={registering}
        onLicenseChange={onLicenseChange} onCertificate={() => setCertificate(true)} onManage={() => setActivityDialog('manage')} />
        : registering ? <RegistrationProgress journey={journey} enrollment={enrollment} />
        : tab === 'usage' ? <MyPageUsage data={data} month={month} onMonthChange={setMonth} />
        : <MyPageEarnings data={data} month={month} onMonthChange={setMonth}><BankSection bank={bank} onOpen={openAccount} /></MyPageEarnings>}
    </section>
    {certificate && <MyPageDialog title="내 라이선스 증서" certificate onClose={() => setCertificate(false)}><MyPageCertificate model={model} license={license} revoked={journey.flag === 'revoked'} /></MyPageDialog>}
    {accountDialog && PAYOUT_ACCOUNT_API_READY && <PayoutAccountDialog model={model} account={bank.account} banks={bank.banks} onClose={() => setAccountDialog(false)} onSaved={value => {
      bank.setAccount(value); setAccountDialog(false); chooseTab('payout');
    }} />}
  </>;
}
