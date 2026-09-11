import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import { CompanyInfoRows } from '@/components/CompanyInfoRows.jsx';
import './SiteFooter.css';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <div className="site-footer__top">
          <div>
            <div className="site-footer__brand">
              <img className="site-footer__logo" src="/assets/brand/logo.svg" alt="" />
              <img className="site-footer__wordmark" src="/assets/brand/wordmark.png" alt="Wearless" />
            </div>
            <p className="site-footer__tagline">쇼핑몰 촬영의 새로운 기준</p>
          </div>
          <a className="site-footer__contact" href="https://wearless.kr/#contact">문의하기</a>
        </div>
        <hr className="site-footer__divider" />
        <nav className="site-footer__links" aria-label="법적 고지">
          <a href={WEARLESS_LEGAL_URLS.terms}>이용약관</a>
          <a href={WEARLESS_LEGAL_URLS.privacy}><strong>개인정보 처리방침</strong></a>
          <a href={WEARLESS_LEGAL_URLS.refund}>환불 정책</a>
          <a href={WEARLESS_LEGAL_URLS.modelLicenseTerms}>모델 라이선스 이용조건</a>
        </nav>
        <div className="site-footer__company">
          <CompanyInfoRows />
        </div>
      </div>
    </footer>
  );
}

export default SiteFooter;
