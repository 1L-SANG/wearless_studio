/* 관리자 출처 추적 · 자동 발견 탭의 순수 헬퍼(2026-09-27) — 테스트가 이 파일만 불러 본다.

   근거 문구 규칙은 직접 추적(adminTrace.js)과 같다: '워터마크 일치'만 확정이고 지문은 '유사도'다. */

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
  return { zigzag: '지그재그 순찰' }[finding?.platform] || '순찰';
}

export function findingMatchText(finding) {
  if (!finding?.method) return '일치하는 배포본·컷 없음 — 직접 확인';
  if (finding.method === 'watermark') return '워터마크 일치';
  const distance = finding.phashDistance != null ? ` · 차이 ${finding.phashDistance}/64` : '';
  return `${finding.confidence === 'medium' ? '유사도 높음' : '유사도 참고'}${distance}`;
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
