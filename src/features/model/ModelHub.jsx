/* FaceMarket 모델의 온보딩 진행표와 활동 중 Digital DNA 대시보드. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  cancelApplication,
  getApplicationConfig,
  getCurrentApplication,
  getCurrentEnrollment,
  listLicenses,
  listMyModels,
  listSettlements,
} from '@/lib/api/facemarket.js';
import {
  APPROVAL_MODE,
  UNIT_PRICE_DEFAULT_KRW,
  formatKrw,
  monthlyPriceFor,
  validityLabel,
} from '../facemarket-landing/facemarketTerms.js';
import { resolveHubJourney } from './modelHubState.js';
import s from './ModelPersonalization.module.css';

async function loadOptional(fn) {
  try { return await fn(); }
  catch (error) { if (error?.status === 404) return null; throw error; }
}

function HubHead({ active = false }) {
  return (
    <header className={s.hubHead}>
      <p className={s.hubEyebrow}>FaceMarket 모델</p>
      <h1 className={s.hubTitle}>Digital DNA 관리</h1>
      <p className={s.hubLead}>
        {active
          ? '내 트윈과 사용 규칙, 정산 기록을 한곳에서 관리해요.'
          : '지원부터 활동 시작까지 지금 어디에 있는지 한눈에 확인해요.'}
      </p>
    </header>
  );
}

function Timeline({ journey, onAction }) {
  return (
    <section aria-labelledby="hub-timeline-title" className={s.hubTimelineSection}>
      <div className={s.hubTimelineHead}>
        <p className={s.hubNextEyebrow}>온보딩 타임라인</p>
        <h2 className={s.hubNextTitle} id="hub-timeline-title">활동까지 한 단계씩 이어가요</h2>
      </div>
      <ol className={s.hubTimeline}>
        {journey.steps.map((step, index) => {
          const current = step.state === 'current';
          const done = step.state === 'done';
          return (
            <li
              aria-current={current ? 'step' : undefined}
              className={`${s.hubTimelineStep} ${s[`hubTimeline_${step.state}`]}`}
              key={step.key}
            >
              <div className={s.hubTimelineRail} aria-hidden="true">
                <span className={s.hubTimelineDot}>
                  {done ? <Icon name="check" size={13} stroke={2.6} /> : String(index + 1).padStart(2, '0')}
                </span>
              </div>
              <div className={s.hubTimelineCopy}>
                <span className={s.hubTimelineState}>{done ? '완료' : current ? '현재' : '예정'}</span>
                <h3>{step.label}</h3>
                <p>{step.description}</p>
                {current && journey.action ? (
                  <Button variant="primary" iconRight="arrowRight" onClick={onAction}>
                    {journey.action.label}
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function TwinImage({ alt, label, src }) {
  return (
    <figure className={s.hubTwinFigure}>
      <div className={s.hubTwinImage}>
        {src ? <img alt={alt} src={src} /> : <Icon name="person" size={30} stroke={1.4} />}
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function ActiveDashboard({ license, model, settlements }) {
  const now = new Date();
  const thisMonth = settlements.reduce((summary, item) => {
    const created = new Date(item.createdAt);
    if (!Number.isNaN(created.getTime())
      && created.getFullYear() === now.getFullYear()
      && created.getMonth() === now.getMonth()) {
      return { count: summary.count + 1, amount: summary.amount + Number(item.modelAmount || 0) };
    }
    return summary;
  }, { count: 0, amount: 0 });

  const unitPrice = license?.unitPrice ?? model?.unitPrice ?? UNIT_PRICE_DEFAULT_KRW;
  const validity = license?.validityDays != null
    ? validityLabel(license.validityDays)
    : license?.licenseValidUntil
      ? `${new Date(license.licenseValidUntil).toLocaleDateString('ko-KR')}까지`
      : validityLabel(null);
  const verifyPath = license?.id ? `/verify/${encodeURIComponent(license.id)}` : null;
  const cover = model?.coverImageUrl || license?.coverImageUrl || null;
  const testCut = model?.testCutUrl || license?.testCutUrl || null;

  return (
    <div className={s.hubActive}>
      <section className={s.hubActiveIntro}>
        <div>
          <span className={s.hubActiveBadge}><Icon name="check" size={13} stroke={2.5} /> 확정 · 활동 중</span>
          <h2>활동 중</h2>
          <p>{model?.displayName || '내 Digital DNA'}가 정한 규칙 안에서 셀러를 만나고 있어요.</p>
        </div>
        <Link className={s.hubOutlineLink} to="/model/license">계약서·증서 보기</Link>
      </section>

      <div className={s.hubActiveGrid}>
        <section className={`${s.hubActiveCard} ${s.hubActiveCardWide}`}>
          <div className={s.hubCardHeading}>
            <span>내 트윈</span>
            <strong>{model?.displayName || 'Digital DNA'}</strong>
          </div>
          <div className={s.hubTwinGrid}>
            <TwinImage alt="내 트윈 대표 컷" label="대표 컷" src={cover} />
            <TwinImage alt="내 트윈 테스트 컷" label="테스트 컷" src={testCut} />
          </div>
          {verifyPath ? (
            <Link className={s.hubSignature} to={verifyPath}>
              <span>서명 URL</span>
              <code>{verifyPath}</code>
              <Icon name="arrowRight" size={14} />
            </Link>
          ) : <p className={s.hubCardHint}>증서가 발급되면 서명 URL이 표시돼요.</p>}
        </section>

        <section className={s.hubActiveCard}>
          <div className={s.hubCardHeading}>
            <span>내 규칙</span>
            <strong>자동 승인</strong>
          </div>
          <dl className={s.hubRuleList}>
            <div><dt>허용 품목</dt><dd>{license?.allowedUse?.length || 0}개</dd></div>
            <div><dt>제외 품목</dt><dd>{license?.forbiddenUse?.length || 0}개</dd></div>
            <div><dt>건당 가격</dt><dd>{formatKrw(unitPrice)}</dd></div>
            <div><dt>월정액</dt><dd>{formatKrw(monthlyPriceFor(unitPrice))}</dd></div>
            <div><dt>유효기간</dt><dd>{validity}</dd></div>
            <div><dt>승인 방식</dt><dd>{APPROVAL_MODE === 'auto' ? '자동' : APPROVAL_MODE}</dd></div>
          </dl>
          <Link className={s.hubTextLink} to="/model/license">규칙 바꾸기 <span aria-hidden="true">→</span></Link>
        </section>

        <section className={s.hubActiveCard}>
          <div className={s.hubCardHeading}>
            <span>이번 달 요약</span>
            <strong>{String(now.getMonth() + 1).padStart(2, '0')}월</strong>
          </div>
          <dl className={s.hubMonthFigures}>
            <div><dt>사용 건수</dt><dd>{thisMonth.count}건</dd></div>
            <div><dt>내 몫</dt><dd>{formatKrw(thisMonth.amount)}</dd></div>
          </dl>
          <Link className={s.hubTextLink} to="/payout">정산 <span aria-hidden="true">→</span></Link>
        </section>
      </div>

      <div className={s.hubFoot}>
        <Link to="/model/withdraw" className={s.hubFootLink}>
          <Icon name="logOut" size={14} />그만두기
        </Link>
      </div>
    </div>
  );
}

export function ModelHub() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading');
  const [ownedModel, setOwnedModel] = useState(null);
  const [enrollment, setEnrollment] = useState(null);
  const [application, setApplication] = useState(null);
  const [applicationRequired, setApplicationRequired] = useState(true);
  const [licenses, setLicenses] = useState([]);
  const [settlements, setSettlements] = useState([]);

  const activeLicense = useMemo(() => {
    const sameModel = licenses.find((license) => license.modelId === ownedModel?.id);
    return sameModel || licenses[0] || null;
  }, [licenses, ownedModel?.id]);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const [mine, cfg, app, enr, licenseRows, settlementRows] = await Promise.all([
        listMyModels(),
        loadOptional(getApplicationConfig),
        loadOptional(getCurrentApplication),
        loadOptional(getCurrentEnrollment),
        listLicenses().catch(() => []),
        listSettlements().catch(() => []),
      ]);
      setOwnedModel(mine?.[0] || null);
      setApplicationRequired(cfg?.applicationRequired !== false);
      setApplication(app);
      setEnrollment(enr);
      setLicenses(Array.isArray(licenseRows) ? licenseRows : []);
      setSettlements(Array.isArray(settlementRows) ? settlementRows : []);
      setPhase('ready');
    } catch (error) {
      push?.(error.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const onCancelApplication = useCallback(async () => {
    if (!application) return;
    try {
      await cancelApplication(application.id);
      push?.('지원을 취소했어요.', { icon: 'check' });
      load();
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
  }, [application, load, push]);

  if (phase === 'loading') {
    return <div className={s.hubPage}><HubHead /><p className={s.hubLoading}>불러오는 중…</p></div>;
  }
  if (phase === 'error') {
    return (
      <div className={s.hubPage}>
        <HubHead />
        <div className={s.hubLoading}><ErrorState desc="상태를 불러오지 못했어요." onRetry={load} /></div>
      </div>
    );
  }

  const journey = resolveHubJourney({ ownedModel, enrollment, application, applicationRequired });
  const onJourneyAction = () => {
    if (journey.action?.kind === 'route') navigate(journey.action.to);
    else if (journey.action?.kind === 'cancel') onCancelApplication();
    else if (journey.action?.kind === 'reload') load();
  };

  return (
    <div className={s.hubPage}>
      <HubHead active={journey.mode === 'active'} />
      {journey.mode === 'active' ? (
        <ActiveDashboard
          license={activeLicense}
          model={ownedModel}
          settlements={settlements}
        />
      ) : <Timeline journey={journey} onAction={onJourneyAction} />}
    </div>
  );
}

export default ModelHub;
