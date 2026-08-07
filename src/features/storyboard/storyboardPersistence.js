import { api } from '@/lib/api/index.js';
import { invalidateStoryboardEntryPrefetch } from './storyboardEntryPrefetch.js';

/* 콘티 저장 직렬 체인 — 모듈 스코프: 컴포넌트 수명(빠른 이탈→재진입의 구·신 인스턴스)과
   프로젝트 경계를 넘어 전 저장의 순서를 보장한다. 늦게 도착한 옛 PUT이 최신을 덮어쓸 수 없다.
   lastSaved 는 프로젝트별 — 다른 프로젝트의 참조와 비교되는 오판 방지. */
let sbSaveChain = Promise.resolve();
export const sbLastSaved = new Map();
export const sbPending = new Map();
export const sbSaveIdle = () => sbSaveChain.catch(() => {});

export const sbStable = (value) => JSON.stringify(value, (key, item) => (
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.keys(item).sort().reduce((out, itemKey) => { out[itemKey] = item[itemKey]; return out; }, {})
    : item
));

export function sbSaveNow(projectId, getSnapshot, options = {}) {
  // 예약된 저장이 착지하기 전의 서버본을 프리패치가 노출하지 않도록 즉시 무효화한다.
  if (projectId) invalidateStoryboardEntryPrefetch(projectId);
  const run = sbSaveChain.catch(() => {}).then(() => {
    const snapshot = getSnapshot();
    if (!projectId || !snapshot) return;
    if (sbLastSaved.get(projectId) === snapshot) {
      sbPending.delete(projectId);
      return;
    }
    return api.saveStoryboard(projectId, snapshot, options).then(
      () => { sbLastSaved.set(projectId, snapshot); sbPending.delete(projectId); },
      (error) => { sbPending.set(projectId, snapshot); throw error; },
    );
  });
  sbSaveChain = run.catch(() => {});
  return run;
}
