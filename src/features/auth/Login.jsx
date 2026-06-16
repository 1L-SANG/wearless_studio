/* =============================================================
   Login — 미인증 사용자에게 보이는 게이트. 구글·카카오 소셜 로그인만.
   버튼 클릭 → supabase OAuth 리다이렉트(전체 페이지 이동) → 복귀 시 세션 생성.
   ============================================================= */
import { useState } from 'react';
import { useAuth } from './AuthProvider.jsx';
import styles from './Login.module.css';

export function Login() {
  const { signIn } = useAuth();
  const [pending, setPending] = useState(null); // 'google' | 'kakao' | null

  const handle = async (provider) => {
    setPending(provider);
    const { error } = await signIn(provider);
    if (error) setPending(null); // 성공 시엔 리다이렉트되어 언마운트됨
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <img className={styles.logo} src="/assets/brand/temp-nav-logo.png" alt="Wearless" />
        <h1 className={styles.title}>Wearless Studio</h1>
        <p className={styles.subtitle}>
          소셜 계정으로 시작하세요.<br />
          상품 사진만 있으면 상세페이지를 만들어 드려요.
        </p>

        <div className={styles.buttons}>
          <button
            type="button"
            className={`${styles.btn} ${styles.google}`}
            onClick={() => handle('google')}
            disabled={pending !== null}
          >
            {pending === 'google' ? '이동 중…' : 'Google로 계속하기'}
          </button>
          <button
            type="button"
            className={`${styles.btn} ${styles.kakao}`}
            onClick={() => handle('kakao')}
            disabled={pending !== null}
          >
            {pending === 'kakao' ? '이동 중…' : '카카오로 계속하기'}
          </button>
        </div>

        <p className={styles.hint}>계속하면 서비스 약관에 동의하는 것으로 간주됩니다.</p>
      </div>
    </div>
  );
}
