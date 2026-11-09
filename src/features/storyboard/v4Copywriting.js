const pending = new Map();
const confirmed = new Set();

// V4 always generates copy. Reuse the store's serialized project patch and wait
// for the same in-flight patch before leaving, even after its optimistic update.
export function ensureV4StoryboardCopywriting(projectId, getState, { force = false } = {}) {
  if (!projectId) return Promise.resolve();
  if (pending.has(projectId)) return pending.get(projectId);
  const state = getState();
  if (state.projectId !== projectId || !force && state.copywriting === true && confirmed.has(projectId)) return Promise.resolve();
  const previous = state.copywriting;
  confirmed.delete(projectId);
  const task = Promise.resolve().then(() => state.setCopywriting(true)).then(() => { confirmed.add(projectId); }).catch(error => {
    if (getState().projectId === projectId) getState().restoreCopywriting(previous);
    throw error;
  }).finally(() => { pending.delete(projectId); });
  pending.set(projectId, task);
  return task;
}
