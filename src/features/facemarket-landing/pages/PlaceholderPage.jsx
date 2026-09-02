/* =============================================================
   아직 만들지 않은 랜딩 페이지의 자리.

   상단바를 `모델 둘러보기 | 라이선스 | 정산` 으로 바꾸면서 앞뒤 두 항목에는 보여 줄 화면이
   아직 없다(2026-09-02 사용자 지시: "없는 것들은 빈 페이지로 냅둬"). 링크만 걸고 404 로
   보내면 상단바가 고장 난 것처럼 읽히고, 완전 백지면 로딩 실패로 읽힌다. 그래서 제목 한 줄과
   '준비 중' 한 줄만 둔다.

   여기에 **없는 기능을 설명해 두지 마라.** 특히 정산은 지급 코드도 모델용 사용 내역 화면도
   아직 없어서, 이 자리에 절차를 적으면 코드가 못 지키는 약속이 된다(라이선싱 페이지가 이미
   "실제 지급 기능은 아직 준비 중"이라고 적고 있다). 화면이 생기면 그때 이 파일을 지운다.
   ============================================================= */
import { LandingShell } from '../LandingShell.jsx';
import s from '../FacemarketLanding.module.css';

export function PlaceholderPage({ title, heading, description }) {
  return (
    <LandingShell description={description} title={title}>
      {() => (
        <section className={s.section}>
          <span className={s.eyebrow}>{heading}</span>
          <h1 className={s.sectionTitle}>준비 중이에요</h1>
          <p className={s.sectionLead}>이 화면은 아직 열지 않았어요. 준비되면 여기에서 볼 수 있습니다.</p>
        </section>
      )}
    </LandingShell>
  );
}
