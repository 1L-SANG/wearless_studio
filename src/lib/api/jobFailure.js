// 서버가 최종 추가 생성까지 사용한 품질 종료는 일반 네트워크 실패와 구분한다.
export function jobFailure(job) {
  const error = new Error(job.errorMessage || '작업에 실패했어요.');
  error.code = job.result?.errorCode === 'mannequin_quality_failed'
    ? 'mannequin_quality_failed' : 'job_failed';
  return error;
}
