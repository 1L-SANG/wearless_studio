import { jobFailure } from '../../lib/api/jobFailure.js';

// Three-minute UI polling expiry is not a failed image. Keep its accepted job.
export async function resolveCanvasAiSubmission(submit, { getJob,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), active = () => true, acceptedJobId = () => null } = {}) {
  let response;
  try { response = await submit(); }
  catch (error) {
    const jobId = error?.jobId || acceptedJobId();
    const terminal = ['job_failed', 'job_cancelled'].includes(error?.code);
    const retryable = error?.code === 'job_timeout' || (!error?.status || error.status >= 500);
    if (!jobId || !getJob || terminal || !retryable) {
      if (jobId && !terminal) error.submissionUnknown = true;
      throw error;
    }
    while (active()) {
      await sleep(4000);
      let job;
      try { job = await getJob(jobId); }
      catch { continue; }
      if (job?.status === 'done') { response = job.result; break; }
      if (['error', 'cancelled'].includes(job?.status)) {
        const failure = jobFailure(job);
        if (job.errorMessage) failure.message = job.errorMessage;
        throw failure;
      }
    }
    if (!active()) throw Object.assign(new Error('사진 작업을 나중에 확인해 주세요.'), { submissionUnknown: true });
  }
  if (!response?.data?.src) throw Object.assign(new Error('생성한 사진 주소를 확인하지 못했어요.'), {
    submissionUnknown: true, ...(acceptedJobId() ? { jobId: acceptedJobId() } : {}),
  });
  return response;
}
