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
  APPLICATION_REJECT_PURGE_DAYS,
  REVIEW_SLA_LABEL,
  formatKrw,
  validityLabel,
} from '../facemarket-landing/facemarketTerms.js';
import { hasCurrentEnrollmentLicense, resolveHubJourney } from './modelHubState.js';
import s from './ModelPersonalization.module.css';
import { seoulDate, seoulDateTime } from '@/lib/datetime.js';
import { FACEMARKET_PRICING } from '../../lib/facemarketPricing.js';

async function loadOptional(fn) {
  try { return await fn(); }
  catch (error) { if (error?.status === 404) return null; throw error; }
}

function HubHead({ active = false, application, email, enrollment, journey }) {
  let title = 'Digital DNA 관리';
  let lead = '지원부터 활동 시작까지 지금 어디에 있는지 한눈에 확인해요.';

  if (active) {
    lead = '내 트윈과 사용 규칙, 정산 기록을 한곳에서 관리해요.';
  } else if (application?.status === 'under_review' && journey?.currentIndex === 1) {
    const receivedAt = seoulDateTime(application.submittedAt || application.createdAt);
    title = '지원서를 검토하고 있어요';
    lead = `${receivedAt ? `${receivedAt}에` : '지원서를'} 받았어요. ${REVIEW_SLA_LABEL}에 ${application.contactEmail || email || '등록한 이메일'}으로 결과를 알려드려요.`;
  } else if (journey?.currentIndex === 2 && !enrollment) {
    title = '승인됐어요. 이제 등록을 시작해요';
    lead = '본인확인, 사진, 조건, 증서 4단계예요. 10분이면 끝나요. 승인 메일의 링크를 눌러도 이 화면으로 와요.';
  } else if (journey?.currentIndex === 2) {
    title = '등록을 이어서 완료해요';
    lead = '본인확인, 사진, 조건, 증서를 모두 마치면 최종 검토가 시작돼요.';
  } else if (journey?.currentIndex === 3) {
    title = '등록서를 최종 검토하고 있어요';
    lead = `${REVIEW_SLA_LABEL}에 ${application?.contactEmail || email || '등록한 이메일'}로 결과를 알려드려요.`;
  } else if (journey?.currentIndex === 4) {
    title = '프로필 이미지를 확정해요';
    lead = '사용할 프로필 이미지를 선택하면 Digital DNA 등록이 끝나요.';
  } else if (journey?.currentIndex === 0) {
    title = 'Digital DNA 모델로 지원해보세요';
    lead = '지원서를 보내면 검토부터 모델 등록까지 이 화면에서 확인할 수 있어요.';
  }

  return (
    <header className={s.hubHead}>
      <p className={s.hubEyebrow}>Digital DNA 관리</p>
      <h1 className={s.hubTitle}>{title}</h1>
      <p className={s.hubLead}>{lead}</p>
    </header>
  );
}

