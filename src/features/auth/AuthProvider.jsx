/* =============================================================
   AuthProvider — Supabase 세션을 앱 전역에 제공.
   일반 회원은 구글·카카오, 이메일 로그인은 로컬 QA와 임시 PG 심사용이다.
   - 마운트 시 현재 세션 조회 + onAuthStateChange 구독
   - signInWithOAuth(google|kakao) / signOut 노출
     **카카오만 예외다**: VITE_KAKAO_OIDC_ENABLED 가 켜지면 Supabase OAuth 대신 카카오
     OpenID Connect 로 직접 로그인한다(lib/kakaoOidc.js 머리말에 KOE205 경위). 구글은 그대로.
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
import { useQueryClient } from '@tanstack/react-query';
import { IS_FACEMARKET } from '@/lib/host.js';
import { supabase } from '@/lib/supabase.js';
import { LoginGate } from './Login.jsx';
import { draftSlot } from '@/lib/draftSlot.js';
import { stampAppOrigin } from '@/lib/appOrigin.js';
import { useAppStore } from '@/store/useAppStore.js';
import { clearApplyDraft } from '@/lib/applyDraft.js';
import { clearSignupConsent } from '@/lib/signupConsent.js';
import { KAKAO_OIDC_ENABLED, isKakaoCallbackPath as isKakaoCallback, startKakaoLogin } from '@/lib/kakaoOidc.js';

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

/* 카카오 OIDC 콜백도 같은 이유로 제외한다 — 여기 붙는 `?code=` 는 **카카오 인가코드**이지
   Supabase PKCE 코드가 아니다. 그대로 두면 위 결제 경로와 똑같은 두 가지가 벌어진다:
     ① 카카오 코드로 supabase.exchangeCodeForSession 을 시도해 실패 로그만 남기고
     ② finally 의 cleanOAuthCodeFromUrl 이 **주소창에서 code 를 지워** 정작 콜백 화면이
        읽기 전에 날려버린다(StrictMode 재마운트·리렌더에서 빈손이 된다).
   콜백 화면(KakaoCallback.jsx)이 code 의 유일한 소비자다. 여기서는 손대지 않는다.
   window 가드를 같이 복제하는 것도 위와 같은 이유다 — SSR 테스트 하네스가 이 코드를
   노드에서 돌리고, 그 하네스의 window 스텁에는 location.pathname 이 없다. */
