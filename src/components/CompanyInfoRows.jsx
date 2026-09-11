import { COMPANY_INFO_ROWS } from '@/lib/companyInfo.js';

export function CompanyInfoRows() {
  return COMPANY_INFO_ROWS.map(({ key, label, text, href }) => (
    <p key={key}>
      {label ? `${label}: ` : null}
      {href ? <a href={href}>{text}</a> : text}
    </p>
  ));
}
