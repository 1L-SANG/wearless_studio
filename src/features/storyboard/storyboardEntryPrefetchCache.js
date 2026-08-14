export const STORYBOARD_ENTRY_TTL_MS = 60_000;

export function createStoryboardEntryPrefetchCache({
  ttlMs = STORYBOARD_ENTRY_TTL_MS,
  now = () => Date.now(),
} = {}) {
  let activeProjectId = null;
  const entries = new Map();

  const scopeTo = (projectId) => {
    if (activeProjectId != null && activeProjectId !== projectId) entries.clear();
    activeProjectId = projectId;
  };

  const freshEntry = (projectId) => {
    if (!projectId) return null;
    scopeTo(projectId);
    const entry = entries.get(projectId);
    if (!entry) return null;
    if (entry.expiresAt <= now()) {
      entries.delete(projectId);
      return null;
    }
    return entry;
  };

  const prefetch = (projectId, load, waitForIdle = () => Promise.resolve()) => {
    const existing = freshEntry(projectId);
    if (existing) return existing.promise;
    if (!projectId) return Promise.resolve(null);

    const entry = {
      status: 'pending',
      data: null,
      expiresAt: now() + ttlMs,
      promise: null,
    };
    entry.promise = Promise.resolve()
      .then(() => waitForIdle())
      .then(() => load())
      .then((data) => {
        if (entries.get(projectId) !== entry) return null;
        entry.status = 'ready';
        entry.data = data;
        entry.expiresAt = now() + ttlMs;
        return data;
      })
      .catch(() => {
        if (entries.get(projectId) === entry) {
          entry.status = 'failed';
          entry.expiresAt = now() + ttlMs;
        }
        return null;
      });
    entries.set(projectId, entry);
    return entry.promise;
  };

  const peek = (projectId) => {
    const entry = freshEntry(projectId);
    return entry?.status === 'ready' ? entry.data : null;
  };

  const consume = async (projectId) => {
    const entry = freshEntry(projectId);
    if (!entry) return null;
    if (entry.status === 'ready') return entry.data;
    if (entry.status === 'failed') return null;
    await entry.promise;
    return peek(projectId);
  };

  const invalidate = (projectId) => {
    if (!projectId) return;
    scopeTo(projectId);
    entries.delete(projectId);
  };

  return { prefetch, peek, consume, invalidate };
}
