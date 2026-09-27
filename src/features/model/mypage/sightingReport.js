/* 내 얼굴 찾기 신고(모델 제보)의 순수 규칙(2026-09-27). 서버 facemarket_sightings.py 와 같은 한도. */

export const SIGHTING_MAX_BYTES = 30 * 1024 * 1024;
const MAX_PAGE_URL = 500;
const MAX_NOTE = 1000;

export function validateSighting({ file, pageUrl = '', note = '' } = {}) {
  if (!file) return '발견한 이미지를 골라 주세요.';
  if (file.type && !file.type.startsWith('image/')) return '이미지 파일만 올릴 수 있어요.';
  if (file.size > SIGHTING_MAX_BYTES) return '이미지는 30MB 이하만 올릴 수 있어요.';
  const url = pageUrl.trim();
  if (url) {
    if (url.length > MAX_PAGE_URL) return '페이지 주소는 500자 이하로 적어 주세요.';
    let parsed;
    try { parsed = new URL(url); } catch { parsed = null; }
    if (!parsed || !['http:', 'https:'].includes(parsed.protocol)) return '페이지 주소는 http:// 또는 https:// 로 시작해야 해요.';
  }
  if (note.length > MAX_NOTE) return '메모는 1000자 이하로 적어 주세요.';
  return '';
}

const STATUS = {
  new: '확인하고 있어요',
  misuse: '허락 없이 쓰인 걸 확인했어요',
  seller_own: '라이선스로 허락된 사용이에요',
  dismissed: '내 얼굴과 관련 없는 이미지였어요',
};

export function sightingStatusLabel(status) {
  return STATUS[status] || STATUS.new;
}
