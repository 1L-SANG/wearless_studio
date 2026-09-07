import { Link } from 'react-router-dom';
import { COMPANY_INFO_LINES } from '@/lib/companyInfo.js';
import s from '../FacemarketLanding.module.css';

export function FooterSection({ compact = false }) {
  return (
    <footer className={`${s.footer}${compact ? ` ${s.footerCompact}` : ''}`}>
      <p className={s.footerBrand}>FaceMarket · Wearless</p>
      {/* 여기 있던 "캐러셀 이미지는 모두 예시" 고지는 지웠다. 지운 이유는 사실이 아니라서가
          아니라 **셋째 사본**이라서다 — 같은 고지가 (1) 캐러셀 메타 바(GallerySection 의
          .galleryNotice, 조작 힌트 바로 밑)와 (2) 카드 안 배지(CarouselStage 의 .badgeNotice
          "예시")에 이미 있다. 그 둘은 지우지 마라: (2)는 사진마다 박혀 있어 이미지만 잘려
          공유돼도 가상 모델이라는 사실이 같이 나가고, (1)은 그걸 문장으로 한 번 더 못박는다. 푸터 사본은
          스크롤 끝이라 캐러셀과 한 화면에 있지도 않아 고지로서 하는 일이 없었다.
          PRD §13-5("예시 사진과 내 사진의 구분 장치가 사라지면 안 된다")는 (1)+(2)로 지켜진다. */}
      <nav className={s.footerLegal} aria-label="법적 고지">
        <Link className={s.footerLink} to="/terms">모델 이용약관</Link>
        <span aria-hidden="true">·</span>
        <Link className={s.footerLink} to="/license-agreement">초상 라이선스 계약</Link>
        <span aria-hidden="true">·</span>
        <Link className={`${s.footerLink} ${s.footerLinkStrong}`} to="/privacy">개인정보 처리방침</Link>
        <span aria-hidden="true">·</span>
        <Link className={s.footerLink} to="/seller-terms">셀러 라이선스 이용조건</Link>
        <span aria-hidden="true">·</span>
        <Link className={s.footerLink} to="/answers">자주 묻는 법적 질문</Link>
      </nav>
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
      <div className={s.footerCompany}>
        {COMPANY_INFO_LINES.map((line) => <p key={line}>{line}</p>)}
      </div>
    </footer>
  );
}
