/* =============================================================
   AuthProvider — Supabase 세션을 앱 전역에 제공 (소셜 로그인 전용).
   이메일/비번 로그인은 두지 않는다 (제품 결정: 구글·카카오만).
   - 마운트 시 현재 세션 조회 + onAuthStateChange 구독
   - signInWithOAuth(google|kakao) / signOut 노출
   - openLogin(redirect)/closeLogin: 분석 CTA·상단바에서 로그인 모달(LoginGate)을 띄운다.
     OAuth 는 풀페이지 리다이렉트라 모달은 redirectTo 를 origin 으로 두고,
     로그인 후 복귀 지점은 sessionStorage('wl_postLogin') 플래그로 전달한다
     (App 의 RootRedirect 가 '/' 복귀 시 그 경로로 이동, 없으면 입력).
   토큰을 컴포넌트로 흘리지 않는다 — API 호출은 httpAdapter 가 supabase 에서 직접 읽는다.
   ============================================================= */
import { createContext, useContext, useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase.js';
import { LoginGate } from './Login.jsx';
import { clearDraft } from '@/lib/draftStore.js';

const AuthCtx = createContext(null);
let oauthExchangeCode = null;
let oauthExchangePromise = null;

function cleanOAuthCodeFromUrl(code) {
  const url = new URL(window.location.href);
  if (url.searchParams.get('code') !== code) return;
  url.searchParams.delete('code');
  window.history.replaceState(window.history.state, '', url.toString());
}

function exchangeOAuthCodeOnce(code) {
  if (!code) return Promise.resolve();
  if (oauthExchangePromise && oauthExchangeCode === code) return oauthExchangePromise;
  oauthExchangeCode = code;
  oauthExchangePromise = supabase.auth.exchangeCodeForSession(code)
    .then(({ error }) => {
      if (error) throw error;
    })
    .finally(() => { cleanOAuthCodeFromUrl(code); });
  return oauthExchangePromise;
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);

  useEffect(() => {
    let alive = true; // StrictMode 이중 마운트: cleanup 이후 state 갱신 방지
    let subscription = null;
    const code = new URLSearchParams(window.location.search).get('code');
    (async () => {
      try {
        if (code) await exchangeOAuthCodeOnce(code);
        const { data } = await supabase.auth.getSession();
        if (!alive) return;
        setSession(data.session);
        const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
          if (!alive) return;
          setSession(next);
        });
        subscription = sub.subscription;
      } catch (error) {
        console.error('[auth] bootstrap failed', error);
        if (!alive) return;
        setSession(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
      subscription?.unsubscribe();
    };
  }, []);

  const signIn = (provider) =>
    supabase.auth.signInWithOAuth({
      provider, // 'google' | 'kakao'
      options: { redirectTo: window.location.origin },
    });

  // 로그아웃 시 미동기화 draft 도 정리 — 공용 브라우저에서 다음 사용자에게 입력이 복원되지 않게.
  const signOut = () => { sessionStorage.removeItem('wl_postLogin'); clearDraft(); return supabase.auth.signOut(); };

  // redirect: 로그인 성공 후 복귀할 앱 내 경로(예: '/create/mannequin'). 없으면 origin 유지.
  // 복귀 플래그는 여기서 단일 관리한다 — 이번 로그인 시도의 의도대로 set/clear 해서,
  // 취소된 이전 시도의 묵은 플래그가 다음 로그인을 엉뚱한 곳으로 보내지 않게 한다.
  // (openLogin 은 로그아웃 상태에서만 호출되므로 이 set/clear 가 stale 창을 닫는다.)
  const openLogin = (redirect = null) => {
    if (redirect) sessionStorage.setItem('wl_postLogin', redirect);
    else sessionStorage.removeItem('wl_postLogin');
    setLoginOpen(true);
  };
  const closeLogin = () => setLoginOpen(false);

  return (
    <AuthCtx.Provider value={{ session, user: session?.user ?? null, loading, signIn, signOut, openLogin, closeLogin }}>
      {children}
      {loginOpen && <LoginGate />}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
