/* =============================================================
   facemarket-landing/FacemarketLanding.jsx
   랜딩 홈('/') — 히어로 + 예시 캐러셀.

   상단바 세 항목은 각자 자기 라우트를 갖는다(SPA, 새 페이지처럼):
   /models · /license · /payout. 전부 무인증 공개라 설명을 다 읽고 나서 전환하게 되고,
   각 페이지 CTA 가 실제 인증 라우트(/model/*)로 보낸다.
   /models 와 /payout 은 아직 화면이 없어 자리만 잡아 뒀다(PlaceholderPage).
   상단바에서 내려온 /register(등록 7단계 안내)와 /model-info(프라이버시)는 살아 있고
   푸터에서 들어간다.
   ============================================================= */
import { LandingShell } from './LandingShell.jsx';
import { HeroSection } from './sections/HeroSection.jsx';
import { GallerySection } from './sections/GallerySection.jsx';
import { IntroSection } from './sections/IntroSection.jsx';
import s from './FacemarketLanding.module.css';

const TITLE = 'FaceMarket — 내 얼굴을 라이선스로';
/* 이 설명은 LicensingSection 과 같은 눈금이어야 한다. "쓰인 만큼 정산받는다"·"어디에
   쓰였는지 확인한다"는 지급(payout) 코드도 모델용 사용 내역 화면도 없는 지금 지키지
   못하는 약속이라(LicensingSection.jsx 헤더 주석 참고) 쓰지 않는다. 여기 남은 세 가지
   —조건 선택·QR 공개 검증·해지—는 전부 실동작하는 것만 골랐다.

   '해지'라고 쓴다. 제품 화면이 그 단어를 쓰고(ModelLicense.jsx·PublicVerify.jsx),
   랜딩의 다른 문자열도 전부 '해지'로 맞춰져 있다 — '폐기'로 되돌리지 마라. 목적어도
   생략하지 마라: '언제든 해지'만 쓰면 등록 자체를 지울 수 있다고 읽히는데, 같은 사이트
   /model-info 가 '등록 자체를 지우는 화면은 아직 없습니다'라고 정반대를 말한다.
   이 문자열은 검색 스니펫으로 나가므로 본문보다 먼저 읽힌다. */
const DESCRIPTION =
  '얼굴을 등록하고, 어떤 품목에 얼마 동안 쓸 수 있는지 직접 정해 라이선스로 발급합니다. '
  + '조건은 QR 로 누구나 확인할 수 있고, 발급한 라이선스는 언제든 해지할 수 있어요.';

export function FacemarketLanding() {
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {({ ctaLabel, onPrimary }) => (
        <>
          {/* 첫 화면 = 원본 spotlight 의 한 뷰포트 구성. 상단바 아래 남는 높이를 이 그리드가
              전부 받아(행: 히어로 / 스테이지 1fr / 메타 바) 캐러셀이 남는 만큼 커지고, 메타 바
              (힌트·점·인덱스·화살표)가 스크롤 없이 첫 화면 바닥에 선다. GallerySection 은
              래퍼 없이 스테이지와 메타 바를 이 그리드의 직접 자식으로 돌려준다.
              리드문·CTA 는 그 아래(IntroSection)다 — 첫 화면에 끼우면 캐러셀이 밀려난다. */}
          <div className={s.screen}>
            <HeroSection />
            <GallerySection />
          </div>
          <IntroSection onPrimary={onPrimary} primaryLabel={ctaLabel} />
        </>
      )}
    </LandingShell>
  );
}
