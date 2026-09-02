/* =============================================================
   등록 상태 — 상단바 두 번째 항목의 목적지(/status).

   예전에 /model(ModelHub)이 보여 주던 "다음 단계 + 등록 상태 세 칸"이 통째로 여기로
   왔다(2026-09-02 사용자 지시: 라이선스 페이지를 지우고 그 자리에 등록 상태를, 상단바
   '모델 지원'은 곧바로 지원서로). /model 은 이제 여기로 넘긴다(modelSectionRoutes).

   왜 RequireAuth 아래가 아니라 **공개 라우트**인가 — 상단바 항목은 비로그인 방문자도
   누른다(LandingHeader 머리말). RequireAuth 는 첫 클릭에 로그인 모달을 띄우고 복귀
   플래그까지 심는다. 여기서는 "로그인하면 볼 수 있어요" 한 줄과 버튼으로 받는다.
   로그인 뒤에는 '/status' 로 돌아온다(facemarketRootTarget 화이트리스트에 있다).
   ============================================================= */
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Icon } from '@/components/ui.jsx';
import { ModelHub } from '@/features/model/ModelHub.jsx';
import { LandingShell } from '../LandingShell.jsx';
import s from '../FacemarketLanding.module.css';

const TITLE = '등록 상태 — FaceMarket';
const DESCRIPTION = '지원서 검토부터 얼굴 등록, 라이선스까지 지금 어디까지 왔는지 확인합니다.';

export function StatusPage() {
  const { session, loading, openLogin } = useAuth();
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {() => {
        // 부트스트랩 중 session=null 은 '비로그인'이 아니라 '아직 모름'이다(LandingShell 주석).
        if (loading) return <section className={s.section}><p className={s.sectionLead}>불러오는 중이에요</p></section>;
        if (!session) {
          return (
            <section className={s.section}>
              <span className={s.eyebrow}>등록 상태</span>
              <h1 className={s.sectionTitle}>로그인하면 내 등록 상태를 볼 수 있어요</h1>
              <p className={s.sectionLead}>
                지원서 검토 결과, 얼굴 등록 진행 단계, 라이선스 준비 여부를 한 화면에서 확인해요.
              </p>
              <button className={s.heroCta} onClick={() => openLogin('/status')} type="button">
                로그인
                <Icon name="arrowRight" size={16} stroke={2} />
              </button>
            </section>
          );
        }
        return <ModelHub />;
      }}
    </LandingShell>
  );
}
