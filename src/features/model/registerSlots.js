import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';

// 사진 캡션은 이 목록에서 고쳐요.
export const SLOTS = Object.freeze([
  { n: 1, group: 'face', title: '정면 · 무표정', hint: '카메라를 똑바로, 눈은 정면', framing: 'face', angle: 'front' },
  { n: 2, group: 'face', title: '정면 · 미소', hint: '같은 구도, 자연스럽게 웃기', framing: 'face', angle: 'smile' },
  { n: 3, group: 'face', title: '왼쪽 45도', hint: '고개만 45도, 두 눈이 다 보이게', framing: 'face', angle: 'left45' },
  { n: 4, group: 'face', title: '오른쪽 45도', hint: '같은 방식으로 반대쪽', framing: 'face', angle: 'right45' },
  { n: 5, group: 'face', title: '왼쪽 측면', hint: '고개를 끝까지, 귀 하나만 보이게', framing: 'face', angle: 'left' },
  { n: 6, group: 'face', title: '오른쪽 측면', hint: '같은 방식으로 반대쪽', framing: 'face', angle: 'right' },
  { n: 7, group: 'face', title: '턱 살짝 위', hint: '카메라를 조금 아래에서', framing: 'face', angle: 'up' },
  { n: 8, group: 'face', title: '턱 살짝 아래', hint: '카메라를 조금 위에서', framing: 'face', angle: 'down' },
  { n: 9, group: 'torso', title: '정면', hint: '어깨선이 보이게', framing: 'torso', angle: 'front' },
  { n: 10, group: 'torso', title: '왼쪽 45도', hint: '몸을 왼쪽으로 살짝 돌리기', framing: 'torso', angle: 'left45' },
  { n: 11, group: 'torso', title: '오른쪽 45도', hint: '같은 방식으로 반대쪽', framing: 'torso', angle: 'right45' },
  { n: 12, group: 'torso', title: '측면', hint: '어깨와 허리의 옆선이 보이게', framing: 'torso', angle: 'left' },
  { n: 13, group: 'torso', title: '자연 포즈', hint: '손을 주머니에, 편하게', framing: 'torso', angle: 'pose' },
  { n: 14, group: 'full', title: '정면', hint: '발끝까지 프레임 안에', framing: 'full', angle: 'front' },
  { n: 15, group: 'full', title: '45도', hint: '몸을 살짝 돌려 서기', framing: 'full', angle: 'left45' },
  { n: 16, group: 'full', title: '측면', hint: '몸의 옆선이 보이게', framing: 'full', angle: 'left' },
  { n: 17, group: 'full', title: '뒷모습', hint: '카메라를 등지고', framing: 'full', angle: 'back' },
  { n: 18, group: 'full', title: '자연 포즈', hint: '걷듯이 편하게', framing: 'full', angle: 'pose' }
].map((slot) => Object.freeze({ ...slot, key: `${slot.group}${String(slot.n - (slot.group === 'face' ? 0 : slot.group === 'torso' ? 8 : 13)).padStart(2, '0')}` })));

export const PHOTO_GROUPS = Object.freeze([
  { id: 'face', title: '얼굴', badge: '얼굴·어깨까지' },
  { id: 'torso', title: '상반신', badge: '허리 위' },
  { id: 'full', title: '전신', badge: '머리부터 발끝' },
]);
export const REGISTER_BODIES = Object.freeze([
  { value: 'delicate', label: '여리여리', width: 0.78 },
  { value: 'slim', label: '마름', width: 0.92 },
  { value: 'regular', label: '보통', width: 1.06 },
  { value: 'plump', label: '통통', width: 1.25 },
]);
export const CONSENT_VERSION = '2026-09-v1';
export const LEGACY_PHOTO_SLOTS = Object.freeze({ front: 'face01', angle45: 'face03', side: 'face05' });
export const photoSlotKey = (photo) => LEGACY_PHOTO_SLOTS[photo.slot || photo.angle] || photo.slot || photo.angle;
export function photoProgress(photos = [], group) {
  const slots = SLOTS.filter((slot) => !group || slot.group === group);
  const filled = new Set(photos.map(photoSlotKey));
  const count = slots.filter((slot) => filled.has(slot.key)).length;
  return { count, total: slots.length, complete: count === slots.length };
}
export function defaultRegisterTerms() {
  return { allowedUse: [...BRAND_USE_CATEGORIES] };
}
export function toggleRegisterCategory(allowed, category) {
  if (!BRAND_USE_CATEGORIES.includes(category)) return allowed;
  if (allowed.includes(category) && allowed.length === 1) return allowed;
  return BRAND_USE_CATEGORIES.filter((value) => value === category ? !allowed.includes(value) : allowed.includes(value));
}
export function restoreRegisterScreen(enrollment) {
  const status = enrollment?.status;
  if (!status) return { step: '1', sub: 1 };
  if (status === 'identity_pending') return { step: '1b', sub: 1 };
  if (status === 'photos_pending' || status === 'liveness_pending') {
    const index = PHOTO_GROUPS.findIndex((group) => !photoProgress(enrollment.photos, group.id).complete);
    return { step: '2', sub: index < 0 ? 4 : index + 1 };
  }
  if (status === 'license_pending') return { step: '3', sub: 5 };
  if (status === 'vc_pending') return { step: '4b', sub: 5 };
  if (['passed', 'review_pending'].includes(status)) return { step: 'done', sub: 5 };
  if (['processing', 'asset_building'].includes(status)) return { step: 'processing', sub: 5 };
  return { step: 'failed', sub: 1 };
}
export function readRegisterDraft(enrollmentId) {
  try {
    const draft = JSON.parse(sessionStorage.getItem(`fm.registration.${enrollmentId}`));
    if (!draft || !Array.isArray(draft.allowedUse)) return defaultRegisterTerms();
    const allowedUse = BRAND_USE_CATEGORIES.filter((value) => draft.allowedUse.includes(value));
    return { allowedUse: allowedUse.length ? allowedUse : [...BRAND_USE_CATEGORIES] };
  } catch { return defaultRegisterTerms(); }
}
export function saveRegisterDraft(enrollmentId, terms) {
  if (!enrollmentId) return;
  try { sessionStorage.setItem(`fm.registration.${enrollmentId}`, JSON.stringify({ allowedUse: terms.allowedUse })); } catch { /* 저장소가 막혀도 현재 화면은 유지해요. */ }
}
