import { nextEnrollmentStep } from '../model/biometricEnrollment.js';
import { seoulDateKey } from '../../lib/datetime.js';
import { registerCta } from './registerCta.js';

const STEP_NAMES = {
  identity: '본인확인', photos: '사진 등록', liveness: '본인확인 마무리',
  processing: '모델 이미지 준비', terms: '사용 조건 정하기', consent: '동의 확인',
};
const nextDetail = step => step ? { before: '다음은 ', strong: step, after: '' } : null;

// CTA 문구와 목적지는 registerCta만 판정한다. 이 함수는 히어로에 더할 상태 정보만 만든다.
export function landingStatusPill({ ownedModel = null, enrollment = null, application = null, settlement = null } = {}) {
  const cta = registerCta(ownedModel, enrollment, { application, scope: 'landing' });
  const pill = (tone, pulse, title, detail) => ({ tone, pulse, title, detail, cta });

  if (ownedModel?.status === 'verified') {
    const count = settlement?.monthCount;
    const detail = Number.isInteger(count) && count >= 0
      ? { before: '이번 달 ', strong: `${count}건`, after: count === 0 ? '' : ' 쓰였어요' }
      : null;
    return pill('live', true, '공개 중', detail);
  }
  if (enrollment && !['cancelled', 'failed', 'passed'].includes(enrollment.status)) {
    return pill('progress', false, '등록 진행 중', nextDetail(STEP_NAMES[nextEnrollmentStep(enrollment)]));
  }
  if (ownedModel) {
    return pill('progress', false, '등록 진행 중', nextDetail('모델 이미지 준비'));
  }
  if (application?.status === 'under_review') {
    const date = seoulDateKey(application.createdAt, null);
    const detail = date
      ? { before: '', strong: `${Number(date.split('-')[1])}월 ${Number(date.split('-')[2])}일`, after: ' 접수' }
      : null;
    return pill('review', true, '검토 중', detail);
  }
  if (application?.status === 'approved') return pill('approved', false, '승인됐어요', nextDetail('본인확인'));
  if (application?.status === 'rejected') return pill('rejected', false, '이번엔 어려워요', null);
  return null;
}
