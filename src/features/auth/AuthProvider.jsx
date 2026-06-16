/* =============================================================
   AuthProvider — Supabase 세션을 앱 전역에 제공 (소셜 로그인 전용).
   이메일/비번 로그인은 두지 않는다 (제품 결정: 구글·카카오만).
   - 마운트 시 현재 세션 조회 + onAuthStateChange 구독
   - signInWithOAuth(google|kakao) / signOut 노출
   토큰을 컴포넌트로 흘리지 않는다 — API 호출은 httpAdapter 가 supabase 에서 직접 읽는다.
   ============================================================= */
import { createContext, useContext, useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase.js';

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true; // StrictMode 이중 마운트: cleanup 이후 state 갱신 방지
    supabase.auth.getSession().then(({ data }) => {
      if (!alive) return;
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  const signIn = (provider) =>
    supabase.auth.signInWithOAuth({
      provider, // 'google' | 'kakao'
      options: { redirectTo: window.location.origin },
    });

  const signOut = () => supabase.auth.signOut();

  return (
    <AuthCtx.Provider value={{ session, user: session?.user ?? null, loading, signIn, signOut }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
