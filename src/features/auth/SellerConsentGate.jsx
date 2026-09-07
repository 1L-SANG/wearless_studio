/* =============================================================
   SellerConsentGate — 셀러 약관·처리방침 동의를 **첫 로그인 뒤 한 번만** 받는다.
   로그인 화면(Login.jsx)에는 체크박스를 두지 않는다: 소셜 로그인은 매번 같은 화면을
   지나므로 거기서 받으면 "로그인할 때마다 동의"가 된다(오너 지적, 2026-09-07).
   서버(/v1/me/consents)가 동의한 문서 버전을 기억하고, 기록이 없거나 문서가 개정돼
   버전이 달라졌을 때만 needsConsent=true 를 준다 — 그때만 이 창이 뜬다.

   - 셀러 앱(App.jsx)에만 마운트한다. FaceMarket 모델은 등록 위저드에서 따로 받고,
     관리자는 셀러 약관의 당사자가 아니다.
   - 닫기 없음(Esc·바깥 클릭 무시): 동의 전엔 앱을 쓸 수 없다. 대신 '로그아웃'을 준다.
   - 조회 실패(네트워크 등)엔 창을 띄우지 않는다: 앱을 막는 것보다 다음 진입에서
     다시 묻는 편이 낫다. 동의 전 API 호출은 서버가 막지 않는다(동의는 증빙용 기록이고,
     서비스 이용 자체가 약관 동의 의사표시라는 게 약관 제3조의 구조다).
   ============================================================= */
import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { isMockMode } from '@/lib/api/index.js';
import { acceptSellerConsent, getSellerConsent } from '@/lib/api/consents.js';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import styles from './SellerConsentGate.module.css';

export function SellerConsentGate() {
  const { session, loading, signOut } = useAuth();
  const [state, setState] = useState(null); // 서버 응답 그대로 | null(미조회·불필요)
  const [checked, setChecked] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const userId = session?.user?.id ?? null;

  useEffect(() => {
    if (loading || !userId || isMockMode) { setState(null); return undefined; }
    const ctrl = new AbortController();
    getSellerConsent({ signal: ctrl.signal })
      .then((res) => { if (!ctrl.signal.aborted) setState(res); })
      .catch(() => { /* 조회 실패 — 위 주석대로 게이트를 띄우지 않는다 */ });
    return () => ctrl.abort();
  }, [loading, userId]);

  if (!state?.needsConsent) return null;
  const { required } = state;

  const submit = async () => {
    if (!checked || pending) return;
    setPending(true); setError('');
    try {
      const res = await acceptSellerConsent({ termsVersion: required.terms, privacyVersion: required.privacy });
      setState(res); // needsConsent=false → 창이 닫힌다
    } catch (e) {
      // 409 = 그 사이 개정. 서버가 새 버전을 함께 주므로 다시 조회해 새 버전으로 그린다.
      if (e?.status === 409) {
        try { setState(await getSellerConsent()); setChecked(false); } catch { /* 다음 진입에서 */ }
        setError('약관이 갱신됐어요. 새 버전을 확인한 뒤 다시 동의해 주세요.');
      } else {
        setError(e?.message || '동의를 저장하지 못했어요. 잠시 후 다시 시도해 주세요.');
      }
    } finally {
      setPending(false);
    }
  };

  const revised = Boolean(state.accepted); // 기록이 있는데 뜬 것 = 개정 재동의
  return (
    <Modal narrow>
      <div className={styles.wrap} role="dialog" aria-modal="true" aria-labelledby="seller-consent-title">
        <h2 id="seller-consent-title" className={styles.title}>
          {revised ? '약관이 바뀌어 다시 확인해 주세요' : '시작하기 전에 한 번만 확인해 주세요'}
        </h2>
        <p className={styles.desc}>
          {revised
            ? '이용약관 또는 개인정보 처리방침이 개정됐어요. 바뀐 문서를 확인하고 동의하면 이어서 쓸 수 있어요.'
            : 'Wearless는 첫 로그인 때 한 번만 동의를 받아요. 다음 로그인부터는 묻지 않고, 문서가 바뀔 때만 다시 안내해요.'}
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
        <label className={styles.check}>
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>만 19세 이상이며, 위 이용약관과 개인정보 처리방침에 동의합니다.</span>
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.actions}>
          <Button variant="ghost" size="sm" onClick={() => signOut?.()} disabled={pending}>로그아웃</Button>
          <Button variant="primary" onClick={submit} disabled={!checked || pending}>
            {pending ? '저장 중…' : '동의하고 시작하기'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
