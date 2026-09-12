import { registerCta } from './registerCta.js';

const permanentOrFuture = (validUntil) => {
  if (validUntil == null) return true;
  const timestamp = new Date(validUntil).getTime();
  return Number.isFinite(timestamp) && timestamp > Date.now();
};
const activeLicense = license => license?.status === 'active'
  && permanentOrFuture(license.licenseValidUntil);

// CTA 문구와 목적지는 registerCta만 판정한다. 이 함수는 히어로에 더할 상태 정보만 만든다.
export function landingStatusPill({ ownedModel = null, enrollment = null, application = null, license = null } = {}) {
  const cta = registerCta(ownedModel, enrollment, { application, license, scope: 'landing' });
  const pill = (tone, pulse, title) => ({ tone, pulse, title, cta });

  if (ownedModel?.status === 'awaiting_confirm' || enrollment?.status === 'confirm_pending') {
    return pill('confirm', false, '테스트컷 확인');
  }
  if (ownedModel?.status === 'verified' && activeLicense(license)) {
    return pill('live', true, '공개 중');
  }
  const activeEnrollment = enrollment
    && !['cancelled', 'failed', 'passed'].includes(enrollment.status);
  const registrationInProgress = activeEnrollment
    || ['pending', 'reverification_required'].includes(ownedModel?.status);
  if (registrationInProgress) {
    return pill('progress', false, '등록 진행 중');
  }
  if (['revoked', 'expired'].includes(license?.status)
    || (license?.status === 'active' && !permanentOrFuture(license.licenseValidUntil))) {
    return pill('rejected', false, '라이선스 종료');
  }
  if (ownedModel?.status === 'suspended') {
    return pill('paused', false, '활동 일시 중지');
  }
  if (ownedModel?.status === 'verified') {
    return pill('progress', false, '공개 준비 중');
  }
  if (ownedModel) {
    return pill('progress', false, '등록 진행 중');
  }
  if (application?.status === 'under_review') {
    return pill('review', true, '검토 중');
  }
  if (application?.status === 'approved') return pill('approved', false, '승인됐어요');
  if (application?.status === 'rejected') return pill('rejected', false, '이번엔 어려워요');
  return null;
}
