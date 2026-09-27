/* 관리자 출처 추적 · 자동 발견 탭의 순수 헬퍼(2026-09-27) — 테스트가 이 파일만 불러 본다.

   근거 문구 규칙은 직접 추적(adminTrace.js)과 같다: '워터마크 일치'만 확정이고 지문은 '유사도'다.
   네이버 순찰 원문(상품 주소·상품명·판매처)은 검색 API 특약 2.4 에 따라 21일 뒤 서버가 지운다 —
   담당자가 그 전에 확인해야 하므로 지워지는 날을 행마다 적는다. */

const DAY = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric' });

export const FINDING_STATUSES = [
  { value: 'new', label: '확인 전' },
  { value: 'misuse', label: '무단 사용' },
  { value: 'seller_own', label: '셀러 정상 사용' },
  { value: 'dismissed', label: '관련 없음' },
  { value: '', label: '전체' },
];

export function findingStatusLabel(status) {
  return FINDING_STATUSES.find(item => item.value === status)?.label || status || '';
}

export function findingSourceLabel(finding) {
  if (finding?.source === 'model_report') return '모델 제보';
  return { naver: '네이버 순찰', zigzag: '지그재그 순찰' }[finding?.platform] || '순찰';
}

export function findingMatchText(finding) {
  if (!finding?.method) return '일치하는 배포본·컷 없음 — 직접 확인';
  if (finding.method === 'watermark') return '워터마크 일치';
  const distance = finding.phashDistance != null ? ` · 차이 ${finding.phashDistance}/64` : '';
  return `${finding.confidence === 'medium' ? '유사도 높음' : '유사도 참고'}${distance}`;
}

export function purgeNote(finding) {
  if (finding?.platform !== 'naver') return '';
  if (finding.externalPurgeAt) {
    const date = new Date(finding.externalPurgeAt);
    if (!Number.isNaN(date.getTime())) return `네이버 검색 결과 원문은 ${DAY.format(date)}에 지워져요(검색 API 약관 21일).`;
  }
  if (!finding.productUrl) return '네이버 검색 결과 원문은 보관 기간(21일)이 지나 지웠어요.';
  return '';
}

export function initialTraceTab(search) {
  try {
    return new URLSearchParams(search || '').get('tab') === 'found' ? 'found' : 'manual';
  } catch {
    return 'manual';
  }
}

export function targetText(finding) {
  if (!finding?.target) return '-';
  return finding.target === 'cut' ? '생성 컷' : '배포본';
}
