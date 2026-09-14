// 서버가 최종 추가 생성까지 사용한 품질 종료는 일반 네트워크 실패와 구분한다.
// 라이선스 확인 서비스(holder) 사정은 여기 오지 않는다 — 서버가 일반 실패로 바꿔서 보낸다.
// 셀러는 "라이선스 확인 중"·"켜는 중" 을 몰라야 한다(2026-09-14 제품 결정).
const PASS_THROUGH_CODES = new Set(['mannequin_quality_failed']);
export function jobFailure(job) {
  const error = new Error(job.errorMessage || '작업에 실패했어요.');
  const code = job.result?.errorCode;
  error.code = PASS_THROUGH_CODES.has(code) ? code : 'job_failed';
  return error;
}
