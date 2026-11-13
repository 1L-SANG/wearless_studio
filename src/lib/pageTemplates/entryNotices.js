const notices = new Map();
export const setStoryboardEntryNotice = (projectId, notice) => notices.set(projectId, notice);
export const storyboardEntryNotice = projectId => notices.get(projectId) || null;

export const STORYBOARD_REVIEW_MESSAGE = '콘티가 새 방식으로 바뀌었어요. 확인하고 다음을 눌러 주세요';

// getStoryboard rebuilds old drafts in memory. The entry notice retains the
// persisted format until the seller confirms the new storyboard with Next.
export async function resolveStoryboardResume(api, projectId, path) {
  if (!projectId || !['/create/mannequin', '/create/generating'].includes(path)) return { path, review: false };
  const blocks = await api.getStoryboard(projectId);
  if (!Array.isArray(blocks)) throw new Error('콘티를 확인하지 못했어요. 다시 시도해 주세요.');
  const review = api.storyboardEntryNotice?.(projectId) === 'rebuilt'
    || blocks?.some(block => block.taxonomyVersion !== 4);
  return { path: review ? '/create/storyboard' : path, review: Boolean(review) };
}
