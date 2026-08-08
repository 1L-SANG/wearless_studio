/* 배선 래퍼 — 순수 코어에 실제 api·store·sessionStorage 를 연결한 앱 싱글턴.
   콘티(진입 시 발사)와 마네킹(컷이 없으면 발사)이 이 모듈 하나를 공유한다. */
import { api } from '../../lib/api/index.js';
import { useAppStore } from '../../store/useAppStore.js';
import { markInitialGenerationRequested } from './initialGenerationSession.js';
import { createMannequinGenerationRunner } from './generationRunnerCore.js';

export function updateMannequinJob(pid, patch) {
  const { projectId, setMannequinJob } = useAppStore.getState();
  // 스토어가 이미 다른 프로젝트로 옮겨갔으면 쓰지 않는다 — 지난 프로젝트의 진행률이 현재
  // 프로젝트의 리본을 덮어쓰면 안 된다. 이 가드는 옳고, 지우지 말 것.
  //
  // 다만 여기서 **러너의 종결 기록(idle/error)까지 조용히 버려질 수 있다.** 새로 만들기
  // (beginProject) 로 projectId 가 null 이 된 뒤 이전 프로젝트의 job 이 끝나면 그 종결
  // 패치는 여기서 반환된다. 즉 러너의 암묵적 계약 "running 뒤엔 반드시 idle 또는 error 가
  // 온다" 는 프로젝트 전환을 가로질러서는 성립하지 않는다.
  //
  // 지금 문제가 안 보이는 이유는 우연이다(설계된 억제가 아니다): beginProject 가
  // mannequinJob 도 initialMannequinJob() 으로 되돌리고, 완료 배지는 job.projectId 가
  // 실행 중이던 id 와 일치할 때만 뜬다(ChromeLayout.jsx). projectId 가 null 이면 둘 다 걸린다.
  // 앞으로 mannequinJob 을 읽는 소비자를 추가한다면 저 계약에 기대지 말 것 — 종결이 영영
  // 오지 않는 running 을 직접 다뤄야 한다.
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
