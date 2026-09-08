/* =============================================================
   SignupCompletion — 가입을 끝내는 화면(그리고 약관 개정 시 재동의 화면).

   동의를 받는 자리는 로그인 모달의 **회원가입 탭**이다(Login.jsx). 이 화면은 그 탭을
   지나지 않고 들어온 사람을 위한 것 — 신규가 '로그인' 탭을 눌렀거나, 다른 탭·기기에서
   로그인해 가입 동의 표시가 없는 경우다. 회원가입 탭에서 동의하고 온 사람에게는
   **아무것도 보이지 않는다**: 그 표시(signupConsent)를 확인해 조용히 서버에 기록만 한다.

   로그인 모달과 **같은 크기의 같은 창**으로 띄우고, 내용은 오른쪽에서 넘어오듯 들어온다
   (2026-09-08 오너). OAuth 왕복 때문에 페이지는 실제로 새로 뜨지만, 사용자에게는 로그인
   창의 다음 장으로 읽혀야 한다 — 그래서 브랜드 락업·모달 폭·전면 버튼을 그대로 맞췄다.
   버튼은 '가입 완료' 하나, 나가기는 우측 위 X 다. X 는 로그아웃이다: 동의 없이 서비스로
   들어갈 길을 만들면 이 화면이 무의미해진다. 그래서 Modal 에 onClose 를 주지 않는다
   (Esc·바깥 클릭으로 닫히면 그 길이 생긴다).

   셀러 앱(App.jsx)에만 마운트한다. 모델(FaceMarket)은 등록 위저드에서, 관리자는 아예
   셀러 약관의 당사자가 아니다. 조회가 실패하면(네트워크 등) 화면을 띄우지 않는다 —
   앱을 막는 것보다 다음 진입에서 다시 확인하는 편이 낫다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { isMockMode } from '@/lib/api/index.js';
import { acceptSellerConsent, getSellerConsent } from '@/lib/api/consents.js';
import { claimSignupConsent, clearSignupConsent, hasFreshSignupConsent, readSignupConsent } from '@/lib/signupConsent.js';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import styles from './SignupCompletion.module.css';

export function SignupCompletion() {
  const { session, loading, signOut } = useAuth();
  const [state, setState] = useState(null); // 서버 응답 | null(미조회·불필요)
  const [checked, setChecked] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const userId = session?.user?.id ?? null;
  const request = useRef(null);

  const record = useCallback(async (required, signal, marker) => {
    if (signal.aborted) return;
    const res = await acceptSellerConsent({
      termsVersion: required.terms, privacyVersion: required.privacy, signal, expectedUserId: userId,
    });
    if (signal.aborted) return;
    clearSignupConsent(marker);
    setState({ ...res, userId });
  }, [userId]);

  useEffect(() => {
    setState(null); setChecked(false); setPending(false); setError('');
    if (loading || !userId || isMockMode) return undefined;
    const ctrl = new AbortController();
    request.current = ctrl;
    // 조회가 늦거나 실패해도 이 가입 동의는 처음 돌아온 계정의 것이다.
    const marker = claimSignupConsent(userId);
    getSellerConsent({ signal: ctrl.signal, expectedUserId: userId })
      .then(async (res) => {
        if (ctrl.signal.aborted) return;
        // 기존 계정으로 돌아온 가입 시도도 여기서 끝난다. 다음 계정에 넘기지 않는다.
        if (res.accepted || !res.needsConsent) clearSignupConsent(marker);
        // 회원가입 탭에서 이미 동의한 사람 — 화면을 띄우지 않고 기록만 남긴다.
        if (res.needsConsent && !res.accepted && marker === readSignupConsent() && hasFreshSignupConsent()) {
          try { await record(res.required, ctrl.signal, marker); return; } catch { /* 아래에서 화면으로 받는다 */ }
        }
        if (!ctrl.signal.aborted) setState({ ...res, userId });
      })
      .catch(() => { /* 조회 실패 — 막지 않는다(위 주석) */ });
    return () => ctrl.abort();
  }, [loading, userId, record]);

  if (state?.userId !== userId || !state?.needsConsent) return null;
  const { required } = state;
  const revised = Boolean(state.accepted); // 기록이 있는데 떴다 = 개정 재동의

  const submit = async () => {
    const signal = request.current?.signal;
    if (!checked || pending || !signal || signal.aborted) return;
    setPending(true); setError('');
    try {
      await record(required, signal, readSignupConsent());
    } catch (e) {
      if (signal.aborted) return;
      if (e?.status === 409) {
        // 화면을 띄운 사이 문서가 개정됐다 — 새 버전으로 다시 그린다.
        try {
          const res = await getSellerConsent({ signal, expectedUserId: userId });
          if (signal.aborted) return;
          setState({ ...res, userId }); setChecked(false);
        } catch { /* 다음 진입에서 */ }
        if (signal.aborted) return;
        setError('약관이 갱신됐어요. 새 버전을 확인한 뒤 다시 동의해 주세요.');
      } else {
        setError(e?.message || '저장하지 못했어요. 잠시 후 다시 시도해 주세요.');
      }
    } finally {
      if (!signal.aborted) setPending(false);
    }
  };

  return (
    <Modal>
      {/* 로그인 창의 다음 장 — 오른쪽에서 넘어오듯 들어온다(클립은 viewport 가 맡는다). */}
      <div className={styles.viewport}>
      <div className={styles.panel} role="dialog" aria-modal="true" aria-labelledby="signup-completion-title">
        <button type="button" className={styles.close} onClick={() => signOut?.()}
          disabled={pending} title="나가기(로그아웃)" aria-label="나가기(로그아웃)">
          <Icon name="x" size={18} stroke={2} />
        </button>

        <div className={styles.brand}>
          <img className={styles.logo} src="/assets/brand/logo.svg" alt="" />
          <div className={styles.mark}>
            <img className={styles.wordmark} src="/assets/brand/wordmark.png" alt="Wearless" />
            <span className={styles.suffix}>Studio</span>
          </div>
        </div>

        <h2 id="signup-completion-title" className={styles.title}>
          {revised ? '약관이 바뀌어 다시 확인해 주세요' : '가입을 마치려면 한 가지만 확인해 주세요'}
        </h2>
        <p className={styles.desc}>
          {revised
            ? '이용약관 또는 개인정보 처리방침이 개정됐어요. 바뀐 문서를 확인하고 동의하면 이어서 쓸 수 있어요.'
            : <>아래 항목에 체크하고<br />바로 서비스를 이용해볼 수 있어요.</>}
        </p>

        <ul className={styles.docs}>
          <li>
            <a href={WEARLESS_LEGAL_URLS.terms} target="_blank" rel="noreferrer">이용약관</a>
            <span className={styles.required}>(필수)</span>
          </li>
          <li>
            <a href={WEARLESS_LEGAL_URLS.privacy} target="_blank" rel="noreferrer">개인정보 처리방침</a>
            <span className={styles.required}>(필수)</span>
          </li>
        </ul>

        <label className={styles.consent}>
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>만 19세 이상이며, 위 이용약관과 개인정보 처리방침에 동의합니다.</span>
        </label>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <Button variant="primary" block onClick={submit} disabled={!checked || pending}>
          {pending ? '처리 중…' : revised ? '동의하고 계속하기' : '가입 완료'}
        </Button>
      </div>
      </div>
    </Modal>
  );
}
