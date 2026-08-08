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
  // "최초 생성은 우리가 요청했다" 는 주장은 **실제로 job 이 생겼을 때만** 해야 한다.
  // 컷이 이미 있어 서버가 200 으로 답한 호출까지 플래그를 세우면
  // cutsExistedBeforeInitialGeneration 이 false 로 뒤집혀 유료 재생성이 조용히 막힌다.
  onJobStarted: markInitialGenerationRequested,
});

export const requestMannequinGeneration = (pid) => runner.request(pid);
export const isMannequinGenerationRunning = (pid) => runner.isRunning(pid);
