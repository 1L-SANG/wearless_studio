/* =============================================================
   LoginGate — 분석 CTA·상단바에서 띄우는 로그인 모달(팝업). 구글·카카오만.
   프로바이더 클릭 → supabase OAuth 리다이렉트(전체 페이지 이동) → 복귀 시 세션 생성.
   복귀 경로(sessionStorage 'wl_postLogin')는 openLogin 이 심고, ai 도메인은 App 의 RootRedirect,
   facemarket 도메인은 FacemarketRoot 가 '/' 복귀 시 그 경로로 이동.
   닫기는 '취소'와 '성공'을 구분해서 AuthProvider 에 알린다 — 취소일 때만 복귀 경로를 버린다.
   두 도메인이 이 한 모달을 함께 쓴다(ai=셀러 스튜디오, facemarket=모델 얼굴 라이선스).
   도메인별로 다른 건 브랜드 디스크립터·부제·약관 고지 세 줄뿐이고, 분기는 IS_FACEMARKET 이다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { useAuth } from './AuthProvider.jsx';
import { supabase } from '@/lib/supabase.js';
import { Modal } from '@/components/ui.jsx';
import { IS_ADMIN, IS_FACEMARKET } from '@/lib/host.js';
import styles from './Login.module.css';

/* 로컬 supabase(127.0.0.1/localhost)일 때만 이메일·비밀번호 로그인을 노출한다.
   운영은 소셜 OAuth 만 쓰므로 prod 에서는 절대 렌더되지 않는다(로컬 QA 전용). */
const IS_LOCAL_SUPABASE = /127\.0\.0\.1|localhost/.test(
  import.meta.env.VITE_SUPABASE_URL || '',
);

/* 이 모달은 **두 제품이 공유한다** — ai.wearless.kr(셀러 스튜디오)과
   facemarket.wearless.kr(모델 얼굴 라이선스). 브랜드 락업의 디스크립터는 도메인마다 다르다.
   facemarket 에서 'Studio' 를 보여주면, 랜딩이 정확히 고치려던 실패(얼굴을 등록하러 온
   모델에게 셀러 제품을 보여주는 것)가 첫 전환 지점에서 그대로 재현된다 — 랜딩의 전환 버튼은
   전부 같은 핸들러로 이 모달을 연다(FacemarketLanding onPrimary → openLogin('/model/register')).
   IS_FACEMARKET 은 shell.jsx(상단바 로그인 복귀 경로)와 App.jsx(라우트)가 이미 쓰는 분기점이다.
   워드마크(alt="Wearless") 아래 'FaceMarket' 은 푸터의 'FaceMarket · Wearless',
   랜딩 헤더의 브랜드 표기와 같은 순서다. **셀러 값('Studio')은 바꾸지 마라.** */
const BRAND_SUFFIX = IS_FACEMARKET ? 'FaceMarket' : 'Studio';

/* 브랜드 락업을 FaceMarket 로고로 가는 도메인.
   facemarket 은 랜딩 헤더가 이미 이 로고를 쓴다 — 로그인 모달만 Wearless 워드마크였다.
   admin 콘솔도 FaceMarket 운영 도구라 같은 락업을 쓴다.
   **셀러(ai.wearless.kr)는 여기 들어오지 않는다** — 오브+wearless+Studio 그대로다. */
const FACEMARKET_LOCKUP = IS_FACEMARKET || IS_ADMIN;

