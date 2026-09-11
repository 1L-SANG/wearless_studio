/* =============================================================
   AuthProvider — Supabase 세션을 앱 전역에 제공 (소셜 로그인 전용).
   이메일/비번 로그인은 두지 않는다 (제품 결정: 구글·카카오만).
   - 마운트 시 현재 세션 조회 + onAuthStateChange 구독
   - signInWithOAuth(google|kakao) / signOut 노출
   - openLogin(redirect)/closeLogin: 분석 CTA·상단바에서 로그인 모달(LoginGate)을 띄운다.
     OAuth 는 풀페이지 리다이렉트라 모달은 redirectTo 를 origin 으로 두고,
     로그인 후 복귀 지점은 sessionStorage('wl_postLogin') 플래그로 전달한다
     (ai 도메인은 App 의 RootRedirect, facemarket 도메인은 FacemarketRoot 가 '/' 복귀 시
      그 경로로 이동. 없으면 각각 입력 화면·랜딩).
     사용자가 **취소**로 모달을 닫으면(closeLogin) 그 플래그도 함께 버린다 — 취소한
     로그인의 복귀 의도가 남아 다음 '/' 진입을 가로채지 않게. 성공은 취소가 아니므로
     닫는 쪽이 알려준다(closeLogin({ cancelled: false })).
   토큰을 컴포넌트로 흘리지 않는다 — API 호출은 httpAdapter 가 supabase 에서 직접 읽는다.
   ============================================================= */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { IS_FACEMARKET } from '@/lib/host.js';
import { supabase } from '@/lib/supabase.js';
import { LoginGate } from './Login.jsx';
import { draftSlot } from '@/lib/draftSlot.js';
import { stampAppOrigin } from '@/lib/appOrigin.js';
import { useAppStore } from '@/store/useAppStore.js';
import { clearSignupConsent } from '@/lib/signupConsent.js';

const MOCK_FACEMARKET = import.meta.env.DEV && import.meta.env.VITE_API_MODE === 'mock' && IS_FACEMARKET;
const AuthCtx = createContext(null);
let oauthExchangeCode = null;
let oauthExchangePromise = null;

/* 복귀 플래그 접근은 전부 이 세 함수를 지난다.
   sessionStorage 는 **접근 자체가 던진다** — 사파리 프라이빗, 쿠키·사이트 데이터 차단,
   서드파티 컨텍스트. 복귀 목표는 편의 기능이라 실패해도 로그인 자체는 열려야 한다:
   던지면 목표만 포기하고 모달 상태 전이는 그대로 진행한다.
   이 키를 만지는 곳은 앱 전체에서 셋뿐이고(여기, App.jsx 의 RootRedirect, FacemarketRoot),
   뒤 둘은 아래 export 를 쓴다 — 키 문자열도 try/catch 도 이 파일 밖에 복제하지 마라.
   한 곳만 맨몸으로 두면 그 도메인만 죽는 반쪽 하드닝이 된다(실제로 그랬다: 랜딩은 뜨는데
   ai 도메인 루트만 흰 화면). */
const POST_LOGIN_KEY = 'wl_postLogin';

/* 읽기·지우기를 export 하는 이유: 같은 키의 소비자가 여기 말고 둘 더 있다 —
   App.jsx 의 RootRedirect(ai 도메인)와 FacemarketRoot(facemarket 도메인). 한 곳만
   맨몸으로 접근하면 그 도메인만 죽는 '반쪽 하드닝'이 된다.
   RootRedirect 가 특히 급했다: 그쪽은 useState 초기화 함수에서, 즉 **렌더 중에** 읽는데
   이 레포엔 ErrorBoundary 가 하나도 없어(componentDidCatch·getDerivedStateFromError 0건)
   던지는 순간 createRoot 가 트리를 통째로 언마운트한다 — 쿠키 차단 브라우저에서
   ai.wearless.kr 루트가 흰 화면이 됐다. 실패는 null 로 떨어뜨려 호출부의 기본 경로
   ('/create/input')가 그대로 살게 한다. */
