import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';

// 사진 캡션은 이 목록에서 고쳐요.
//
// 순서·문구는 촬영 가이드를 따라요 — 번호대로 찍으면 그대로 채워져요.
// 슬롯 키는 서버(`server/app/facemarket_photos.py` PHOTO_SLOTS)와 **같은 이름**이에요.
// 네 자리(sh·sl·sr·bl) × 컷 3장 = 학습 12장, 첫 자리에서 기준 3장,
// 공개 프로필에 쓰는 옆모습(왼쪽) 1장, 그리고 각도 수집 2장(옆모습 오른쪽·뒷모습) = 18장.
//
// 접두어(sh/sl/sr/bl)와 서버의 조명 이름은 학습 캡션과 내보내기 파일 이름에 쓰여요.
// cut은 슬롯 키에서 접두어를 뺀 서버 컷 키예요. framing/angle은 기존 호환 값이에요.
// 기준에서 '턱 살짝 내리기'는 뺐어요 — 기준끼리의 얼굴 점수가 기준선 아래로 떨어져요.
// 옆모습·뒷모습은 학습 12장에 **안 들어가요**. 지금은 모아 두고, 검증 뒤에 쓰임새를 정해요.
export const SLOTS = Object.freeze([
  { n: 1, key: 'sh_front', group: 'sh', cut: 'front', title: '정면 · 무표정', hint: '렌즈를 보고 입을 다물어요.', framing: 'face', angle: 'front' },
  { n: 2, key: 'sh_smile', group: 'sh', cut: 'smile', title: '정면 · 미소', hint: '입을 다물고 가볍게 웃어요.', framing: 'face', angle: 'front', expression: 'smile' },
  { n: 3, key: 'sh_34', group: 'sh', cut: '34', title: '비스듬히 · 무표정', hint: '고개만 30~40도 돌려요. 두 눈이 보이게.', framing: 'face', angle: 'left45' },
  { n: 4, key: 'sh_front2', group: 'sh', cut: 'front2', title: '정면 · 무표정 (한 번 더)', hint: '1번과 같은 자리·표정으로 한 번 더 찍어요.', framing: 'face', angle: 'front' },
  { n: 5, key: 'sh_gaze_left', group: 'sh', cut: 'gaze_left', title: '정면 · 시선만 왼쪽', hint: '고개는 고정, 눈동자만 화면 왼쪽으로.', framing: 'face', angle: 'front', gaze: 'left' },
  { n: 6, key: 'sh_gaze_right', group: 'sh', cut: 'gaze_right', title: '정면 · 시선만 오른쪽', hint: '고개는 고정, 눈동자만 화면 오른쪽으로.', framing: 'face', angle: 'front', gaze: 'right' },
  { n: 7, key: 'sh_side', group: 'sh', cut: 'side', title: '옆모습 · 왼쪽 · 무표정', hint: '코가 화면 왼쪽으로. 한쪽 눈만 보이게.', framing: 'face', angle: 'left' },
  { n: 8, key: 'sh_side_right', group: 'sh', cut: 'side_right', title: '옆모습 · 오른쪽 · 무표정', hint: '코가 화면 오른쪽으로. 한쪽 눈만 보이게.', framing: 'face', angle: 'right' },
  { n: 9, key: 'sh_back', group: 'sh', cut: 'back', title: '뒷모습', hint: '얼굴이 안 보이게 완전히 뒤돌아요.', framing: 'face', angle: 'back' },
  { n: 10, key: 'sl_front', group: 'sl', cut: 'front', title: '정면 · 무표정', hint: '눈을 뜨고 렌즈를 봐요.', framing: 'face', angle: 'front' },
  { n: 11, key: 'sl_smile', group: 'sl', cut: 'smile', title: '정면 · 미소', hint: '같은 자리에서 입을 다물고 웃어요.', framing: 'face', angle: 'front', expression: 'smile' },
  { n: 12, key: 'sl_34', group: 'sl', cut: '34', title: '비스듬히 · 무표정', hint: '3번과 같은 쪽으로 고개를 돌려요.', framing: 'face', angle: 'left45' },
  { n: 13, key: 'sr_front', group: 'sr', cut: 'front', title: '정면 · 무표정', hint: '렌즈를 봐요. 얼굴에 그림자가 져도 괜찮아요.', framing: 'face', angle: 'front' },
  { n: 14, key: 'sr_smile', group: 'sr', cut: 'smile', title: '정면 · 미소', hint: '같은 자리에서 입을 다물고 웃어요.', framing: 'face', angle: 'front', expression: 'smile' },
  { n: 15, key: 'sr_34', group: 'sr', cut: '34', title: '비스듬히 · 무표정', hint: '3번과 같은 쪽으로 고개를 돌려요.', framing: 'face', angle: 'left45' },
  { n: 16, key: 'bl_front', group: 'bl', cut: 'front', title: '정면 · 무표정', hint: '얼굴을 눌러 밝기를 맞춰요. 플래시는 꺼요.', framing: 'face', angle: 'front' },
  { n: 17, key: 'bl_smile', group: 'bl', cut: 'smile', title: '정면 · 미소', hint: '같은 자리에서 입을 다물고 웃어요.', framing: 'face', angle: 'front', expression: 'smile' },
  { n: 18, key: 'bl_34', group: 'bl', cut: '34', title: '비스듬히 · 무표정', hint: '3번과 같은 쪽으로 고개를 돌려요.', framing: 'face', angle: 'left45' },
].map((slot) => Object.freeze(slot)));

