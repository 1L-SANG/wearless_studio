import { api } from '../../lib/api/index.js';
import { createStoryboardEntryPrefetchCache } from './storyboardEntryPrefetchCache.js';
export { shouldRenderStoryboardLoadingFrame } from './storyboardEntryPrefetchCache.js';

export function loadStoryboardEntry(projectId, apiClient = api) {
  return Promise.all([
    apiClient.getStoryboard(projectId),
    apiClient.getCatalogs(),
    apiClient.getMatchClothing(projectId),
    apiClient.getProduct(projectId),
    apiClient.getAnalysis(projectId),
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
