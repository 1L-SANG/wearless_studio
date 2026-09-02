/* =============================================================
   두 도메인 앱이 함께 쓰는 라우트 가드.

   App.jsx(셀러)와 AppFacemarket.jsx(모델)로 갈라지면서, 양쪽이 다 필요로 하는 것만
   여기로 뺐다 — 로그인 가드 하나다. 모델 소유 가드(RequireModel 계열)는 facemarket
   쪽에만 필요하므로 modelSectionRoutes.jsx 안에 둔다. **여기에 올리지 마라**:
   그 순간 listMyModels·모델 화면이 셀러 번들로 딸려 들어가 분리한 의미가 없어진다.
   ============================================================= */
import { useEffect, useRef } from 'react';
import { Link, Navigate, Outlet } from 'react-router-dom';
import { Button } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { IS_FACEMARKET } from '@/lib/host.js';
import { isMockMode } from '@/lib/api/index.js';

export function FacemarketLoginPrompt() {
  const { openLogin } = useAuth();
  // 모달을 딱 한 번만 연다. openLogin 은 AuthProvider 가 매 렌더 새로 만드는 함수라
  // deps 에 두면, 사용자가 모달을 닫아(closeLogin → AuthProvider 리렌더) identity 가
  // 바뀌는 순간 effect 가 다시 돌아 모달이 곧장 다시 열린다 — 닫을 수 없는 모달이 된다.
  // (AuthProvider 에서 useCallback 으로도 안정화했지만, 재발 방지는 여기서도 건다.)
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    openLogin?.('/model/register');
  }, [openLogin]);
  return (
    <div className="route-loading">
      모델 등록은 로그인이 필요해요 — 로그인 창을 열었어요.
      {/* 모달을 닫은 사람에게 나갈 길과 되돌릴 길을 준다. effect 가 1회성이라 닫은 모달은
          스스로 다시 열리지 않고, 이 화면은 등록 라우트라 링크가 없으면 주소창을 직접
          고치는 수밖에 없다.
          맨 <button>·맨 <a> 로 두면 안 된다. 이 레포의 전역 스타일에는 버튼·링크 리셋이
          없어서(app.css 는 `button { font-family: inherit }` 한 줄, 링크는 `a.link` 클래스
          한정) 그대로 두면 OS 기본 회색 버튼과 파란 밑줄 하이퍼링크가 프로덕션에 나온다 —
          생체정보를 맡기라고 설득하는 도메인의 첫 화면 중 하나다. 앱의 Button·`a.link` 를 쓴다.
          소개 링크는 Button 이 아니라 <Link> 로 남긴다: 이동이지 동작이 아니라서
          가운데클릭·새 탭 열기가 살아야 한다. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
        <Button variant="primary" size="sm" onClick={() => openLogin?.('/model/register')}>로그인 다시 열기</Button>
        <Link className="link" to="/">FaceMarket 소개 보기</Link>
      </div>
    </div>
  );
}

export function RequireAuth() {
  const { session, loading } = useAuth();
  // mock 데모 샌드박스 — 로그인 없이 전 플로우 확인(주소창 직접 진입 포함).
  // mock api 는 토큰을 쓰지 않으므로 세션 부재가 기능에 영향 없다. http 모드는 기존 가드 유지.
  if (isMockMode) return <Outlet />;
  if (loading) return <div className="route-loading">불러오는 중이에요</div>;
  if (!session) {
    // 번들이 갈라졌어도 이 분기는 남는다 — 로그인 모달은 두 도메인이 함께 쓰고,
    // 여기서 갈리는 건 '로그인 안 된 사람을 어디로 보내나' 하나다.
    if (IS_FACEMARKET) return <FacemarketLoginPrompt />;
    return <Navigate to="/create/input" replace />;
  }
  return <Outlet />;
}
