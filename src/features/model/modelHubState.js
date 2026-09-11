import { APPLY_LABEL } from '../facemarket-landing/registerCta.js';

export const HUB_STEPS = Object.freeze([
  { key: 'application', label: '지원 접수' },
  { key: 'reviewApplication', label: '내부 검토' },
  { key: 'registration', label: '모델 등록' },
  { key: 'review', label: '최종 검토' },
  { key: 'confirm', label: '프로필 이미지 확정' },
]);
const route = (label, to) => ({ label, kind: 'route', to });
const reload = (label) => ({ label, kind: 'reload' });

export function hasCurrentEnrollmentLicense(licenses = []) {
  return licenses.some(license => ['pending', 'active'].includes(license?.status));
}

export function currentModelLicense(licenses = [], model) {
  if (!model?.id) return null;
  return licenses.filter(row => row.modelId === model.id)
    .sort((a, b) => (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0))[0] || null;
}

export function resolveHubJourney({
  authenticated = true, ownedModel = null, enrollment = null, application = null,
  applicationRequired = true, hasLicense = false, license = null, now = new Date(),
} = {}) {
  const timestamps = [
    application?.createdAt,
    application?.status === 'approved' ? application.reviewedAt : undefined,
    enrollment?.completedAt || ownedModel?.enrollmentCompletedAt,
    ownedModel?.reviewCompletedAt,
    ownedModel?.confirmedAt,
  ];
  const make = (mode, currentIndex, state = 'progress', options = {}) => ({
    mode, flag: 'none', currentIndex, action: null, ...options,
    steps: HUB_STEPS.map((row, index) => ({
      ...row,
      state: index < currentIndex ? 'done' : index === currentIndex ? state : 'upcoming',
      timestamp: index < currentIndex ? timestamps[index] : undefined,
      tag: index < currentIndex ? (index === 1 ? '승인 완료' : '완료')
        : index === currentIndex ? (state === 'todo' ? '지금 할 일' : '진행 중') : undefined,
    })),
  });
  if (!authenticated) return make('guest', -1);

  const sameModelLicense = !license?.modelId || license.modelId === ownedModel?.id;
  if (sameModelLicense && ['verified', 'suspended'].includes(ownedModel?.status)
    && ['active', 'revoked', 'suspended'].includes(license?.status)) {
    const expiry = license.licenseValidUntil && Date.parse(license.licenseValidUntil);
    const expiring = Number.isFinite(expiry) && expiry <= new Date(now).getTime() + 30 * 86400000;
    const flag = license.status === 'revoked' ? 'revoked'
      : ownedModel.status === 'suspended' || license.status === 'suspended' ? 'paused'
      : expiring ? 'expiring' : 'none';
    return make('active', 5, 'done', { flag });
  }
  if (ownedModel?.status === 'awaiting_confirm' || enrollment?.status === 'confirm_pending') {
    return make('review', 4, 'todo', { sub: 'confirm', action: route('테스트컷 고르기', '/model/confirm') });
  }
  if (enrollment?.status === 'review_pending') {
    return make('review', 3, 'progress', { sub: 'review', action: reload('검수 상태 새로고침') });
  }
  if (['processing', 'asset_building', 'passed'].includes(enrollment?.status)
    || (ownedModel?.status === 'pending' && !enrollment && (ownedModel.redoCount > 0 || hasLicense))) {
    return make('review', 3, 'progress', { sub: 'assets', action: reload('생성 상태 새로고침') });
  }
  if (enrollment && !['cancelled', 'failed'].includes(enrollment.status)) {
    // 조건·증서 단계도 등록 위저드(3·4단계)가 이어받아요. 위저드가 저장된 단계를 복원해요.
    return make('onboarding', 2, 'todo', { step: 2, action: route('이어서 하기', '/model/register') });
  }
  if (application?.status === 'rejected') {
    return make('onboarding', 1, 'progress', { step: 3, action: route('다시 지원하기', '/model/apply') });
  }
  if (application?.status === 'under_review') {
    return make('onboarding', 1, 'progress', { step: 0, action: { label: '지원 취소', kind: 'cancel' } });
  }
  if (ownedModel) {
    return make('onboarding', 2, 'todo', { step: 2, action: route('이어서 하기', '/model/register') });
  }
  if (application?.status === 'approved' || !applicationRequired) {
    return make('onboarding', 2, 'todo', { step: 1, action: route('등록 시작하기', '/model/register') });
  }
  return make('onboarding', 0, 'todo', { step: 0, needsApplication: true, action: route(APPLY_LABEL, '/model/apply') });
}
