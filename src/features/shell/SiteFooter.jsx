import { Link } from 'react-router-dom';
import { COMPANY_INFO_LINES } from '@/lib/companyInfo.js';
import './SiteFooter.css';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <nav className="site-footer__links" aria-label="법적 고지">
          <Link to="/terms">이용약관</Link>
          <span aria-hidden="true">·</span>
          <Link to="/privacy"><strong>개인정보 처리방침</strong></Link>
          <span aria-hidden="true">·</span>
          <Link to="/refund">환불 정책</Link>
          <span aria-hidden="true">·</span>
          <Link to="/model-license-terms">모델 라이선스 이용조건</Link>
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
