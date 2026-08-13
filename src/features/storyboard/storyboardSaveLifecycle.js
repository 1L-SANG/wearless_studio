export const STORYBOARD_AUTOSAVE_DELAY_MS = 10_000;

export function scheduleStoryboardAutosave(timerRef, callback, {
  delay = STORYBOARD_AUTOSAVE_DELAY_MS,
  setTimer = globalThis.setTimeout,
  clearTimer = globalThis.clearTimeout,
} = {}) {
  if (timerRef.current != null) clearTimer(timerRef.current);
  const timer = setTimer(() => {
    if (timerRef.current === timer) timerRef.current = null;
    callback();
  }, delay);
  timerRef.current = timer;
  return () => {
    clearTimer(timer);
    if (timerRef.current === timer) timerRef.current = null;
  };
}

export function bindStoryboardExitFlush({
  windowTarget = globalThis.window,
  documentTarget = globalThis.document,
  getProjectId,
  flushLatest,
}) {
  const flush = () => {
    void Promise.resolve(flushLatest(getProjectId(), { keepalive: true })).catch(() => {});
  };
  const flushWhenHidden = () => {
    if (documentTarget?.hidden === true) flush();
  };

  windowTarget?.addEventListener?.('pagehide', flush);
  documentTarget?.addEventListener?.('visibilitychange', flushWhenHidden);
  return () => {
    windowTarget?.removeEventListener?.('pagehide', flush);
    documentTarget?.removeEventListener?.('visibilitychange', flushWhenHidden);
    flush();
  };
}