function Timeline({ application, journey, onAction, onCancel }) {
  const submittedAt = seoulDateTime(application?.submittedAt || application?.createdAt);
  const reviewedAt = seoulDateTime(application?.reviewedAt);

  return (
    <section aria-label="Digital DNA 등록 여정" className={s.hubJourneySection}>
      <ol className={s.hubJourneyList}>
        {journey.steps.map((step, index) => {
          const current = step.state === 'current';
          const done = step.state === 'done';
          const actionCard = current && [2, 4].includes(index) && journey.action?.kind === 'route';
          const completedAt = index === 0 ? submittedAt : index === 1 ? reviewedAt : null;
          const stateLabel = done
            ? index === 1 && application?.status === 'approved' ? '승인 완료' : '완료'
            : current ? actionCard ? '지금 할 일' : '진행 중' : null;
          return (
            <li
              aria-current={current ? 'step' : undefined}
              className={`${s.hubJourneyStep} ${s[`hubJourney_${step.state}`]}`}
              key={step.key}
            >
              <div className={s.hubJourneyRail} aria-hidden="true">
                <span className={s.hubJourneyDot}>
                  {done ? <Icon name="check" size={13} stroke={2.6} /> : index + 1}
                </span>
              </div>
              <div className={`${s.hubJourneyCopy} ${actionCard ? s.hubJourneyActionCard : ''} ${actionCard && index === 2 ? s.hubJourneyActionCardGradient : ''}`}>
                <div className={s.hubJourneyText}>
                  <h3>
                    {step.label}
                    {stateLabel ? <span className={s.hubJourneyState}>{stateLabel}</span> : null}
                  </h3>
                  {done && completedAt ? <p className={s.hubJourneyWhen}>{completedAt}</p> : null}
                  {current && step.description ? <p>{step.description}</p> : null}
                </div>
                {actionCard ? (
                  <Button variant="primary" iconRight="arrowRight" onClick={onAction}>
                    {journey.action.label}
                  </Button>
                ) : current && journey.action && journey.action.kind !== 'cancel' ? (
                  <Button
                    icon={journey.action.kind === 'reload' ? 'refresh' : undefined}
                    iconRight={journey.action.kind === 'route' ? 'arrowRight' : undefined}
                    onClick={onAction}
                    variant={journey.action.kind === 'reload' ? 'ghost' : 'primary'}
                  >
                    {journey.action.label}
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      {journey.action?.kind === 'cancel' ? (
        <div className={s.hubJourneyCancelRow}>
          <button className={s.hubJourneyCancel} onClick={onCancel} type="button">지원 취소</button>
        </div>
      ) : null}
    </section>
  );
}

function RejectedApplication({ application, onAction }) {
  const reviewedAt = seoulDateTime(application?.reviewedAt);
  return (
    <div className={s.hubRejected}>
      <header className={s.hubHead}>
        <p className={s.hubEyebrow}>Digital DNA 관리</p>
        <h1 className={s.hubTitle}>이번에는 승인되지 않았어요</h1>
        <p className={s.hubLead}>
          {reviewedAt ? `${reviewedAt}에 ` : ''}검토가 끝났어요. 아래 사유를 보고 고쳐서 다시 지원할 수 있어요.
        </p>
      </header>
      {application?.rejectReason ? (
        <section className={s.hubRejectReason} aria-labelledby="hub-reject-reason-title">
          <h2 id="hub-reject-reason-title">검토한 사람이 남긴 사유</h2>
          <p>{application.rejectReason}</p>
        </section>
      ) : (
        <p className={s.hubRejectMissing}>
          검토자가 사유를 남기지 않았어요. 준비할 것을 다시 보고 지원해 주세요.
          <Link to="/apply">지원 준비 다시 보기</Link>
        </p>
      )}
      <dl className={s.hubRejectRetention}>
        <div><dt>적은 내용</dt><dd>{APPLICATION_REJECT_PURGE_DAYS}일 안에 지워요. 그전에 다시 지원하면 불러와서 고칠 수 있어요.</dd></div>
        <div><dt>사진</dt><dd>같이 지워요. 다시 지원할 때 새로 올려 주세요.</dd></div>
      </dl>
      <Link className={s.hubRejectBrowse} to="/models">모델 리스트 둘러보기</Link>
      <div className={s.hubRejectAction}>
        <Button variant="primary" iconRight="arrowRight" onClick={onAction}>다시 지원하기</Button>
      </div>
    </div>
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

export function ModelHub({ email = '' }) {
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
      {application?.status === 'rejected' && journey.mode === 'onboarding' && journey.currentIndex === 1 ? (
        <RejectedApplication application={application} onAction={onJourneyAction} />
      ) : journey.mode === 'active' ? (
        <>
          <HubHead active />
          <ActiveDashboard
            license={activeLicense}
            model={ownedModel}
            settlementSummary={settlementSummary}
          />
        </>
      ) : (
        <div className={s.hubOnboarding}>
          <HubHead application={application} email={email} enrollment={enrollment} journey={journey} />
          <Timeline
            application={application}
            journey={journey}
            onAction={onJourneyAction}
            onCancel={onCancelApplication}
          />
        </div>
      )}
    </div>
  );
}

export default ModelHub;
