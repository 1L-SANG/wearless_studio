export const TOP_SIZES = Object.freeze(['XS', 'S', 'M', 'L', 'XL']);
const LEGACY_TOP_SIZES = Object.freeze([...TOP_SIZES, 'FREE']);
export const BOTTOM_WAIST_SIZES = Object.freeze(Array.from({ length: 11 }, (_, i) => 24 + i));
export const SPONSORSHIP_CHANGED = 'facemarket:sponsorship-changed';

export const INSTAGRAM_HANDLE_PATTERN = /^[a-zA-Z0-9_](?:[a-zA-Z0-9_.]{0,28}[a-zA-Z0-9_])?$/;

export function normalizeInstagramHandle(value) {
  return String(value ?? '').trim().replace(/^@/, '');
}

export function sponsorshipDraft(model = {}) {
  return {
    sponsorshipEnabled: model.sponsorshipEnabled === true,
    instagramHandle: model.instagramHandle ?? '',
    instagramFollowers: model.instagramFollowers == null ? '' : String(model.instagramFollowers),
    sizeTop: model.sizeTop ?? '',
    sizeBottomWaist: model.sizeBottomWaist == null ? '' : String(model.sizeBottomWaist),
    // 프로필 정보 수집 동의(E-2b). 서버에 기록된 시각이 있으면 이미 동의한 상태예요.
    profileConsent: model.sponsorshipProfileConsentAt != null,
  };
}

export function sponsorshipPayload(draft) {
  return validatedSponsorship(draft, TOP_SIZES);
}

function validatedSponsorship(draft, topSizes) {
  if (!draft.sponsorshipEnabled) return { sponsorshipEnabled: false };
  const instagramHandle = normalizeInstagramHandle(draft.instagramHandle);
  if (!INSTAGRAM_HANDLE_PATTERN.test(instagramHandle) || instagramHandle.includes('..')) {
    throw new Error('인스타 계정을 확인해 주세요. 링크 대신 아이디만 적어주세요.');
  }
  const followers = String(draft.instagramFollowers ?? '').trim();
  const instagramFollowers = Number(followers);
  if (!/^\d+$/.test(followers) || !Number.isInteger(instagramFollowers) || instagramFollowers > 2147483647) {
    throw new Error('팔로워 수를 0 이상의 정수로 적어주세요.');
  }
  if (!topSizes.includes(draft.sizeTop)) throw new Error('상의 사이즈를 골라 주세요.');
  const sizeBottomWaist = Number(draft.sizeBottomWaist);
  if (!BOTTOM_WAIST_SIZES.includes(sizeBottomWaist)) throw new Error('하의 허리 사이즈를 골라 주세요.');
  if (draft.profileConsent !== true) throw new Error('프로필 정보 수집에 동의해 주세요.');
  return { sponsorshipEnabled: true, instagramHandle, instagramFollowers, sizeTop: draft.sizeTop, sizeBottomWaist, profileConsent: true };
}

export function formatFollowers(value) {
  if (!Number.isInteger(value) || value < 0) return '미입력';
  return value >= 1000 ? `${(value / 1000).toFixed(1).replace(/\.0$/, '')}천` : String(value);
}

export function filterSponsorshipModels(models, enabledOnly) {
  return enabledOnly ? models.filter(model => model.kind === 'real' && model.sponsorship?.enabled) : models;
}

export function publicSponsorship(item) {
  if (!item.sponsorshipEnabled) return null;
  // 비로그인 공개 응답은 켜짐만 알리고 상세를 비워요. 그때는 배지만 그려요.
  if (item.instagramHandle == null && item.instagramFollowers == null && item.sizeTop == null) return { enabled: true, masked: true };
  try {
    const { profileConsent, ...value } = validatedSponsorship({ ...item, profileConsent: true }, LEGACY_TOP_SIZES);
    return { enabled: true, masked: false, ...value, instagramUrl: `https://www.instagram.com/${value.instagramHandle}/`, reportedAt: item.instagramFollowersReportedAt || null };
  } catch { return null; }
}
