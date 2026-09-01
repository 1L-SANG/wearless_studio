/* =============================================================
   facemarket-shell/FacemarketModelLayout.jsx
   facemarket 도메인의 /model/* 화면(등록 위저드·허브·라이선스)이 입는 껍데기.

   왜 ChromeLayout 이 아닌가 — ChromeLayout 은 셀러 스튜디오 것이다. TopNav 의 크레딧
   배지·요금제 배지·플로우 스테퍼, 마네킹 완료 토스트, 상세페이지 잡 리본이 전부
   상품컷 만드는 사람 물건이라, 얼굴을 등록하러 온 모델에게는 잡음이고 "내가 맞는 곳에
   왔나" 를 흔든다. 랜딩과 같은 상단바를 써야 한 사이트로 읽힌다.

   ChromeLayout 밖으로 나가도 안전한 근거: ToastProvider·AuthProvider·QueryClientProvider
   는 전부 main.jsx 에서 App 위에 있다. ChromeLayout 이 자기 안에서만 제공하던 건
   셀러 전용 위젯뿐이라 여기서 빠져도 /model/* 이 잃는 기능이 없다.

   상단바 CTA 는 넘기지 않는다 — 이 화면들이 곧 그 CTA 의 목적지다(LandingHeader 주석).
   ============================================================= */
import { Outlet } from 'react-router-dom';
import { LandingHeader } from '@/features/facemarket-landing/LandingHeader.jsx';

export function FacemarketModelLayout() {
  return (
    <div className="fm-theme fm-theme-page">
      {/* 상단바는 좌우 여백을 바깥에서 받는다(랜딩에서는 .shell 이 준다). */}
      <div className="fm-theme-inset">
        <LandingHeader />
      </div>
      {/* 상단바가 랜딩과 같은 좌우 여백(--fm-pad)을 쓰므로 본문도 같은 자를 쓴다.
          안쪽 화면들이 .wizard 로 자기 최대폭을 잡고, facemarketTheme.css 가 그
          .wizard 를 이 테마 눈금으로 다시 정의한다. */}
      <main>
        <Outlet />
      </main>
    </div>
  );
}
