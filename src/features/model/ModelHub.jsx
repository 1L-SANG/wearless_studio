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
  getSettlementSummary,
} from '@/lib/api/facemarket.js';
import {
  APPROVAL_MODE,
  formatKrw,
  validityLabel,
} from '../facemarket-landing/facemarketTerms.js';
import { hasCurrentEnrollmentLicense, resolveHubJourney } from './modelHubState.js';
import s from './ModelPersonalization.module.css';
import { seoulDate } from '@/lib/datetime.js';
import { FACEMARKET_PRICING } from '../../lib/facemarketPricing.js';

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

function ActiveDashboard({ license, model, settlementSummary }) {
  const now = new Date();
  const thisMonth = settlementSummary;
  const monthLabel = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', month: '2-digit',
  }).format(now);

  const unitPrice = license ? FACEMARKET_PRICING.perCut : null;
  const validity = !license
    ? '—'
    : license.validityDays != null
    ? validityLabel(license.validityDays)
    : license.licenseValidUntil
      ? `${seoulDate(license.licenseValidUntil)}까지`
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
            <div><dt>허용 품목</dt><dd>{license ? `${license.allowedUse?.length || 0}개` : '—'}</dd></div>
            <div><dt>제외 품목</dt><dd>{license ? `${license.forbiddenUse?.length || 0}개` : '—'}</dd></div>
            <div><dt>건당 가격</dt><dd>{unitPrice == null ? '—' : formatKrw(unitPrice)}</dd></div>
            <div><dt>월정액</dt><dd>{unitPrice == null ? '—' : formatKrw(FACEMARKET_PRICING.monthly)}</dd></div>
            <div><dt>유효기간</dt><dd>{validity}</dd></div>
            <div><dt>승인 방식</dt><dd>{license ? (APPROVAL_MODE === 'auto' ? '자동' : APPROVAL_MODE) : '—'}</dd></div>
          </dl>
          <Link className={s.hubTextLink} to="/model/license">규칙 바꾸기 <span aria-hidden="true">→</span></Link>
        </section>

        <section className={s.hubActiveCard}>
          <div className={s.hubCardHeading}>
            <span>이번 달 요약</span>
            <strong>{monthLabel}</strong>
          </div>
          <dl className={s.hubMonthFigures}>
            <div><dt>사용 건수</dt><dd>{thisMonth.monthCount}건</dd></div>
            <div><dt>내 몫</dt><dd>{formatKrw(thisMonth.monthAmount)}</dd></div>
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

function ConfirmationNotice({ model, onConfirm, onRefresh }) {
  if (model?.status === 'awaiting_confirm') {
    return (
      <section className={s.hubGateNotice} aria-labelledby="test-cuts-ready-title">
        <div>
          <p className={s.hubNextEyebrow}>공개 전 마지막 확인</p>
          <h2 className={s.hubNextTitle} id="test-cuts-ready-title">테스트컷이 도착했어요</h2>
          <p className={s.hubNextBody}>프로필로 쓸 컷을 직접 고르고 공개 품질을 확인해 주세요.</p>
        </div>
        <Button variant="primary" iconRight="arrowRight" onClick={onConfirm}>확인하러 가기</Button>
      </section>
    );
  }
  if (model?.status === 'pending' && model?.redoCount > 0) {
    return (
      <section className={s.hubGateNotice} aria-labelledby="test-cuts-redo-title">
        <div>
          <p className={s.hubNextEyebrow}>테스트 컷 생성</p>
          <h2 className={s.hubNextTitle} id="test-cuts-redo-title">다시 만드는 중이에요</h2>
          <p className={s.hubNextBody}>새 테스트컷이 준비되면 여기와 이메일로 알려드려요.</p>
        </div>
        <Button variant="ghost" icon="refresh" onClick={onRefresh}>상태 새로고침</Button>
      </section>
    );
  }
  return null;
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
  const [settlementSummary, setSettlementSummary] = useState(null);

  const activeLicense = useMemo(() => {
    const sameModel = licenses.find((license) => license.modelId === ownedModel?.id);
    return sameModel || licenses[0] || null;
  }, [licenses, ownedModel?.id]);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const [mine, cfg, app, enr, licenseRows, summary] = await Promise.all([
        listMyModels(),
        loadOptional(getApplicationConfig),
        loadOptional(getCurrentApplication),
        loadOptional(getCurrentEnrollment),
        listLicenses(),
        getSettlementSummary(),
      ]);
      setOwnedModel(mine?.[0] || null);
      setApplicationRequired(cfg?.applicationRequired !== false);
      setApplication(app);
      setEnrollment(enr);
      setLicenses(Array.isArray(licenseRows) ? licenseRows : []);
      setSettlementSummary(summary);
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

  const journey = resolveHubJourney({
    ownedModel,
    enrollment,
    application,
    applicationRequired,
    hasLicense: hasCurrentEnrollmentLicense(licenses),
  });
  const onJourneyAction = () => {
    if (journey.action?.kind === 'route') navigate(journey.action.to);
    else if (journey.action?.kind === 'cancel') onCancelApplication();
    else if (journey.action?.kind === 'reload') load();
  };

  return (
    <div className={s.hubPage}>
      <HubHead active={journey.mode === 'active'} />
      <ConfirmationNotice
        model={ownedModel}
        onConfirm={() => navigate('/model/confirm')}
        onRefresh={load}
      />
      {journey.mode === 'active' ? (
        <ActiveDashboard
          license={activeLicense}
          model={ownedModel}
          settlementSummary={settlementSummary}
        />
      ) : <Timeline journey={journey} onAction={onJourneyAction} />}
    </div>
  );
}

export default ModelHub;
