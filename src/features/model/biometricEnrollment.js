import poseFront from './assets/pose-front.svg';
import poseAngle45 from './assets/pose-angle45.svg';
import poseSide from './assets/pose-side.svg';

export const ENROLLMENT_ANGLES = Object.freeze([
  { value: 'front', label: '정면', guide: '정면을 바라보고 얼굴 전체가 나오게 찍어주세요.',
    exampleImage: poseFront },
  { value: 'angle45', label: '45도', guide: '정면에서 약 45도만 돌려 반측면이 보이게 찍어주세요.',
    exampleImage: poseAngle45 },
  { value: 'side', label: '측면', guide: '고개를 약 90도 돌려 옆모습 윤곽이 보이게 찍어주세요.',
    exampleImage: poseSide },
]);

export const ENROLLMENT_STEPS = Object.freeze([
  'consent', 'identity', 'photos', 'profile', 'liveness', 'processing', 'terms', 'done',
]);

const REASON_COPY = Object.freeze({
  id_portrait_unavailable: '신분증 사진을 확인할 수 없어요.',
  liveness_retry: '라이브 인증을 새 등록에서 다시 시도해 주세요.',
  liveness_failed: '라이브 인증을 통과하지 못했어요.',
  face_match_failed: '얼굴 일치 확인에 실패했어요.',
  qc_unavailable: '얼굴 검사를 지금 수행할 수 없어요.',
  identity_recovery_required: '기존 모델 소유권 확인이 필요해요.',
  vc_issue_delayed: 'VC 발급이 지연되고 있어요. 잠시 후 다시 시도해 주세요.',
});

export function enrollmentReasonMessage(reason) {
  return REASON_COPY[reason] || '인증을 완료하지 못했어요. 다시 시도해 주세요.';
}

export function nextEnrollmentStep(enrollment) {
  if (!enrollment) return 'consent';
  if (enrollment.status === 'identity_pending') return 'identity';
  if (enrollment.status === 'photos_pending') return 'photos';
  if (enrollment.status === 'liveness_pending') return 'liveness';
  if (enrollment.status === 'processing' || enrollment.status === 'asset_building') return 'processing';
  if (enrollment.status === 'license_pending' || enrollment.status === 'vc_pending') return 'terms';
  if (enrollment.status === 'passed') return 'done';
  return 'failed';
}
