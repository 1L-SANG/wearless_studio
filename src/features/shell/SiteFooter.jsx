import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import { COMPANY_INFO_LINES } from '@/lib/companyInfo.js';
import './SiteFooter.css';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <nav className="site-footer__links" aria-label="법적 고지">
          <a href={WEARLESS_LEGAL_URLS.terms}>이용약관</a>
          <span aria-hidden="true">·</span>
          <a href={WEARLESS_LEGAL_URLS.privacy}><strong>개인정보 처리방침</strong></a>
          <span aria-hidden="true">·</span>
          <a href={WEARLESS_LEGAL_URLS.refund}>환불 정책</a>
          <span aria-hidden="true">·</span>
          <a href={WEARLESS_LEGAL_URLS.modelLicenseTerms}>모델 라이선스 이용조건</a>
        </nav>
        <div className="site-footer__company">
          {COMPANY_INFO_LINES.map((line) => <p key={line}>{line}</p>)}
          <p>호스팅 Amazon Web Services, Inc.</p>
        </div>
      </div>
    </footer>
  );
}

export default SiteFooter;
