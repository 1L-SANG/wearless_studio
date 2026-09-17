import { Link } from 'react-router-dom';
import { CompanyInfoRows } from '@/components/CompanyInfoRows.jsx';
import s from '../FacemarketLanding.module.css';

export function FooterSection({ compact = false }) {
  return (
    <footer className={`${s.footer}${compact ? ` ${s.footerCompact}` : ''}`}>
      <div className={s.footerTop}>
        <p className={s.footerBrand}>FaceMarket · Wearless</p>
        <a className={s.footerLink} href="https://wearless.kr/#contact">문의하기</a>
      </div>
      <nav className={s.footerLegal} aria-label="법적 고지">
        <Link className={s.footerLink} to="/terms">모델 이용약관</Link>
        <Link className={s.footerLink} to="/license-agreement">초상 라이선스 계약</Link>
        <Link className={`${s.footerLink} ${s.footerLinkStrong}`} to="/privacy">개인정보 처리방침</Link>
        <Link className={s.footerLink} to="/seller-terms">셀러 라이선스 이용조건</Link>
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
        <Link className={s.footerLink} to="/photo-guide">18장 촬영 가이드</Link>
        {' · '}
        <Link className={s.footerLink} to="/model-info">내 얼굴이 어떻게 다뤄지나요</Link>
      </p>
      <hr className={s.footerDivider} />
      <div className={s.footerCompany}>
        <CompanyInfoRows />
      </div>
    </footer>
  );
}
