export function createPromotionTaskRegistry({ maxTracked = 8, snapshot = (task) => task } = {}) {
  const tasks = new Map();
  const listenersByKey = new Map();

  const notify = (key) => {
    const state = tasks.has(key) ? snapshot(tasks.get(key)) : null;
    for (const listener of listenersByKey.get(key) || []) {
      try { listener(state); } catch { /* 한 구독자 오류가 다른 구독자를 막지 않는다 */ }
    }
  };

  const set = (key, task) => {
    tasks.set(key, task);
    notify(key);
    if (tasks.size > maxTracked) {
      const oldest = tasks.keys().next().value;
      if (oldest !== key) {
        tasks.delete(oldest);
        notify(oldest);
      }
    }
    return task;
  };

  const remove = (key) => {
    const removed = tasks.delete(key);
    if (removed) notify(key);
    return removed;
  };

  const move = (from, to) => {
    const task = tasks.get(from);
    if (!task) return null;
    tasks.delete(from);
    notify(from);
    set(to, task);
    return task;
  };

  const subscribe = (key, listener) => {
    if (!key || typeof listener !== 'function') return () => {};
    const listeners = listenersByKey.get(key) || new Set();
    listeners.add(listener);
    listenersByKey.set(key, listeners);
    listener(tasks.has(key) ? snapshot(tasks.get(key)) : null);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) listenersByKey.delete(key);
    };
  };

  return {
    get: (key) => tasks.get(key) ?? null,
    set,
    delete: remove,
    move,
    notify,
    subscribe,
    values: () => tasks.values(),
  };
}
