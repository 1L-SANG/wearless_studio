/* =============================================================
   SignupCompletion — 가입을 끝내는 화면(그리고 약관 개정 시 재동의 화면).

   동의를 받는 자리는 로그인 모달의 **회원가입 탭**이다(Login.jsx). 이 화면은 그 탭을
   지나지 않고 들어온 사람을 위한 것 — 신규가 '로그인' 탭을 눌렀거나, 다른 탭·기기에서
   로그인해 가입 동의 표시가 없는 경우다. 회원가입 탭에서 동의하고 온 사람에게는
   **아무것도 보이지 않는다**: 그 표시(signupConsent)를 확인해 조용히 서버에 기록만 한다.

   모달이 아니라 전체 화면이다 — 로그인 뒤 튀어나오는 팝업이 아니라 가입 절차의 마지막
   단계로 보여야 한다(2026-09-07 오너). 버튼은 '가입 완료' 하나, 나가기는 우측 위 X 다.
   X 는 로그아웃이다: 동의 없이 서비스로 들어갈 길을 만들면 이 화면이 무의미해진다.

   셀러 앱(App.jsx)에만 마운트한다. 모델(FaceMarket)은 등록 위저드에서, 관리자는 아예
   셀러 약관의 당사자가 아니다. 조회가 실패하면(네트워크 등) 화면을 띄우지 않는다 —
   앱을 막는 것보다 다음 진입에서 다시 확인하는 편이 낫다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { Button, Icon } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { isMockMode } from '@/lib/api/index.js';
import { acceptSellerConsent, getSellerConsent } from '@/lib/api/consents.js';
import { clearSignupConsent, hasFreshSignupConsent } from '@/lib/signupConsent.js';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import styles from './SignupCompletion.module.css';

export function SignupCompletion() {
  const { session, loading, signOut } = useAuth();
  const [state, setState] = useState(null); // 서버 응답 | null(미조회·불필요)
  const [checked, setChecked] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const userId = session?.user?.id ?? null;

  const record = useCallback(async (required) => {
    const res = await acceptSellerConsent({
      termsVersion: required.terms, privacyVersion: required.privacy,
    });
    clearSignupConsent();
    setState(res);
  }, []);

  useEffect(() => {
    if (loading || !userId || isMockMode) { setState(null); return undefined; }
    const ctrl = new AbortController();
    getSellerConsent({ signal: ctrl.signal })
      .then(async (res) => {
        if (ctrl.signal.aborted) return;
        // 회원가입 탭에서 이미 동의한 사람 — 화면을 띄우지 않고 기록만 남긴다.
        if (res.needsConsent && !res.accepted && hasFreshSignupConsent()) {
          try { await record(res.required); return; } catch { /* 아래에서 화면으로 받는다 */ }
        }
        if (!ctrl.signal.aborted) setState(res);
      })
      .catch(() => { /* 조회 실패 — 막지 않는다(위 주석) */ });
    return () => ctrl.abort();
  }, [loading, userId, record]);

  if (!state?.needsConsent) return null;
  const { required } = state;
  const revised = Boolean(state.accepted); // 기록이 있는데 떴다 = 개정 재동의

  const submit = async () => {
    if (!checked || pending) return;
    setPending(true); setError('');
    try {
      await record(required);
    } catch (e) {
      if (e?.status === 409) {
        // 화면을 띄운 사이 문서가 개정됐다 — 새 버전으로 다시 그린다.
        try { setState(await getSellerConsent()); setChecked(false); } catch { /* 다음 진입에서 */ }
        setError('약관이 갱신됐어요. 새 버전을 확인한 뒤 다시 동의해 주세요.');
      } else {
        setError(e?.message || '저장하지 못했어요. 잠시 후 다시 시도해 주세요.');
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <div className={styles.screen} role="dialog" aria-modal="true" aria-labelledby="signup-completion-title">
      <header className={styles.bar}>
        <span className={styles.brand}>
          <img className={styles.logo} src="/assets/brand/logo.svg" alt="" />
          <img className={styles.wordmark} src="/assets/brand/wordmark.png" alt="Wearless" />
        </span>
        {/* 나가기 = 로그아웃. 동의 없이 서비스로 들어갈 문을 만들지 않는다. */}
        <button type="button" className={styles.close} onClick={() => signOut?.()}
          disabled={pending} title="나가기(로그아웃)" aria-label="나가기(로그아웃)">
          <Icon name="x" size={18} stroke={2} />
        </button>
      </header>

      <main className={styles.body}>
        <h1 id="signup-completion-title" className={styles.title}>
          {revised ? '약관이 바뀌어 다시 확인해 주세요' : '가입을 마치려면 한 가지만 확인해 주세요'}
        </h1>
        <p className={styles.desc}>
          {revised
            ? '이용약관 또는 개인정보 처리방침이 개정됐어요. 바뀐 문서를 확인하고 동의하면 이어서 쓸 수 있어요.'
            : '아직 가입이 끝나지 않았어요. 아래 문서에 동의하면 바로 시작할 수 있어요.'}
        </p>

        <ul className={styles.docs}>
          <li>
            <a href={WEARLESS_LEGAL_URLS.terms} target="_blank" rel="noreferrer">이용약관</a>
            <span className={styles.ver}>{required.terms}</span>
          </li>
          <li>
            <a href={WEARLESS_LEGAL_URLS.privacy} target="_blank" rel="noreferrer">개인정보 처리방침</a>
            <span className={styles.ver}>{required.privacy}</span>
          </li>
        </ul>

        <label className={styles.consent}>
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>만 19세 이상이며, 위 이용약관과 개인정보 처리방침에 동의합니다.</span>
        </label>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <div className={styles.actions}>
          <Button variant="primary" onClick={submit} disabled={!checked || pending}>
            {pending ? '처리 중…' : revised ? '동의하고 계속하기' : '가입 완료'}
          </Button>
        </div>
      </main>
    </div>
  );
}
