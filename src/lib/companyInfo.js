import companyInfo from './companyInfo.json';

export const COMPANY_INFO = Object.freeze(companyInfo);

export const COMPANY_INFO_ROWS = Object.freeze([
  { key: 'copyright', text: `Copyright © ${new Date().getFullYear()} ${COMPANY_INFO.name}. All rights reserved.` },
  { key: 'brn', label: '사업자등록번호', text: COMPANY_INFO.businessRegistrationNumber },
  { key: 'mail-order', label: '통신판매업번호', text: COMPANY_INFO.mailOrderRegistration },
  { key: 'rep', label: '대표자', text: COMPANY_INFO.representative },
  { key: 'phone', label: '연락처', text: COMPANY_INFO.phone, href: `tel:${COMPANY_INFO.phone}` },
  { key: 'email', label: '이메일', text: COMPANY_INFO.email, href: `mailto:${COMPANY_INFO.email}` },
  { key: 'address', label: '사업자주소', text: COMPANY_INFO.address },
]);
