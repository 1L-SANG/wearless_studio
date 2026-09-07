import companyInfo from './companyInfo.json';

export const COMPANY_INFO = Object.freeze(companyInfo);

export const COMPANY_INFO_LINES = Object.freeze([
  `${COMPANY_INFO.name} | 대표 ${COMPANY_INFO.representative} | 사업자등록번호 ${COMPANY_INFO.businessRegistrationNumber}`,
  `${COMPANY_INFO.address} | ${COMPANY_INFO.phone} | ${COMPANY_INFO.email}`,
  `통신판매업 신고: ${COMPANY_INFO.mailOrderRegistration} | 개인정보 보호책임자 ${COMPANY_INFO.privacyOfficer} (${COMPANY_INFO.email})`,
]);