export function readPostLogin() {
  try { return sessionStorage.getItem(POST_LOGIN_KEY); } catch { return null; }
}

function rememberPostLogin(path) {
  try { sessionStorage.setItem(POST_LOGIN_KEY, path); } catch { /* 복귀 목표만 포기한다 */ }
}

export function forgetPostLogin() {
  try { sessionStorage.removeItem(POST_LOGIN_KEY); } catch { /* 위와 같다 */ }
}

/* `code` 는 OAuth 전용 이름이 아니다. 토스는 결제·카드등록 실패를 `?code=…&message=…` 로
   돌려보낸다(PAY_PROCESS_CANCELED 등). 그 화면에서 이 파일이 code 를 건드리면 두 가지가 깨진다:
     ① PKCE 교환을 시도했다가 실패한다(위 부트스트랩에서 세션까지 날아갔었다)
     ② cleanOAuthCodeFromUrl 이 code 를 지워, 실패 화면이 사용자에게 보여줄 사유를 잃는다
   그래서 결제 결과 경로에서는 OAuth 처리를 통째로 건너뛴다. OAuth 복귀는 이 경로로 오지 않는다
   (redirectTo 는 window.location.origin 이다). */
const PAYMENT_RESULT_PATHS = [
  '/payments/success', '/payments/fail',
  '/subscription/success', '/subscription/fail',
];

