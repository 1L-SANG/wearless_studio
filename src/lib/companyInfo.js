export const COMPANY_INFO = Object.freeze({
  name: '데일리모먼트',
  representative: '정일상',
  businessRegistrationNumber: '371-02-03688',
  address: '서울특별시 노원구 석계로 98-2 광운대역 3층 스타트업스테이션',
  phone: '010-9592-0333',
  email: 'contact@wearless.kr',
  mailOrderRegistration: '면제 대상(직전연도 거래 50회 미만)',
  privacyOfficer: '정일상',
});

export const COMPANY_INFO_LINES = Object.freeze([
  `${COMPANY_INFO.name} | 대표 ${COMPANY_INFO.representative} | 사업자등록번호 ${COMPANY_INFO.businessRegistrationNumber}`,
  `${COMPANY_INFO.address} | ${COMPANY_INFO.phone} | ${COMPANY_INFO.email}`,
  `통신판매업 신고: ${COMPANY_INFO.mailOrderRegistration} | 개인정보 보호책임자 ${COMPANY_INFO.privacyOfficer} (${COMPANY_INFO.email})`,
]);