/* 브랜드 로고 — Lucide(단색 스트로크) 세트와 성격이 달라 인라인 SVG 로 둔다. */
function GoogleIcon() {
  return (
    <svg className={styles.brandIco} viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}
function KakaoIcon() {
  return (
    <svg className={styles.brandIco} viewBox="0 0 24 24" fill="rgba(0,0,0,0.85)" aria-hidden="true">
      <path d="M12 3C6.48 3 2 6.58 2 10.99c0 2.86 1.9 5.37 4.76 6.78-.21.78-.76 2.82-.87 3.26-.14.55.2.54.42.39.17-.11 2.71-1.84 3.81-2.59.6.09 1.22.13 1.88.13 5.52 0 10-3.58 10-7.99S17.52 3 12 3z" />
    </svg>
  );
}

export function LoginGate() {
  const { session, signIn, closeLogin } = useAuth();
  const [pending, setPending] = useState(null); // 'google' | 'kakao' | null
  const [email, setEmail] = useState('qa@local.test');
  const [password, setPassword] = useState('');
  const [localErr, setLocalErr] = useState('');

  /* 사용자 조작으로 모달을 닫는 유일한 경로(Esc·바깥 클릭). 진행 중인 로그인이 있으면
     취소가 아니다 — ui.jsx Modal 의 Escape 리스너는 window 에 붙어 있어서 프로바이더
     리다이렉트가 커밋되기 전에도 살아 있는데, 그 순간의 Esc 를 취소로 처리하면 이미
     시작된 로그인의 복귀 목표(wl_postLogin)를 지워버린다(로그인 자체는 그대로 진행된다). */
  const dismiss = () => closeLogin({ cancelled: pending === null });

  /* 열릴 때 세션이 없었는데 세션이 도착하면 스스로 닫는다 — 다른 탭에서 로그인한 경우,
     그리고 아래 로컬 폼 로그인. 열릴 때 이미 세션이 있었으면 닫지 않는다: Editor 의
     401 '다시 로그인' 모달은 세션이 살아 있는 채로 뜨므로(만료 토큰), 토큰 갱신 한 번에
     모달이 사라지면 ai 도메인 회귀가 된다. 성공이니 복귀 목표는 남긴다.

     ── 이 두 줄(hadSessionOnOpen 가드, cancelled: false)은 되돌리지 마라. 둘 다 앞선
     라운드가 정반대 방향으로 왕복하다 못박은 자리다.
     (1) 가드를 빼면 = Editor 의 401 재로그인 모달이 토큰 갱신 한 번에 사라져, 사용자가
         아무것도 하지 않았는데 재인증이 끝난 것처럼 보인다(ai 도메인 회귀).
     (2) `cancelled: true` 로 바꿔 남은 wl_postLogin 을 여기서 치우고 싶어질 것이다
         (facemarket 랜딩이 다음 '/' 진입 한 번을 등록 위저드로 가로채는 건이 그 유혹이다).
         바꾸지 마라 — AuthProvider.closeLogin 은 '취소'일 때만 복귀 목표를 버리는데,
         이건 성공이다. 취소로 신고하면 ai 도메인에서 App.jsx RootRedirect 의 wantsStoryboard
         승격이 스킵돼 로그인 전 입력·분석이 콘티로 이어지지 않는다(2라운드가 실제로 만든
         손실이고 3라운드가 되돌린 것이다). 남은 플래그 정리는 심는 쪽이 아니라 **소비하는
         쪽**(FacemarketRoot 의 지우기 이펙트)의 몫이다 — 거기서 한 번 튕긴 뒤 스스로
         낫는다(1회성). 여기서 고치면 두 도메인이 같은 플래그에 반대 요구를 하게 된다.
         (AuthProvider 가 forgetPostLogin 을 export 하므로 여기서 부르는 것도 기술적으로는
          된다. 부르지 않는 건 능력이 아니라 의미의 문제다 — 이 자리에서 지우면 ai 도메인의
          승격 경로까지 같이 지워진다.)
     ── (2)의 같은 증상에 처방이 두 개 더 올라왔다: "자동 닫기에서 wl_postLogin 을 읽어
     그 경로로 navigate 한 뒤 지워라", "심는 쪽(App.jsx FacemarketLoginPrompt)이 redirect
     인자를 빼라". 둘 다 코드로 확인해 기각했다 — 다시 시도하지 마라.
         · navigate 는 **불가능**하다 — LoginGate 를 그리는 AuthProvider 가 라우터
           **바깥**에 있다(main.jsx: AuthProvider > BrowserRouter > App). 이 컴포넌트에서
           useNavigate() 를 부르면 라우터 컨텍스트가 없어 그 자리에서 던진다.
         · redirect 인자를 빼면 **주경로가 깨진다** — signIn 의 redirectTo 는
           window.location.origin 이라 OAuth 복귀는 언제나 '/' 다. 플래그가 없으면
           /model/register 로 직접 들어와 로그인한 사람이 위저드가 아니라 랜딩에 떨어진다.
           지금 남는 플래그는 페이지 이동이 없는 곁가지(다른 탭 로그인·로컬 폼)뿐인데,
           그걸 고치자고 전체 리다이렉트 왕복을 망가뜨리는 교환이다.
         · 남는 증상은 '다음 /' 진입 한 번이 위저드로 튕기는 것뿐이고, 그 진입에서
           FacemarketRoot 의 forgetReturnTargetIfUnchanged 가 플래그를 지워 한 번으로
           끝난다(self-healing). 감수한다. */
  const hadSessionOnOpen = useRef(Boolean(session));
  useEffect(() => {
    if (!session || hadSessionOnOpen.current) return;
    closeLogin({ cancelled: false });
  }, [session, closeLogin]);

  const handleLocal = async (e) => {
    e.preventDefault();
    setPending('local'); setLocalErr('');
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) { setLocalErr(error.message || '로그인 실패'); setPending(null); }
    // 성공 — 세션은 AuthProvider 의 onAuthStateChange 가 반영한다. 이 경로는 페이지 이동이
    // 없어서 닫기가 실제로 실행되므로, 취소가 아니라고 분명히 알려 복귀 목표를 지킨다.
    else closeLogin({ cancelled: false });
  };

  // 복귀 지점(sessionStorage 'wl_postLogin')은 openLogin 이 이미 심어둠 — 여기선 redirect 만.
  const handle = async (provider) => {
    setPending(provider);
    const { error } = await signIn(provider);
    if (error) setPending(null); // 성공 시엔 리다이렉트되어 언마운트됨
  };

  return (
    <Modal onClose={dismiss}>
      <div className={styles.gate}>
        <div className={styles.brand}>
          {FACEMARKET_LOCKUP ? (
            /* 로고 한 장이 심볼과 워드마크를 다 담고 있어 오브를 따로 얹지 않는다. */
            <img
              className={styles.fmLockup}
              src="/assets/brand/facemarket-logo.svg"
              alt="FaceMarket"
            />
          ) : (
            <>
              <img className={styles.logo} src="/assets/brand/logo.svg" alt="" />
              <div className={styles.mark}>
                <img className={styles.wordmark} src="/assets/brand/wordmark.png" alt="Wearless" />
                <span className={styles.suffix}>{BRAND_SUFFIX}</span>
              </div>
            </>
          )}
        </div>
        {/* 부제도 같은 이유로 도메인을 가른다. facemarket 에서 이 모달이 열리는 경로는
            셋 다 등록으로 향한다 — 랜딩 CTA·상단바 로그인(shell.jsx)·미인증 /model/*
            진입(App.jsx FacemarketLoginPrompt, 그 화면 문구도 '모델 등록은 로그인이
            필요해요'). 셀러 문구는 한 글자도 건드리지 않는다. */}
        <p className={styles.subtitle}>
          {IS_FACEMARKET ? (
            <>소셜 계정으로 로그인하고<br />모델 등록을 이어가세요.</>
          ) : (
            <>소셜 계정으로 로그인하고<br />마네킹컷 생성으로 이어가세요.</>
          )}
        </p>

        <div className={styles.buttons}>
          <button
            type="button"
            className={`${styles.btn} ${styles.google}`}
            onClick={() => handle('google')}
            disabled={pending !== null}
          >
            <span className={styles.icon}><GoogleIcon /></span>
            {pending === 'google' ? '이동 중…' : 'Google로 계속하기'}
          </button>
          <button
            type="button"
            className={`${styles.btn} ${styles.kakao}`}
            onClick={() => handle('kakao')}
            disabled={pending !== null}
          >
            <span className={styles.icon}><KakaoIcon /></span>
            {pending === 'kakao' ? '이동 중…' : '카카오로 계속하기'}
          </button>
        </div>

        {IS_LOCAL_SUPABASE && (
          <form onSubmit={handleLocal} style={{ marginTop: 16, display: 'grid', gap: 8 }}>
            <div style={{ fontSize: 12, opacity: 0.6, textAlign: 'center' }}>로컬 QA 전용 · 이메일 로그인</div>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="이메일" autoComplete="username"
              style={{ padding: '8px 10px', border: '1px solid #ccc', borderRadius: 6 }} />
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="비밀번호" autoComplete="current-password"
              style={{ padding: '8px 10px', border: '1px solid #ccc', borderRadius: 6 }} />
            {localErr && <div style={{ color: '#c0392b', fontSize: 12 }}>{localErr}</div>}
            <button type="submit" disabled={pending !== null}
              style={{ padding: '9px 12px', borderRadius: 6, cursor: 'pointer' }}>
              {pending === 'local' ? '로그인 중…' : '이메일로 로그인'}
            </button>
          </form>
        )}

        {/* 약관 고지. **셀러 도메인 문구는 그대로 둔다** — 법적 문구를 약화시키지 않는다.
            facemarket 에서만 바꾸는 이유: 이 모달을 여는 랜딩의 푸터가 '개인정보처리방침·
            이용약관 링크는 공개 문서가 준비되면 여기에 겁니다' 라고 적는다(FooterSection.jsx).
            실제로 걸 링크가 없다 — /legal/* 라우트는 App.jsx 에 없고, 서버 NOTICE_URIS
            (personalization.py)도 '법무 확정 URI 자리' 플레이스홀더다. 그 상태에서 '볼 수 없는
            약관에 동의한 것으로 간주'한다고 말하면, 두 화면이 두 클릭 안에 서로를 부정하고
            분쟁 시 '어떤 약관에 동의했는지' 특정도 안 된다 — 생체정보를 넘기라고 설득하는
            페이지에서 가장 비싼 종류의 모순이다.
            그래서 여기서는 실제로 일어나는 일만 적는다: 처리 내용 고지와 동의는 등록 1단계
            (ModelRegister STEP 1 의 처리 안내 5줄 + 동의 체크박스)에서 명시적으로 받는다.
            문장은 푸터와 같은 말이 되도록 맞췄다. 없는 주소를 지어 걸지 마라 — 법무 문서가
            공개되면 그때 푸터와 이 줄을 **같은 링크로 함께** 잇는다. */}
        <p className={styles.hint}>
          {IS_FACEMARKET
            ? '무엇을 어떻게 처리하는지는 모델 등록 첫 단계에서 동의하기 전에 화면에 표시됩니다.'
            : '계속하면 서비스 약관에 동의하는 것으로 간주됩니다.'}
        </p>
      </div>
    </Modal>
  );
}
