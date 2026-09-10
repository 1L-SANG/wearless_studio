import { APPLY_LABEL } from '../facemarket-landing/registerCta.js';
import { REVIEW_SLA_LABEL } from '../facemarket-landing/facemarketTerms.js';

export const HUB_STEPS = Object.freeze([
  { key: 'application', label: '지원 접수', description: null },
  { key: 'review', label: '내부 검토', description: `지원서를 검토중이에요. ${REVIEW_SLA_LABEL} 결과를 전달해드릴게요.` },
  { key: 'registration', label: '모델 등록', description: '얼굴 구현을 위한 이미지들과, 라이선스 증서에 대한 설정이 필요해요.' },
  { key: 'final_review', label: '최종 검토', description: `등록서를 최종 검토중이에요. ${REVIEW_SLA_LABEL} 결과를 전달해드릴게요.` },
  { key: 'confirm', label: '프로필 이미지 확정', description: '저희가 만들어드리는 프로필 이미지 중 사용될 이미지를 선택해주시면 모델 등록이 끝나요.' },
]);

const route = (label, to) => ({ label, kind: 'route', to });
const reload = (label) => ({ label, kind: 'reload' });

export function hasCurrentEnrollmentLicense(licenses = []) {
  return licenses.some((license) => ['pending', 'active'].includes(license?.status));
}

function journeyAt(currentIndex, action) {
  return {
    mode: 'onboarding',
    currentIndex,
    action,
    steps: HUB_STEPS.map((step, index) => ({
      ...step,
      state: index < currentIndex ? 'done' : index === currentIndex ? 'current' : 'upcoming',
    })),
  };
}

function applicationJourney(application, applicationRequired) {
  if (application?.status === 'under_review') {
    return journeyAt(1, { label: '지원 취소', kind: 'cancel' });
  }
  if (application?.status === 'approved') {
    return journeyAt(2, route('등록 시작하기', '/model/register'));
  }
  if (application?.status === 'rejected') {
    return journeyAt(1, route('다시 지원하기', '/model/apply'));
  }
  if (!applicationRequired) {
    return journeyAt(2, route('등록 시작하기', '/model/register'));
  }
  return journeyAt(0, route(APPLY_LABEL, '/apply'));
}

export function resolveHubJourney({
  ownedModel = null,
  enrollment = null,
  application = null,
  applicationRequired = true,
  hasLicense = false,
} = {}) {
  if (ownedModel?.status === 'verified') {
    return {
      mode: 'active',
      currentIndex: 4,
      action: null,
      steps: HUB_STEPS.map((step) => ({ ...step, state: 'done' })),
    };
  }

  if (ownedModel?.status === 'awaiting_confirm') {
    return journeyAt(4, route('이미지 선택하기', '/model/confirm'));
  }

  if (ownedModel?.status === 'pending' && (!enrollment || enrollment.status === 'passed')
    && (ownedModel?.redoCount > 0 || hasLicense)) {
    return journeyAt(3, reload('검토 상태 새로고침'));
  }

  if (enrollment) {
    const status = enrollment.status;
    if (['review_pending', 'processing', 'asset_building'].includes(status)) {
      return journeyAt(3, reload('검토 상태 새로고침'));
    }
    if (['confirm_pending', 'awaiting_confirm'].includes(status)) {
      return journeyAt(4, route('이미지 선택하기', '/model/confirm'));
    }
    if (status === 'passed') return journeyAt(4, reload('확정 상태 새로고침'));
    if (['license_pending', 'terms_pending', 'vc_pending'].includes(status)) {
      const id = encodeURIComponent(enrollment.id || '');
      return journeyAt(2, route('조건·증서 이어가기', `/model/license?step=terms&enrollment=${id}`));
    }
    return journeyAt(2, route('등록 이어가기', '/model/register'));
  }

  if (ownedModel && ['pending', 'reverification_required'].includes(ownedModel.status)) {
    return journeyAt(2, route('등록 이어가기', '/model/register'));
  }

  return applicationJourney(application, applicationRequired);
}
