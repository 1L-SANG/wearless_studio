const PREFIX = 'wl_fmApplyDraft:';

export function readApplyDraft(userId) {
  if (!userId) return null;
  try {
    const draft = JSON.parse(sessionStorage.getItem(PREFIX + userId));
    return draft && typeof draft === 'object' && !Array.isArray(draft) ? draft : null;
  } catch { return null; }
}

export function writeApplyDraft(userId, form) {
  if (!userId) return;
  try { sessionStorage.setItem(PREFIX + userId, JSON.stringify(form)); } catch {}
}

export function clearApplyDraft(userId) {
  if (!userId) return;
  try { sessionStorage.removeItem(PREFIX + userId); } catch {}
}
