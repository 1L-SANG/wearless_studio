import { createStoryboardEntryPrefetchCache } from './storyboardEntryPrefetchCache.js';
import {
  getProductPhotoPromotionTask,
  NEW_PROJECT_KEY,
  productPhotosReady,
} from '../../lib/productPhotoPromotion.js';

// api 를 모듈 top-level 에서 정적 import 하지 않는다 — lib/api/index.js 는 mock/http 두
// 어댑터를 모두 물어 `@/` 별칭 경로로 끌어오는데, 이 파일은 (invalidate 등 캐시 조작
// 함수만 쓰는) product-input/saveRouting.js 처럼 node --test 로 직접 실행되는 모듈에서도
// import 된다. 정적 import 였다면 그 시점에 별칭 해석이 실패한다(Vite 밖이라 `@/` 를
// 모른다) — apiClient 를 안 넘긴 실사용(런타임) 경로에서만 지연 로드한다.
export async function loadStoryboardEntry(projectId, apiClient) {
  const client = apiClient || (await import('../../lib/api/index.js')).api;
  return Promise.all([
    client.getStoryboard(projectId),
    client.getCatalogs(),
    client.getMatchClothing(projectId),
    client.getProduct(projectId),
    client.getAnalysis(projectId),
  ]);
}

const storyboardEntryCache = createStoryboardEntryPrefetchCache();

export const prefetchStoryboardEntry = (projectId, waitForIdle) => (
  storyboardEntryCache.prefetch(
    projectId,
    () => loadStoryboardEntry(projectId),
    waitForIdle,
  )
);
export const peekStoryboardEntry = (projectId) => storyboardEntryCache.peek(projectId);
export const consumeStoryboardEntry = (projectId) => storyboardEntryCache.consume(projectId);
export const invalidateStoryboardEntryPrefetch = (projectId) => storyboardEntryCache.invalidate(projectId);

export async function prefetchStoryboardAfterProductPhotos(projectId, {
  getTask = getProductPhotoPromotionTask,
  ready = productPhotosReady,
  prefetch = prefetchStoryboardEntry,
  isActive = () => true,
} = {}) {
  if (!projectId || !isActive()) return null;
  const pending = [getTask(NEW_PROJECT_KEY), getTask(projectId)]
    .find((task) => task?.status === 'pending');
  if (pending) {
    try { await pending.promise; } catch { return null; }
  }
  if (!isActive()) return null;
  try { await ready(projectId); } catch { return null; }
  if (!isActive() || getTask(projectId)?.status === 'failed') return null;
  return prefetch(projectId);
}
