import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';

// 사진 캡션은 이 목록에서 고쳐요.
//
// 순서·문구는 촬영 가이드를 따라요 — 번호대로 찍으면 그대로 채워져요.
// 슬롯 키는 서버(`server/app/facemarket_photos.py` PHOTO_SLOTS)와 **같은 이름**이에요.
// 네 자리(sh·sl·sr·bl) × 컷 3장 = 학습 12장, 첫 자리에서 기준 3장,
// 공개 프로필에 쓰는 옆모습(왼쪽) 1장, 그리고 각도 수집 2장(옆모습 오른쪽·뒷모습) = 18장.
//
// ★ 2026-09-16 대표 결정: 화면 문구에서 **조명 용어를 뺐어요.** 등록자가 "그늘·역광" 을
//   이해해야 찍을 수 있는 안내였는데, 실제로 필요한 건 "밝은 야외에서 몸을 90도씩 돌린다"
//   하나예요. 접두어(sh/sl/sr/bl)와 서버의 조명 이름(facemarket_photos.LIGHTING_LABELS ·
//   export_name)은 **그대로** 둬요 — 학습 캡션과 내보내기 파일명이 거기서 나와요.
// 기준에서 '턱 살짝 내리기'는 뺐어요 — 기준끼리의 얼굴 점수가 기준선 아래로 떨어져요.
// 옆모습·뒷모습은 학습 12장에 **안 들어가요**. 지금은 모아 두고, 검증 뒤에 쓰임새를 정해요.
export const SLOTS = Object.freeze([
  { n: 1, key: 'sh_front', group: 'sh', title: '정면 · 무표정', hint: '카메라를 똑바로 보고, 입 다물고 힘 뺀 얼굴', framing: 'face', angle: 'front' },
  { n: 2, key: 'sh_smile', group: 'sh', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 3, key: 'sh_34', group: 'sh', title: '3/4 · 무표정', hint: '고개를 한쪽으로 30~40도. 끝까지 같은 쪽으로, 먼 쪽 눈이 꼭 보이게', framing: 'face', angle: 'left45' },
  { n: 4, key: 'sh_front2', group: 'sh', title: '정면 · 무표정 (한 번 더)', hint: '1번과 같은 자리, 같은 얼굴', framing: 'face', angle: 'front' },
  { n: 5, key: 'sh_gaze_left', group: 'sh', title: '정면 · 시선만 왼쪽', hint: '얼굴은 정면 그대로, 눈동자만 왼쪽', framing: 'face', angle: 'front' },
  { n: 6, key: 'sh_gaze_right', group: 'sh', title: '정면 · 시선만 오른쪽', hint: '얼굴은 정면 그대로, 눈동자만 오른쪽', framing: 'face', angle: 'front' },
  { n: 7, key: 'sh_side', group: 'sh', title: '옆모습 · 코가 화면 왼쪽', hint: '고개를 끝까지 돌려 완전한 옆모습(한쪽 눈만 보이게), 턱은 수평, 어깨까지', framing: 'face', angle: 'left' },
  { n: 8, key: 'sh_side_right', group: 'sh', title: '옆모습 · 코가 화면 오른쪽', hint: '반대쪽으로 고개를 끝까지 돌려 완전한 옆모습(한쪽 눈만 보이게), 턱은 수평, 어깨까지', framing: 'face', angle: 'right' },
  { n: 9, key: 'sh_back', group: 'sh', title: '뒷모습', hint: '뒤돌아서 뒤통수 정중앙을 머리 높이에서, 고개 똑바로, 어깨까지', framing: 'face', angle: 'back' },
  { n: 10, key: 'sl_front', group: 'sl', title: '정면 · 무표정', hint: '1번과 같은 얼굴. 눈부시면 잠깐 감았다 뜨고 바로', framing: 'face', angle: 'front' },
  { n: 11, key: 'sl_smile', group: 'sl', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 12, key: 'sl_34', group: 'sl', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
  { n: 13, key: 'sr_front', group: 'sr', title: '정면 · 무표정', hint: '1번과 같은 얼굴. 돌아선 자리에서 카메라를 똑바로', framing: 'face', angle: 'front' },
  { n: 14, key: 'sr_smile', group: 'sr', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 15, key: 'sr_34', group: 'sr', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
  { n: 16, key: 'bl_front', group: 'bl', title: '정면 · 무표정', hint: '1번과 같은 얼굴. 얼굴이 어두우면 화면에서 얼굴을 눌러 밝기를 맞춰요', framing: 'face', angle: 'front' },
  { n: 17, key: 'bl_smile', group: 'bl', title: '정면 · 미소', hint: '입 다문 가벼운 미소', framing: 'face', angle: 'front' },
  { n: 18, key: 'bl_34', group: 'bl', title: '3/4 · 무표정', hint: '3번과 같은 방향·같은 각도', framing: 'face', angle: 'left45' },
].map((slot) => Object.freeze(slot)));

// 단계별로 한 화면씩 — **몸을 90도씩 돌리는 동작**이 안내의 전부예요. `sun` 은 위에서 본
// 그림(RegisterIllustration.SunDiagram)이 쓰는 값이라 그대로 둬요(그림은 자리를 보여 줘요).
export const PHOTO_GROUPS = Object.freeze([
  { id: 'sh', sun: 'shade', title: '1단계 · 그늘진 곳', badge: '해가 얼굴에 직접 안 닿는 곳', note: '건물 그림자나 나무 아래에 서요. 9장 모두 같은 자리에서 찍고, 7~9번은 몸만 돌려요.' },
  { id: 'sl', sun: 'left', title: '2단계 · 햇빛 드는 곳', badge: '그대로 정면', note: '그늘에서 나와 햇빛이 드는 곳에 서요. 찍는 사람은 계속 얼굴 정면에.' },
  { id: 'sr', sun: 'right', title: '3단계 · 오른쪽으로 90도', badge: '몸을 돌려서', note: '선 자리에서 오른쪽으로 90도 돌아요. 찍는 사람이 따라 움직여 얼굴 정면에 서요.' },
  { id: 'bl', sun: 'back', title: '4단계 · 한 번 더 90도', badge: '해를 등지고', note: '한 번 더 90도 돌면 해를 등지게 돼요. 얼굴이 어두우면 화면에서 얼굴을 눌러 밝기를 맞추고 플래시는 꺼요.' },
]);
// 사진 확인 화면의 sub 번호. 단계 화면 다음 자리예요(단계가 늘면 같이 밀려요).
export const PHOTO_REVIEW_SUB = PHOTO_GROUPS.length + 1;

// 찍기 전에 확인할 것. 하나라도 어기면 업로드에서 반려돼요(서버 검사와 같은 항목).
export const SHOOT_RULES = Object.freeze([
  { title: '18장 모두 얼굴·어깨까지', body: '전신·반신은 받지 않아요. 배우는 건 얼굴이라 그 위로는 필요 없어요.' },
  { title: '안경 · 선글라스 · 모자 없이', body: '얼굴 교체가 눈을 덮은 안경을 지워요. 모자는 머리 모양 학습을 망쳐요.' },
  { title: '머리부터 가슴까지, 얼굴이 크게', body: '얼굴 폭이 사진 가로의 1/4쯤 되게. 멀면 "얼굴이 작아요"로 반려돼요.' },
  { title: '한 사진에 한 사람만', body: '뒤에 다른 얼굴이 크게 걸리면 반려돼요.' },
  { title: '뒷카메라 1배, 기본 설정 그대로', body: '인물사진 모드 · 뷰티 필터 · 0.5배 광각은 쓰지 마세요.' },
  { title: '같은 날 한 번에, 지금 머리 그대로', body: '머리 모양까지 배워요. 중간에 바꾸면 섞여요.' },
  { title: '기준 3장(4~6번)은 전부 같은 자리', body: '자리를 옮기면 기준 사진끼리 점수가 떨어져 기준으로 못 써요.' },
  { title: '옆모습 2장(7~8번)은 고개를 끝까지', body: '덜 돌리면 3/4 와 구분이 안 돼 반려돼요. 한쪽 눈만 보이게 돌려요.' },
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
