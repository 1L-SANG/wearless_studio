/* 지원 상태와 라이선스를 불러와 마이페이지에 연결해요. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ErrorState, useToast } from '@/components/ui.jsx';
import { cancelApplication, getApplicationConfig, getCurrentApplication, getCurrentEnrollment, listLicenses, listMyModels } from '@/lib/api/facemarket.js';
import { currentModelLicense, hasCurrentEnrollmentLicense, resolveHubJourney } from './modelHubState.js';
import { MyPage } from './mypage/MyPage.jsx';
import s from './mypage/MyPage.module.css';

async function loadOptional(fn) {
  try { return await fn(); }
  catch (error) { if (error?.status === 404) return null; throw error; }
}

export function ModelHub({ onSignOut }) {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading');
  const [ownedModel, setOwnedModel] = useState(null);
  const [enrollment, setEnrollment] = useState(null);
  const [application, setApplication] = useState(null);
  const [applicationRequired, setApplicationRequired] = useState(true);
  const [licenses, setLicenses] = useState([]);
  const [attempt, setAttempt] = useState(0);
  const license = useMemo(() => currentModelLicense(licenses, ownedModel), [licenses, ownedModel]);
  const journey = resolveHubJourney({ ownedModel, enrollment, application, applicationRequired, license, hasLicense: hasCurrentEnrollmentLicense(licenses) });
  const load = useCallback(() => setAttempt(value => value + 1), []);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    Promise.all([listMyModels(), loadOptional(getApplicationConfig), loadOptional(getCurrentApplication), loadOptional(getCurrentEnrollment), listLicenses({ includeRevoked: true })])
      .then(([mine, cfg, app, enr, rows]) => {
        if (!alive) return;
        setOwnedModel(mine?.[0] || null);
        setApplicationRequired(cfg?.applicationRequired !== false);
        setApplication(app);
        setEnrollment(enr);
        setLicenses(Array.isArray(rows) ? rows : []);
        setPhase('ready');
      }).catch(() => { if (alive) setPhase('error'); });
    return () => { alive = false; };
  }, [attempt]);

  const onJourneyAction = async () => {
    if (journey.action?.kind === 'route') navigate(journey.action.to);
    else if (journey.action?.kind === 'reload') load();
    else if (journey.action?.kind === 'cancel' && application) {
      try { await cancelApplication(application.id); load(); }
      catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    }
  };
  const onLicenseChange = updated => setLicenses(rows => rows.map(row => row.id === updated.id ? updated : row));
  if (phase === 'loading') return <div className={s.page}><h1>마이페이지</h1><p role="status">불러오는 중이에요.</p></div>;
  if (phase === 'error') return <div className={s.page}><h1>마이페이지</h1><ErrorState desc="상태를 불러오지 못했어요." onRetry={load} /></div>;
  return <MyPage journey={journey} model={ownedModel} enrollment={enrollment} application={application} license={license} licenses={licenses}
    onAction={onJourneyAction} onModelChange={setOwnedModel} onLicenseChange={onLicenseChange} onSignOut={onSignOut} />;
}
export default ModelHub;
