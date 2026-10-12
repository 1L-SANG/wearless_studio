/** 준비는 생성 시도가 아니다. 접수된 job은 기존 식별자로 즉시 합류한다. */
export async function waitForInitialMannequinPreparation(projectId, {
  readAttempt, getPromotionTask, photosReady, readProject, readSession, isScreenActive,
}) {
  if (readAttempt(projectId)?.jobId) return true;
  const session = readSession();
  const active = () => readProject() === projectId && readSession() === session && isScreenActive();
  if (!active()) return false;
  const task = getPromotionTask(projectId);
  if (task && task.status !== 'settled') await photosReady(projectId);
  return active();
}
