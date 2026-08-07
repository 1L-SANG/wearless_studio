/* 배선 래퍼 — 순수 코어에 실제 api·store·sessionStorage 를 연결한 앱 싱글턴.
   콘티(진입 시 발사)와 마네킹(컷이 없으면 발사)이 이 모듈 하나를 공유한다. */
import { api } from '../../lib/api/index.js';
import { useAppStore } from '../../store/useAppStore.js';
import { markInitialGenerationRequested } from './initialGenerationSession.js';
import { createMannequinGenerationRunner } from './generationRunnerCore.js';

export function updateMannequinJob(pid, patch) {
  const { projectId, setMannequinJob } = useAppStore.getState();
  if (projectId !== pid) return;
  setMannequinJob({ projectId: pid, ...patch });
}

export function generationProgressFor(pid) {
  const job = useAppStore.getState().mannequinJob;
  return job?.projectId === pid ? Number(job.progress) || 0 : 0;
}

const runner = createMannequinGenerationRunner({
  generate: (pid, options) => api.generateMannequins(pid, options),
  readProgress: generationProgressFor,
  onJobChange: updateMannequinJob,
  onRequested: markInitialGenerationRequested,
});

export const requestMannequinGeneration = (pid) => runner.request(pid);
export const isMannequinGenerationRunning = (pid) => runner.isRunning(pid);
