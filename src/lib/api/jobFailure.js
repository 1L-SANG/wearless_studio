// 서버가 최종 추가 생성까지 사용한 품질 종료는 일반 네트워크 실패와 구분한다.
// holder_starting 도 그대로 넘긴다 — 고장이 아니라 "라이선스 확인 서비스를 켜는 중"이라
// 1~2분 뒤 같은 버튼이 그대로 된다. job_failed 로 뭉개면 화면이 그 차이를 못 만든다.
const PASS_THROUGH_CODES = new Set(['mannequin_quality_failed', 'holder_starting']);
export function jobFailure(job) {
  const error = new Error(job.errorMessage || '작업에 실패했어요.');
  const code = job.result?.errorCode;
  error.code = PASS_THROUGH_CODES.has(code) ? code : 'job_failed';
  return error;
}
