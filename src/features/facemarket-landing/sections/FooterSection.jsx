/* =============================================================
   푸터.

   개인정보처리방침·이용약관 링크가 없다. 넣지 않은 게 아니라 **걸 곳이 없다** —
   레포에 해당 페이지가 없고(`/legal/...` 라우트는 App.jsx 에 존재하지 않는다),
   서버 NOTICE_URIS(personalization.py:59-63)도 "법무 확정 URI 자리" 라고 적힌
   플레이스홀더라 지금 걸면 catch-all 로 튕긴다. 없는 주소를 지어 걸지 마라 —
   생체정보를 넘기라고 설득하는 페이지다.

   법무 문서가 생기면 이 문단을 링크로 바꾼다. 근거로 ModelConsent.jsx 를 들지 마라 —
   그 컴포넌트는 어디에도 마운트되지 않는다(App.jsx 의 /model/consent 는 Navigate 로
   대체됐고 import 하는 파일이 0개다). 죽은 화면을 살아 있는 고지로 세면 안 된다.
   사업자 정보도 여기 들어가야 하는데, 값을 확인할 소스가 레포에 없어 비워 뒀다.
   ============================================================= */
import { Link } from 'react-router-dom';
import s from '../FacemarketLanding.module.css';

export function FooterSection() {
  return (
    <footer className={s.footer}>
      <p className={s.footerBrand}>FaceMarket · Wearless</p>
      {/* 여기 있던 "캐러셀 이미지는 모두 예시" 고지는 지웠다. 지운 이유는 사실이 아니라서가
          아니라 **셋째 사본**이라서다 — 같은 고지가 (1) 캐러셀 메타 바(GallerySection 의
          .galleryNotice, 조작 힌트 바로 밑)와 (2) 카드 안 배지(CarouselStage 의 .badgeNotice
          "예시")에 이미 있다. 그 둘은 지우지 마라: (2)는 사진마다 박혀 있어 이미지만 잘려
          공유돼도 가상 모델이라는 사실이 같이 나가고, (1)은 그걸 문장으로 한 번 더 못박는다. 푸터 사본은
          스크롤 끝이라 캐러셀과 한 화면에 있지도 않아 고지로서 하는 일이 없었다.
          PRD §13-5("예시 사진과 내 사진의 구분 장치가 사라지면 안 된다")는 (1)+(2)로 지켜진다. */}
      {/* 등록 첫 단계(ModelRegister.jsx STEP 1/7)에서 실제로 보이는 건 처리 안내 5줄
          —신분증 본인 확인·초상 대조·us-east-1 처리·신분증 초상 미저장·체형 저장— 과
          동의 체크박스 하나다. **보관 기간(일수)은 그 화면 어디에도 뜨지 않는다.**
          FaceMarket 서버에는 보관기간 상수도 고지 URI 도 없다(GET /v1/facemarket/config 는
          livenessRequired 하나만 돌려준다). RETENTION_DAYS·NOTICE_URIS 는 개인화 도메인
          것이고 그걸 그리는 ModelConsent.jsx 는 마운트되지 않는다. STEP 1 에 보관 기간을
          실제로 렌더하기 전에는 여기서 보관 기간을 약속하지 마라.

          "이용약관이 없다"고 단정하지도 않는다 — 이 랜딩 CTA 가 여는 로그인 모달
          (Login.jsx)이 "계속하면 서비스 약관에 동의하는 것으로 간주됩니다" 라고 말한다.
          두 화면이 두 클릭 안에 서로를 부정하지 않도록, 여기서는 '이 페이지에 걸 링크가
          아직 없다'는 사실까지만 적는다. */}
      <p className={s.footerNote}>
        개인정보처리방침·이용약관 링크는 공개 문서가 준비되면 여기에 겁니다.
        무엇을 어떻게 처리하는지는 모델 등록 첫 단계에서 동의하기 전에 화면에 표시됩니다.
      </p>
      <p className={s.footerNote}>
        상품 상세페이지를 만드는 셀러라면 <a className={s.footerLink} href="https://ai.wearless.kr">ai.wearless.kr</a> 로 오세요.
      </p>
      {/* 상단바가 모델 둘러보기·라이선스·정산으로 바뀌면서 자리를 잃은 두 화면. 지우지 않은
          이유는 App.jsx 라우트 주석에 있다 — 생체정보를 넘기기 전에 등록 절차와 취급 규칙을
          읽을 자리가 사이트에 하나는 있어야 한다. 여기가 그 자리다. */}
      <p className={s.footerNote}>
        <Link className={s.footerLink} to="/register">모델 등록 안내</Link>
        {' · '}
        <Link className={s.footerLink} to="/model-info">내 얼굴이 어떻게 다뤄지나요</Link>
      </p>
    </footer>
  );
}
