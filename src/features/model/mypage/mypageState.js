export function isRegistrationJourney(journey) {
  return journey?.mode === 'review'
    || (journey?.mode === 'onboarding' && journey.step === 2 && !journey.needsApplication);
}

export function registrationCard(journey, enrollment) {
  if (!isRegistrationJourney(journey)) return null;
  if (journey.mode === 'review' && journey.sub === 'confirm') {
    return {
      title: '공개할 테스트컷을 골라 주세요.',
      description: '마음에 드는 컷을 확인하고 공개할 프로필을 정해요.',
      label: '테스트컷 확인하기',
      to: '/model/confirm',
      currentStep: 5,
    };
  }
  if (journey.mode === 'review') {
    return {
      title: '테스트컷을 준비하고 있어요.',
      description: '모델님의 얼굴을 활용한 테스트컷은 2일 이내 보내드릴게요. 테스트컷을 확정하면 라이선스 증서 발급과 함께 등록이 끝나요.',
      label: '등록 내용 확인',
      to: '/model/register',
      currentStep: 4,
    };
  }
  if (['license_pending', 'processing', 'asset_building'].includes(enrollment?.status)) {
    return {
      title: '사용 조건을 정해 주세요.',
      description: '조건을 정하고 증서 발급하기를 누르면 등록 절차가 끝나요.',
      label: '이어서 등록하기',
      to: '/model/register',
      currentStep: 3,
    };
  }
  return {
    title: '등록을 이어서 마쳐 주세요.',
    description: '얼굴 사진과 사용 조건을 확인하면 등록이 끝나요.',
    label: '이어서 등록하기',
    to: '/model/register',
    currentStep: 2,
  };
}

export function activityOptions(journey, model) {
  if (journey?.flag === 'revoked' || isRegistrationJourney(journey)) return ['contact', 'data'];
  if (journey?.flag === 'paused') {
    return [model?.suspensionSource === 'owner' ? 'resume' : 'contact', 'revoke', 'data'];
  }
  if (journey?.mode === 'active') return ['pause', 'revoke', 'data'];
  return ['contact', 'data'];
}

export function tabFromHash(hash) {
  const value = String(hash ?? '').trim().replace(/^#/, '').toLowerCase();
  if (value === 'payout' || value === 'earnings') return 'payout';
  if (value === 'license' || value === 'conditions') return 'license';
  return 'usage';
}
