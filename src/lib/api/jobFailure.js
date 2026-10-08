import { clientEvents } from '../clientEvents.js';

// 서버가 최종 추가 생성까지 사용한 품질 종료는 일반 네트워크 실패와 구분한다.
// 라이선스 확인 서비스(holder) 사정은 여기 오지 않는다 — 서버가 일반 실패로 바꿔서 보낸다.
// 셀러는 "라이선스 확인 중"·"켜는 중" 을 몰라야 한다(2026-09-14 제품 결정).
const PASS_THROUGH_CODES = new Set(['mannequin_quality_failed']);
export function jobFailureMessage(message, { creditsReleased = false } = {}) {
  if (message && !/실패/.test(message)) return message;
  return '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.'
    + (creditsReleased ? ' 크레딧은 빠지지 않았어요.' : '');
}
export function jobFailure(job, options) {
  const error = new Error(jobFailureMessage(job.errorMessage, options));
  const code = job.result?.errorCode;
  error.code = job.status === 'cancelled' ? 'job_cancelled' : PASS_THROUGH_CODES.has(code) ? code : 'job_failed';
  const requestId = job.requestId || job.metadata?.requestId || job.meta?.requestId;
  if (requestId) error.requestId = requestId;
  if (job.id || job.jobId) error.jobId = job.id || job.jobId;
  return clientEvents.bindGeneration(error, clientEvents.generationOf(job));
}
