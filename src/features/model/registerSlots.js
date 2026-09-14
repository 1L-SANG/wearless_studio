import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';

// 사진 캡션은 이 목록에서 고쳐요.
//
// 순서·문구는 촬영 가이드('얼굴 촬영 16컷')와 같아요 — 번호대로 찍으면 그대로 채워져요.
// 슬롯 키는 서버(`server/app/facemarket_photos.py` PHOTO_SLOTS)와 **같은 이름**이에요.
// 조명 4가지(그늘·해가 왼쪽·해가 오른쪽·역광) × 컷 3장 = 학습 12장, 그늘에서 기준 4장,
// 그리고 공개 프로필에만 쓰는 측면 1장 = 17장.
export const SLOTS = Object.freeze([
  { n: 1, key: 'sh_front', group: 'sh', title: '정면 · 무표정', hint: '카메라를 똑바로, 입 다물고 힘 뺀 얼굴', framing: 'face', angle: 'front' },
  { n: 2, key: 'sh_smile', group: 'sh', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 3, key: 'sh_34', group: 'sh', title: '3/4 · 무표정', hint: '고개를 한쪽으로 30~40도. 끝까지 같은 쪽으로, 먼 쪽 눈이 꼭 보이게', framing: 'face', angle: 'left45' },
  { n: 4, key: 'sh_front2', group: 'sh', title: '정면 · 무표정 (한 번 더)', hint: '1번과 같은 자리, 같은 얼굴', framing: 'face', angle: 'front' },
  { n: 5, key: 'sh_chin_down', group: 'sh', title: '정면 · 턱 살짝 내리기', hint: '얼굴은 정면, 턱만 조금 당기기', framing: 'face', angle: 'down' },
  { n: 6, key: 'sh_gaze_left', group: 'sh', title: '정면 · 시선만 왼쪽', hint: '얼굴은 정면 그대로, 눈동자만 왼쪽', framing: 'face', angle: 'front' },
  { n: 7, key: 'sh_gaze_right', group: 'sh', title: '정면 · 시선만 오른쪽', hint: '얼굴은 정면 그대로, 눈동자만 오른쪽', framing: 'face', angle: 'front' },
  { n: 8, key: 'sh_side', group: 'sh', title: '측면', hint: '고개를 끝까지 돌려 옆모습. 이 한 장만 학습이 아니라 공개 프로필에 써요', framing: 'face', angle: 'left' },
  { n: 9, key: 'sl_front', group: 'sl', title: '정면 · 무표정', hint: '눈부시면 잠깐 감았다 뜨고 바로', framing: 'face', angle: 'front' },
  { n: 10, key: 'sl_smile', group: 'sl', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 11, key: 'sl_34', group: 'sl', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
  { n: 12, key: 'sr_front', group: 'sr', title: '정면 · 무표정', hint: '카메라를 똑바로', framing: 'face', angle: 'front' },
  { n: 13, key: 'sr_smile', group: 'sr', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 14, key: 'sr_34', group: 'sr', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
  { n: 15, key: 'bl_front', group: 'bl', title: '정면 · 무표정', hint: '플래시 끄고, 얼굴이 까맣게 나오면 다시', framing: 'face', angle: 'front' },
  { n: 16, key: 'bl_smile', group: 'bl', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 17, key: 'bl_34', group: 'bl', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
].map((slot) => Object.freeze(slot)));

// 조명별로 한 화면씩. `sun` 은 위에서 본 그림(RegisterIllustration.SunDiagram)이 쓰는 값이에요.
export const PHOTO_GROUPS = Object.freeze([
  { id: 'sh', sun: 'shade', title: '그늘', badge: '건물 그림자 · 나무 그늘', note: '해가 얼굴에 직접 안 닿는 곳에 서고, 찍는 사람은 얼굴 정면에.' },
  { id: 'sl', sun: 'left', title: '해가 왼쪽', badge: '해가 왼쪽 옆에서', note: '해가 화면 왼쪽 옆에서 얼굴을 비추게 서요.' },
  { id: 'sr', sun: 'right', title: '해가 오른쪽', badge: '해가 오른쪽 옆에서', note: '반대로 돌아서, 해가 화면 오른쪽 옆에서 비추게 서요.' },
  { id: 'bl', sun: 'back', title: '역광', badge: '해를 등지고', note: '해를 등지고 서요. 화면에서 얼굴을 눌러 밝기를 얼굴에 맞춰요.' },
]);
// 사진 확인 화면의 sub 번호. 조명 화면 다음 자리예요(조명이 늘면 같이 밀려요).
export const PHOTO_REVIEW_SUB = PHOTO_GROUPS.length + 1;

// 찍기 전에 확인할 것. 하나라도 어기면 업로드에서 반려돼요(서버 검사와 같은 항목).
export const SHOOT_RULES = Object.freeze([
  { title: '안경 · 선글라스 · 모자 없이', body: '얼굴 교체가 눈을 덮은 안경을 지워요. 모자는 머리 모양 학습을 망쳐요.' },
  { title: '머리부터 가슴까지, 얼굴이 크게', body: '얼굴 폭이 사진 가로의 1/4쯤 되게. 멀면 "얼굴이 작아요"로 반려돼요.' },
  { title: '한 사진에 한 사람만', body: '뒤에 다른 얼굴이 크게 걸리면 반려돼요.' },
  { title: '뒷카메라 1배, 기본 설정 그대로', body: '인물사진 모드 · 뷰티 필터 · 0.5배 광각은 쓰지 마세요.' },
  { title: '같은 날, 지금 머리 그대로', body: '머리 모양까지 배워요. 중간에 바꾸면 섞여요.' },
  { title: '기준 4장(4~7번)은 전부 그늘 같은 자리', body: '조명이 섞이면 기준 사진끼리 점수가 떨어져 기준으로 못 써요.' },
]);

export const REGISTER_BODIES = Object.freeze([
  { value: 'delicate', label: '여리여리', width: 0.78 },
  { value: 'slim', label: '마름', width: 0.92 },
  { value: 'regular', label: '보통', width: 1.06 },
  { value: 'plump', label: '통통', width: 1.25 },
]);
export const CONSENT_VERSION = '2026-09-v1';
// 옛 등록(3각도 · 18칸)이 돌려주는 이름 → 지금 슬롯. 서버 facemarket_photos.SLOT_CANDIDATES 와 같아요.
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
