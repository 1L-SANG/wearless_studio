import { APPLY_LABEL } from '../facemarket-landing/registerCta.js';

export const HUB_STEPS = Object.freeze([
  { key: 'application', label: '지원 접수', description: '지원서를 보내고 검토 결과를 기다려요.' },
  { key: 'invite', label: '등록 링크', description: '승인 메일의 링크로 안전한 등록을 시작해요.' },
  { key: 'registration', label: '본인확인·사진·조건·증서', description: '본인확인과 사진, 사용 규칙, 증서 발급을 마쳐요.' },
  { key: 'review', label: '우리 검수', description: '제출한 정보와 사진을 같은 사람인지 확인해요.' },
  { key: 'assets', label: '테스트 컷 생성', description: '셀러에게 보일 디지털 트윈의 테스트 컷을 만들어요.' },
  { key: 'confirm', label: '확정', description: '테스트 컷을 확인하고 공개할 모습을 확정해요.' },
  { key: 'active', label: '활동 중', description: '정한 규칙 안에서 활동하고 사용 기록을 확인해요.' },
]);

const route = (label, to) => ({ label, kind: 'route', to });
const reload = (label) => ({ label, kind: 'reload' });

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
    return journeyAt(0, { label: '지원 취소', kind: 'cancel' });
  }
  if (application?.status === 'approved') {
    return journeyAt(1, route('등록 시작하기', '/model/register'));
  }
  if (application?.status === 'rejected') {
    return journeyAt(0, route('다시 지원하기', '/model/apply'));
  }
  if (!applicationRequired) {
    return journeyAt(1, route('등록 시작하기', '/model/register'));
  }
  return journeyAt(0, route(APPLY_LABEL, '/model/apply'));
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
      currentIndex: 6,
      action: null,
      steps: HUB_STEPS.map((step) => ({ ...step, state: 'done' })),
    };
  }

  if (enrollment) {
    const status = enrollment.status;
    if (status === 'review_pending') return journeyAt(3, reload('검수 상태 새로고침'));
    if (status === 'processing' || status === 'asset_building') {
      return hasLicense
        ? journeyAt(4, reload('생성 상태 새로고침'))
        : journeyAt(2, reload('등록 상태 새로고침'));
    }
    if (status === 'confirm_pending') {
      return journeyAt(5, route('테스트 컷 확인하기', '/model/confirm'));
    }
    if (status === 'passed') return journeyAt(5, reload('확정 상태 새로고침'));
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
