/* =============================================================
   facemarket.wearless.kr 의 라우트 트리.

   왜 App.jsx 와 갈라져 있나 — 두 도메인이 한 번들을 쓰던 시절에는 셀러가 받는 JS 에 모델
   등록 화면(생체정보 동의·신분증·얼굴 사진)이 통째로 실려 있었다. 화면에는 안 떴지만
   (host.js 의 domainRouteRedirect 가 라우터보다 먼저 막는다) 파일에는 있었다.
   진입점을 둘로 나눠 각자 자기 화면만 싣는다:

     index.html      → src/main.jsx           → App.jsx            (셀러)
     facemarket.html → src/mainFacemarket.jsx → 이 파일            (모델)

   **배포는 여전히 한 벌이다.** Vercel 프로젝트도 빌드도 하나이고, 어느 문서를 줄지는
   vercel.json 의 host rewrite 가 정한다. 갈라지는 건 문서와 그 문서가 무는 진입 청크뿐이며,
   공통 코드(React·supabase·ui·결제)는 rollup 이 공유 청크로 묶는다.

   ⚠️ 여기서 셀러 전용 화면을 import 하지 마라(ChromeLayout·ProductInput·Editor…).
   반대로 App.jsx 에서 모델 화면을 import 하지 마라. 그 순간 분리가 무효가 되고, 그걸
   tests/frontend/bundle-separation.test.mjs 가 잡는다.
   ============================================================= */
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { FacemarketRoot } from '@/features/facemarket-landing/FacemarketRoot.jsx';
import { FacemarketModelLayout } from '@/features/facemarket-shell/FacemarketModelLayout.jsx';
import { ModelsPage } from '@/features/facemarket-landing/pages/ModelsPage.jsx';
import { PayoutPage } from '@/features/facemarket-landing/pages/PayoutPage.jsx';
import { StatusPage } from '@/features/facemarket-landing/pages/StatusPage.jsx';
import { RegisterPage } from '@/features/facemarket-landing/pages/RegisterPage.jsx';
import { ModelInfoPage } from '@/features/facemarket-landing/pages/ModelInfoPage.jsx';
import { Pricing } from '@/features/pricing/Pricing.jsx';
import { CreditsHistory } from '@/features/credits/CreditsHistory.jsx';
import { PaymentSuccess, PaymentFail } from '@/features/payments/PaymentResult.jsx';
import { PublicVerify } from '@/features/verify/PublicVerify.jsx';
import { RequireAuth } from '../guards.jsx';
import { MODEL_SECTION_ROUTES } from './modelSectionRoutes.jsx';
import { domainRouteRedirect } from '@/lib/host.js';
import { isSupabaseConfigured } from '@/lib/supabase.js';

export default function AppFacemarket() {
  const { pathname } = useLocation();
  const domainRedirect = domainRouteRedirect(pathname);

  // 환경변수 미설정(예: Vercel env 누락)이면 화이트스크린 대신 원인을 보여준다.
  if (!isSupabaseConfigured) {
    return (
      <div className="route-loading">
        설정 오류: Supabase 환경변수(VITE_SUPABASE_URL·VITE_SUPABASE_ANON_KEY)가 없습니다.
      </div>
    );
  }
  /* 번들이 갈라진 뒤에도 이 가드를 남긴다 — 셀러 전용 주소(/create/…)로 들어온 사람을
     404 대신 등록 입구로 보낸다. 아래 catch-all 과 겹쳐 보이지만 그쪽은 '이 앱이 모르는
     주소', 이쪽은 '다른 도메인의 주소'라 목적지가 다르다. */
  if (domainRedirect) return <Navigate to={domainRedirect} replace />;

  return (
    <Routes>
      {/* 랜딩은 앱 크롬 밖이다 — 등록 전 방문자에게 셀러 TopNav(크레딧·스테퍼)는 잡음이고,
          랜딩은 자기 상단바를 갖는다. 로그인 복귀(wl_postLogin) 소비는 FacemarketRoot 가
          이어받는다. 상단바 세 항목은 각자 라우트다(SPA). 랜딩 전체가 **RequireAuth 밖 =
          무인증 공개**여야 한다 — 설명을 읽기 전에 로그인 모달을 띄우지 않는 게 랜딩의
          존재 이유다. 인증이 필요한 곳(/model/*)으로는 각 페이지 끝 CTA 가 보낸다. */}
      <Route index element={<FacemarketRoot />} />
      <Route path="models" element={<ModelsPage />} />
      {/* 등록 상태 — 예전 /model 허브의 내용. 공개 라우트지만 내용은 로그인 뒤에 보인다
          (StatusPage 머리말). 라이선스 페이지는 2026-09-02 지시로 지웠다. */}
      <Route path="status" element={<StatusPage />} />
      <Route path="payout" element={<PayoutPage />} />
      {/* 상단바에서 내려왔지만 화면은 그대로 살아 있다 — 푸터에서 들어간다.
          지우지 않는 이유: 등록 7단계 안내와 프라이버시 하드룰 설명은 승인받은 내용이고,
          생체정보를 넘기기 전에 읽을 자리가 사이트에 하나는 있어야 한다. */}
      <Route path="register" element={<RegisterPage />} />
      <Route path="model-info" element={<ModelInfoPage />} />
      {/* 옛 주소. 지워진 라이선스 페이지(/license·/licensing)로 공유된 링크가 404 로 떨어지지 않게
          그 자리를 이어받은 등록 상태로 보낸다. */}
      <Route path="license" element={<Navigate to="/status" replace />} />
      <Route path="licensing" element={<Navigate to="/status" replace />} />

      {/* /model/* 과 결제·크레딧은 랜딩 상단바를 입는다. 이 도메인에 온 사람은 얼굴을
          등록하러 온 모델이고, 셀러 TopNav 의 크레딧 배지·요금제·플로우 스테퍼는 전부
          상품컷 만드는 사람 물건이라 잡음이다. 인증 가드는 종전과 같다. */}
      <Route element={<FacemarketModelLayout />}>
        <Route element={<RequireAuth />}>
          {MODEL_SECTION_ROUTES}
          {/* 결제·크레딧은 두 도메인이 함께 쓰는 화면이다(host.js 의 허용 목록에도 있다).
              셀러 번들이 갈라지기 전에는 ChromeLayout 아래였는데, 그 크롬은 셀러 물건이라
              여기로 오면서 랜딩 상단바 아래로 옮겼다. 화면 자체는 그대로다. */}
          <Route path="pricing" element={<Pricing />} />
          <Route path="credits/history" element={<CreditsHistory />} />
          <Route path="payments/success" element={<PaymentSuccess />} />
          <Route path="payments/fail" element={<PaymentFail />} />
        </Route>
      </Route>

      {/* 얼굴 라이선스 공개 검증(step02 QR 대상) — **RequireAuth 밖**. 심사위원·구매자가
          VC 카드의 QR 을 자기 폰으로 찍어 로그인 없이 유효성을 확인한다(로그인 게이트를
          두면 QR 이 무의미해진다). 상단바 밖에도 둔다 — 스캔으로 진입한 사람에게 앱
          내비게이션은 잡음이다. 얼굴은 이 페이지에 렌더되지 않는다(PublicVerify 주석). */}
      <Route path="verify/:licenseId" element={<PublicVerify />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
