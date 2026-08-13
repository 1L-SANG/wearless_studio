const snapshotJob = (job) => ({
  projectId: job?.projectId || null,
  status: job?.status || 'idle',
  progress: Number(job?.progress) || 0,
  errorMessage: job?.errorMessage || '',
});

export function createMannequinCompletionState(job) {
  return { previousJob: snapshotJob(job) };
}

/* 완료 토스트의 작은 상태 머신. 매 호출마다 previousJob 을 먼저 전진시키므로 같은 idle/100
   스냅샷이 다시 들어와도 한 번 관찰한 전환은 다시 알리지 않는다. */
export function advanceMannequinCompletion(state, job, pathname) {
  const previousJob = state?.previousJob || snapshotJob();
  const nextJob = snapshotJob(job);
  const completedProjectId = previousJob.status === 'running'
    && nextJob.status === 'idle'
    && nextJob.progress === 100
    && !nextJob.errorMessage
    && previousJob.projectId
    && nextJob.projectId === previousJob.projectId
    && pathname !== '/create/mannequin'
    ? nextJob.projectId
    : null;

  return {
    state: { previousJob: nextJob },
    completedProjectId,
  };
}