function isPaymentResultPath() {
  if (typeof window === 'undefined') return false;
  return PAYMENT_RESULT_PATHS.includes(window.location.pathname);
}

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
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let alive = true; // StrictMode 이중 마운트: cleanup 이후 state 갱신 방지
    let subscription = null;
    if (MOCK_FACEMARKET) {
      import('../../mock/facemarket.js').then(({ getMockSession }) => {
        if (!alive) return;
        setSession(getMockSession());
        setLoading(false);
      });
      return () => { alive = false; };
    }
    const code = isPaymentResultPath()
      ? null
      : new URLSearchParams(window.location.search).get('code');
    (async () => {
      try {
        if (code) {
          // 교환 실패를 부트스트랩 실패로 취급하지 않는다. `code` 는 OAuth 만 쓰는 이름이
          // 아니다 — 토스 결제 실패 리다이렉트가 `?code=PAY_PROCESS_CANCELED` 로 돌아오고
          // (/payments/fail·/subscription/fail), 그걸 PKCE 코드로 오인해 교환하면 당연히
          // 실패한다. 예전에는 그 실패가 아래 catch 로 떨어져 setSession(null) 을 불렀다 —
          // **쿠키에 멀쩡한 세션이 있는데도 로그아웃**됐다. 즉 카드 등록을 취소한 사용자가
          // 로그인까지 풀렸다. 교환은 '되면 좋은 것'이고, 세션의 정본은 getSession 이다.
          try {
            await exchangeOAuthCodeOnce(code);
          } catch (exchangeError) {
            console.warn('[auth] OAuth code 교환 실패 — 기존 세션 유지',
              exchangeError?.message || exchangeError);
          }
        }
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

  /* 로그인된 계정이 어느 앱에서 왔는지 서버에 한 번 알린다(lib/appOrigin.js).
     세션 부트스트랩·OAuth 복귀·토큰 갱신이 전부 여기 session 을 지나므로 배선 지점이
     하나로 끝난다. deps 를 user.id 로 두는 이유: 토큰이 갱신될 때마다 session 객체는
     새로 오지만 사용자는 그대로다 — 객체를 deps 에 두면 갱신마다 다시 보낸다.
     실패는 무시한다(가입 흐름이 아니라 라벨 한 칸이다). */
  const userId = session?.user?.id ?? null;
  useEffect(() => {
    if (userId && !MOCK_FACEMARKET) stampAppOrigin(userId);
  }, [userId]);

  const signIn = (provider) =>
    supabase.auth.signInWithOAuth({
      provider, // 'google' | 'kakao'
      options: { redirectTo: window.location.origin },
    });

  // 로그아웃 시 미동기화 draft 도 정리 — 공용 브라우저에서 다음 사용자에게 입력이 복원되지 않게.
  const signOut = async () => {
    if (MOCK_FACEMARKET) { setSession(null); return; }
    clearSignupConsent();
    forgetPostLogin();
    setSigningOut(true);
    try {
      draftSlot.resetIdentity();
      await useAppStore.getState().beginProject().catch(() => {});
      return await supabase.auth.signOut();
    } finally {
      setSigningOut(false);
    }
  };

  // redirect: 로그인 성공 후 복귀할 앱 내 경로(예: '/create/mannequin'). 없으면 origin 유지.
  // 복귀 플래그는 여기서 단일 관리한다 — 이번 로그인 시도의 의도대로 set/clear 해서,
  // 취소된 이전 시도의 묵은 플래그가 다음 로그인을 엉뚱한 곳으로 보내지 않게 한다.
  // (호출부는 언제나 이번 시도의 목표를 함께 넘긴다 — 상단바·입력 CTA·랜딩 CTA·Editor 의
  //  401 '다시 로그인'. 그래서 여기서 덮어쓰면 묵은 값이 남는 창이 닫힌다. Editor 경로처럼
  //  세션 객체가 살아 있는 채로 열리는 호출도 있으니 '로그아웃 상태 전용'은 아니다.)
  //
  // useCallback 으로 identity 를 고정한다. 안 그러면 이 함수를 effect deps 에 둔 쪽이
  // 렌더마다 재실행된다 — 실제로 App 의 FacemarketLoginPrompt 가 그래서 "닫으면 즉시
  // 다시 열리는" 모달이 됐다(closeLogin → 리렌더 → 새 openLogin → effect 재실행).
  const openLogin = useCallback((redirect = null) => {
    if (MOCK_FACEMARKET) {
      import('../../mock/facemarket.js').then(({ signInMock }) => { setSession(signInMock()); });
      return;
    }
    if (redirect) rememberPostLogin(redirect);
    else forgetPostLogin();
    setLoginOpen(true);
  }, []);

  // 사용자가 **취소**로 모달을 닫으면 이번 로그인 시도의 복귀 의도도 같이 버린다.
  // 남겨두면 그 플래그가 다음 '/' 진입을 소비해, 랜딩 대신 로그인 벽으로 튕긴다
  // (facemarket 에서는 랜딩이 그 탭에서 영영 안 보이는 상태가 됐다).
  //
  // 성공까지 같이 지우면 ai 도메인이 깨진다. "성공 경로는 여기를 지나지 않는다"는 앞선
  // 주석의 단정은 틀렸다 — 실제 호출부가 둘 있다.
  //   1) Login.jsx 의 로컬 이메일 로그인(handleLocal)은 페이지 이동이 없어서, 성공 직후
  //      이 함수로 모달을 닫는다.
  //   2) ui.jsx Modal 의 Escape 리스너는 window 에 붙어 있어, 프로바이더 클릭 뒤 리다이렉트가
  //      커밋되기 전에 Esc 를 눌러도 여기를 지난다(로그인은 그대로 진행된다).
  // 두 경우에 플래그가 지워지면 App.jsx RootRedirect 의 wantsStoryboard 분기가 스킵돼
  // 로그인 전 입력·분석이 콘티로 승격되지 않는다. 그래서 '취소인지'는 닫는 쪽이 알려준다.
  // 인자 없이(또는 이벤트 객체로) 불리면 취소로 본다 — 기존 호출 형태가 그대로 안전하게.
  const closeLogin = useCallback((options) => {
    if (options?.cancelled !== false) forgetPostLogin();
    setLoginOpen(false);
  }, []);

  return (
    <AuthCtx.Provider value={{ session, user: session?.user ?? null, loading, signingOut, signIn, signOut, openLogin, closeLogin }}>
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