function isKakaoCallbackPath() {
  if (typeof window === 'undefined') return false;
  return isKakaoCallback(window.location.pathname);
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
  const queryClient = useQueryClient();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const publishSession = useCallback((next) => {
    // 새 세션이 화면에 보이기 전에 이전 계정 정보와 진행 중 조회를 버린다.
    // 같은 사용자의 토큰 갱신은 캐시를 유지한다.
    if (useAppStore.getState().setAccountIdentity(next?.user?.id ?? null)) {
      queryClient.clear();
    }
    setSession(next);
  }, [queryClient]);

  useEffect(() => {
    let alive = true; // StrictMode 이중 마운트: cleanup 이후 state 갱신 방지
    let subscription = null;
    if (MOCK_FACEMARKET) {
      import('../../mock/facemarket.js').then(({ getMockSession }) => {
        if (!alive) return;
        publishSession(getMockSession());
        setLoading(false);
      });
      return () => { alive = false; };
    }
    /* 두 제외 경로의 **순서는 의미가 있다**(바꾸지 마라): tests/frontend/subscription.test.mjs
       가 `isPaymentResultPath() ? null` 이라는 모양을 소스 텍스트로 단언한다. 결제 경로에서
       code 를 건드려 멀쩡한 세션이 로그아웃되던 사고의 회귀 가드라, 리팩터링으로 그 모양을
       깨면 이 변경과 무관해 보이는 이유로 CI 가 빨개진다. */
    const code = isKakaoCallbackPath() || isPaymentResultPath()
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
        publishSession(data.session);
        const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
          if (!alive) return;
          publishSession(next);
        });
        subscription = sub.subscription;
      } catch (error) {
        console.error('[auth] bootstrap failed', error);
        if (!alive) return;
        publishSession(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
      subscription?.unsubscribe();
    };
  }, [publishSession]);

  /* 로그인된 계정이 어느 앱에서 왔는지 서버에 한 번 알린다(lib/appOrigin.js).
     세션 부트스트랩·OAuth 복귀·토큰 갱신이 전부 여기 session 을 지나므로 배선 지점이
     하나로 끝난다. deps 를 user.id 로 두는 이유: 토큰이 갱신될 때마다 session 객체는
     새로 오지만 사용자는 그대로다 — 객체를 deps 에 두면 갱신마다 다시 보낸다.
     실패는 무시한다(가입 흐름이 아니라 라벨 한 칸이다). */
  const userId = session?.user?.id ?? null;
  useEffect(() => {
    if (userId && !MOCK_FACEMARKET) stampAppOrigin(userId);
  }, [userId]);

  /* 카카오만 다른 길로 간다 — 자세한 경위는 lib/kakaoOidc.js 머리말.
     한 줄 요약: Supabase GoTrue 가 카카오 scope 에 account_email 을 하드코딩해 붙이는데
     우리 앱은 그 항목이 "권한 없음"이라 인가 요청이 전부 KOE205 로 거절된다.

     **기존 signInWithOAuth 분기를 지우지 마라.** VITE_KAKAO_OIDC_ENABLED 한 줄로 되돌릴
     수 있게 남겨 둔 롤백 경로다(코드를 지우면 되돌릴 때 다시 만들어야 한다 — tossKeys.js
     의 기능 스위치와 같은 판단). 카카오 콘솔의 Supabase 콜백 URI 와 대시보드의 Kakao
     provider 도 같은 이유로 살아 있어야 한다.
     **구글은 이 분기에 들어오지 않는다** — 손대지 않는다. */
  const signIn = (provider) => {
    if (provider === 'kakao' && KAKAO_OIDC_ENABLED) return startKakaoLogin();
    return supabase.auth.signInWithOAuth({
      provider, // 'google' | 'kakao'
      options: { redirectTo: window.location.origin },
    });
  };

  const signInWithPassword = (credentials) => supabase.auth.signInWithPassword(credentials);

  /* 카카오 OIDC 콜백이 쓰는 유일한 세션 생성 경로. 화면 컴포넌트는 supabase 를 직접 부르지
     않는다는 이 레포의 경계를 지키려고 여기로 뺐다(Login.jsx 가 지키는 것과 같은 규율).
     nonce 는 authorize 때 심은 값이다 — 빠지면 GoTrue 가 nonce 클레임 대조에서 거절한다.
     access_token 은 넘기지 않는다: 카카오 discovery 의 claims_supported 에 at_hash 가
     없어 대조할 것이 없고, 넘기려면 브라우저로 내보내야 해서 더 위험하다. */
  const signInWithKakaoIdToken = ({ token, nonce }) =>
    supabase.auth.signInWithIdToken({ provider: 'kakao', token, nonce });

  // 로그아웃 시 미동기화 draft 도 정리 — 공용 브라우저에서 다음 사용자에게 입력이 복원되지 않게.
  const signOut = async () => {
    clearApplyDraft(userId);
    if (MOCK_FACEMARKET) { publishSession(null); return; }
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
      import('../../mock/facemarket.js').then(({ signInMock }) => { publishSession(signInMock()); });
      return;
    }
    if (redirect) rememberPostLogin(redirect);
    else forgetPostLogin();
    setLoginOpen(true);
  }, [publishSession]);

  // 사용자가 **취소**로 모달을 닫으면 이번 로그인 시도의 복귀 의도도 같이 버린다.
  // 남겨두면 그 플래그가 다음 '/' 진입을 소비해, 랜딩 대신 로그인 벽으로 튕긴다
  // (facemarket 에서는 랜딩이 그 탭에서 영영 안 보이는 상태가 됐다).
  //
  // 성공까지 같이 지우면 ai 도메인이 깨진다. "성공 경로는 여기를 지나지 않는다"는 앞선
  // 주석의 단정은 틀렸다 — 실제 호출부가 둘 있다.
  //   1) Login.jsx 의 이메일 로그인(handleEmail)은 페이지 이동이 없어서, 성공 직후
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
    <AuthCtx.Provider value={{ session, user: session?.user ?? null, loading, signingOut, signIn, signInWithPassword, signInWithKakaoIdToken, signOut, openLogin, closeLogin }}>
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
