/* =============================================================
   KakaoCallback — 카카오 OpenID Connect 로그인의 착지점(/auth/kakao/callback).

   세 앱(셀러·FaceMarket·admin)이 같은 컴포넌트를 쓴다. 각 앱의 라우터에서
   **RequireAuth 밖·레이아웃 밖·catch-all 앞**에 둔다 — 여기 도착하는 사람은 정의상
   아직 로그인 전이라, 인증 가드 안에 두면 로그인 프롬프트가 다시 떠서 모달이 열리고
   인가코드는 소비되지 않는다(admin 이 특히 그렇다: 원래 전 라우트가 가드 안이다).
   경위 전체는 lib/kakaoOidc.js 머리말(KOE205), 판단 로직은 lib/kakaoCallbackFlow.js.

   ## '/' 로 보내는 이유 (wl_postLogin 을 여기서 직접 읽지 마라)
   복귀 목표(sessionStorage 'wl_postLogin')의 소비자는 이미 둘 있다 — 셀러의 RootRedirect
   와 facemarket 의 FacemarketRoot. 그 둘은 오픈 리다이렉트 방어(facemarketRootTarget 의
   화이트리스트)와 draft→콘티 승격까지 같이 한다. 여기서 플래그를 직접 읽어 navigate 하면
   그 기계장치를 통째로 우회하게 되고, 같은 검사를 여기 다시 구현해야 한다.
   '/' 는 그 소비자들이 서 있는 자리다 — 이미 심긴 복귀 의도가 거기서 그대로 처리된다.

   ## 반드시 **세션이 만들어진 뒤에** 이동해야 한다
   FacemarketRoot 는 `settled && session` 일 때만 복귀 목표로 Navigate 한다. 세션 없이
   '/' 로 보내면 `settled && !session` 판정이 나서 복귀 플래그를 버리고 랜딩을 그린다.

   ## StrictMode 이중 마운트
   인가코드는 **일회용**이다. 두 번 보내면 두 번째가 실패하면서 첫 성공을 덮을 수 있다.
   그래서 교환은 모듈 스코프 프라미스 싱글플라이트로 한 번만 돈다(AuthProvider 의
   oauthExchangeCode/oauthExchangePromise 가 같은 이유로 같은 모양이다).
   컴포넌트 ref 만으로는 StrictMode 에서 두 번 돈다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui.jsx';
import { useAuth } from './AuthProvider.jsx';
import { http } from '@/lib/api/httpAdapter.js';
import { readKakaoCallbackParams, safeInternalPath } from '@/lib/kakaoOidc.js';
import { runKakaoCallbackFlow } from '@/lib/kakaoCallbackFlow.js';

/* 로그인 뒤 목적지. 상수를 그대로 navigate 하지 않고 같은 검사기를 한 번 지나게 둔다 —
   나중에 누가 여기에 URL 을 흘려 넣으면 그때 막힌다(오픈 리다이렉트 방어의 마지막 문). */
const POST_LOGIN_DESTINATION = '/';

let pendingKey = null;
let pendingPromise = null;

function runOnce(search, signInWithKakaoIdToken) {
  // 인가코드 한 개당 한 번. 에러 복귀도 같은 키로 묶어 StrictMode 이중 실행을 접는다.
  const params = readKakaoCallbackParams(search);
  const key = params.code || `error:${params.error || 'missing'}`;
  if (pendingPromise && pendingKey === key) return pendingPromise;
  pendingKey = key;
  pendingPromise = runKakaoCallbackFlow({ search, http, signInWithKakaoIdToken });
  return pendingPromise;
}

export function KakaoCallback() {
  const navigate = useNavigate();
  const { signInWithKakaoIdToken, openLogin } = useAuth();
  const [failed, setFailed] = useState(null);
  /* AuthProvider 는 context value 를 매 렌더 새로 만든다 — 세션이 도착해 리렌더되면
     signInWithKakaoIdToken 의 identity 가 바뀌어 아래 effect 가 다시 돈다. 교환 자체는
     위 싱글플라이트가 막지만, 이미 끝난 결과로 navigate·setState 를 또 부르는 건
     의미가 없다. 한 번 결론이 나면 그걸로 끝낸다. */
  const settled = useRef(false);

  useEffect(() => {
    let alive = true;    // StrictMode 이중 마운트: cleanup 이후 state 갱신 방지
    runOnce(window.location.search, signInWithKakaoIdToken).then((result) => {
      if (!alive || settled.current) return;
      settled.current = true;
      if (result.ok) {
        // 세션이 **만들어진 뒤에** 이동한다(머리말). replace 라 뒤로가기가 콜백으로
        // 되돌아오지 않는다 — 인가코드는 이미 소비돼 두 번째는 반드시 실패한다.
        navigate(safeInternalPath(POST_LOGIN_DESTINATION) || '/', { replace: true });
        return;
      }
      setFailed(result);
    });
    return () => { alive = false; };
  }, [navigate, signInWithKakaoIdToken]);

  if (!failed) return <div className="route-loading">카카오 로그인을 마무리하고 있어요…</div>;

  return (
    <div className="route-loading">
      <div style={{ display: 'grid', gap: 16, justifyItems: 'center', padding: 24, textAlign: 'center' }}>
        <p style={{ margin: 0 }}>{failed.message}</p>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          {/* 모달은 AuthProvider 가 전역으로 그린다 — 어느 라우트에서든 열린다.
              먼저 '/' 로 옮긴 뒤 여는 이유: 이 주소에는 소비된 인가코드가 붙어 있어
              새로고침·뒤로가기가 같은 실패를 반복한다. */}
          <Button
            variant="primary"
            size="sm"
            onClick={() => { navigate('/', { replace: true }); openLogin?.(); }}
          >
            로그인 다시 시도
          </Button>
          <Link className="link" to="/" replace>처음으로</Link>
        </div>
      </div>
    </div>
  );
}
