import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { RequireAuth } from '@/apps/guards.jsx';
import { useAuth, forgetPostLogin, readPostLogin } from '@/features/auth/AuthProvider.jsx';
import { Button } from '@/components/ui.jsx';
import { captureCafe24Launch } from '@/lib/cafe24Launch.js';
import { isMockMode } from '@/lib/api/index.js';
import styles from '@/apps/guards.module.css';

// Only the Cafe24 App URL gets a login continuation; other guards stay unchanged.
export function Cafe24LaunchGate() {
  const { session, loading, openLogin } = useAuth();
  const { search } = useLocation();
  const [entry, setEntry] = useState(null);
  const opened = useRef(false);
  const returnTo = useRef('/settings/malls');
  useEffect(() => {
    const launch = captureCafe24Launch();
    const params = new URLSearchParams(window.location.search);
    const back = params.get('returnTo');
    returnTo.current = `/settings/malls${back ? `?${new URLSearchParams({ returnTo: back })}` : ''}`;
    setEntry({ launch: !!launch });
  }, [search]);
  useEffect(() => {
    if (!entry?.launch || loading || isMockMode) return;
    if (session) {
      // Password login stays on this page; OAuth return consumes this at '/'.
      if (readPostLogin() === returnTo.current) forgetPostLogin();
    } else if (!opened.current) {
      opened.current = true;
      openLogin(returnTo.current);
    }
  }, [entry, loading, session, openLogin]);
  if (!entry || loading && !isMockMode) return <div className="route-loading">불러오는 중이에요</div>;
  if (!isMockMode && !session && entry.launch) return <div className="route-loading"><div className={styles.prompt}>
    <p>로그인하면 쇼핑몰 연결을 이어서 진행해요.</p>
    <div className={styles.actions}><Button variant="primary" onClick={() => openLogin(returnTo.current)}>로그인 다시 열기</Button></div>
  </div></div>;
  return <RequireAuth />;
}