export const REGISTRATION_PHOTO_COUNT = SLOTS.length;
export const SHOOTING_TIME_MINUTES = 15;

// 저장 키는 유지하고, 화면에서는 촬영자가 따라 할 행동으로 안내해요.
export const PHOTO_GROUPS = Object.freeze([
  { id: 'sh', title: '그늘', action: '그늘에서', badge: '건물 그림자 · 나무 아래', note: '그늘 한 자리에서 1~9번을 찍어요.' },
  { id: 'sl', title: '햇빛', action: '햇빛 드는 곳으로 나와서', badge: '그늘에서 나오기', note: '햇빛으로 나와 아래 자세로 3장을 찍어요.' },
  { id: 'sr', title: '90도 회전', action: '오른쪽으로 90도 돌아서', badge: '선 자리에서 몸만 돌리기', note: '찍는 사람도 얼굴 정면으로 이동해요.' },
  { id: 'bl', title: '한 번 더 회전', action: '한 번 더 90도 돌아서', badge: '해를 등지고', note: '얼굴이 어두우면 화면에서 얼굴을 눌러 주세요.' },
]);
// 사진 확인 화면의 sub 번호. 단계 화면 다음 자리예요(단계가 늘면 같이 밀려요).
export const PHOTO_REVIEW_SUB = PHOTO_GROUPS.length + 1;

// 촬영 전에는 네 가지 핵심 안내만 보여줘요. 자세는 각 사진에서 설명해요.
export const SHOOT_RULES = Object.freeze([
  { icon: 'glasses', title: '안경 모자 벗기', body: '얼굴을 가리면 안 돼요.' },
  { icon: 'person', title: '혼자 나오기', body: '배경에 다른 사람이 없게 해주세요.' },
  { icon: 'camera', title: '후면카메라 촬영', body: '셀카보단 후면카메라로, 필터는 지양해주세요.' },
  { icon: 'calendar', title: '같은 날 찍기', body: '머리, 옷이 일관되게 한 번에 찍어주세요.' },
]);

export const REGISTER_BODIES = Object.freeze([
  { value: 'delicate', label: '여리여리', width: 0.78 },
  { value: 'slim', label: '마름', width: 0.92 },
  { value: 'regular', label: '보통', width: 1.06 },
  { value: 'plump', label: '통통', width: 1.25 },
]);
export const CONSENT_VERSION = '2026-09-v3';
// 옛 등록(3각도 · 16칸)이 돌려주는 이름 → 지금 슬롯. 서버 facemarket_photos.SLOT_CANDIDATES 와 같아요.
export const LEGACY_PHOTO_SLOTS = Object.freeze({
  front: 'sh_front', angle45: 'sh_34', side: 'sh_side',
  face01: 'sh_front', face03: 'sh_34', face05: 'sh_side',
});
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
  // 관리자가 재촬영을 요청했으면 그게 지금 할 일이에요 — 등록은 이미 끝났고(passed) 증서도
  // 있을 수 있지만, 요청된 칸을 다시 받기 전엔 학습이 안 돌아요. 상태(status)보다 먼저 봐요.
  if (enrollment.photoReviewStatus === 'reshoot_requested' && (enrollment.reshootSlots || []).length) {
    return { step: 'reshoot', sub: 1 };
  }
  // 간편인증(simple_auth) 경로 전용 두 상태. id_capture_pending 은 본인확인 전에 신분증을
  // 찍어 올리는 단계이고, review_pending 은 완료 직전 관리자 육안 심사를 기다리는 단계다
  // — 둘 다 '끝났다'(done)로 보내면 사용자는 증서도 없이 축하 화면을 보게 된다.
  if (status === 'id_capture_pending') return { step: 'id_capture', sub: 1 };
  if (status === 'review_pending') return { step: 'review', sub: PHOTO_REVIEW_SUB };
  if (status === 'identity_pending') return { step: '1', sub: 1 };
  if (status === 'photos_pending' || status === 'liveness_pending') {
    const index = PHOTO_GROUPS.findIndex((group) => !photoProgress(enrollment.photos, group.id).complete);
    return { step: '2', sub: index < 0 ? PHOTO_REVIEW_SUB : index + 1 };
  }
  if (status === 'license_pending') return { step: '3', sub: PHOTO_REVIEW_SUB };
  if (status === 'vc_pending') return { step: '4b', sub: PHOTO_REVIEW_SUB };
  if (status === 'passed') return { step: 'done', sub: PHOTO_REVIEW_SUB };
  if (['processing', 'asset_building'].includes(status)) return { step: 'processing', sub: PHOTO_REVIEW_SUB };
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
