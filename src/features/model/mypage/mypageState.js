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
      title: '사진을 검수하고 있어요.',
      description: '검수가 끝나면 다음 단계를 알려드릴게요.',
      label: '등록 내용 확인',
      to: '/model/register',
      currentStep: 4,
    };
  }
  if (['vc_pending', 'license_pending'].includes(enrollment?.status)) {
    return {
      title: '라이선스 증서를 발급하고 있어요.',
      description: '발급이 완료되면 내 증서 카드에서 확인할 수 있어요.',
      label: '진행 상황 확인',
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
