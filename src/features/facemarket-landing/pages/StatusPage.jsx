import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { ModelHub } from '@/features/model/ModelHub.jsx';
import { MyPage } from '@/features/model/mypage/MyPage.jsx';
import { resolveHubJourney } from '@/features/model/modelHubState.js';
import { LandingShell } from '../LandingShell.jsx';
import s from '../../model/mypage/MyPage.module.css';

export function StatusPage() {
  const { session, loading, openLogin, signOut } = useAuth();
  const navigate = useNavigate();
  const onSignOut = () => { navigate('/'); signOut?.(); };
  return <LandingShell title="마이페이지 · FaceMarket" description="지원 상태와 얼굴 참조 자산, 사용 조건과 수익을 한곳에서 확인해요.">
    {() => loading ? <div className={s.page}><p>불러오는 중이에요.</p></div>
      : session ? <ModelHub key={session.user?.id} onSignOut={onSignOut} />
      : <MyPage journey={resolveHubJourney({ authenticated: false })} onLogin={() => openLogin(`/status${window.location.hash}`)} />}
  </LandingShell>;
}
