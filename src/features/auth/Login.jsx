/* =============================================================
   LoginGate — 분석 CTA·상단바에서 띄우는 로그인 모달(팝업). 구글·카카오만.
   프로바이더 클릭 → supabase OAuth 리다이렉트(전체 페이지 이동) → 복귀 시 세션 생성.
   복귀 경로(sessionStorage 'wl_postLogin')는 openLogin 이 심고, App 의 RootRedirect 가 그 경로로 이동.
   ============================================================= */
import { useState } from 'react';
import { useAuth } from './AuthProvider.jsx';
import { Modal } from '@/components/ui.jsx';
import styles from './Login.module.css';

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
  const { signIn, closeLogin } = useAuth();
  const [pending, setPending] = useState(null); // 'google' | 'kakao' | null

  // 복귀 지점(sessionStorage 'wl_postLogin')은 openLogin 이 이미 심어둠 — 여기선 redirect 만.
  const handle = async (provider) => {
    setPending(provider);
    const { error } = await signIn(provider);
    if (error) setPending(null); // 성공 시엔 리다이렉트되어 언마운트됨
  };

  return (
    <Modal onClose={closeLogin}>
      <div className={styles.gate}>
        <div className={styles.brand}>
          <img className={styles.logo} src="/assets/brand/logo.svg" alt="" />
          <div className={styles.mark}>
            <img className={styles.wordmark} src="/assets/brand/wordmark.svg" alt="Wearless" />
            <span className={styles.suffix}>Studio</span>
          </div>
        </div>
        <p className={styles.subtitle}>
          소셜 계정으로 로그인하고<br />
          마네킹컷 생성으로 이어가세요.
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

        <p className={styles.hint}>계속하면 서비스 약관에 동의하는 것으로 간주됩니다.</p>
      </div>
    </Modal>
  );
}
