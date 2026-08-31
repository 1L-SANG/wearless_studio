import poseFront from './assets/pose-front.svg';
import poseAngle45 from './assets/pose-angle45.svg';
import poseSide from './assets/pose-side.svg';

// examplePhoto = 실사진 예시 자리(public/models/pose/). 파일이 있으면 그 사진을 쓰고,
// 없으면(404) 카드가 exampleImage(라인 일러스트)로 되돌아간다 — 사진 3장을 그 경로에
// 떨구는 것만으로 교체된다(코드 수정 0). 각도가 말로만 설명될 때보다 재촬영이 줄어든다.
export const ENROLLMENT_ANGLES = Object.freeze([
  { value: 'front', label: '정면', guide: '카메라를 정면으로 바라보세요. 두 눈·두 귀가 모두 보여요.',
    exampleImage: poseFront, examplePhoto: '/models/pose/front.webp' },
  { value: 'angle45', label: '45도(반측면)', guide: '고개를 살짝만 돌린 반측면. 두 눈은 여전히 다 보여요.',
    exampleImage: poseAngle45, examplePhoto: '/models/pose/angle45.webp' },
  { value: 'side', label: '측면(옆모습)', guide: '고개를 끝까지 돌린 완전 옆모습. 한쪽 얼굴과 귀 하나만 보여요.',
    exampleImage: poseSide, examplePhoto: '/models/pose/side.webp' },
]);

export const ENROLLMENT_STEPS = Object.freeze([
  'consent', 'identity', 'photos', 'physique', 'profile', 'liveness', 'processing', 'terms', 'done',
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
