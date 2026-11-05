import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { RequireAuth } from '@/apps/guards.jsx';
import { useAuth, forgetPostLogin, readPostLogin } from '@/features/auth/AuthProvider.jsx';
import { Button } from '@/components/ui.jsx';
import { captureCafe24Launch, consumeCafe24Launch, CAFE24_INSTALL_COPY } from '@/lib/cafe24Launch.js';
import { api, isMockMode } from '@/lib/api/index.js';
import styles from '@/apps/guards.module.css';

export function Cafe24LaunchGate() {
  const { session, loading, openLogin } = useAuth();
  const { search } = useLocation();
  const [entry, setEntry] = useState(null);
  const [error, setError] = useState('');
  const opened = useRef(false);
  const claiming = useRef(false);
  const launching = useRef(false);
  const active = useRef(true);
  const returnTo = useRef('/settings/malls');
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const back = params.get('returnTo');
    const pending = params.get('mall') === 'cafe24' && params.get('result') === 'pending';
    const result = params.get('result');
    const failure = params.get('mall') === 'cafe24' && Object.hasOwn(CAFE24_INSTALL_COPY, result) ? result : null;
    returnTo.current = `/settings/malls?${new URLSearchParams({ ...(pending ? { mall: 'cafe24', result: 'pending' } : {}), ...(back ? { returnTo: back } : {}) })}`;
    if (isMockMode) { setEntry({}); return; }
    if (launching.current) return;
    const captured = captureCafe24Launch();
    const launch = consumeCafe24Launch() || captured;
    if (!launch) { setEntry({ pending, failure }); return; }
    launching.current = true;
    setEntry({ launch: true });
    // Consent starts before authentication bootstrap or the service login dialog.
    api.launchCafe24({ query: launch.query, returnTo: back || undefined }).then((result) => {
      if (!active.current) return;
      if (result.startUrl) window.location.assign(result.startUrl);
      else setError('설치 요청을 확인하지 못했어요. 카페24에서 앱을 다시 열어 주세요');
    }).catch((failure) => {
      if (active.current) setError(CAFE24_INSTALL_COPY[failure.code] || '연결을 시작하지 못했어요. 카페24에서 앱을 다시 열어 주세요');
    });
  }, [search]);
  useEffect(() => {
    if (!entry?.pending || loading || isMockMode || error) return;
    if (!session) {
      if (!opened.current) { opened.current = true; openLogin(returnTo.current); }
      return;
    }
    if (claiming.current) return;
    claiming.current = true;
    api.claimCafe24Pending({ expectedUserId: session.user.id }).then(() => {
      if (!active.current) return;
      if (readPostLogin() === returnTo.current) forgetPostLogin();
      const url = new URL(window.location.href);
      url.searchParams.set('mall', 'cafe24'); url.searchParams.set('result', 'connected');
      window.location.replace(`${url.pathname}${url.search}${url.hash}`);
    }).catch((failure) => {
      if (active.current) setError(CAFE24_INSTALL_COPY[failure.code] || '연결을 마치지 못했어요. 카페24에서 앱을 다시 설치해 주세요');
    });
  }, [entry, loading, session, openLogin, error]);
  if (error) return <div className="route-loading"><div className={styles.prompt}>
    <p role="alert">{error}</p>
    <div className={`${styles.actions} modal-actions`}><Button variant="primary" onClick={() => window.location.assign('/settings/malls')}>쇼핑몰 설정</Button></div>
  </div></div>;
  if (!entry || entry.launch || loading && !isMockMode || entry.pending && session) return <div className="route-loading" role="status">{entry?.launch ? '카페24 동의 화면을 여는 중이에요' : entry?.pending && session ? '쇼핑몰을 연결하는 중이에요' : '불러오는 중이에요'}</div>;
  if (!isMockMode && !session && entry.failure) return <div className="route-loading"><div className={styles.prompt}>
    <p role="alert">{CAFE24_INSTALL_COPY[entry.failure]}</p>
    <div className={`${styles.actions} modal-actions`}><Button variant="primary" onClick={() => openLogin(`/settings/malls${search}`)}>로그인</Button></div>
  </div></div>;
  if (!isMockMode && !session && entry.pending) return <div className="route-loading"><div className={styles.prompt}>
    <p>30분 안에 로그인하거나 가입해 주세요.</p>
    <div className={`${styles.actions} modal-actions`}><Button variant="primary" onClick={() => openLogin(returnTo.current)}>로그인 또는 가입</Button></div>
  </div></div>;
  return <RequireAuth />;
}
